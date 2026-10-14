"""CPU temperature, under-voltage and graceful degradation.

Read from sysfs, which works as the unprivileged service user (vcgencmd
needs /dev/vcio, root-only on Raspberry Pi OS Trixie):

  temperature   /sys/class/thermal/thermal_zone0/temp
  under-voltage /sys/class/hwmon/hwmonN/in0_lcrit_alarm   (driver "rpi_volt")

Degrade levels (with hysteresis so it doesn't flap at a boundary):

  0 normal
  1 >= soft_c       analysis interval x2, live view capped at 2 fps
  2 >= hard_c       + heavy (ML) analyzers and active learning skipped, interval x4
  3 >= critical_c   + live view off           (also on under-voltage)
    or under-voltage
"""

from __future__ import annotations

import contextlib
import glob
import logging
import os
import resource
from pathlib import Path

from ..config import ThermalCfg

log = logging.getLogger("plant-cam.thermal")


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


class ThermalMonitor:
    def __init__(self, cfg: ThermalCfg) -> None:
        self.cfg = cfg
        self.level = 0
        self._undervolt_path = self._find_undervolt()
        self.last_temp: float | None = None
        self.undervoltage = False

    def _find_undervolt(self) -> str | None:
        for d in glob.glob(self.cfg.hwmon_glob):
            if _read(f"{d}/name") == "rpi_volt" and os.path.exists(f"{d}/in0_lcrit_alarm"):
                return f"{d}/in0_lcrit_alarm"
        return None

    def temperature(self) -> float | None:
        raw = _read(self.cfg.temp_path)
        try:
            return int(raw) / 1000.0 if raw else None
        except ValueError:
            return None

    def poll(self) -> int:
        temp = self.temperature()
        self.last_temp = temp
        self.undervoltage = bool(self._undervolt_path and _read(self._undervolt_path) == "1")
        target = 0
        if temp is not None:
            for lvl, limit in ((3, self.cfg.critical_c), (2, self.cfg.hard_c), (1, self.cfg.soft_c)):
                # going up: at the limit; coming down: only once below limit - hysteresis
                threshold = limit if lvl > self.level else limit - self.cfg.hysteresis_c
                if temp >= threshold:
                    target = lvl
                    break
        if self.undervoltage:
            target = 3
        if target != self.level:
            log.warning(
                "thermal level changed",
                extra={"fields": {"from": self.level, "to": target, "temp_c": temp, "undervoltage": self.undervoltage}},
            )
            self.level = target
        return self.level

    @property
    def interval_factor(self) -> float:
        return {0: 1.0, 1: 2.0}.get(self.level, 4.0)

    @property
    def allow_heavy(self) -> bool:
        return self.level < 2

    @property
    def stream_fps_cap(self) -> float | None:
        return {0: None, 1: 2.0, 2: 2.0}.get(self.level, 0.0)

    def system_metrics(self) -> dict[str, float]:
        out: dict[str, float] = {"degrade_level": float(self.level)}
        if self.last_temp is not None:
            out["cpu_temp_c"] = round(self.last_temp, 1)
        out["undervoltage"] = 1.0 if self.undervoltage else 0.0
        out["rss_mb"] = round(_rss_mb(), 1)
        with contextlib.suppress(OSError):
            out["load_1m"] = round(os.getloadavg()[0], 2)
        return out


def _rss_mb() -> float:
    status = _read("/proc/self/status") or ""
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
