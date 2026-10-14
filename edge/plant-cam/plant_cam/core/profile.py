"""Crop profiles: everything crop-specific, kept out of the core.

A profile is a folder, e.g. profiles/strawberry/:

    profile.yaml   conditions, persistence, risk indices, scoring, notes, default analyzers
    plugins.py     optional: crop-specific analyzers (imported for their @register_* side effects)

Adding a crop means adding a folder; nothing in plant_cam/ changes.
All thresholds in a profile are starting points to tune against your own
greenhouse, not agronomic truth -- see docs/PROFILES.md.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

log = logging.getLogger("plant-cam.profile")

DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[2] / "profiles"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Corroboration(_Strict):
    """A condition only counts if a metric agrees, e.g. a nutrient flag
    needs the canopy's yellow ratio to actually be up."""

    metric: str
    op: Literal[">", ">=", "<", "<="]
    value: float

    def holds(self, metrics: dict[str, float]) -> bool:
        v = metrics.get(self.metric)
        if v is None:
            return False
        return {
            ">": v > self.value,
            ">=": v >= self.value,
            "<": v < self.value,
            "<=": v <= self.value,
        }[self.op]


class ConditionDef(_Strict):
    id: str
    kind: Literal["disease", "nutrient", "pest", "fruit"]
    # Signal names (classifier labels) that count as evidence.
    signals: list[str]
    suspect_at: float = Field(0.45, ge=0, le=1)
    detect_at: float = Field(0.75, ge=0, le=1)
    # Nutrient findings are never more than "suspected" from images alone.
    max_status: Literal["detected", "suspected"] = "detected"
    severity: float = Field(0.5, ge=0, le=1)  # how much an active condition costs the visual score
    min_count: int = Field(1, ge=1)  # signals needed in one analysis (e.g. 2 spotted leaves)
    corroborate: list[Corroboration] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def _order(self) -> ConditionDef:
        if self.detect_at < self.suspect_at:
            raise ValueError(f"{self.id}: detect_at must be >= suspect_at")
        return self


class Persistence(_Strict):
    # (Not `on:`/`off:` -- YAML 1.1 reads those keys as booleans.)
    activate_after: int = Field(3, ge=1)  # consecutive analyses before a condition becomes active (alerts)
    clear_after: int = Field(2, ge=1)  # consecutive misses before it clears


class RiskIndexDef(_Strict):
    """Hours in the last `window_h` where the conditions held, ramped
    linearly from 0 at `onset_h` to 1 at `full_h`."""

    window_h: float = 24.0
    temp_c: tuple[float, float]
    rh_pct: tuple[float, float] = (0.0, 101.0)
    # How free water (leaf wetness, or condensation estimated from RH and
    # dew point) combines with the RH band:
    #   ignore    RH band only
    #   or        RH band OR wet          (e.g. Botrytis: long wet or humid spells)
    #   required  RH band AND wet
    #   excluded  RH band AND NOT wet     (e.g. powdery mildew: humid, but no free water)
    wet: Literal["ignore", "or", "required", "excluded"] = "ignore"
    onset_h: float = Field(4.0, ge=0)
    full_h: float = Field(12.0, gt=0)
    note: str = ""

    @model_validator(mode="after")
    def _order(self) -> RiskIndexDef:
        if self.full_h <= self.onset_h:
            raise ValueError("full_h must be greater than onset_h")
        return self


class WetnessDef(_Strict):
    rh_min: float = 95.0  # RH at/above this counts as wet when there is no leaf-wetness sensor
    dewpoint_spread_c: float = 1.0  # or air within this of its dew point
    leaf_wetness_min: float = 50.0  # leaf-wetness sensor reading that means wet


class Scoring(_Strict):
    thriving: float = 0.85
    steady: float = 0.65
    stressed: float = 0.45
    env_weight: float = Field(0.25, ge=0, le=1)
    yellow_weight: float = Field(0.5, ge=0)
    brown_weight: float = Field(0.8, ge=0)
    min_canopy_pct: float = 2.0  # below this, "no plants in view"


class AnalyzerEntry(_Strict):
    name: str
    id: str | None = None  # defaults to name; set it to run one analyzer twice
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.id or self.name


class Profile(_Strict):
    id: str
    version: str
    crop: str = ""
    plugins: str | None = None
    analyzers: list[AnalyzerEntry] = Field(default_factory=list)
    persistence: Persistence = Field(default_factory=Persistence)
    scoring: Scoring = Field(default_factory=Scoring)
    conditions: list[ConditionDef] = Field(default_factory=list)
    risk_indices: dict[str, RiskIndexDef] = Field(default_factory=dict)
    wetness: WetnessDef = Field(default_factory=WetnessDef)
    # Model label vocabularies, checked against a model's meta.json at load.
    labels: dict[str, list[str]] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)
    path: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _unique(self) -> Profile:
        ids = [c.id for c in self.conditions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate condition ids")
        keys = [a.key for a in self.analyzers]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate analyzer ids; give repeated analyzers an `id:`")
        return self

    def condition(self, cid: str) -> ConditionDef | None:
        return next((c for c in self.conditions if c.id == cid), None)

    def note(self, key: str, default: str = "") -> str:
        return self.notes.get(key, default)


class ProfileError(Exception):
    pass


def load_profile(profile_id: str, profiles_dir: Path | None = None) -> Profile:
    base = Path(profiles_dir) if profiles_dir else DEFAULT_PROFILES_DIR
    folder = base / profile_id
    path = folder / "profile.yaml"
    if not path.exists():
        available = sorted(p.parent.name for p in base.glob("*/profile.yaml"))
        raise ProfileError(f"no crop profile {profile_id!r} in {base} (available: {available})")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profile = Profile.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ProfileError(f"invalid crop profile {path}:\n{exc}") from exc
    if profile.id != profile_id:
        raise ProfileError(f"{path} declares id {profile.id!r} but lives in folder {profile_id!r}")
    profile.path = folder
    if profile.plugins:
        _import_plugins(folder / profile.plugins, profile.id)
    return profile


def _import_plugins(path: Path, profile_id: str) -> None:
    if not path.exists():
        raise ProfileError(f"profile plugin module {path} does not exist")
    name = f"plant_cam_profile_{profile_id}_{hashlib.sha1(str(path).encode()).hexdigest()[:8]}"
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ProfileError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    log.debug("loaded profile plugins from %s", path)
