"""Wires the pieces together and runs the main loop:

  every second      ping the systemd watchdog, check the camera is alive,
                    let the stream switch itself off if nobody's watching
  every interval_s  analyse the newest frame, publish a smoothed reading,
                    upload an evidence photo when it's due or things got worse
  every dataset.interval_s  save a raw photo locally for future labelling

A single bad frame or failed upload is logged and skipped; only a camera
that has stopped producing frames altogether ends the process (so
systemd restarts it cleanly).
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path

from .bus import Bus
from .camera import Camera
from .config import Config
from .detector import Analysis, load_detector
from .health import HealthTracker, label_rank
from .overlay import annotate, encode_jpeg, rgb_to_jpeg
from .stream import StreamServer
from .uploader import Uploader

log = logging.getLogger("plant-cam")


def sd_notify(message: str) -> None:
    """Minimal systemd notify (no extra dependency); a no-op outside systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode())
    except OSError as exc:
        log.debug("sd_notify failed: %s", exc)


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.camera = Camera(cfg.camera)
        self.detector = load_detector(cfg.model)
        self.classes = {c.name: c for c in self.detector.classes}
        self.tracker = HealthTracker(self.detector, cfg.analysis.window)
        self.uploader = Uploader(cfg)
        self.stream = StreamServer(cfg.stream, self.camera, self._render, self._status)
        self.bus = Bus(cfg, self._on_command, capabilities={
            "reads": ["health_score", "label", "note", "image_ref"],
            "commands": ["start_stream", "stop_stream", "analyze_now"],
            "model_version": self.detector.model_version,
        })

        self._stop = threading.Event()
        self._analyze_now = threading.Event()
        self._last_analysis: Analysis | None = None
        self._last_reading: dict = {}
        self._last_evidence_at = float("-inf")  # first analysis always uploads one
        self._last_evidence_label = "thriving"
        self._last_dataset_at = float("-inf")

    # ---- lifecycle ------------------------------------------------------

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())

        self.camera.start()
        self.stream.start()
        self.bus.start()
        sd_notify("READY=1")
        log.info("running: %s, analysing every %.0fs", self.detector.model_version, self.cfg.analysis.interval_s)

        exit_code = 0
        next_analysis = time.monotonic() + 5  # let auto-exposure settle first
        try:
            while not self._stop.is_set():
                if self.camera.seconds_since_last_frame() > self.cfg.camera.stall_timeout_s:
                    log.error("no camera frame for %.0fs -- exiting so systemd can restart cleanly",
                              self.cfg.camera.stall_timeout_s)
                    exit_code = 1
                    break
                sd_notify("WATCHDOG=1")
                self.stream.tick()

                now = time.monotonic()
                if now >= next_analysis or self._analyze_now.is_set():
                    self._analyze_now.clear()
                    next_analysis = now + self.cfg.analysis.interval_s
                    self._safely(self._analyse_once)
                if self.cfg.dataset.enabled and now - self._last_dataset_at >= self.cfg.dataset.interval_s:
                    self._last_dataset_at = now
                    self._safely(self._save_dataset_frame)

                self._stop.wait(1.0)
        finally:
            sd_notify("STOPPING=1")
            self.bus.stop()
            self.stream.stop()
            self.camera.stop()
        return exit_code

    @staticmethod
    def _safely(fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 -- one bad cycle must not end the process
            log.exception("%s failed; will try again next cycle", fn.__name__)

    # ---- commands -------------------------------------------------------

    def _on_command(self, command: str, _payload: dict) -> None:
        if command == "start_stream":
            self.stream.request_start()
        elif command == "stop_stream":
            self.stream.request_stop()
        elif command == "analyze_now":
            self._analyze_now.set()
        else:
            log.info("unknown command %r ignored", command)

    # ---- the work -------------------------------------------------------

    def _analyse_once(self) -> None:
        frame, _ = self.camera.latest()
        if frame is None:
            log.info("no frame yet, skipping analysis")
            return
        analysis = self.detector.analyze(frame)
        self._last_analysis = analysis
        self.tracker.add(analysis)
        reading = self.tracker.reading()
        reading["latency_ms"] = round(analysis.latency_ms, 1)

        ref = self._maybe_upload_evidence(frame, analysis, reading["label"])
        if ref:
            reading["image_ref"] = ref
        self._last_reading = reading
        self.bus.publish_reading(reading)
        log.info("%s (%.0f%%, %d detections, %.0f ms): %s", reading["label"], reading["health_score"] * 100,
                 len(analysis.detections), analysis.latency_ms, reading["note"])

    def _maybe_upload_evidence(self, frame, analysis: Analysis, label: str) -> str | None:
        now = time.monotonic()
        # Track recoveries too, so a later relapse to the same level
        # counts as a fresh decline and gets photographed again.
        if label_rank(label) < label_rank(self._last_evidence_label):
            self._last_evidence_label = label
        due = now - self._last_evidence_at >= self.cfg.evidence.interval_s
        declined = self.cfg.evidence.on_decline and label_rank(label) > label_rank(self._last_evidence_label)
        if not (due or declined):
            return None
        image = annotate(frame, analysis.detections, self.classes, self._status_text(label))
        ref = self.uploader.upload(encode_jpeg(image, 85))
        if ref:
            self._last_evidence_at = now
            self._last_evidence_label = label
        return ref

    def _save_dataset_frame(self) -> None:
        frame, _ = self.camera.latest()
        if frame is None:
            return
        folder = Path(self.cfg.dataset.dir)
        folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + f"_{self.cfg.device.id}.jpg"
        (folder / name).write_bytes(rgb_to_jpeg(frame, 92))
        files = sorted(folder.glob("*.jpg"))
        for old in files[: max(len(files) - self.cfg.dataset.max_files, 0)]:
            old.unlink(missing_ok=True)

    # ---- stream helpers ---------------------------------------------------

    def _status_text(self, label: str | None = None) -> str:
        label = label or self._last_reading.get("label")
        if not label:
            return "warming up"
        score = self._last_reading.get("health_score")
        return f"{label.replace('_', ' ')} {score:.0%}" if score is not None else label

    def _render(self, frame):
        detections = self._last_analysis.detections if self._last_analysis else []
        return annotate(frame, detections, self.classes, self._status_text())

    def _status(self) -> dict:
        return {
            "device_id": self.cfg.device.id,
            "model_version": self.detector.model_version,
            "broker_connected": self.bus.connected.is_set(),
            "seconds_since_last_frame": round(self.camera.seconds_since_last_frame(), 1),
            "stream_enabled": self.stream.enabled,
            "stream_clients": self.stream.clients,
            "last_reading": self._last_reading,
        }
