"""Runs one analysis: preprocess -> analyzers (in order) -> Stage C -> aggregate.

Analyzers come from config `analyzers:` or, if unset, the crop profile's
default list. Relative `model_dir` params resolve against `models_dir`.
A failing analyzer is logged and recorded as skipped; it never takes the
others (or the process) down.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from .. import preprocess
from ..config import Config
from .aggregator import Aggregator
from .profile import AnalyzerEntry, Profile
from .registry import Analyzer, create_analyzer
from .temporal import ConditionTracker
from .types import ROI, AnalyzerResult, Context, EnvSnapshot, Frame, HistoryStore, SampleCandidate, ZoneReport

log = logging.getLogger("plant-cam.pipeline")


def resolve_entries(cfg: Config, profile: Profile) -> list[AnalyzerEntry]:
    entries = cfg.analyzers if cfg.analyzers is not None else profile.analyzers
    out = []
    for e in entries:
        params: dict[str, Any] = dict(e.params)
        md = params.get("model_dir")
        if isinstance(md, str) and not Path(md).is_absolute():
            params["model_dir"] = str(Path(cfg.models_dir) / md)
        out.append(e.model_copy(update={"params": params}))
    return out


class Pipeline:
    def __init__(
        self, cfg: Config, profile: Profile, history: HistoryStore, tracker_state: dict[str, Any] | None = None
    ) -> None:
        self.cfg = cfg
        self.profile = profile
        self.history = history
        self.rois: list[ROI] = cfg.roi_list()
        ref = cfg.preprocess.reference_roi
        self.reference: ROI | None = next((r.to_roi() for r in cfg.rois if r.name == ref), None) if ref else None
        self.analyzers: list[Analyzer] = [
            create_analyzer(e.name, e.params, profile, e.key) for e in resolve_entries(cfg, profile) if e.enabled
        ]
        self.tracker = ConditionTracker(profile, tracker_state)
        self.aggregator = Aggregator(profile, self.tracker)
        log.info(
            "pipeline ready",
            extra={
                "fields": {
                    "profile": f"{profile.id}@{profile.version}",
                    "analyzers": [a.id for a in self.analyzers],
                    "rois": [r.name for r in self.rois],
                }
            },
        )

    def close(self) -> None:
        for a in self.analyzers:
            try:
                a.close()
            except Exception:
                log.exception("closing %s failed", a.id)

    def models(self) -> dict[str, str]:
        out = {}
        for a in self.analyzers:
            model = getattr(a, "model", None)
            if model is not None:
                out[a.id] = f"{model.meta.model_id}@{model.meta.model_version}"
        return out

    def run(
        self,
        frame: Frame,
        env: EnvSnapshot | None = None,
        system: dict[str, float] | None = None,
        allow_heavy: bool = True,
        degrade_level: int = 0,
    ) -> tuple[ZoneReport, list[SampleCandidate]]:
        t0 = time.perf_counter()
        gains = None
        if self.reference is not None:
            frame.image, gains = preprocess.correct(frame.image, self.reference, self.cfg.preprocess.max_gain)
            if gains is not None and frame.get_hires is not None:
                raw_hires, g = frame.get_hires, gains
                frame.get_hires = lambda: preprocess.apply_gains(raw_hires(), g)
        pre_ms = (time.perf_counter() - t0) * 1000

        ctx = Context(
            profile=self.profile,
            rois=self.rois,
            history=self.history,
            env=env or EnvSnapshot(),
            degrade_level=degrade_level,
        )
        for a in self.analyzers:
            if a.cost == "heavy" and not allow_heavy:
                ctx.results[a.id] = AnalyzerResult(a.id, skipped="thermal")
                continue
            start = time.perf_counter()
            try:
                result = a.analyze(frame, ctx)
            except Exception:
                log.exception("analyzer %s failed on this frame", a.id)
                result = AnalyzerResult(a.id, skipped="error")
            result.latency_ms = (time.perf_counter() - start) * 1000
            ctx.results[a.id] = result

        report = self.aggregator.build(frame, ctx.results, ctx.env, system or {})
        report.latency_ms["preprocess"] = round(pre_ms, 1)
        if gains is not None:
            report.metrics["ref_gain_r"], report.metrics["ref_gain_g"], report.metrics["ref_gain_b"] = gains
        samples = [s for r in ctx.results.values() for s in r.samples]
        samples.extend(self._alert_samples(frame, ctx, report))
        return report, samples

    def _alert_samples(self, frame: Frame, ctx: Context, report: ZoneReport) -> list[SampleCandidate]:
        """Crops behind a condition that just became active -- the most
        valuable images to have a person confirm or correct."""
        if not report.newly_active:
            return []
        wanted = {s for cid in report.newly_active for s in (self.profile.condition(cid) or _NoCond).signals}
        out = []
        hires = frame.hires()
        h, w = hires.shape[:2]
        for r in ctx.results.values():
            for s in r.signals:
                if s.name in wanted and s.box is not None:
                    x1, y1, x2, y2 = s.box
                    crop = hires[round(y1 * h) : round(y2 * h), round(x1 * w) : round(x2 * w)]
                    if crop.size:
                        out.append(
                            SampleCandidate(
                                np.ascontiguousarray(crop), "alert", s.name, {s.name: s.confidence}, s.box, s.roi
                            )
                        )
        return out


class _NoCond:
    signals: list[str] = []
