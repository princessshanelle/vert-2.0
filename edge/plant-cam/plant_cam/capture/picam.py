"""Raspberry Pi camera via Picamera2/libcamera (never the legacy stack).

Two streams run continuously at a low frame rate:
  * lores (640x480 YUV420): live view and change detection -- cheap to copy
  * main  (1296x972 RGB):   only read when an analysis asks for a frame
A full-sensor still is taken on demand for Stage B crops (switch_mode_and_
capture_array), so the 15 MB buffers exist only for a moment.

White balance and exposure can be locked to values measured by
`plant_cam calibrate` (see CameraCfg.awb/.exposure): colour-based plant
analysis is only comparable across days if the camera's own colour
processing isn't drifting with the light.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import cv2

from ..config import CameraCfg
from ..core.types import FrameMeta, Image, utc_now

log = logging.getLogger("plant-cam.picamera2")


class Picamera2Source:
    name = "picamera2"

    def __init__(self, cfg: CameraCfg) -> None:
        self.cfg = cfg
        self.cam: Any = None
        self._still_config: Any = None

    def _transform(self) -> Any:
        from libcamera import Transform

        return Transform(hflip=self.cfg.hflip, vflip=self.cfg.vflip)

    def open(self) -> None:
        from picamera2 import Picamera2

        self.cam = Picamera2()
        video = self.cam.create_video_configuration(
            # Picamera2 follows libcamera's naming: "BGR888" is R,G,B in memory.
            main={"size": tuple(self.cfg.main_size), "format": "BGR888"},
            lores={"size": tuple(self.cfg.lores_size), "format": "YUV420"},
            transform=self._transform(),
            controls={"FrameRate": float(self.cfg.fps)},
            buffer_count=3,
        )
        self.cam.configure(video)
        if self.cfg.still_size:
            self._still_config = self.cam.create_still_configuration(
                main={"size": tuple(self.cfg.still_size), "format": "BGR888"},
                transform=self._transform(),
                buffer_count=1,
            )
        self.cam.start()
        if "AfMode" in self.cam.camera_controls:  # Camera Module 3
            from libcamera import controls

            self.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        self._apply_calibration()

    # ---- calibration --------------------------------------------------------

    def _apply_calibration(self) -> None:
        path = Path(self.cfg.calibration_file)
        if self.cfg.awb == "auto" and self.cfg.exposure == "auto":
            return
        if not path.exists():
            log.warning("no camera calibration yet -- auto white balance/exposure until `plant_cam calibrate` runs")
            return
        try:
            cal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning(
                "ignoring unreadable calibration file", extra={"fields": {"path": str(path), "error": repr(exc)}}
            )
            return
        controls: dict[str, Any] = {}
        if self.cfg.awb == "locked" and cal.get("colour_gains"):
            controls.update(AwbEnable=False, ColourGains=tuple(cal["colour_gains"]))
        if self.cfg.exposure == "locked" and cal.get("exposure_us"):
            controls.update(
                AeEnable=False, ExposureTime=int(cal["exposure_us"]), AnalogueGain=float(cal.get("analogue_gain", 1.0))
            )
        if controls:
            self.cam.set_controls(controls)
            log.info("camera calibration applied", extra={"fields": {k: str(v) for k, v in controls.items()}})

    def calibrate(self, settle_s: float) -> dict[str, object]:
        self.cam.set_controls({"AwbEnable": True, "AeEnable": True})
        deadline = time.monotonic() + settle_s
        meta: dict[str, Any] = {}
        while time.monotonic() < deadline:
            meta = self.cam.capture_metadata()
        result: dict[str, object] = {
            "colour_gains": list(meta.get("ColourGains", (1.0, 1.0))),
            "exposure_us": int(meta.get("ExposureTime", 0)),
            "analogue_gain": float(meta.get("AnalogueGain", 1.0)),
            "colour_temperature": meta.get("ColourTemperature"),
            "lux": meta.get("Lux"),
            "measured_at": utc_now().isoformat(),
        }
        path = Path(self.cfg.calibration_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
        tmp.replace(path)
        self._apply_calibration()
        return result

    # ---- frames ---------------------------------------------------------------

    def read_live(self) -> Image:
        yuv = self.cam.capture_array("lores")
        rgb: Image = cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB_I420)
        return rgb

    def capture(self, seq: int) -> tuple[Image, FrameMeta]:
        request = self.cam.capture_request()
        try:
            image: Image = request.make_array("main").copy()
            md = request.get_metadata()
        finally:
            request.release()
        gains = md.get("ColourGains")
        return image, FrameMeta(
            seq=seq,
            ts=utc_now(),
            source=self.name,
            exposure_us=md.get("ExposureTime"),
            analogue_gain=md.get("AnalogueGain"),
            colour_gains=(float(gains[0]), float(gains[1])) if gains else None,
            lux=float(md["Lux"]) if md.get("Lux") is not None else None,
        )

    def capture_hires(self) -> Image:
        if self._still_config is None:
            image, _ = self.capture(0)
            return image
        still: Image = self.cam.switch_mode_and_capture_array(self._still_config, "main")
        return still

    def close(self) -> None:
        if self.cam is not None:
            try:
                self.cam.stop()
            finally:
                self.cam.close()
                self.cam = None
