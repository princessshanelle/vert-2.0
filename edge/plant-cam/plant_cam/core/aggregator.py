"""Turns one frame's analyzer results into a ZoneReport.

Visual findings and environmental risk are scored separately and then
combined:

    visual_score   = 1 - sum(severity x confidence of ACTIVE conditions)
                       - yellow_weight x yellow_ratio - brown_weight x brown_ratio
    env_risk_score = max(risk indices)              (absent without sensor data)
    health_score   = visual_score x (1 - env_weight x env_risk_score)

health_score stays on the 0-1 scale the dashboard's health card already
uses. Only *active* (persistent) conditions cost score, so one noisy
frame never moves it.
"""

from __future__ import annotations

import datetime as dt

from .profile import Profile
from .temporal import ConditionTracker, evaluate
from .types import AnalyzerResult, Detection, EnvSnapshot, Frame, Signal, ZoneReport

LABELS = ("thriving", "steady", "stressed", "needs_attention")


def _label(score: float, profile: Profile) -> str:
    s = profile.scoring
    if score >= s.thriving:
        return "thriving"
    if score >= s.steady:
        return "steady"
    if score >= s.stressed:
        return "stressed"
    return "needs_attention"


class Aggregator:
    def __init__(self, profile: Profile, tracker: ConditionTracker) -> None:
        self.profile = profile
        self.tracker = tracker

    def _severity(self, cid: str) -> float:
        cdef = self.profile.condition(cid)
        return cdef.severity if cdef else 0.5

    def build(
        self,
        frame: Frame,
        results: dict[str, AnalyzerResult],
        env: EnvSnapshot,
        system: dict[str, float],
        now: dt.datetime | None = None,
    ) -> ZoneReport:
        now = now or frame.meta.ts
        p = self.profile
        metrics: dict[str, float] = {}
        signals: list[Signal] = []
        detections: list[Detection] = []
        latency: dict[str, float] = {}
        models: list[str] = []
        vocabulary: set[str] = set()
        stage_b_conf: list[float] = []

        for key, r in results.items():
            latency[key] = round(r.latency_ms, 1)
            if r.skipped and not r.vocabulary:
                continue
            metrics.update(r.metrics)
            for roi, rm in r.roi_metrics.items():
                for k, v in rm.items():
                    metrics[f"roi_{roi}_{k}"] = v
            signals.extend(r.signals)
            detections.extend(r.detections)
            vocabulary.update(r.vocabulary)
            stage_b_conf.extend(d.score for d in r.detections if d.attrs.get("stage") == "B")
            if r.model_id:
                models.append(f"{key}:{r.model_id}@{r.model_version}")

        exposure_ok = metrics.get("exposure_ok", 1.0) >= 1.0
        has_plants = metrics.get("canopy_coverage_pct", 100.0) >= p.scoring.min_canopy_pct

        evaluable = {c.id for c in p.conditions if vocabulary & set(c.signals)} if exposure_ok else set()
        observations = evaluate(p, signals, metrics) if exposure_ok else []
        conditions, newly = self.tracker.update(observations, now, evaluable)

        # Flat, chartable per-condition metrics -- only for conditions a
        # loaded model can actually see, so automation rules get explicit
        # zeros to clear on, and the payload doesn't fill with dead keys.
        for cid in sorted(evaluable):
            mine = [c for c in conditions if c.id == cid]
            metrics[f"cond_{cid}_conf"] = max((c.confidence for c in mine), default=0.0)
            metrics[f"cond_{cid}_persist"] = float(max((c.persistence for c in mine), default=0))
            metrics[f"cond_{cid}_active"] = 1.0 if any(c.active for c in mine) else 0.0

        active = [c for c in conditions if c.active]
        penalty = sum(self._severity(c.id) * c.confidence for c in active)
        if exposure_ok:
            penalty += p.scoring.yellow_weight * metrics.get("yellow_ratio", 0.0)
            penalty += p.scoring.brown_weight * metrics.get("brown_ratio", 0.0)
        visual = max(0.0, min(1.0, 1.0 - penalty))
        env_risk = max(env.risks.values()) if env.risks else None
        health = visual * (1.0 - p.scoring.env_weight * (env_risk or 0.0))
        label = _label(health, p)

        # Stage B's own confidence when it ran; the colour baseline alone is worth 0.6.
        confidence = sum(stage_b_conf) / len(stage_b_conf) if stage_b_conf else 0.6
        note = p.note(label)
        if not exposure_ok:
            label, note, confidence = "steady", p.note("too_dark"), 0.3
        elif not has_plants:
            label, note, confidence = "steady", p.note("no_plants"), 0.3
        elif active:
            top = max(active, key=lambda c: self._severity(c.id) * c.confidence)
            note = top.note or note
        elif env_risk is not None and env_risk >= 0.5:
            top_risk = max(env.risks, key=lambda k: env.risks[k])
            idx = p.risk_indices.get(top_risk)
            note = idx.note if idx and idx.note else note
        elif metrics.get("red_leaf_ratio", 0.0) >= 0.15 and label in ("thriving", "steady"):
            note = p.note("red_tint", note)

        model_id = ",".join(m.split("@")[0] for m in models) or "none"
        model_version = ",".join(models) or "classical"
        return ZoneReport(
            ts=now,
            frame=frame,
            profile_id=p.id,
            profile_version=p.version,
            model_id=model_id,
            model_version=model_version,
            health_score=round(health, 3),
            visual_score=round(visual, 3),
            env_risk_score=round(env_risk, 3) if env_risk is not None else None,
            label=label,
            note=note,
            confidence=round(confidence, 2),
            metrics=metrics,
            conditions=conditions,
            newly_active=newly,
            detections=detections,
            env=env,
            latency_ms=latency,
            system=system,
        )
