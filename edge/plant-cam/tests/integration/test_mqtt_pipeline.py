"""The whole pipeline against a real MQTT broker (throwaway mosquitto in Docker):
folder frame source -> analyzers -> outbox -> broker, plus broker outages,
commands, heartbeat, Last Will and graceful shutdown."""

from __future__ import annotations

import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import pytest
import yaml
from paho.mqtt.enums import CallbackAPIVersion

from plant_cam.app import App

pytestmark = pytest.mark.integration

IMAGE = "eclipse-mosquitto:2.0.21"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Broker:
    def __init__(self, tmp: Path) -> None:
        self.port = _free_port()
        self.name = f"plantcam-test-{self.port}"
        conf = tmp / "mosquitto.conf"
        # Persistent, like the real backend (RabbitMQ's durable queues hold
        # readings for consumers that are briefly away).
        conf.write_text(
            "listener 1883\nallow_anonymous true\npersistence true\npersistence_location /mosquitto/data/\n"
        )
        self.conf = conf

    def start(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.name,
                "-p",
                f"127.0.0.1:{self.port}:1883",
                "-v",
                f"{self.conf}:/mosquitto/config/mosquitto.conf:ro",
                IMAGE,
            ],
            check=True,
            capture_output=True,
        )
        self.wait_up()

    def wait_up(self, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    time.sleep(0.3)
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("broker did not come up")

    def stop(self) -> None:
        subprocess.run(["docker", "stop", "-t", "5", self.name], capture_output=True)  # SIGTERM: saves sessions

    def restart(self) -> None:
        subprocess.run(["docker", "start", self.name], check=True, capture_output=True)
        self.wait_up()

    def remove(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


class Listener:
    """Records everything under vert/#. A persistent session (clean_session
    False, QoS 1), so the broker queues messages for it across restarts --
    the way the gateway's durable queue does in production."""

    def __init__(self, port: int) -> None:
        self.messages: queue.Queue[tuple[str, dict[str, Any], bool]] = queue.Queue()
        self.client = mqtt.Client(
            CallbackAPIVersion.VERSION2, client_id=f"listener-{port}-{id(self)}", clean_session=False
        )
        self.client.on_connect = lambda c, *_: c.subscribe("vert/#", qos=1)
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(0.2, 1)
        self.client.connect("127.0.0.1", port)
        self.client.loop_start()

    def _on_message(self, _c: Any, _u: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            body = json.loads(msg.payload)
        except ValueError:
            body = {"raw": msg.payload.decode(errors="replace")}
        self.messages.put((msg.topic, body, bool(msg.retain)))

    def wait_for(self, kind: str, timeout: float = 30, pred: Any = None) -> tuple[str, dict[str, Any], bool]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                topic, body, retain = self.messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if topic.endswith(f"/{kind}") and (pred is None or pred(body)):
                return topic, body, retain
        raise AssertionError(f"no {kind} message within {timeout}s")

    def publish(self, topic: str, body: dict[str, Any]) -> None:
        self.client.publish(topic, json.dumps(body), qos=1).wait_for_publish(5)

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


@pytest.fixture
def broker(tmp_path: Path) -> Iterator[Broker]:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        pytest.skip(f"docker image {IMAGE} not available")
    b = Broker(tmp_path)
    b.start()
    yield b
    b.remove()


@pytest.fixture
def listener(broker: Broker) -> Iterator[Listener]:
    lst = Listener(broker.port)
    time.sleep(0.5)
    yield lst
    lst.close()


def _cfg(make_cfg, scenes: Path, broker: Broker, **extra: Any):
    return make_cfg(
        broker={"host": "127.0.0.1", "port": broker.port, "backoff_min_s": 0.2, "backoff_max_s": 1.0},
        camera={"source": "folder", "folder": str(scenes), "fps": 10},
        schedule={"interval_s": 3600, "warmup_s": 0.5, "change_detection": False},
        publishers=[{"name": "mqtt"}],
        heartbeat_s=5,
        **extra,
    )


def _run_app(app: App) -> threading.Thread:
    t = threading.Thread(target=app.run, daemon=True)
    t.start()
    return t


def test_reading_envelope_announce_and_commands(make_cfg, scenes, profile, broker, listener):
    app = App(_cfg(make_cfg, scenes, broker), profile)
    thread = _run_app(app)
    try:
        topic, announce, _ = listener.wait_for("announce")
        assert topic == "vert/zoneA/ai-camera/plantcam-test/announce"
        late = Listener(broker.port)  # a dashboard starting later still gets the announce (retained)
        try:
            _, _, retained = late.wait_for("announce", timeout=10)
            assert retained
        finally:
            late.close()
        assert announce["kind"] == "announce" and "analyze_now" in announce["capabilities"]["commands"]
        assert announce["capabilities"]["profile"].startswith("strawberry@")

        topic, env, _ = listener.wait_for("reading")
        assert topic == "vert/zoneA/ai-camera/plantcam-test/reading"
        assert env["schema_version"] == "1.0" and env["device_type"] == "ai-camera" and env["zone"] == "zoneA"
        assert env["ts"].endswith("Z") and isinstance(env["seq"], int)
        p = env["payload"]
        assert p["payload_schema"] == "plant-health/2" and p["profile_id"] == "strawberry"
        assert 0 <= p["health_score"] <= 1 and isinstance(p["note"], str)

        # a command from the dashboard (zone wildcarded on our side)
        listener.publish("vert/renamedZone/ai-camera/plantcam-test/cmd", {"payload": {"command": "analyze_now"}})
        _, env2, _ = listener.wait_for("reading", pred=lambda b: b["seq"] > env["seq"])
        assert env2["seq"] > env["seq"]

        _, hb, _ = listener.wait_for("status", timeout=15)
        assert hb["payload"]["kind"] == "heartbeat" and hb["payload"]["camera_ok"] == 1
    finally:
        app.stop()
        thread.join(30)
    _, off, _ = listener.wait_for("offline", timeout=10)
    assert off == {"kind": "offline", "device_id": "plantcam-test", "device_type": "ai-camera"}
    assert app.exit_code == 0


def test_outbox_replays_after_broker_outage_in_order(make_cfg, scenes, profile, broker, listener):
    app = App(_cfg(make_cfg, scenes, broker), profile)
    thread = _run_app(app)
    try:
        _, first, _ = listener.wait_for("reading")
        broker.stop()
        time.sleep(1.0)
        for _ in range(3):
            app.request_analysis("command")
            time.sleep(1.5)
        assert app.store.outbox_size() >= 2  # held locally while the broker is down
        held = app.store.outbox_size()
        broker.restart()
        seqs = []
        for _ in range(held):
            _, env, _ = listener.wait_for("reading", timeout=30, pred=lambda b: b["seq"] > first["seq"])
            seqs.append(env["seq"])
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        deadline = time.monotonic() + 10
        while app.store.outbox_size() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert app.store.outbox_size() == 0  # acknowledged rows are deleted
    finally:
        app.stop()
        thread.join(30)


def test_process_kill_triggers_last_will_and_sigterm_exits_cleanly(make_cfg, scenes, broker, listener, tmp_path):
    cfg = _cfg(make_cfg, scenes, broker)
    raw = json.loads(cfg.model_dump_json(by_alias=True))
    raw["device"]["secret"] = "s3cret"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    proc = subprocess.Popen(
        [sys.executable, "-m", "plant_cam", "run", "--config", str(path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        listener.wait_for("reading", timeout=60)
        proc.send_signal(signal.SIGKILL)  # power cut / crash: no goodbye message
        proc.wait(10)
        _, will, _ = listener.wait_for("offline", timeout=90)  # keepalive 30s x 1.5
        assert will["kind"] == "offline"
    finally:
        if proc.poll() is None:
            proc.kill()

    proc = subprocess.Popen(
        [sys.executable, "-m", "plant_cam", "run", "--config", str(path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    listener.wait_for("reading", timeout=60)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(60) == 0
    out = proc.stdout.read().decode() if proc.stdout else ""
    assert "stopped" in out and "exit_code=0" in out
