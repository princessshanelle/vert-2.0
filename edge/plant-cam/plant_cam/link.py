"""The device's MQTT connection -- the same contract every VERT device speaks
(firmware/*/src/main.cpp, services/gateway/internal/api/rabbitmq_auth.go):

  * username/password = device id / provisioned secret; client id = device
    id (the broker only lets a device use its own mqtt-subscription queue)
  * announce on every (re)connect, retained, with this device's LAN IP so
    the dashboard's live view points at the right address
  * Last Will on .../offline (JSON, the gateway reads `kind`)
  * commands arrive on vert/+/<type>/<id>/cmd as {"payload": {"command": ...}}
  * readings go out on .../reading in the standard envelope, through the
    SQLite outbox: a row is deleted only when the broker's PUBACK arrives
  * a heartbeat on .../status every heartbeat_s (QoS 0, never stored)
  * zone sensor readings (vert/<zone>/+/+/reading) feed the env monitor

Reconnects use exponential backoff with full jitter, so a fleet doesn't
stampede a restarted broker. paho's own auto-reconnect is not used; this
thread owns the loop.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import random
import socket
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .config import Config
from .core.types import iso_utc, utc_now
from .store import Store

log = logging.getLogger("plant-cam.mqtt")

SCHEMA_VERSION = "1.0"  # the gateway rejects anything else; payload carries payload_schema
INFLIGHT_WINDOW = 20
INFLIGHT_TIMEOUT_S = 60.0


def _route_ip(towards: str, port: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((towards, port))  # UDP connect: picks a route, sends nothing
            return str(s.getsockname()[0])
        except OSError:
            return ""


def local_ip(towards: str, port: int) -> str:
    """The LAN address the dashboard should use for the live view. When the
    broker is on this same machine (it is, if this Pi hosts the stack) the
    route to it is loopback, which no other device can reach -- so fall back
    to the address of the default route."""
    ip = _route_ip(towards, port)
    if not ip or ip.startswith("127."):
        ip = _route_ip("192.0.2.1", 9) or ip  # TEST-NET-1: never contacted, just routed
    return ip


def backoff_delay(attempt: int, lo: float, hi: float) -> float:
    """Exponential backoff with full jitter, never below lo/2."""
    cap = min(hi, lo * (2 ** min(attempt, 16)))
    return max(lo / 2, random.uniform(0, cap))


class MqttLink:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        on_command: Callable[[str, dict[str, Any]], None],
        on_zone_reading: Callable[[str, dict[str, Any], float | None], None] | None,
        capabilities: Callable[[], dict[str, Any]],
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.on_command = on_command
        self.on_zone_reading = on_zone_reading
        self.capabilities = capabilities

        base = cfg.topic_base
        self.topic_reading = f"{base}/reading"
        self.topic_announce = f"{base}/announce"
        self.topic_offline = f"{base}/offline"
        self.topic_status = f"{base}/status"
        # Zone wildcarded: the gateway builds command topics from the zone
        # name currently in the database, so a renamed zone still reaches us.
        self.topic_cmd = f"vert/+/{cfg.device.type}/{cfg.device.id}/cmd"
        self.topic_zone = f"vert/{cfg.device.zone}/+/+/reading"

        self.connected = threading.Event()
        self._stop = threading.Event()
        self._kick = threading.Event()
        self._inflight: dict[int, tuple[int, float]] = {}  # mid -> (seq, sent_at)
        self._inflight_lock = threading.Lock()
        self._zone_sub_mid: int | None = None
        self.zone_subscribed = False
        self.zone_sub_refused = False
        self.connects = 0
        self._thread = threading.Thread(target=self._run, name="mqtt", daemon=True)
        self.client = self._make_client()

    # ---- setup --------------------------------------------------------------

    def _make_client(self) -> mqtt.Client:
        c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=self.cfg.device.id, clean_session=True)
        c.username_pw_set(self.cfg.device.id, self.cfg.secret())
        c.will_set(self.topic_offline, self._lifecycle("offline"), qos=1, retain=False)
        c.max_inflight_messages_set(INFLIGHT_WINDOW)
        tls = self.cfg.broker.tls
        if tls.enabled:
            c.tls_set(
                ca_certs=tls.ca_file,
                certfile=tls.cert_file,
                keyfile=tls.key_file,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            c.tls_insecure_set(tls.insecure)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.on_publish = self._on_publish
        c.on_subscribe = self._on_subscribe
        return c

    def _lifecycle(self, kind: str) -> str:
        return json.dumps({"kind": kind, "device_id": self.cfg.device.id, "device_type": self.cfg.device.type})

    def envelope(self, payload: dict[str, Any], ts: dt.datetime | None = None) -> tuple[int, str]:
        seq = self.store.next_seq()
        body = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "device_id": self.cfg.device.id,
                "device_type": self.cfg.device.type,
                "zone": self.cfg.device.zone,
                "ts": iso_utc(ts or utc_now()),
                "seq": seq,
                "payload": payload,
            },
            separators=(",", ":"),
            allow_nan=False,
        )
        return seq, body

    # ---- public API ------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self.connected.is_set():
            with contextlib.suppress(Exception):
                info = self.client.publish(self.topic_offline, self._lifecycle("offline"), qos=1)
                info.wait_for_publish(timeout=3)
        self._stop.set()
        self._kick.set()
        with contextlib.suppress(Exception):
            self.client.disconnect()
        self._thread.join(timeout=timeout)

    def enqueue_reading(self, payload: dict[str, Any], ts: dt.datetime) -> int:
        """Persist first, then send: survives broker outages and restarts."""
        seq, body = self.envelope(payload, ts)
        self.store.enqueue(seq, self.topic_reading, body, qos=1)
        self._kick.set()
        return seq

    def publish_status(self, payload: dict[str, Any]) -> bool:
        if not self.connected.is_set():
            return False
        _, body = self.envelope(payload)
        return self.client.publish(self.topic_status, body, qos=0).rc == mqtt.MQTT_ERR_SUCCESS

    def inflight(self) -> int:
        with self._inflight_lock:
            return len(self._inflight)

    # ---- network thread -------------------------------------------------------------

    def _run(self) -> None:
        attempt = 0
        b = self.cfg.broker
        while not self._stop.is_set():
            try:
                log.info("connecting", extra={"fields": {"host": b.host, "port": b.port, "tls": b.tls.enabled}})
                self.client.connect(b.host, b.port, keepalive=b.keepalive_s)
            except (OSError, ssl.SSLError) as exc:
                delay = backoff_delay(attempt, b.backoff_min_s, b.backoff_max_s)
                log.warning("broker unreachable", extra={"fields": {"error": repr(exc), "retry_in_s": round(delay, 1)}})
                attempt += 1
                self._stop.wait(delay)
                continue
            while not self._stop.is_set():
                rc = self.client.loop(timeout=0.5)
                if rc != mqtt.MQTT_ERR_SUCCESS:
                    break
                if self.connected.is_set():
                    attempt = 0
                    self._flush()
            self.connected.clear()
            if self._stop.is_set():
                break
            delay = backoff_delay(attempt, b.backoff_min_s, b.backoff_max_s)
            log.warning("disconnected from broker", extra={"fields": {"retry_in_s": round(delay, 1)}})
            attempt += 1
            self._stop.wait(delay)
        with contextlib.suppress(Exception):
            self.client.loop(timeout=0.2)

    def _flush(self) -> None:
        now = time.monotonic()
        with self._inflight_lock:
            # Anything unacknowledged for too long is forgotten here and sent
            # again from the outbox (at-least-once; duplicates are harmless).
            for mid in [m for m, (_, t) in self._inflight.items() if now - t > INFLIGHT_TIMEOUT_S]:
                del self._inflight[mid]
                self._kick.set()
            busy = {seq for seq, _ in self._inflight.values()}
            room = INFLIGHT_WINDOW - len(self._inflight)
        if room <= 0 or not self._kick.is_set():
            return
        self._kick.clear()
        for seq, topic, body, qos in self.store.pending(limit=INFLIGHT_WINDOW + len(busy)):
            if seq in busy:
                continue
            if room <= 0:
                self._kick.set()
                break
            info = self.client.publish(topic, body, qos=qos)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                self._kick.set()
                break
            if qos == 0:
                self.store.ack([seq])
                continue
            with self._inflight_lock:
                self._inflight[info.mid] = (seq, now)
            room -= 1

    # ---- paho callbacks (network thread) ------------------------------------------

    def _on_connect(self, client: mqtt.Client, _ud: Any, _flags: Any, reason: Any, _props: Any) -> None:
        if reason.is_failure:
            # "Not authorized": the broker was reached but rejected id/secret.
            # Re-provisioning a device invalidates its old secret.
            log.error(
                "broker refused connection -- check device.id / secret", extra={"fields": {"reason": str(reason)}}
            )
            return
        self.connects += 1
        self.connected.set()
        client.subscribe(self.topic_cmd, qos=1)
        if self.on_zone_reading is not None and not self.zone_sub_refused:
            _, self._zone_sub_mid = client.subscribe(self.topic_zone, qos=0)
        announce = json.loads(self._lifecycle("announce"))
        announce["ip"] = self.cfg.device.advertise_ip or local_ip(self.cfg.broker.host, self.cfg.broker.port)
        announce["capabilities"] = self.capabilities()
        client.publish(self.topic_announce, json.dumps(announce), qos=1, retain=True)
        self._kick.set()
        log.info("connected", extra={"fields": {"outbox": self.store.outbox_size(), "ip": announce["ip"]}})

    def _on_disconnect(self, _c: mqtt.Client, _ud: Any, _flags: Any, reason: Any, _props: Any) -> None:
        self.connected.clear()
        self.zone_subscribed = False
        log.info("mqtt disconnected", extra={"fields": {"reason": str(reason)}})

    def _on_subscribe(self, _c: mqtt.Client, _ud: Any, mid: int, reasons: list[Any], _props: Any) -> None:
        if mid != self._zone_sub_mid:
            return
        if reasons and reasons[0].is_failure:
            self.zone_sub_refused = True
            log.warning(
                "broker refused the zone sensor subscription -- environmental risk disabled. "
                "Deploy the gateway ACL change (docs/INTEGRATION.md) to enable it.",
                extra={"fields": {"topic": self.topic_zone}},
            )
        else:
            self.zone_subscribed = True

    def _on_publish(self, _c: mqtt.Client, _ud: Any, mid: int, _reason: Any, _props: Any) -> None:
        with self._inflight_lock:
            entry = self._inflight.pop(mid, None)
        if entry is not None:
            self.store.ack([entry[0]])

    def _on_message(self, _c: mqtt.Client, _ud: Any, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split("/")
        try:
            doc = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            log.warning("ignoring malformed message", extra={"fields": {"topic": msg.topic}})
            return
        if not isinstance(doc, dict):
            return
        if parts[-1] == "cmd":
            payload = doc.get("payload")
            command = payload.get("command") if isinstance(payload, dict) else None
            if not isinstance(command, str):
                log.warning("ignoring malformed command", extra={"fields": {"topic": msg.topic}})
                return
            log.info("command received", extra={"fields": {"command": command}})
            try:
                self.on_command(command, payload if isinstance(payload, dict) else {})
            except Exception:
                log.exception("command %s failed", command)
        elif parts[-1] == "reading" and len(parts) == 5 and self.on_zone_reading is not None:
            payload = doc.get("payload")
            if not isinstance(payload, dict):
                return
            # Trust the topic's device id (the broker checked it), not the body's.
            try:
                self.on_zone_reading(parts[3], payload, _parse_ts(doc.get("ts")))
            except Exception:
                log.exception("env reading from %s failed", parts[3])


def _parse_ts(raw: Any) -> float | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.UTC)
    t = ts.timestamp()
    # A sensor with no clock (DHT22 firmware sends no ts) or a wild one: use arrival time.
    return t if abs(t - time.time()) < 3600 else None
