"""Stage C: conditions from signals, and persistence over time.

1. `evaluate()` maps this analysis's signals to the crop profile's
   conditions: confidence thresholds, minimum count, corroborating metrics,
   and the "never more than suspected" cap for nutrient findings.
2. `ConditionTracker` requires a condition to be seen in N consecutive
   analyses before it becomes *active* (which is what alerts key off), and
   M consecutive misses before it clears. Single-frame flicker -- a leaf
   turning, a glare spot -- never reaches the grower.

An analysis where nothing could have seen a condition (too dark, model
skipped for heat) is not a miss: the tracker simply isn't updated.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Literal

from .profile import Profile
from .types import ConditionState, Signal


@dataclasses.dataclass(frozen=True)
class Observation:
    condition: str
    roi: str
    status: Literal["detected", "suspected"]
    confidence: float
    count: int


def evaluate(profile: Profile, signals: list[Signal], metrics: dict[str, float]) -> list[Observation]:
    out: list[Observation] = []
    for cond in profile.conditions:
        by_roi: dict[str, list[Signal]] = {}
        for s in signals:
            if s.name in cond.signals:
                by_roi.setdefault(s.roi, []).append(s)
        for roi, sigs in by_roi.items():
            strong = [s for s in sigs if s.confidence >= cond.suspect_at]
            if len(strong) < cond.min_count:
                continue
            if not all(c.holds(metrics) for c in cond.corroborate):
                continue
            conf = max(s.confidence for s in strong)
            detected = conf >= cond.detect_at and cond.max_status == "detected"
            out.append(Observation(cond.id, roi, "detected" if detected else "suspected", round(conf, 3), len(strong)))
    return out


@dataclasses.dataclass
class _Track:
    hits: int = 0
    misses: int = 0
    active: bool = False
    first_seen: str = ""
    status: str = "suspected"
    confidence: float = 0.0


class ConditionTracker:
    def __init__(self, profile: Profile, state: dict[str, Any] | None = None) -> None:
        self.profile = profile
        self.on = profile.persistence.activate_after
        self.off = profile.persistence.clear_after
        self._tracks: dict[str, _Track] = {}
        for key, raw in (state or {}).items():
            try:
                self._tracks[key] = _Track(**raw)
            except TypeError:
                continue  # state from an older version; start that track fresh

    @staticmethod
    def _key(condition: str, roi: str) -> str:
        return f"{condition}|{roi}"

    def state(self) -> dict[str, Any]:
        return {k: dataclasses.asdict(t) for k, t in self._tracks.items()}

    def update(
        self, observations: list[Observation], now: dt.datetime, evaluable: set[str]
    ) -> tuple[list[ConditionState], list[str]]:
        """Returns (current conditions, ids that just became active)."""
        seen = {self._key(o.condition, o.roi): o for o in observations}
        newly: list[str] = []
        for key, obs in seen.items():
            t = self._tracks.setdefault(key, _Track(first_seen=now.isoformat()))
            t.hits += 1
            t.misses = 0
            t.status, t.confidence = obs.status, obs.confidence
            if not t.active and t.hits >= self.on:
                t.active = True
                newly.append(obs.condition)
        for key in list(self._tracks):
            if key in seen:
                continue
            condition = key.split("|", 1)[0]
            if condition not in evaluable:
                continue  # nothing could see it this time: not a miss
            t = self._tracks[key]
            t.misses += 1
            t.hits = 0
            t.confidence = 0.0
            if t.misses >= self.off:
                del self._tracks[key]

        states: list[ConditionState] = []
        for key, t in self._tracks.items():
            condition, roi = key.split("|", 1)
            cdef = self.profile.condition(condition)
            if cdef is None:
                continue
            states.append(
                ConditionState(
                    id=condition,
                    kind=cdef.kind,
                    status=t.status,  # type: ignore[arg-type]
                    confidence=t.confidence,
                    persistence=t.hits,
                    active=t.active,
                    roi=roi,
                    first_seen=dt.datetime.fromisoformat(t.first_seen),
                    note=cdef.note,
                )
            )
        return states, sorted(set(newly))
