"""Draws ROIs, detections and a status banner onto a frame, and encodes JPEGs.
Used for both the live view and the uploaded snapshots, so what a grower
sees live is what gets saved."""

from __future__ import annotations

import datetime as dt

import cv2
import numpy as np

from ..core.types import ROI, Detection, Image

# BGR, since drawing happens on the BGR copy that gets encoded.
_COLOURS = {
    "leaf": (80, 175, 76),
    "flower": (230, 230, 230),
    "runner": (160, 160, 60),
    "fruit": (60, 60, 230),
    "fruit_ripe": (40, 40, 220),
    "fruit_overripe": (40, 20, 120),
    "fruit_turning": (120, 120, 255),
    "fruit_white": (220, 220, 220),
    "fruit_green": (60, 200, 120),
}
_FINDING = (0, 170, 255)
_ROI = (200, 160, 40)


def _colour(label: str, healthy: bool) -> tuple[int, int, int]:
    if label in _COLOURS:
        return _COLOURS[label]
    return (80, 175, 76) if healthy else _FINDING


def annotate(
    rgb: Image,
    detections: list[Detection],
    rois: list[ROI],
    status: str,
    healthy_labels: frozenset[str] = frozenset({"healthy_leaf", "healthy_flower", "background"}),
) -> Image:
    """Returns a new BGR image with boxes and a banner."""
    img: Image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    scale = max(w / 1280, 0.5)
    thick = max(int(2 * scale), 1)
    font = cv2.FONT_HERSHEY_SIMPLEX

    for roi in rois:
        x1, y1, x2, y2 = roi.pixels(w, h)
        cv2.rectangle(img, (x1, y1), (x2 - 1, y2 - 1), _ROI, 1)
        cv2.putText(img, roi.name, (x1 + 4, y2 - 6), font, 0.45 * scale, _ROI, 1, cv2.LINE_AA)

    for d in detections:
        if d.label in healthy_labels:
            continue
        x1, y1, x2, y2 = (round(d.box[0] * w), round(d.box[1] * h), round(d.box[2] * w), round(d.box[3] * h))
        colour = _colour(d.label, d.label in healthy_labels)
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, thick)
        text = f"{d.label.replace('_', ' ')} {d.score:.0%}"
        (tw, th), _ = cv2.getTextSize(text, font, 0.45 * scale, 1)
        top = max(y1 - th - 6, 0)
        cv2.rectangle(img, (x1, top), (x1 + tw + 6, top + th + 6), colour, -1)
        cv2.putText(img, text, (x1 + 3, top + th + 2), font, 0.45 * scale, (0, 0, 0), 1, cv2.LINE_AA)

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    banner = f"VERT plant-cam | {status} | {stamp}"
    (tw, th), _ = cv2.getTextSize(banner, font, 0.55 * scale, 1)
    roi_band = img[0 : th + 14, 0 : min(tw + 20, w)]
    roi_band[:] = (roi_band * 0.4 + np.array((20, 60, 20)) * 0.6).astype(np.uint8)
    cv2.putText(img, banner, (10, th + 7), font, 0.55 * scale, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def status_text(label: str, health_score: float, confidence: float) -> str:
    """Banner text; never shows a confident-looking score for a reading
    that was withheld (too dark, no plants in view)."""
    if confidence <= 0.3:
        return f"{label.replace('_', ' ')} (can't judge right now)"
    return f"{label.replace('_', ' ')} {health_score:.0%}"


def encode_jpeg(bgr: Image, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return bytes(buf.tobytes())
