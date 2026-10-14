"""FrameSource interface and the capture loop that owns it.

One thread owns the camera. It keeps the newest live frame (the lores
stream on a Pi) in a slot that always holds only the latest frame, so a
slow reader never builds up latency -- old frames are simply replaced.
Analysis asks the same thread for a main-stream frame (and, lazily, a
full-sensor still) through a small request queue, so the hardware is only
ever touched from one thread.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

from ..core.types import Frame, FrameMeta, Image

log = logging.getLogger("plant-cam.capture")

T = TypeVar("T")


class FrameSource(Protocol):
    """What the rest of the app needs from a camera, a folder or a video."""

    name: str

    def open(self) -> None: ...

    def read_live(self) -> Image:
        """Blocks for the next live-view frame (small, RGB)."""
        ...

    def capture(self, seq: int) -> tuple[Image, FrameMeta]:
        """An analysis frame (main stream, RGB) with its capture metadata."""
        ...

    def capture_hires(self) -> Image:
        """A sharper frame of the same view for Stage B crops (RGB)."""
        ...

    def calibrate(self, settle_s: float) -> dict[str, object]:
        """Measure white balance/exposure under current light and lock them."""
        ...

    def close(self) -> None: ...


class LatestSlot:
    """A bounded queue of size N that drops the oldest item when full.
    With N=1 it is "the newest frame", which is what live view and change
    detection want: never a backlog, never stale."""

    def __init__(self, maxlen: int = 1) -> None:
        self._maxlen = maxlen
        self._items: list[tuple[int, Image]] = []
        self._seq = 0
        self._cond = threading.Condition()
        self.dropped = 0

    def put(self, item: Image) -> int:
        with self._cond:
            self._seq += 1
            self._items.append((self._seq, item))
            if len(self._items) > self._maxlen:
                self._items.pop(0)
                self.dropped += 1
            self._cond.notify_all()
            return self._seq

    def latest(self) -> tuple[Image | None, int]:
        with self._cond:
            if not self._items:
                return None, self._seq
            seq, item = self._items[-1]
            return item, seq

    def wait_newer(self, after_seq: int, timeout: float) -> tuple[Image | None, int]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq > after_seq, timeout=timeout)
            if not self._items:
                return None, self._seq
            seq, item = self._items[-1]
            return item, seq

    def wake_all(self) -> None:
        with self._cond:
            self._cond.notify_all()


class CaptureLoop:
    def __init__(self, source: FrameSource, stall_timeout_s: float = 30.0) -> None:
        self.source = source
        self.stall_timeout_s = stall_timeout_s
        self.live = LatestSlot(1)
        self._requests: queue.Queue[tuple[Callable[[], object], concurrent.futures.Future[object]]] = queue.Queue()
        self._stop = threading.Event()
        self._opened = threading.Event()
        self._last_frame_at = time.monotonic()
        # Frames OR reopen attempts: a camera that is unplugged and being
        # retried is alive; one hung inside a blocking driver call is not.
        self._last_activity = time.monotonic()
        self._capture_seq = 0
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self.errors = 0

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.live.wake_all()
        self._thread.join(timeout=10)

    def wait_open(self, timeout: float) -> bool:
        return self._opened.wait(timeout)

    def seconds_since_last_frame(self) -> float:
        return time.monotonic() - self._last_frame_at

    @property
    def camera_ok(self) -> bool:
        return self._opened.is_set() and self.seconds_since_last_frame() < 10.0

    @property
    def wedged(self) -> bool:
        """No frame and no retry for stall_timeout_s: the capture thread is
        stuck in the driver. Only a process restart recovers from that."""
        return time.monotonic() - self._last_activity > self.stall_timeout_s

    # ---- requests from other threads ---------------------------------------

    def _call(self, fn: Callable[[], T], timeout: float) -> T:
        fut: concurrent.futures.Future[object] = concurrent.futures.Future()
        self._requests.put((fn, fut))
        return fut.result(timeout=timeout)  # type: ignore[return-value]

    def capture_frame(self, timeout: float = 20.0) -> Frame:
        """An analysis frame; its hi-res still is only taken if an analyzer asks."""

        def grab() -> tuple[Image, FrameMeta]:
            self._capture_seq += 1
            return self.source.capture(self._capture_seq)

        image, meta = self._call(grab, timeout)
        return Frame(image=image, meta=meta, get_hires=lambda: self._call(self.source.capture_hires, 60.0))

    def calibrate(self, settle_s: float = 3.0) -> dict[str, object]:
        return self._call(lambda: self.source.calibrate(settle_s), settle_s + 30.0)

    # ---- the camera thread ----------------------------------------------------

    def _serve_requests(self) -> None:
        while True:
            try:
                fn, fut = self._requests.get_nowait()
            except queue.Empty:
                return
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except Exception as exc:  # handed to the caller, who decides
                fut.set_exception(exc)

    def _fail_pending(self, exc: Exception) -> None:
        while True:
            try:
                _, fut = self._requests.get_nowait()
            except queue.Empty:
                return
            if fut.set_running_or_notify_cancel():
                fut.set_exception(exc)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.source.open()
                self._opened.set()
                log.info("camera open", extra={"fields": {"source": self.source.name}})
                backoff = 1.0
                while not self._stop.is_set():
                    self._serve_requests()
                    frame = self.source.read_live()
                    self.live.put(frame)
                    self._last_frame_at = self._last_activity = time.monotonic()
            except Exception as exc:
                self.errors += 1
                self._opened.clear()
                log.warning(
                    "camera error, reopening", extra={"fields": {"error": repr(exc), "retry_in_s": round(backoff, 1)}}
                )
                self._fail_pending(exc)
                try:
                    self.source.close()
                except Exception:
                    log.debug("close after error failed", exc_info=True)
                self._last_activity = time.monotonic()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 20.0)
                self._last_activity = time.monotonic()
        self._fail_pending(RuntimeError("capture loop stopped"))
        try:
            self.source.close()
        except Exception:
            log.debug("close on stop failed", exc_info=True)
