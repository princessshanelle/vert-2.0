"""The data that flows through the pipeline.

    FrameSource -> Frame -> Analyzers -> AnalyzerResult -> Aggregator -> ZoneReport -> Publishers

Images are always HxWx3 uint8 numpy arrays in RGB order. Boxes and ROIs
are normalised to 0-1 of the frame, so an analyzer can work on a
downscaled copy and its boxes still land in the right place on the
full-resolution still or the 640x480 live view.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from .profile import Profile

Image = npt.NDArray[np.uint8]
Box = tuple[float, float, float, float]  # x1, y1, x2, y2, normalised 0-1


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso_utc(ts: dt.datetime) -> str:
    """UTC ISO 8601 with milliseconds and a Z, the format every VERT device uses."""
    return ts.astimezone(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclasses.dataclass(frozen=True)
class FrameMeta:
    seq: int
    ts: dt.datetime
    source: str
    exposure_us: int | None = None
    analogue_gain: float | None = None
    colour_gains: tuple[float, float] | None = None
    lux: float | None = None  # the camera's own light estimate; None off-device


@dataclasses.dataclass
class Frame:
    """One analysis sample.

    `image` is what analyzers look at by default (the camera's main stream,
    or a folder image). `get_hires` lazily returns a sharper image of the
    same field of view for Stage B crops -- on the Pi that triggers a full
    sensor still, so it is only paid for when an analyzer actually asks.
    """

    image: Image
    meta: FrameMeta
    get_hires: Callable[[], Image] | None = None
    _hires: Image | None = dataclasses.field(default=None, repr=False)

    def hires(self) -> Image:
        if self._hires is None:
            self._hires = self.get_hires() if self.get_hires else self.image
        return self._hires


@dataclasses.dataclass(frozen=True)
class ROI:
    """A named region of the frame, e.g. one tray, or the strip below a
    gutter where fruit hangs. `kinds` says what lives there, so the fruit
    counter can skip a canopy-only ROI and vice versa."""

    name: str
    box: Box
    kinds: frozenset[str] = frozenset({"canopy", "fruit"})

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = self.box
        return (
            round(x1 * width),
            round(y1 * height),
            max(round(x2 * width), round(x1 * width) + 1),
            max(round(y2 * height), round(y1 * height) + 1),
        )

    def crop(self, image: Image) -> Image:
        h, w = image.shape[:2]
        x1, y1, x2, y2 = self.pixels(w, h)
        return image[y1:y2, x1:x2]

    def to_frame_box(self, inner: Box) -> Box:
        """Maps a box normalised to this ROI back to frame coordinates."""
        x1, y1, x2, y2 = self.box
        w, h = x2 - x1, y2 - y1
        return (x1 + inner[0] * w, y1 + inner[1] * h, x1 + inner[2] * w, y1 + inner[3] * h)


WHOLE_FRAME = ROI("frame", (0.0, 0.0, 1.0, 1.0))


@dataclasses.dataclass(frozen=True)
class Detection:
    """Something located in the frame: a leaf, a flower, a fruit, a lesion."""

    label: str
    score: float
    box: Box
    roi: str = WHOLE_FRAME.name
    attrs: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Signal:
    """Evidence for a condition, e.g. `powdery_mildew_leaf` at 0.71 on one
    crop. Signals are what the crop profile maps to conditions; an analyzer
    never decides on its own that a plant "has" something."""

    name: str
    confidence: float
    roi: str = WHOLE_FRAME.name
    box: Box | None = None
    source: str = ""


@dataclasses.dataclass(frozen=True)
class SampleCandidate:
    """An image region worth keeping for labelling (active learning)."""

    image: Image
    reason: Literal["uncertain", "alert", "random", "frame"]
    label: str
    scores: dict[str, float]
    box: Box | None = None
    roi: str = WHOLE_FRAME.name


@dataclasses.dataclass
class AnalyzerResult:
    analyzer: str
    metrics: dict[str, float] = dataclasses.field(default_factory=dict)
    roi_metrics: dict[str, dict[str, float]] = dataclasses.field(default_factory=dict)
    detections: list[Detection] = dataclasses.field(default_factory=list)
    signals: list[Signal] = dataclasses.field(default_factory=list)
    samples: list[SampleCandidate] = dataclasses.field(default_factory=list)
    model_id: str | None = None
    model_version: str | None = None
    # Signal names this analyzer *could* have produced this time (its model's
    # labels); conditions built only from other signals aren't "missed" by it.
    vocabulary: list[str] = dataclasses.field(default_factory=list)
    latency_ms: float = 0.0
    skipped: str | None = None  # why it didn't run (no model, thermal, ...)


@dataclasses.dataclass(frozen=True)
class EnvSnapshot:
    """What the zone's sensors say right now, plus the derived risk indices."""

    temp_c: float | None = None
    rh_pct: float | None = None
    leaf_wetness: float | None = None
    age_s: float | None = None
    risks: dict[str, float] = dataclasses.field(default_factory=dict)
    hours: dict[str, float] = dataclasses.field(default_factory=dict)  # hours each index's conditions held
    dew_point_c: float | None = None

    @property
    def available(self) -> bool:
        return self.temp_c is not None and self.rh_pct is not None


class HistoryStore:
    """What an analyzer may remember between runs (growth tracking). The
    real implementation is SQLite-backed; tests use the in-memory one."""

    def __init__(self) -> None:
        self._points: dict[str, list[tuple[float, float]]] = {}

    def add(self, key: str, ts: float, value: float) -> None:
        self._points.setdefault(key, []).append((ts, value))

    def near(self, key: str, ts: float, tolerance_s: float) -> float | None:
        best: tuple[float, float] | None = None
        for t, v in self._points.get(key, []):
            d = abs(t - ts)
            if d <= tolerance_s and (best is None or d < best[0]):
                best = (d, v)
        return best[1] if best else None


@dataclasses.dataclass
class Context:
    """Everything an analyzer may need besides the frame itself."""

    profile: Profile
    rois: list[ROI]
    history: HistoryStore
    env: EnvSnapshot = dataclasses.field(default_factory=EnvSnapshot)
    # Results of the analyzers that already ran on this frame, in order --
    # Stage B reads Stage A's detections from here.
    results: dict[str, AnalyzerResult] = dataclasses.field(default_factory=dict)
    degrade_level: int = 0

    def rois_for(self, kind: str) -> list[ROI]:
        return [r for r in self.rois if kind in r.kinds] or [WHOLE_FRAME]

    def detections(self, *labels: str) -> list[Detection]:
        out: list[Detection] = []
        for result in self.results.values():
            out.extend(d for d in result.detections if not labels or d.label in labels)
        return out


@dataclasses.dataclass(frozen=True)
class ConditionState:
    id: str
    kind: str  # disease | nutrient | pest | fruit
    status: Literal["detected", "suspected"]
    confidence: float
    persistence: int
    active: bool
    roi: str
    first_seen: dt.datetime
    note: str = ""


@dataclasses.dataclass
class ZoneReport:
    """One analysis, aggregated: what every publisher receives."""

    ts: dt.datetime
    frame: Frame
    profile_id: str
    profile_version: str
    model_id: str
    model_version: str
    health_score: float
    visual_score: float
    env_risk_score: float | None
    label: str
    note: str
    confidence: float
    metrics: dict[str, float]
    conditions: list[ConditionState]
    newly_active: list[str]
    detections: list[Detection]
    env: EnvSnapshot
    latency_ms: dict[str, float]
    system: dict[str, float]
    image_ref: str | None = None
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)
