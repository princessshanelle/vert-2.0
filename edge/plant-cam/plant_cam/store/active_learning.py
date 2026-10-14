"""Active-learning sample store: crops worth labelling, kept on disk with
their metadata, bounded in size and rate.

Saved: Stage B crops the model was unsure about, crops that contributed to
a condition becoming active, and a small random sample (so the labelled
set isn't only hard cases). Each sample is a JPEG plus a JSON sidecar:

    <dir>/<YYYYmmdd>/<ts>_<reason>_<label>_<n>.jpg
    <dir>/<YYYYmmdd>/<ts>_<reason>_<label>_<n>.json   model, scores, box, roi, env, ...

`plant_cam export-al --out DIR` copies them out for Label Studio; see
training/README.md. When the folder exceeds max_bytes the oldest go first.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Any

import cv2

from ..config import ActiveLearningCfg
from ..core.types import SampleCandidate, ZoneReport

log = logging.getLogger("plant-cam.active-learning")


class ActiveLearningStore:
    def __init__(self, cfg: ActiveLearningCfg, device_id: str) -> None:
        self.cfg = cfg
        self.device_id = device_id
        self.dir = Path(cfg.dir)
        self._recent: deque[float] = deque()
        self._bytes: int | None = None
        self._n = 0

    def _allow(self, now: float) -> bool:
        while self._recent and now - self._recent[0] > 3600:
            self._recent.popleft()
        return len(self._recent) < self.cfg.max_per_hour

    def save(self, samples: list[SampleCandidate], report: ZoneReport) -> int:
        if not self.cfg.enabled or not samples:
            return 0
        saved = 0
        # alert-related first, then uncertain, then random
        order = {"alert": 0, "uncertain": 1, "frame": 2, "random": 3}
        for s in sorted(samples, key=lambda s: order[s.reason]):
            now = time.time()
            if not self._allow(now):
                break
            try:
                self._write(s, report, now)
                self._recent.append(now)
                saved += 1
            except OSError as exc:
                log.warning("could not save sample", extra={"fields": {"error": repr(exc)}})
                break
        if saved:
            self._enforce_cap()
        return saved

    def _write(self, s: SampleCandidate, report: ZoneReport, now: float) -> None:
        day = time.strftime("%Y%m%d", time.gmtime(now))
        folder = self.dir / day
        folder.mkdir(parents=True, exist_ok=True)
        self._n += 1
        stem = f"{time.strftime('%Y%m%dT%H%M%S', time.gmtime(now))}_{s.reason}_{s.label}_{self._n}"
        ok, buf = cv2.imencode(
            ".jpg", cv2.cvtColor(s.image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality]
        )
        if not ok:
            raise OSError("jpeg encode failed")
        meta: dict[str, Any] = {
            "device_id": self.device_id,
            "ts": report.ts.isoformat(),
            "reason": s.reason,
            "predicted": s.label,
            "scores": s.scores,
            "box": s.box,
            "roi": s.roi,
            "profile": f"{report.profile_id}@{report.profile_version}",
            "model_version": report.model_version,
            "frame_seq": report.frame.meta.seq,
            "env": {"temp_c": report.env.temp_c, "rh_pct": report.env.rh_pct},
        }
        jpg, side = folder / f"{stem}.jpg", folder / f"{stem}.json"
        jpg.write_bytes(buf.tobytes())
        side.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        if self._bytes is not None:
            self._bytes += jpg.stat().st_size + side.stat().st_size

    def _enforce_cap(self) -> None:
        files = sorted(self.dir.rglob("*.jpg"))
        if self._bytes is None:
            self._bytes = sum(f.stat().st_size for f in self.dir.rglob("*") if f.is_file())
        while self._bytes > self.cfg.max_bytes and files:
            oldest = files.pop(0)
            for f in (oldest, oldest.with_suffix(".json")):
                if f.exists():
                    self._bytes -= f.stat().st_size
                    f.unlink()
        for d in self.dir.iterdir():
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    def export(self, out: Path) -> int:
        out.mkdir(parents=True, exist_ok=True)
        n = 0
        for jpg in sorted(self.dir.rglob("*.jpg")):
            shutil.copy2(jpg, out / jpg.name)
            side = jpg.with_suffix(".json")
            if side.exists():
                shutil.copy2(side, out / side.name)
            n += 1
        return n
