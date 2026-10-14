"""Off-device frame sources: a folder of images and a video file.

They behave like the camera as far as the rest of the app can tell, which
is what lets the whole pipeline run (and be tested) on a laptop.

Folder: each analysis `capture()` advances to the next image; live view
shows the current one. Video: frames play at the configured fps, and an
analysis captures whatever is on screen.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from ..core.types import FrameMeta, Image, utc_now

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def fit(image: Image, size: tuple[int, int]) -> Image:
    """Downscale (never upscale) to fit inside size=(w, h), keeping aspect."""
    h, w = image.shape[:2]
    scale = min(size[0] / w, size[1] / h)
    if scale >= 1:
        return image
    out: Image = cv2.resize(image, (max(round(w * scale), 1), max(round(h * scale), 1)), interpolation=cv2.INTER_AREA)
    return out


def read_rgb(path: Path) -> Image:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError(f"could not read image {path}")
    rgb: Image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return rgb


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)


class FolderSource:
    name = "folder"

    def __init__(
        self, folder: str, main_size: tuple[int, int], lores_size: tuple[int, int], fps: float, loop: bool = True
    ) -> None:
        self.folder = Path(folder)
        self.main_size = main_size
        self.lores_size = lores_size
        self.fps = fps
        self.loop = loop
        self.files: list[Path] = []
        self.index = -1
        self._current: Image | None = None
        self.current_path: Path | None = None

    def open(self) -> None:
        if not self.folder.is_dir():
            raise RuntimeError(f"camera.folder {self.folder.resolve()} is not a folder")
        self.files = list_images(self.folder)
        if not self.files:
            raise RuntimeError(f"no images in {self.folder.resolve()}")
        if self._current is None:
            self._advance()

    def _advance(self) -> None:
        nxt = self.index + 1
        if nxt >= len(self.files):
            if not self.loop:
                raise EOFError("folder source exhausted")
            nxt = 0
        self.index = nxt
        self.current_path = self.files[nxt]
        self._current = read_rgb(self.current_path)

    def read_live(self) -> Image:
        time.sleep(1.0 / self.fps)
        assert self._current is not None
        return fit(self._current, self.lores_size)

    def capture(self, seq: int) -> tuple[Image, FrameMeta]:
        if seq > 1:
            self._advance()
        assert self._current is not None and self.current_path is not None
        return fit(self._current, self.main_size), FrameMeta(seq=seq, ts=utc_now(), source=self.current_path.name)

    def capture_hires(self) -> Image:
        assert self._current is not None
        return self._current

    def calibrate(self, settle_s: float) -> dict[str, object]:
        return {"source": "folder", "note": "nothing to calibrate"}

    def close(self) -> None:
        pass


class VideoSource:
    name = "video"

    def __init__(
        self, path: str, main_size: tuple[int, int], lores_size: tuple[int, int], fps: float, loop: bool = True
    ) -> None:
        self.path = path
        self.main_size = main_size
        self.lores_size = lores_size
        self.fps = fps
        self.loop = loop
        self.cap: cv2.VideoCapture | None = None
        self._current: Image | None = None

    def open(self) -> None:
        self.cap = cv2.VideoCapture(self.path)
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open video {self.path}")

    def _next(self) -> Image:
        assert self.cap is not None
        ok, bgr = self.cap.read()
        if not ok:
            if not self.loop:
                raise EOFError("video source exhausted")
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, bgr = self.cap.read()
            if not ok:
                raise RuntimeError(f"video {self.path} has no frames")
        rgb: Image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return rgb

    def read_live(self) -> Image:
        time.sleep(1.0 / self.fps)
        self._current = self._next()
        return fit(self._current, self.lores_size)

    def capture(self, seq: int) -> tuple[Image, FrameMeta]:
        if self._current is None:
            self._current = self._next()
        return fit(self._current, self.main_size), FrameMeta(seq=seq, ts=utc_now(), source=Path(self.path).name)

    def capture_hires(self) -> Image:
        if self._current is None:
            self._current = self._next()
        return np.ascontiguousarray(self._current)

    def calibrate(self, settle_s: float) -> dict[str, object]:
        return {"source": "video", "note": "nothing to calibrate"}

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
