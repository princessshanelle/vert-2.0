"""Stage D: disease-pressure indices from temperature, humidity and leaf wetness.

Each index (defined in the crop profile) counts the hours in a trailing
window where its conditions held, then ramps 0 -> 1 between `onset_h` and
`full_h`. With no leaf-wetness sensor, "wet" is estimated: RH at or above
`wetness.rh_min`, or air within `dewpoint_spread_c` of its dew point.

These are deliberately simple, transparent pressure indicators with
conservative, tunable thresholds -- not validated epidemiological models.
"""

from __future__ import annotations

import dataclasses
import math

from ..core.profile import RiskIndexDef, WetnessDef

MAX_GAP_S = 1800.0  # a sample "covers" at most 30 min; longer gaps count as unknown


@dataclasses.dataclass(frozen=True)
class EnvSample:
    ts: float  # epoch seconds
    temp_c: float | None
    rh_pct: float | None
    leaf_wetness: float | None = None


def dew_point_c(temp_c: float, rh_pct: float) -> float:
    """Magnus formula (Alduchov & Eskridge coefficients)."""
    a, b = 17.625, 243.04
    rh = min(max(rh_pct, 1e-3), 100.0)
    gamma = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


def is_wet(s: EnvSample, w: WetnessDef) -> bool | None:
    if s.leaf_wetness is not None:
        return s.leaf_wetness >= w.leaf_wetness_min
    if s.temp_c is None or s.rh_pct is None:
        return None
    return s.rh_pct >= w.rh_min or (s.temp_c - dew_point_c(s.temp_c, s.rh_pct)) <= w.dewpoint_spread_c


def condition_holds(s: EnvSample, idx: RiskIndexDef, w: WetnessDef) -> bool:
    if s.temp_c is None or s.rh_pct is None:
        return False
    if not idx.temp_c[0] <= s.temp_c <= idx.temp_c[1]:
        return False
    in_band = idx.rh_pct[0] <= s.rh_pct < idx.rh_pct[1] if idx.rh_pct[1] <= 100 else s.rh_pct >= idx.rh_pct[0]
    wet = bool(is_wet(s, w))
    if idx.wet == "or":
        return in_band or wet
    if idx.wet == "required":
        return in_band and wet
    if idx.wet == "excluded":
        return in_band and not wet
    return in_band


def hours_meeting(samples: list[EnvSample], idx: RiskIndexDef, w: WetnessDef, now: float) -> float:
    start = now - idx.window_h * 3600
    ordered = sorted((s for s in samples if s.ts <= now), key=lambda s: s.ts)
    total = 0.0
    for i, s in enumerate(ordered):
        nxt = ordered[i + 1].ts if i + 1 < len(ordered) else now
        a, b = max(s.ts, start), min(nxt, s.ts + MAX_GAP_S)
        if b > a and condition_holds(s, idx, w):
            total += b - a
    return total / 3600.0


def risk_from_hours(hours: float, idx: RiskIndexDef) -> float:
    return max(0.0, min(1.0, (hours - idx.onset_h) / (idx.full_h - idx.onset_h)))


def compute_risks(
    samples: list[EnvSample], indices: dict[str, RiskIndexDef], w: WetnessDef, now: float
) -> dict[str, tuple[float, float]]:
    """{name: (risk 0-1, hours)}"""
    out = {}
    for name, idx in indices.items():
        hours = hours_meeting(samples, idx, w, now)
        out[name] = (round(risk_from_hours(hours, idx), 3), round(hours, 2))
    return out
