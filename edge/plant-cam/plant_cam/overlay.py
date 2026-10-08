"""Draws detections and a status banner onto a frame, and encodes JPEGs.
Used for both the live stream and the evidence photos, so what a grower
sees live is exactly what gets saved."""
from __future__ import annotations

import datetime as dt

import cv2
import numpy as np

from .detector import ClassInfo, Detection

# BGR, since drawing happens on the BGR copy that gets encoded.
_KIND_COLOURS = {"healthy": (80, 175, 76), "issue": (0, 170, 255), "pest": (40, 40, 220)}
_DEFAULT_COLOUR = (200, 200, 200)


def annotate(rgb: np.ndarray, detections: list[Detection], classes: dict[str, ClassInfo], status: str) -> np.ndarray:
    """Returns a new BGR image (ready for encode_jpeg) with boxes and banner."""
    img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    scale = max(img.shape[1] / 1280, 0.5)
    thickness = max(int(2 * scale), 1)
    font = cv2.FONT_HERSHEY_SIMPLEX

    for d in detections:
        info = classes.get(d.cls)
        colour = _KIND_COLOURS.get(info.kind if info else "", _DEFAULT_COLOUR)
        x1, y1, x2, y2 = d.box
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, thickness)
        text = f"{d.cls.replace('_', ' ')} {d.score:.0%}"
        (tw, th), _ = cv2.getTextSize(text, font, 0.5 * scale, 1)
        cv2.rectangle(img, (x1, max(y1 - th - 6, 0)), (x1 + tw + 6, max(y1, th + 6)), colour, -1)
        cv2.putText(img, text, (x1 + 3, max(y1 - 4, th + 2)), font, 0.5 * scale, (255, 255, 255), 1, cv2.LINE_AA)

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    banner = f"VERT  |  AI plant monitor  |  {status}  |  {stamp}"
    (tw, th), _ = cv2.getTextSize(banner, font, 0.6 * scale, 1)
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (tw + 20, th + 16), (20, 60, 20), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    cv2.putText(img, banner, (10, th + 8), font, 0.6 * scale, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def encode_jpeg(bgr: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def rgb_to_jpeg(rgb: np.ndarray, quality: int) -> bytes:
    return encode_jpeg(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), quality)
