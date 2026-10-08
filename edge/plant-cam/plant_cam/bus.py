"""MQTT side of the device contract -- the same one the ESP32 camera
speaks (firmware/xiao-esp32s3-camera/src/main.cpp):

  * username/password = device id / provisioned secret; client id = device
    id (the broker only lets a device use its own mqtt-subscription queue)
  * announce on every (re)connect, with this device's current LAN IP so
    the dashboard's live view always points at the right address
  * last-will on .../offline so the dashboard sees a crash or power cut
  * commands arrive on .../cmd as {"payload": {"command": "..."}}
  * readings go out on .../reading in the standard envelope

Readings published while the broker is unreachable are spooled to disk
and replayed, with their original timestamps, on reconnect.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt

from .config import Config
from .envelope import Envelope

log = logging.getLogger("plant-cam.bus")


def local_ip(towards: str) -> str:
    """The address the broker will see us at (no packet is actually sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((towards, 1883))
            return s.getsockname()[0]
        except OSError:
            return ""


class Spool:
    """Append-only JSONL of envelopes that couldn't be sent yet. Capped so
    a long outage can't fill the SD card -- oldest readings go first."""

    def __init__(self, path: str, max_entries: int):
        self.path = Path(path)
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def append(self, topic: str, body: str) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"topic": topic, "body": body}) + "\n")
            self._trim()

    def drain(self) -> list[tuple[str, str]]:
        with self._lock:
            if not self.path.exists():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()
            self.path.unlink()
        out = []
        for line in lines:
            try:
                rec = json.loads(line)
                out.append((rec["topic"], rec["body"]))
            except (json.JSONDecodeError, KeyError):
                continue  # a torn last line after a power cut; skip it
        return out

    def _trim(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) > self.max_entries:
            self.path.write_text("\n".join(lines[-self.max_entries:]) + "\n", encoding="utf-8")


class Bus:
    def __init__(self, cfg: Config, on_command: Callable[[str, dict], None], capabilities: dict):
        self.cfg = cfg
        self.on_command = on_command
        self.capabilities = capabilities
        self.spool = Spool(cfg.spool.path, cfg.spool.max_entries)
        self._seq = 0
        self._seq_lock = threading.Lock()
        self.connected = threading.Event()

        base = cfg.topic_base
        self.topic_reading = f"{base}/reading"
        self.topic_announce = f"{base}/announce"
        self.topic_offline = f"{base}/offline"
        # Zone wildcarded: the gateway builds command topics from the zone
        # name currently in the database, so a renamed zone still reaches us.
        self.topic_cmd = f"vert/+/{cfg.device.type}/{cfg.device.id}/cmd"

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.device.id, clean_session=True)
        self.client.username_pw_set(cfg.device.id, cfg.device.secret)
        # JSON, not a bare "1": the gateway's lifecycle consumer reads `kind`.
        self.client.will_set(self.topic_offline, self._lifecycle("offline"), qos=1, retain=False)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        log.info("connecting to %s:%d as %s", self.cfg.broker.host, self.cfg.broker.port, self.cfg.device.id)
        # connect_async + loop_start: paho owns reconnection from here on,
        # including when the broker is down at boot.
        self.client.connect_async(self.cfg.broker.host, self.cfg.broker.port, keepalive=self.cfg.broker.keepalive_s)
        self.client.loop_start()

    def stop(self) -> None:
        if self.connected.is_set():
            self.client.publish(self.topic_offline, self._lifecycle("offline"), qos=1).wait_for_publish(timeout=3)
        self.client.disconnect()
        self.client.loop_stop()

    def publish_reading(self, payload: dict) -> None:
        with self._seq_lock:
            seq = self._seq
            self._seq += 1
        env = Envelope(
            device_id=self.cfg.device.id,
            device_type=self.cfg.device.type,
            zone=self.cfg.device.zone,
            payload=payload,
            seq=seq,
        )
        body = json.dumps(env.to_dict())
        if not self.connected.is_set():
            self.spool.append(self.topic_reading, body)
            log.info("broker unreachable, spooled reading #%d", seq)
            return
        info = self.client.publish(self.topic_reading, body, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self.spool.append(self.topic_reading, body)

    def _lifecycle(self, kind: str) -> str:
        return json.dumps({"kind": kind, "device_id": self.cfg.device.id, "device_type": self.cfg.device.type})

    def _announce(self) -> None:
        msg = json.loads(self._lifecycle("announce"))
        msg["ip"] = local_ip(self.cfg.broker.host)
        msg["capabilities"] = self.capabilities
        self.client.publish(self.topic_announce, json.dumps(msg), qos=1, retain=True)

    def _on_connect(self, client, _userdata, _flags, reason_code, _props) -> None:
        if reason_code.is_failure:
            # "Not authorized" means the broker was reached but rejected
            # the id/secret -- re-provisioning invalidates the old secret.
            log.error("broker refused connection: %s -- check device.id / device.secret", reason_code)
            return
        self.connected.set()
        client.subscribe(self.topic_cmd, qos=1)
        self._announce()
        replay = self.spool.drain()
        for topic, body in replay:
            client.publish(topic, body, qos=1)
        log.info("connected%s", f", replayed {len(replay)} spooled readings" if replay else "")

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _props) -> None:
        self.connected.clear()
        log.warning("disconnected from broker (%s); paho will keep retrying", reason_code)

    def _on_message(self, _client, _userdata, msg) -> None:
        try:
            doc = json.loads(msg.payload)
            command = doc["payload"]["command"]
        except (json.JSONDecodeError, KeyError, TypeError):
            log.warning("ignoring malformed command on %s", msg.topic)
            return
        log.info("command received: %s", command)
        try:
            self.on_command(command, doc.get("payload", {}))
        except Exception:  # noqa: BLE001 -- a bad command must never kill the network thread
            log.exception("command %s failed", command)
