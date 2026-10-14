"""Frame sources: the Pi camera, a folder of images, or a video file."""

from __future__ import annotations

from ..config import CameraCfg
from .base import CaptureLoop, FrameSource, LatestSlot
from .files import FolderSource, VideoSource

__all__ = ["CaptureLoop", "FolderSource", "FrameSource", "LatestSlot", "VideoSource", "make_source"]


def make_source(cfg: CameraCfg) -> FrameSource:
    if cfg.source == "folder":
        return FolderSource(cfg.folder, cfg.main_size, cfg.lores_size, cfg.fps, cfg.loop)
    if cfg.source == "video":
        return VideoSource(cfg.video, cfg.main_size, cfg.lores_size, cfg.fps, cfg.loop)
    from .picam import Picamera2Source

    return Picamera2Source(cfg)
