"""Turns a rolling window of Analyses into one reading payload.

Single frames are noisy (a leaf turns, a cloud passes), so every
published reading is smoothed over the last `window` analyses. Anything
"sustained for N minutes" is left to the automation engine, which
already does that well.

The payload deliberately keeps the shape the dashboard's plant-health
card already understands (label, note, health_score, confidence -- see
frontend/src/views/DeviceDetail.vue), and every count is a number so the
gateway charts it and automation rules can use it, e.g.
"pest_count > 0 for 10 minutes".
"""
from __future__ import annotations

import collections
import dataclasses

from .detector import Analysis, Detector

# Same exposure band, labels and greenness cut-offs as
# services/inference/vision.py, so both report on the same scale.
BRIGHTNESS_TRUSTED_LOW = 0.12
BRIGHTNESS_TRUSTED_HIGH = 0.92
LABEL_ORDER = ("thriving", "steady", "stressed", "needs_attention")

_NOTES = {
    "thriving": "Lots of healthy green growth -- whatever you're doing, it's working.",
    "steady": "Looking alright overall. Nothing here calls for action right now.",
    "too_dark": "The light is too dim or too harsh to read the plants clearly right now -- this will update as the light changes.",
    "no_plants": "Couldn't make out any leaves in view -- check the camera is still pointed at the plants.",
}


@dataclasses.dataclass
class _Summary:
    counts: dict[str, int]
    mean_score: float | None
    green_fraction: float
    brightness: float


def label_rank(label: str) -> int:
    return LABEL_ORDER.index(label) if label in LABEL_ORDER else 0


class HealthTracker:
    def __init__(self, detector: Detector, window: int):
        self.detector = detector
        self.classes = {c.name: c for c in detector.classes}
        self.window: collections.deque[_Summary] = collections.deque(maxlen=max(window, 1))

    def add(self, analysis: Analysis) -> None:
        counts = collections.Counter(d.cls for d in analysis.detections)
        scores = [d.score for d in analysis.detections]
        self.window.append(_Summary(
            counts=dict(counts),
            mean_score=sum(scores) / len(scores) if scores else None,
            green_fraction=analysis.green_fraction,
            brightness=analysis.brightness,
        ))

    def reading(self) -> dict:
        n = len(self.window)
        green = sum(s.green_fraction for s in self.window) / n
        brightness = sum(s.brightness for s in self.window) / n
        exposure_ok = BRIGHTNESS_TRUSTED_LOW <= brightness <= BRIGHTNESS_TRUSTED_HIGH

        payload: dict = {}
        if self.detector.model_based:
            mean_counts = {name: sum(s.counts.get(name, 0) for s in self.window) / n for name in self.classes}
            health, confidence, label, note, top_issue, affected = self._from_detections(mean_counts)
            for name, value in mean_counts.items():
                payload[f"{name}_count"] = round(value, 1)
            payload["pest_count"] = round(sum(v for k, v in mean_counts.items() if self.classes[k].kind == "pest"), 1)
            payload["affected_pct"] = round(affected * 100, 1)
        else:
            health, confidence, label, note, top_issue = self._from_greenness(green)

        if not exposure_ok:
            label, note, confidence = "steady", _NOTES["too_dark"], 0.35

        payload.update({
            "health_score": round(health, 3),
            "confidence": round(confidence, 2),
            "label": label,
            "note": note,
            "top_issue": top_issue,
            "green_fraction": round(green, 3),
            "model_version": self.detector.model_version,
        })
        return payload

    def _from_detections(self, mean_counts: dict[str, float]):
        leaves = sum(v for k, v in mean_counts.items() if self.classes[k].kind in ("healthy", "issue"))
        pests = sum(v for k, v in mean_counts.items() if self.classes[k].kind == "pest")
        scored = [s.mean_score for s in self.window if s.mean_score is not None]
        confidence = sum(scored) / len(scored) if scored else 0.3

        if leaves < 0.5 and pests < 0.5:
            return 0.0, 0.3, "steady", _NOTES["no_plants"], "", 0.0

        weighted = sum(self.classes[k].weight * v for k, v in mean_counts.items() if self.classes[k].kind == "issue")
        affected = min(weighted / leaves, 1.0) if leaves else 0.0
        # Disease spreads: a fifth of the leaves affected is already worth
        # acting on, so the affected share counts 1.5x against health
        # (20% -> 0.70 steady, 30% -> 0.55 stressed, 40% -> 0.40 needs attention).
        health = max(0.0, 1.0 - 1.5 * affected - min(0.4, 0.15 * pests))

        if health >= 0.9:
            label = "thriving"
        elif health >= 0.7:
            label = "steady"
        elif health >= 0.45:
            label = "stressed"
        else:
            label = "needs_attention"

        problems = {k: self.classes[k].weight * v for k, v in mean_counts.items()
                    if self.classes[k].kind in ("issue", "pest") and v >= 0.5}
        # Any sustained problem rules out "thriving", so the note below
        # names it while it's still small. Even one sustained pest
        # sighting is worth a look, however green the rest of the plant is.
        if problems and label == "thriving":
            label = "steady"
        if pests >= 0.5 and label_rank(label) < label_rank("stressed"):
            label = "stressed"

        # The note names the single biggest problem, in words a grower acts on.
        if not problems or label == "thriving":
            return health, confidence, label, _NOTES.get(label, _NOTES["steady"]), "", affected
        top = max(problems, key=problems.get)
        info = self.classes[top]
        count = max(round(mean_counts[top]), 1)
        if info.kind == "pest":
            where = f" About {count} spotted."
        else:
            where = f" Seen on about {count} {'leaf' if count == 1 else 'leaves'}."
        note = (info.note or f"Signs of {top.replace('_', ' ')} spotted.") + where
        return health, confidence, label, note, top, affected

    @staticmethod
    def _from_greenness(green: float):
        health = min(max(green, 0.0), 1.0)
        if health >= 0.55:
            label = "thriving"
        elif health >= 0.30:
            label = "steady"
        elif health >= 0.12:
            label = "stressed"
        else:
            label = "needs_attention"
        notes = {
            **_NOTES,
            "stressed": "Less green than usual in view. Not an emergency, but worth a closer look when you have a moment.",
            "needs_attention": "Very little green visible. This might need attention soon -- check water, light, and soil.",
        }
        return health, 0.85, label, notes[label], ""
