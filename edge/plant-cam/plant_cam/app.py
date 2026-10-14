"""Wires the pipeline together and runs it.

Threads:
  capture   owns the camera; newest live frame in a 1-slot buffer
  analysis  waits for a request, captures a fresh frame, runs the pipeline,
            publishes; a newer request replaces an unstarted older one, so
            work never queues up behind a slow analysis
  mqtt      broker connection, outbox flushing, commands, zone sensors
  stream    MJPEG live view (on demand)
  main      once a second: watchdog, schedule, change detection, thermal,
            heartbeat, and the decision to exit if something is wedged

Exit codes: 0 on SIGTERM/SIGINT (systemd won't restart a clean stop),
1 when the camera driver or the analysis worker is wedged (systemd's
Restart=on-failure brings it back), 2 for bad config.
"""

from __future__ import annotations

import datetime as dt
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from .capture import CaptureLoop, make_source
from .config import Config
from .core.pipeline import Pipeline
from .core.profile import Profile
from .core.registry import Publisher, create_publisher
from .core.services import Services
from .core.types import EnvSnapshot, Image, SampleCandidate, ZoneReport
from .env import EnvMonitor
from .link import MqttLink
from .publishers.overlay import annotate, status_text
from .publishers.payload import PAYLOAD_SCHEMA
from .publishers.stream import StreamServer
from .publishers.uploader import GatewayUploader
from .store import SqliteHistory, Store
from .store.active_learning import ActiveLearningStore
from .system import watchdog
from .system.thermal import ThermalMonitor

log = logging.getLogger("plant-cam")

COMMANDS = ("start_stream", "stop_stream", "analyze_now", "calibrate")
MAX_ANALYSIS_S = 300.0  # an analysis running longer than this is wedged


def thumbnail(rgb: Image) -> npt.NDArray[np.float32]:
    grey = cv2.cvtColor(cv2.resize(rgb, (64, 48), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
    out: npt.NDArray[np.float32] = grey.astype(np.float32) / 255.0
    return out


class _Requests:
    """A bounded (size 1) request queue that keeps only the newest request."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pending: str | None = None
        self.dropped = 0

    def put(self, reason: str) -> None:
        with self._cond:
            if self._pending is not None:
                self.dropped += 1
            # a person's request outranks a routine one it replaces
            if not (self._pending == "command" and reason != "command"):
                self._pending = reason
            self._cond.notify()

    def get(self, timeout: float) -> str | None:
        with self._cond:
            self._cond.wait_for(lambda: self._pending is not None, timeout=timeout)
            reason, self._pending = self._pending, None
            return reason


class App:
    def __init__(self, cfg: Config, profile: Profile) -> None:
        self.cfg = cfg
        self.profile = profile
        self.store = Store(Path(cfg.store.dir) / "plant-cam.db", cfg.store.outbox_max_rows, cfg.store.outbox_max_age_h)
        self.pipeline = Pipeline(cfg, profile, SqliteHistory(self.store), self.store.get("tracker", {}))
        self.capture = CaptureLoop(make_source(cfg.camera), cfg.camera.stall_timeout_s)
        self.thermal = ThermalMonitor(cfg.thermal)
        self.env = EnvMonitor(cfg.env, profile, self.store, cfg.device.id) if cfg.env.enabled else None
        self.link = MqttLink(
            cfg, self.store, self._on_command, self.env.ingest if self.env else None, self._capabilities
        )
        self.services = Services(
            cfg=cfg, store=self.store, link=self.link, uploader=GatewayUploader(cfg), rois=self.pipeline.rois
        )
        self.publishers: list[Publisher] = [
            create_publisher(p.name, p.params, self.services) for p in cfg.publishers if p.enabled
        ]
        self.active_learning = ActiveLearningStore(cfg.active_learning, cfg.device.id)
        self.stream = (
            StreamServer(cfg.stream, self.capture.live, self._render, self._status, lambda: self.thermal.stream_fps_cap)
            if cfg.stream.enabled
            else None
        )

        self._stop = threading.Event()
        self._requests = _Requests()
        self._worker = threading.Thread(target=self._work, name="analysis", daemon=True)
        self._busy_since: float | None = None
        self._last_report: ZoneReport | None = None
        self._last_analysis_at: float | None = None
        self._ref_thumb: npt.NDArray[np.float32] | None = None
        self._last_frame_sample = float("-inf")
        self._started = time.monotonic()
        self.analyses = 0
        self.exit_code = 0

    # ---- lifecycle --------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> int:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
            signal.signal(signal.SIGINT, lambda *_: self.stop())

        self.capture.start()
        if self.stream:
            self.stream.start()
        self.link.start()
        for p in self.publishers:
            p.start()
        self._worker.start()
        self.capture.wait_open(timeout=30)
        watchdog.notify("READY=1")
        log.info(
            "running",
            extra={
                "fields": {
                    "device": self.cfg.device.id,
                    "profile": f"{self.profile.id}@{self.profile.version}",
                    "interval_s": self.cfg.schedule.interval_s,
                    "models": self.pipeline.models() or "none",
                }
            },
        )

        s = self.cfg.schedule
        next_due = time.monotonic() + s.warmup_s
        next_beat = time.monotonic() + 5
        next_thermal = next_change = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if self.capture.wedged:
                    log.error("camera driver unresponsive -- exiting so systemd restarts us cleanly")
                    self.exit_code = 1
                    break
                if self._busy_since is not None and now - self._busy_since > MAX_ANALYSIS_S:
                    log.error("analysis wedged -- exiting so systemd restarts us cleanly")
                    self.exit_code = 1
                    break
                watchdog.notify("WATCHDOG=1")
                if self.stream:
                    self.stream.tick()
                if now >= next_thermal:
                    self.thermal.poll()
                    next_thermal = now + 5
                if now >= next_due:
                    if s.in_window(dt.datetime.now()):
                        self._requests.put("schedule")
                    next_due = now + s.interval_s * self.thermal.interval_factor
                if s.change_detection and now >= next_change:
                    next_change = now + 2
                    self._check_change(now)
                if now >= next_beat:
                    next_beat = now + self.cfg.heartbeat_s
                    self.link.publish_status(self._heartbeat())
                self._stop.wait(1.0)
        finally:
            self._shutdown()
        return self.exit_code

    def _shutdown(self) -> None:
        watchdog.notify("STOPPING=1")
        log.info("shutting down")
        self._stop.set()
        self._worker.join(timeout=60)  # let an in-flight analysis finish and queue its reading
        for p in self.publishers:
            try:
                p.stop()
            except Exception:
                log.exception("publisher %s stop failed", p.name)
        self.store.put("tracker", self.pipeline.tracker.state())
        # Give the outbox a moment to drain; anything left goes out next start.
        deadline = time.monotonic() + 5
        while self.link.connected.is_set() and self.store.outbox_size() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.link.stop()
        if self.stream:
            self.stream.stop()
        self.capture.stop()
        self.pipeline.close()
        self.store.close()
        log.info("stopped", extra={"fields": {"exit_code": self.exit_code}})

    # ---- analysis ----------------------------------------------------------------------

    def request_analysis(self, reason: str = "command") -> None:
        self._requests.put(reason)

    def _check_change(self, now: float) -> None:
        if self._ref_thumb is None or self._last_analysis_at is None:
            return
        if now - self._last_analysis_at < self.cfg.schedule.change_min_interval_s:
            return
        frame, _ = self.capture.live.latest()
        if frame is None:
            return
        diff = float(np.abs(thumbnail(frame) - self._ref_thumb).mean())
        if diff >= self.cfg.schedule.change_threshold and self.cfg.schedule.in_window(dt.datetime.now()):
            log.info("scene changed", extra={"fields": {"diff": round(diff, 3)}})
            self._requests.put("change")

    def _work(self) -> None:
        while not self._stop.is_set():
            reason = self._requests.get(timeout=1.0)
            if reason is None:
                continue
            self._busy_since = time.monotonic()
            try:
                self.analyse_once(reason)
            except Exception:
                log.exception("analysis cycle failed; will try again next time")
            finally:
                self._busy_since = None

    def analyse_once(self, reason: str) -> ZoneReport | None:
        if not self.capture.camera_ok:
            log.warning("camera not ready, skipping analysis", extra={"fields": {"reason": reason}})
            return None
        frame = self.capture.capture_frame()
        env = self.env.snapshot() if self.env else EnvSnapshot()
        system = self.thermal.system_metrics()
        system["outbox_pending"] = float(self.store.outbox_size())
        self.services.on_demand = reason == "command"
        report, samples = self.pipeline.run(
            frame, env, system, allow_heavy=self.thermal.allow_heavy, degrade_level=self.thermal.level
        )
        report.extra["reason"] = reason
        for p in self.publishers:
            try:
                p.publish(report)
            except Exception:
                log.exception("publisher %s failed", p.name)
        self.store.put("tracker", self.pipeline.tracker.state())
        if self.thermal.allow_heavy:
            al = self.cfg.active_learning
            now_s = time.monotonic()
            if (
                al.frame_every_s
                and now_s - self._last_frame_sample >= al.frame_every_s
                and report.metrics.get("exposure_ok", 1.0) >= 1.0
            ):
                samples.append(SampleCandidate(frame.image, "frame", "frame", {}))
                self._last_frame_sample = now_s
            self.active_learning.save(samples, report)
        live, _ = self.capture.live.latest()
        if live is not None:
            self._ref_thumb = thumbnail(live)
        self._last_report = report
        self._last_analysis_at = time.monotonic()
        self.analyses += 1
        log.info(
            "analysis",
            extra={
                "fields": {
                    "reason": reason,
                    "label": report.label,
                    "health": report.health_score,
                    "canopy_pct": report.metrics.get("canopy_coverage_pct"),
                    "active": [c.id for c in report.conditions if c.active],
                    "latency_ms": round(sum(report.latency_ms.values())),
                    "image_ref": report.image_ref,
                }
            },
        )
        return report

    # ---- commands -------------------------------------------------------------------------

    def _on_command(self, command: str, payload: dict[str, Any]) -> None:
        if command == "start_stream" and self.stream:
            self.stream.request_start()
        elif command == "stop_stream" and self.stream:
            self.stream.request_stop()
        elif command == "analyze_now":
            self.request_analysis("command")
        elif command == "calibrate":
            threading.Thread(target=self._calibrate, name="calibrate", daemon=True).start()
        else:
            log.info("unknown command ignored", extra={"fields": {"command": command}})

    def _calibrate(self) -> None:
        try:
            result = self.capture.calibrate(settle_s=3.0)
            log.info("camera calibrated", extra={"fields": {k: str(v) for k, v in result.items()}})
        except Exception:
            log.exception("calibration failed")

    # ---- status ------------------------------------------------------------------------------

    def _capabilities(self) -> dict[str, Any]:
        return {
            "reads": [
                "health_score",
                "label",
                "note",
                "confidence",
                "visual_score",
                "env_risk_score",
                "canopy_coverage_pct",
                "fruit_ripe_count",
                "flower_count",
                "image_ref",
            ],
            "commands": list(COMMANDS),
            "payload_schema": PAYLOAD_SCHEMA,
            "profile": f"{self.profile.id}@{self.profile.version}",
            "models": self.pipeline.models(),
            "model_version": ",".join(self.pipeline.models().values()) or "classical",
        }

    def _heartbeat(self) -> dict[str, Any]:
        hb: dict[str, Any] = {
            "kind": "heartbeat",
            "uptime_s": round(time.monotonic() - self._started),
            "camera_ok": 1 if self.capture.camera_ok else 0,
            "outbox_pending": self.store.outbox_size(),
            "analyses": self.analyses,
            "env_subscribed": 1 if self.link.zone_subscribed else 0,
        }
        if self._last_analysis_at is not None:
            hb["last_analysis_age_s"] = round(time.monotonic() - self._last_analysis_at)
        hb.update(self.thermal.system_metrics())
        return hb

    def _status(self) -> dict[str, Any]:
        r = self._last_report
        return {
            "device_id": self.cfg.device.id,
            "profile": f"{self.profile.id}@{self.profile.version}",
            "models": self.pipeline.models(),
            "broker_connected": self.link.connected.is_set(),
            "env_subscribed": self.link.zone_subscribed,
            "outbox_pending": self.store.outbox_size(),
            "camera_ok": self.capture.camera_ok,
            "seconds_since_last_frame": round(self.capture.seconds_since_last_frame(), 1),
            "degrade_level": self.thermal.level,
            "cpu_temp_c": self.thermal.last_temp,
            "stream_enabled": self.stream.enabled if self.stream else False,
            "stream_clients": self.stream.clients if self.stream else 0,
            "last_report": None
            if r is None
            else {
                "ts": r.ts.isoformat(),
                "label": r.label,
                "health_score": r.health_score,
                "note": r.note,
                "active_conditions": [c.id for c in r.conditions if c.active],
                "image_ref": r.image_ref,
            },
        }

    def _render(self, rgb: Image) -> Image:
        r = self._last_report
        status = status_text(r.label, r.health_score, r.confidence) if r else "warming up"
        return annotate(rgb, r.detections if r else [], self.pipeline.rois, status)
