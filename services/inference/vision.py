"""VERT's plant-health heuristic.

This is deliberately NOT a trained deep-learning classifier -- there is
no model file, no dataset, and no training pipeline behind this yet (see
the project overview's gaps list). What it is instead: a real, documented
computer-vision technique -- the Excess Green Index (ExG = 2G - R - B),
long used in agricultural imaging to separate live vegetation from soil,
pots, and background -- applied honestly, with its limitations stated
plainly rather than dressed up as something it isn't.

The point of building even this much is that `analyze()` is the entire
surface a future real model needs to replace: same input (raw image
bytes), same output shape (HealthResult). Nothing in consumer.py or
main.py needs to change on the day someone trains a real classifier and
swaps this function out. Until then, a grower gets an honest, immediate,
free signal instead of nothing.
"""
from __future__ import annotations

import dataclasses
import io
import logging

import numpy as np
from PIL import Image, UnidentifiedImageError

log = logging.getLogger("vert-inference")

# Longest edge an incoming photo is resized to before analysis. Plenty
# of resolution for a greenness estimate, small enough to keep this fast
# and light on memory on a Raspberry-Pi-class host.
_MAX_EDGE = 512

# A pixel counts as "vegetation" once its Excess Green Index clears this.
# 0.0 is the conventional starting point for ExG-based segmentation;
# real-world lighting varies enough that this is a reasonable default,
# not a precisely tuned constant.
_EXG_VEGETATION_THRESHOLD = 0.05

# Normalized mean brightness (0-1) outside this band means the exposure
# itself is unreliable enough that the greenness estimate shouldn't be
# trusted much -- a very dark or blown-out photo can't be read honestly.
_BRIGHTNESS_TRUSTED_LOW = 0.12
_BRIGHTNESS_TRUSTED_HIGH = 0.92


@dataclasses.dataclass(frozen=True)
class HealthResult:
    health_score: float   # 0.0-1.0, higher is healthier-looking
    label: str            # one of the _LABELS below
    confidence: float     # 0.0-1.0, how much to trust health_score
    green_fraction: float # share of the frame read as live vegetation
    note: str             # a short, warm, plain-language sentence for a person

    def to_payload(self) -> dict:
        return dataclasses.asdict(self)


_LABELS = ("thriving", "steady", "stressed", "needs_attention")


def analyze(image_bytes: bytes) -> HealthResult | None:
    """Returns None only when the bytes genuinely can't be read as an
    image -- every other outcome, including a low-confidence one, is a
    HealthResult, because "we're not sure, but here's our best honest
    read" is more useful to a grower than silence.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        log.warning("could not decode image: %s", exc)
        return None

    image.thumbnail((_MAX_EDGE, _MAX_EDGE))
    pixels = np.asarray(image, dtype=np.float32) / 255.0

    r, g, b = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    exg = 2 * g - r - b
    green_fraction = float(np.mean(exg > _EXG_VEGETATION_THRESHOLD))

    brightness = float(np.mean(pixels))
    exposure_ok = _BRIGHTNESS_TRUSTED_LOW <= brightness <= _BRIGHTNESS_TRUSTED_HIGH

    health_score = round(min(max(green_fraction, 0.0), 1.0), 3)
    confidence = round(0.85 if exposure_ok else 0.35, 2)
    label = _label_for(health_score, exposure_ok)
    note = _note_for(label, exposure_ok)

    return HealthResult(
        health_score=health_score,
        label=label,
        confidence=confidence,
        green_fraction=round(green_fraction, 3),
        note=note,
    )


def _label_for(score: float, exposure_ok: bool) -> str:
    if not exposure_ok:
        return "steady"  # withheld judgment -- see the note for why
    if score >= 0.55:
        return "thriving"
    if score >= 0.30:
        return "steady"
    if score >= 0.12:
        return "stressed"
    return "needs_attention"


def _note_for(label: str, exposure_ok: bool) -> str:
    if not exposure_ok:
        return "This photo was too dark or too bright to read clearly -- worth another shot in better light when you get a chance."
    return {
        "thriving": "Lots of healthy green growth in this photo -- whatever you're doing, it's working.",
        "steady": "Looking alright overall. Nothing here calls for action right now.",
        "stressed": "Less green than usual in this shot. Not an emergency, but worth a closer look when you have a moment.",
        "needs_attention": "Very little green visible here. This one might need some attention soon -- check water, light, and soil when you can.",
    }[label]
