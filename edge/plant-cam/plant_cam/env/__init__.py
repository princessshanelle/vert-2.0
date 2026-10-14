"""Environmental context from the zone's other sensors (Stage D).

The MQTT link subscribes to `vert/<zone>/+/+/reading` and hands every
reading to EnvMonitor.ingest(). The monitor keeps the latest value per
device, samples a zone-level history every `sample_every_s` (persisted, so
a restart doesn't forget the last 24 h of humidity), and computes the
crop profile's risk indices on demand.

Visual detections and environmental risk are reported separately; the
aggregator combines them into one health score.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Protocol

from ..config import EnvCfg
from ..core.profile import Profile
from ..core.types import EnvSnapshot
from .risk import EnvSample, compute_risks, dew_point_c

log = logging.getLogger("plant-cam.env")


class EnvHistory(Protocol):
    def add_env(self, sample: EnvSample) -> None: ...

    def env_since(self, ts: float) -> list[EnvSample]: ...


class MemoryEnvHistory:
    def __init__(self) -> None:
        self.samples: list[EnvSample] = []

    def add_env(self, sample: EnvSample) -> None:
        self.samples.append(sample)

    def env_since(self, ts: float) -> list[EnvSample]:
        return [s for s in self.samples if s.ts >= ts]


def _first_number(payload: dict[str, Any], fields: list[str]) -> float | None:
    for f in fields:
        v = payload.get(f)
        if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v):
            return float(v)
    return None


class EnvMonitor:
    def __init__(
        self, cfg: EnvCfg, profile: Profile, history: EnvHistory | None = None, own_device_id: str = ""
    ) -> None:
        self.cfg = cfg
        self.profile = profile
        self.history = history or MemoryEnvHistory()
        self.own_device_id = own_device_id
        self._latest: dict[str, tuple[float, float | None, float | None, float | None]] = {}
        self._last_sample = 0.0
        self._lock = threading.Lock()

    def ingest(self, device_id: str, payload: dict[str, Any], ts: float | None = None) -> None:
        if device_id == self.own_device_id:
            return
        temp = _first_number(payload, self.cfg.temp_fields)
        rh = _first_number(payload, self.cfg.rh_fields)
        wet = _first_number(payload, self.cfg.wetness_fields)
        if temp is None and rh is None and wet is None:
            return
        if rh is not None and not 0 <= rh <= 100:
            log.debug("ignoring implausible RH %s from %s", rh, device_id)
            rh = None
        now = ts if ts is not None else time.time()
        with self._lock:
            self._latest[device_id] = (now, temp, rh, wet)
            if now - self._last_sample >= self.cfg.sample_every_s:
                sample = self._zone_sample(now)
                if sample.temp_c is not None or sample.rh_pct is not None:
                    self._last_sample = now
                    self.history.add_env(sample)

    def _zone_sample(self, now: float) -> EnvSample:
        fresh = [v for v in self._latest.values() if now - v[0] <= self.cfg.max_age_s]

        def mean(i: int) -> float | None:
            vals = [v[i] for v in fresh if v[i] is not None]
            return sum(vals) / len(vals) if vals else None  # type: ignore[arg-type]

        return EnvSample(now, mean(1), mean(2), mean(3))

    def snapshot(self, now: float | None = None) -> EnvSnapshot:
        now = now if now is not None else time.time()
        with self._lock:
            current = self._zone_sample(now)
            ages = [now - v[0] for v in self._latest.values() if now - v[0] <= self.cfg.max_age_s]
        if current.temp_c is None or current.rh_pct is None:
            return EnvSnapshot(age_s=min(ages) if ages else None)
        samples = self.history.env_since(
            now - max(i.window_h for i in self.profile.risk_indices.values()) * 3600
            if self.profile.risk_indices
            else now
        )
        risks = compute_risks([*samples, current], self.profile.risk_indices, self.profile.wetness, now)
        return EnvSnapshot(
            temp_c=round(current.temp_c, 2),
            rh_pct=round(current.rh_pct, 1),
            leaf_wetness=current.leaf_wetness,
            age_s=round(min(ages), 1) if ages else None,
            risks={name: r for name, (r, _hours) in risks.items()},
            hours={name: h for name, (_r, h) in risks.items()},
            dew_point_c=round(dew_point_c(current.temp_c, current.rh_pct), 2),
        )
