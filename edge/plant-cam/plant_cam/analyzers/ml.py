"""Two-stage ML analysis.

Stage A (`detector`): find leaves, flowers and fruit in the main-stream
frame with a small detector (~320 px input).

Stage B (`classifier`): classify each found object -- or, with no Stage A
model, tiles of the canopy -- on crops cut from the full-resolution still.
Early lesions are a few pixels at whole-frame scale; at crop scale they
are visible. The classifier's labels become Signals (evidence the crop
profile maps to conditions) or, for fruit, ripeness detections.

Both analyzers run fine with no model installed: they report `skipped`,
and the classical baseline carries the device.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Literal

import numpy as np

from ..core.profile import Profile
from ..core.registry import Analyzer, AnalyzerParams, register_analyzer
from ..core.types import ROI, AnalyzerResult, Box, Context, Detection, Frame, Image, SampleCandidate, Signal
from ..runtime import Array, Model, ModelError, nms, softmax
from .classical import ClassicalParams, resize_max_edge, vegetation_masks

log = logging.getLogger("plant-cam.ml")


class _ModelParams(AnalyzerParams):
    model_dir: str
    threads: int = 2  # of the Pi's 4 cores; the broker and databases live here too
    backend: Literal["auto", "onnxruntime", "litert"] = "auto"
    # Profile label vocabulary this model must match (profile.labels[...]); null = don't check.
    labels_key: str | None = None


class _ModelAnalyzer(Analyzer):
    cost = "heavy"
    params: _ModelParams

    def __init__(self, params: AnalyzerParams, profile: Profile, instance_id: str | None = None) -> None:
        super().__init__(params, profile, instance_id)
        self.model: Model | None = None
        self.load_error: str | None = None
        self._load()

    def _load(self) -> None:
        folder = Path(self.params.model_dir)
        if not (folder / "meta.json").exists():
            self.load_error = f"no model installed in {folder}"
            log.info("%s: %s -- skipping until one is deployed", self.id, self.load_error)
            return
        try:
            model = Model(folder, self.params.threads, self.params.backend)
            self._check(model)
            self.model = model
        except (ModelError, Exception) as exc:  # a broken model must never take the device down
            self.load_error = f"model in {folder} unusable: {exc}"
            log.exception("%s: %s", self.id, self.load_error)

    def _check(self, model: Model) -> None:
        key = self.params.labels_key
        if key is None:
            return
        expected = self.profile.labels.get(key)
        if expected is None:
            raise ModelError(f"profile has no labels.{key}")
        unknown = set(model.meta.labels) - set(expected)
        if unknown:
            raise ModelError(f"model labels {sorted(unknown)} are not in profile labels.{key}")

    def _skipped(self, why: str) -> AnalyzerResult:
        return AnalyzerResult(self.id, skipped=why)


class DetectorParams(_ModelParams):
    score_threshold: float = 0.35
    iou_threshold: float = 0.45
    max_detections: int = 100


@register_analyzer("detector")
class StageADetector(_ModelAnalyzer):
    Params = DetectorParams
    params: DetectorParams

    def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult:
        if self.model is None:
            return self._skipped(self.load_error or "no model")
        start = time.perf_counter()
        x, geom = self.model.prepare([frame.image])
        raw = self.model.run(x)
        dets = self._decode(raw, geom[0], frame.image.shape[1], frame.image.shape[0])
        result = AnalyzerResult(self.id, model_id=self.model.meta.model_id, model_version=self.model.meta.model_version)
        for label, score, box in dets:
            result.detections.append(Detection(label, score, box, roi=_roi_for(box, ctx.rois)))
        for label in self.model.meta.labels:
            result.metrics[f"det_{label}_count"] = float(sum(1 for d in result.detections if d.label == label))
        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    def _decode(
        self, raw: list[Array], geom: tuple[float, float, float], frame_w: int, frame_h: int
    ) -> list[tuple[str, float, Box]]:
        assert self.model is not None
        meta, p = self.model.meta, self.params
        in_w, in_h = meta.input.size
        labels = meta.labels
        if meta.output.format == "dets_v1":
            d = raw[0].reshape(-1, 6).astype(np.float32)
            boxes = d[:, :4] * np.array([in_w, in_h, in_w, in_h], np.float32)
            scores, classes = d[:, 4], d[:, 5].astype(int)
        else:  # yolov8: [1, 4+nc, N] or [1, N, 4+nc], xywh
            pred = raw[0][0]
            if pred.shape[0] == 4 + len(labels):
                pred = pred.T
            scores, classes = pred[:, 4:].max(axis=1), pred[:, 4:].argmax(axis=1)
            xywh = pred[:, :4].astype(np.float32)
            if xywh.max() <= 2.0:
                xywh = xywh * np.array([in_w, in_h, in_w, in_h], np.float32)
            boxes = np.concatenate([xywh[:, :2] - xywh[:, 2:] / 2, xywh[:, :2] + xywh[:, 2:] / 2], axis=1)
        keep = scores >= p.score_threshold
        boxes, scores, classes = boxes[keep], scores[keep], classes[keep]
        if not len(scores):
            return []
        offset = classes[:, None].astype(np.float32) * (max(in_w, in_h) + 1)  # class-aware NMS
        kept = nms(boxes + offset, scores, p.iou_threshold)[: p.max_detections]
        r, px, py = geom
        out: list[tuple[str, float, Box]] = []
        for i in kept:
            if not 0 <= classes[i] < len(labels):
                continue
            x1, y1, x2, y2 = boxes[i]
            # back to frame-normalised coordinates
            if meta.input.resize == "letterbox":
                fx1, fx2 = (x1 - px) / r / frame_w, (x2 - px) / r / frame_w
                fy1, fy2 = (y1 - py) / r / frame_h, (y2 - py) / r / frame_h
            else:
                fx1, fx2, fy1, fy2 = x1 / in_w, x2 / in_w, y1 / in_h, y2 / in_h
            box = (
                float(np.clip(fx1, 0, 1)),
                float(np.clip(fy1, 0, 1)),
                float(np.clip(fx2, 0, 1)),
                float(np.clip(fy2, 0, 1)),
            )
            if box[2] > box[0] and box[3] > box[1]:
                out.append((labels[classes[i]], float(scores[i]), box))
        return out


class ClassifierParams(_ModelParams):
    # Stage A labels whose crops this model classifies, e.g. ["leaf"] or ["fruit"].
    targets: list[str] = ["leaf"]
    # With no Stage A detections, classify tiles of the canopy instead.
    fallback_tiles: bool = True
    tile_grid: tuple[int, int] = (4, 3)  # columns, rows per canopy ROI
    tile_min_vegetation: float = 0.25
    crop_pad: float = 0.10  # grow each box by this fraction for context
    min_crop_px: int = 24  # on the hi-res image; smaller objects aren't classifiable
    max_crops: int = 24  # per analysis, largest first: bounds latency
    batch: int = 8
    # Labels meaning "nothing wrong"; everything else becomes a Signal.
    healthy_labels: list[str] = ["healthy_leaf", "healthy_fruit", "healthy_flower", "background"]
    # Labels that are fruit ripeness stages: become detections, not signals.
    ripeness_labels: list[str] = []
    signal_min_conf: float = 0.30
    uncertain_band: tuple[float, float] = (0.35, 0.65)
    random_sample_rate: float = 0.02


@register_analyzer("classifier")
class StageBClassifier(_ModelAnalyzer):
    Params = ClassifierParams
    params: ClassifierParams

    def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult:
        if self.model is None:
            return self._skipped(self.load_error or "no model")
        start = time.perf_counter()
        p = self.params
        regions = self._regions(frame, ctx)
        result = AnalyzerResult(
            self.id,
            model_id=self.model.meta.model_id,
            model_version=self.model.meta.model_version,
            vocabulary=list(self.model.meta.labels),
        )
        if not regions:
            result.skipped = "nothing to classify"
            result.latency_ms = (time.perf_counter() - start) * 1000
            return result

        hires = frame.hires()
        hh, hw = hires.shape[:2]
        crops: list[Image] = []
        kept: list[tuple[Box, str, str]] = []
        for box, roi, origin in regions:
            x1, y1, x2, y2 = _pad(box, p.crop_pad)
            c = hires[round(y1 * hh) : round(y2 * hh), round(x1 * hw) : round(x2 * hw)]
            if min(c.shape[:2]) >= p.min_crop_px:
                crops.append(np.ascontiguousarray(c))
                kept.append((box, roi, origin))

        labels = self.model.meta.labels
        for i in range(0, len(crops), p.batch):
            chunk = crops[i : i + p.batch]
            x, _ = self.model.prepare(chunk)
            out = self.model.run(x)[0].reshape(len(chunk), -1)
            probs = out if self.model.meta.output.format == "probs" else softmax(out.astype(np.float32))
            for crop, (box, roi, origin), pr in zip(chunk, kept[i : i + p.batch], probs, strict=True):
                k = int(pr.argmax())
                label, conf = labels[k], float(pr[k])
                scores = {labels[j]: round(float(pr[j]), 4) for j in np.argsort(pr)[::-1][:3]}
                result.detections.append(Detection(label, conf, box, roi=roi, attrs={"stage": "B", "from": origin}))
                is_finding = label not in p.ripeness_labels and label not in p.healthy_labels
                if is_finding and conf >= p.signal_min_conf:
                    result.signals.append(Signal(label, conf, roi=roi, box=box, source=self.id))
                lo, hi = p.uncertain_band
                if lo <= conf <= hi:
                    result.samples.append(SampleCandidate(crop, "uncertain", label, scores, box, roi))
                elif random.random() < p.random_sample_rate:
                    result.samples.append(SampleCandidate(crop, "random", label, scores, box, roi))
        result.metrics[f"{self.id}_crops"] = float(len(crops))
        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    def _regions(self, frame: Frame, ctx: Context) -> list[tuple[Box, str, str]]:
        p = self.params
        dets = [d for d in ctx.detections(*p.targets) if d.attrs.get("stage") != "B"]
        if dets:
            dets.sort(key=lambda d: (d.box[2] - d.box[0]) * (d.box[3] - d.box[1]), reverse=True)
            return [(d.box, d.roi, "detector") for d in dets[: p.max_crops]]
        if not p.fallback_tiles:
            return []
        return self._tiles(frame, ctx)[: p.max_crops]

    def _tiles(self, frame: Frame, ctx: Context) -> list[tuple[Box, str, str]]:
        p = self.params
        cols, rows = p.tile_grid
        cp = ClassicalParams()
        tiles: list[tuple[float, Box, str]] = []
        for roi in ctx.rois_for("canopy"):
            rgb = resize_max_edge(roi.crop(frame.image), 320)
            # near_green, not the strict canopy: a lesion can break up a leaf's
            # green outline, and those are exactly the tiles worth classifying.
            veg = vegetation_masks(rgb, cp)["near_green"]
            h, w = veg.shape
            for r in range(rows):
                for c in range(cols):
                    cell = veg[r * h // rows : (r + 1) * h // rows, c * w // cols : (c + 1) * w // cols]
                    frac = float(cell.mean()) if cell.size else 0.0
                    if frac >= p.tile_min_vegetation:
                        inner = (c / cols, r / rows, (c + 1) / cols, (r + 1) / rows)
                        tiles.append((frac, roi.to_frame_box(inner), roi.name))
        tiles.sort(key=lambda t: t[0], reverse=True)
        return [(box, roi_name, "tile") for _, box, roi_name in tiles]


def _pad(box: Box, frac: float) -> Box:
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * frac, (y2 - y1) * frac
    return (max(x1 - dx, 0.0), max(y1 - dy, 0.0), min(x2 + dx, 1.0), min(y2 + dy, 1.0))


def _roi_for(box: Box, rois: list[ROI]) -> str:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for roi in rois:
        x1, y1, x2, y2 = roi.box
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return roi.name
    return "frame"
