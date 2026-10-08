"""The one and only owner of the camera.

A background thread keeps the newest frame in memory; the live stream,
the detector and the dataset saver all read from it instead of touching
the hardware themselves. That is what lets this device stream and analyse
at the same time, where the ESP32 camera has to pick one.

Frames are always HxWx3 uint8 numpy arrays in RGB order, whatever the
backend.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from .config import CameraCfg

log = logging.getLogger("plant-cam.camera")


class _Picamera2Backend:
    def __init__(self, cfg: CameraCfg):
        self.cfg = cfg
        self.cam = None

    def open(self) -> None:
        from libcamera import Transform, controls
        from picamera2 import Picamera2

        self.cam = Picamera2()
        # Picamera2's names follow libcamera's (little-endian) convention:
        # "BGR888" is laid out in memory as R, G, B -- i.e. what we want.
        config = self.cam.create_video_configuration(
            main={"size": (self.cfg.width, self.cfg.height), "format": "BGR888"},
            transform=Transform(hflip=self.cfg.hflip, vflip=self.cfg.vflip),
            controls={"FrameRate": float(self.cfg.fps)},
        )
        self.cam.configure(config)
        self.cam.start()
        # Camera Module 3 has autofocus; older modules simply don't list it.
        if "AfMode" in self.cam.camera_controls:
            self.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})

    def read(self) -> np.ndarray:
        return self.cam.capture_array("main")  # blocks until the next frame

    def close(self) -> None:
        if self.cam is not None:
            try:
                self.cam.stop()
                self.cam.close()
            finally:
                self.cam = None


class _OpenCVBackend:
    """USB webcam -- handy for developing on a laptop before the Pi."""

    def __init__(self, cfg: CameraCfg):
        self.cfg = cfg
        self.cap = None

    def open(self) -> None:
        import cv2

        self.cap = cv2.VideoCapture(self.cfg.opencv_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open webcam #{self.cfg.opencv_index}")

    def read(self) -> np.ndarray:
        import cv2

        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("webcam returned no frame")
        if self.cfg.hflip or self.cfg.vflip:
            frame = cv2.flip(frame, -1 if (self.cfg.hflip and self.cfg.vflip) else (1 if self.cfg.hflip else 0))
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class _FolderBackend:
    """Cycles through still images -- test the whole pipeline (and a
    trained model) with no camera at all."""

    _EXTS = {".jpg", ".jpeg", ".png", ".bmp"}

    def __init__(self, cfg: CameraCfg):
        self.cfg = cfg
        self.files: list[Path] = []
        self.i = 0

    def open(self) -> None:
        folder = Path(self.cfg.folder)
        self.files = sorted(p for p in folder.glob("*") if p.suffix.lower() in self._EXTS)
        if not self.files:
            raise RuntimeError(f"no images in {folder.resolve()}")

    def read(self) -> np.ndarray:
        import cv2

        time.sleep(1.0 / max(self.cfg.fps, 1))
        path = self.files[self.i % len(self.files)]
        self.i += 1
        frame = cv2.imread(str(path))
        if frame is None:
            raise RuntimeError(f"could not read {path}")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        pass


_BACKENDS = {"picamera2": _Picamera2Backend, "opencv": _OpenCVBackend, "folder": _FolderBackend}


class Camera:
    def __init__(self, cfg: CameraCfg):
        self.cfg = cfg
        self._backend = _BACKENDS[cfg.source](cfg)
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._last_frame_at = time.monotonic()  # start-up counts as "fresh"
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(timeout=5)
        self._backend.close()

    def latest(self) -> tuple[np.ndarray | None, int]:
        with self._cond:
            return self._frame, self._seq

    def wait_next(self, after_seq: int, timeout: float) -> tuple[np.ndarray | None, int]:
        """Blocks until a frame newer than `after_seq` exists (or timeout)."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > after_seq or self._stopping.is_set(), timeout=timeout)
            return self._frame, self._seq

    def seconds_since_last_frame(self) -> float:
        with self._cond:
            return time.monotonic() - self._last_frame_at

    # The thread survives transient camera errors by reopening with
    # backoff; only a camera that stays dead past stall_timeout_s gets
    # escalated (by app.py) to a full process restart.
    def _run(self) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                self._backend.open()
                log.info("camera open (%s, %dx%d @ %d fps)", self.cfg.source, self.cfg.width, self.cfg.height, self.cfg.fps)
                backoff = 1.0
                while not self._stopping.is_set():
                    frame = self._backend.read()
                    with self._cond:
                        self._frame = frame
                        self._seq += 1
                        self._last_frame_at = time.monotonic()
                        self._cond.notify_all()
            except Exception as exc:  # noqa: BLE001 -- any camera failure is handled the same way
                log.warning("camera error, reopening in %.0fs: %s", backoff, exc)
                self._backend.close()
                self._stopping.wait(backoff)
                backoff = min(backoff * 2, 30.0)
        with self._cond:
            self._cond.notify_all()
