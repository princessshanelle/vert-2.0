"""Per-analysis image corrections, applied once before any analyzer runs.

Only one today: if a grey or white reference card is in frame (a ROI with
kinds: [reference]), rescale R, G and B so the card reads neutral. That
cancels what's left of a grow light's colour cast after the camera's own
white balance, at the cost of three multiplies per pixel.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from ..core.types import ROI, Image

log = logging.getLogger("plant-cam.preprocess")


def reference_gains(image: Image, card: ROI, max_gain: float) -> tuple[float, float, float] | None:
    patch = card.crop(image).reshape(-1, 3).astype(np.float32)
    if patch.size == 0:
        return None
    # Ignore clipped and very dark pixels: they carry no colour information.
    lum = patch.mean(axis=1)
    usable = patch[(lum > 20) & (patch.max(axis=1) < 250)]
    if len(usable) < 50:
        log.warning("reference card unusable (too dark, clipped or covered); skipping colour correction")
        return None
    means = usable.mean(axis=0)
    target = float(means.mean())
    gains = np.clip(target / np.maximum(means, 1e-3), 1 / max_gain, max_gain)
    return float(gains[0]), float(gains[1]), float(gains[2])


def apply_gains(image: Image, gains: tuple[float, float, float]) -> Image:
    lut = np.stack([np.clip(np.arange(256) * g, 0, 255) for g in gains], axis=1).astype(np.uint8)
    out: Image = cv2.LUT(image, lut.reshape(256, 1, 3))  # one table lookup per pixel, saturating
    return out


def correct(image: Image, card: ROI | None, max_gain: float = 2.5) -> tuple[Image, tuple[float, float, float] | None]:
    if card is None:
        return image, None
    gains = reference_gains(image, card, max_gain)
    if gains is None:
        return image, None
    return apply_gains(image, gains), gains
