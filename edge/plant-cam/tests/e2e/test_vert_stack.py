"""End-to-end against the real VERT stack (docker compose) on this machine.

    make e2e     # or: pytest -m e2e

Creates (or reuses) dedicated test identities -- zone `e2e`, devices
`plantcam-e2e` (ai-camera) and `dht22-e2e` (temp-humidity) -- and checks,
through the same REST API the dashboard uses:

  * plant-cam's readings arrive in the gateway (last_reading, charts)
  * the zone's humidity sensor reaches plant-cam through the broker ACL
    (env_available = 1, risk indices present)
  * an annotated snapshot lands in the frame store (photo gallery)
  * a broker restart loses nothing: readings taken while it was down
    arrive afterwards, in order

Admin login: VERT_E2E_EMAIL / VERT_E2E_PASSWORD (default: the dev account
from docs/TESTING.md, registered if the instance has no users yet).
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import pytest
import requests
from paho.mqtt.enums import CallbackAPIVersion

from plant_cam.app import App

pytestmark = pytest.mark.e2e

API = os.environ.get("VERT_E2E_API", "http://127.0.0.1:8080/api/v1")
BROKER = os.environ.get("VERT_E2E_BROKER", "127.0.0.1")
EMAIL = os.environ.get("VERT_E2E_EMAIL", "you@example.com")
PASSWORD = os.environ.get("VERT_E2E_PASSWORD", "a-real-password")
REPO = Path(__file__).resolve().parents[4]
ZONE = "e2e"


def _token() -> str:
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=10)
    if r.status_code != 200:
        r = requests.post(
            f"{API}/auth/register",
            timeout=10,
            json={"email": EMAIL, "password": PASSWORD, "display_name": "VERT admin", "locale": "en"},
        )
    r.raise_for_status()
    return str(r.json()["token"])


@pytest.fixture(scope="module")
def api() -> requests.Session:
    try:
        requests.get(f"{API}/zones", timeout=3)
    except requests.RequestException:
        pytest.skip("VERT stack not reachable")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_token()}"
    return s


def _zone_id(api: requests.Session) -> str:
    zones = api.get(f"{API}/zones", timeout=10).json() or []  # the gateway sends null for none
    for z in zones:
        if z["name"] == ZONE:
            return str(z["id"])
    r = api.post(f"{API}/zones", json={"name": ZONE}, timeout=10)
    r.raise_for_status()
    return str(r.json()["id"])


def _provision(api: requests.Session, device_id: str, device_type: str, zone_id: str) -> str:
    r = api.post(f"{API}/devices/provision", json={"device_id": device_id, "device_type": device_type}, timeout=10)
    r.raise_for_status()
    secret = str(r.json()["secret"])
    api.put(f"{API}/devices/{device_id}/zone", json={"zone_id": zone_id}, timeout=10).raise_for_status()
    return secret


class FakeSensor:
    """A DHT22 speaking the firmware's exact contract (no ts, as the firmware sends none)."""

    def __init__(self, device_id: str, secret: str) -> None:
        self.topic = f"vert/{ZONE}/temp-humidity/{device_id}/reading"
        self.device_id = device_id
        self.c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=device_id)
        self.c.username_pw_set(device_id, secret)
        self.c.connect(BROKER, 1883)
        self.c.loop_start()
        self.seq = 0

    def publish(self, temp: float, rh: float) -> None:
        body = {
            "schema_version": "1.0",
            "device_id": self.device_id,
            "device_type": "temp-humidity",
            "zone": ZONE,
            "seq": self.seq,
            "payload": {"temp_c": temp, "humidity_pct": rh},
        }
        self.seq += 1
        self.c.publish(self.topic, json.dumps(body), qos=1).wait_for_publish(5)

    def close(self) -> None:
        self.c.loop_stop()
        self.c.disconnect()


def _wait(fn: Any, timeout: float, what: str) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {what} (last: {last})")


def _consumers(queue: str) -> int:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "broker", "rabbitmqctl", "list_queues", "name", "consumers"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == queue:
            return int(parts[1])
    return -1


def test_full_stack(api: requests.Session, make_cfg, scenes: Path, profile, tmp_path: Path):
    started = datetime.datetime.now(datetime.UTC)
    zone_id = _zone_id(api)
    cam_secret = _provision(api, "plantcam-e2e", "ai-camera", zone_id)
    sensor = FakeSensor("dht22-e2e", _provision(api, "dht22-e2e", "temp-humidity", zone_id))

    cfg = make_cfg(
        device={"id": "plantcam-e2e", "zone": ZONE, "secret": cam_secret},
        broker={"host": BROKER, "port": 1883, "backoff_min_s": 0.5, "backoff_max_s": 2.0},
        gateway={"url": API.rsplit("/api/v1", 1)[0]},
        camera={"source": "folder", "folder": str(scenes), "fps": 5},
        schedule={"interval_s": 3600, "warmup_s": 1, "change_detection": False},
        publishers=[{"name": "snapshot", "params": {"interval_s": 0}}, {"name": "mqtt"}],
        heartbeat_s=10,
    )
    app = App(cfg, profile)
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    try:
        _wait(lambda: app.link.connected.is_set(), 30, "plant-cam to connect with its provisioned secret")
        _wait(lambda: app.link.zone_subscribed, 15, "the zone sensor subscription to be granted by the ACL")
        for _ in range(3):
            sensor.publish(18.5, 93.0)
            time.sleep(0.3)
        app.request_analysis("command")

        def latest() -> dict[str, Any] | None:
            d = api.get(f"{API}/devices/plantcam-e2e", timeout=10).json()
            seen = datetime.datetime.fromisoformat(d["last_seen_at"]) if d.get("last_seen_at") else None
            r = d.get("last_reading") or {}
            fresh = seen is not None and seen > started  # not a leftover from an earlier run
            return r if fresh and r.get("env_available") == 1 else None

        reading = _wait(latest, 60, "a plant-health reading with environment data")
        assert reading["payload_schema"] == "plant-health/2"
        assert reading["env_rh_pct"] == pytest.approx(93.0) and "risk_botrytis" in reading
        assert isinstance(reading["note"], str) and 0 <= reading["health_score"] <= 1
        device = api.get(f"{API}/devices/plantcam-e2e", timeout=10).json()
        assert device["status"] == "online" and device["device_type"] == "ai-camera"
        assert device["capabilities"]["profile"].startswith("strawberry@")

        metrics = _wait(lambda: api.get(f"{API}/devices/plantcam-e2e/metrics", timeout=10).json(), 30, "metrics")
        assert {"health_score", "canopy_coverage_pct", "risk_botrytis", "env_rh_pct"} <= set(metrics)
        frames = _wait(lambda: api.get(f"{API}/devices/plantcam-e2e/frames", timeout=10).json(), 30, "a snapshot")
        assert frames[0]["url"]

        # --- broker restart: nothing lost ---------------------------------------
        def count() -> int:
            pts = api.get(
                f"{API}/devices/plantcam-e2e/readings", params={"metric": "health_score", "minutes": 60}, timeout=10
            ).json()
            return len(pts or [])

        before = count()
        subprocess.run(["docker", "compose", "stop", "broker"], cwd=REPO, check=True, capture_output=True)
        _wait(lambda: not app.link.connected.is_set(), 30, "plant-cam to notice the broker is down")
        for _ in range(3):
            app.request_analysis("command")
            time.sleep(2)
        assert app.store.outbox_size() >= 2
        subprocess.run(["docker", "compose", "start", "broker"], cwd=REPO, check=True, capture_output=True)
        _wait(lambda: app.link.connected.is_set(), 120, "plant-cam to reconnect after the broker restart")
        # plant-cam's part: everything held during the outage is now acknowledged
        # by RabbitMQ, i.e. safe in the gateway's durable queue.
        _wait(lambda: app.store.outbox_size() == 0, 60, "the outbox to drain")
        # The broker takes ~33 s to restart here, longer than the gateway's and
        # automation's 30 s connect retry; both must keep trying, not give up.
        _wait(lambda: count() >= before + 3, 90, "the readings taken during the outage to reach the database")
        _wait(lambda: _consumers("q.automation.rules") >= 1, 60, "automation to resume consuming")
    finally:
        app.stop()
        thread.join(60)
        sensor.close()
    assert app.exit_code == 0
