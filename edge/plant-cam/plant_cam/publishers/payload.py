"""The reading payload, schema `plant-health/2` (docs/SCHEMAS.md).

Design rules, from how the VERT backend treats a payload:
  * every top-level NUMBER is charted (TimescaleDB) and usable in
    automation rules, so metrics are flat numeric keys
  * strings/objects are kept only as the device's latest reading
    (devices.last_reading), so details go in `conditions` / `env`
  * `label`, `note`, `health_score` (0-1) and `confidence` keep the shape
    the dashboard's plant-health card already renders
  * the envelope stays schema_version "1.0" (the gateway rejects others);
    this payload's own version is `payload_schema`
"""

from __future__ import annotations

import math
from typing import Any

from ..core.types import ZoneReport, iso_utc

PAYLOAD_SCHEMA = "plant-health/2"


def _num(v: float) -> float | None:
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else v


def build_payload(report: ZoneReport) -> dict[str, Any]:
    p: dict[str, Any] = {
        "payload_schema": PAYLOAD_SCHEMA,
        "profile_id": report.profile_id,
        "profile_version": report.profile_version,
        "model_id": report.model_id,
        "model_version": report.model_version,
        "health_score": report.health_score,
        "label": report.label,
        "note": report.note,
        "confidence": report.confidence,
        "visual_score": report.visual_score,
    }
    if report.env_risk_score is not None:
        p["env_risk_score"] = report.env_risk_score
    for k, v in sorted(report.metrics.items()):
        n = _num(v)
        if n is not None and k not in p:
            p[k] = round(float(n), 4)

    env = report.env
    p["env_available"] = 1.0 if env.available else 0.0
    if env.available:
        p["env_temp_c"] = env.temp_c
        p["env_rh_pct"] = env.rh_pct
        if env.dew_point_c is not None:
            p["env_dew_point_c"] = env.dew_point_c
        if env.leaf_wetness is not None:
            p["env_leaf_wetness"] = env.leaf_wetness
        if env.age_s is not None:
            p["env_age_s"] = env.age_s
        for name, risk in sorted(env.risks.items()):
            p[f"risk_{name}"] = risk
            p[f"risk_{name}_hours"] = env.hours.get(name, 0.0)

    for k, v in sorted(report.system.items()):
        p[k] = v
    p["latency_ms"] = round(sum(report.latency_ms.values()), 1)

    p["conditions"] = [
        {
            "id": c.id,
            "kind": c.kind,
            "status": c.status,  # "detected" | "suspected" -- never a diagnosis
            "confidence": round(c.confidence, 3),
            "persistence": c.persistence,
            "active": c.active,
            "roi": c.roi,
            "first_seen": iso_utc(c.first_seen),
        }
        for c in sorted(report.conditions, key=lambda c: (not c.active, -c.confidence))
    ]
    if report.image_ref:
        p["image_ref"] = report.image_ref
    return p
