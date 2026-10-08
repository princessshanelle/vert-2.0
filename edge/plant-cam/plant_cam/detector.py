"""Turns one camera frame into an Analysis: what the model saw, plus a
cheap greenness/brightness read that's always computed.

`Detector.analyze(rgb) -> Analysis` is the whole surface the rest of the
app depends on (like services/inference/vision.py's `analyze()`), so
swapping in a retrained model -- or a different runtime, such as a Hailo
accelerator later -- never touches anything else.

With no model installed (or a model that fails to load), GreennessOnly
takes over: the same Excess Green Index heuristic the server-side
inference service uses. The device keeps reporting something honest
instead of going dark.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import time
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .config import ModelCfg

log = logging.getLogger("plant-cam.detector")


@dataclasses.dataclass(frozen=True)
class ClassInfo:
    name: str
    kind: str  # "healthy" | "issue" | "pest"
    weight: float = 1.0  # how much one of these counts against health
    note: str = ""


@dataclasses.dataclass(frozen=True)
class Detection:
    cls: str
    score: float
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 in frame pixels


@dataclasses.dataclass(frozen=True)
class Analysis:
    detections: list[Detection]
    green_fraction: float
    brightness: float  # 0-1 mean
    model_version: str
    latency_ms: float


class Detector(Protocol):
    model_version: str
    classes: list[ClassInfo]
    model_based: bool

    def analyze(self, rgb: np.ndarray) -> Analysis: ...


# ---- Greenness (mirrors services/inference/vision.py) -------------------

_GREENNESS_MAX_EDGE = 512
EXG_VEGETATION_THRESHOLD = 0.05


def greenness(rgb: np.ndarray) -> tuple[float, float]:
    """Returns (green_fraction, brightness), both 0-1."""
    h, w = rgb.shape[:2]
    scale = _GREENNESS_MAX_EDGE / max(h, w)
    if scale < 1:
        rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    px = rgb.astype(np.float32) / 255.0
    r, g, b = px[:, :, 0], px[:, :, 1], px[:, :, 2]
    exg = 2 * g - r - b
    return float(np.mean(exg > EXG_VEGETATION_THRESHOLD)), float(np.mean(px))


class GreennessOnly:
    model_version = "exg-heuristic"
    classes: list[ClassInfo] = []
    model_based = False

    def analyze(self, rgb: np.ndarray) -> Analysis:
        start = time.perf_counter()
        green, bright = greenness(rgb)
        return Analysis([], green, bright, self.model_version, (time.perf_counter() - start) * 1000)


# ---- TFLite YOLO detector -----------------------------------------------

def _interpreter_class():
    # ai-edge-litert is the current name of the standalone TFLite runtime
    # (what tflite-runtime became); full TensorFlow is the last resort,
    # e.g. on a dev laptop where LiteRT has no wheel.
    for module in ("ai_edge_litert.interpreter", "tflite_runtime.interpreter"):
        try:
            return importlib.import_module(module).Interpreter
        except ImportError:
            continue
    try:
        import tensorflow as tf  # type: ignore

        return tf.lite.Interpreter
    except ImportError as exc:
        raise RuntimeError("no TFLite runtime installed -- pip install ai-edge-litert") from exc


def _letterbox(rgb: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize keeping aspect ratio, pad to `size` (h, w) with grey --
    exactly how Ultralytics prepares images during training."""
    h, w = rgb.shape[:2]
    r = min(size[0] / h, size[1] / w)
    nh, nw = round(h * r), round(w * r)
    resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, left = (size[0] - nh) // 2, (size[1] - nw) // 2
    canvas = np.full((size[0], size[1], 3), 114, dtype=np.uint8)
    canvas[top:top + nh, left:left + nw] = resized
    return canvas, r, (left, top)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        iw = (np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest])).clip(0)
        ih = (np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest])).clip(0)
        inter = iw * ih
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


class TFLiteDetector:
    """Runs a YOLO detection model exported by training/export.py.

    Handles both float and fully-int8-quantized inputs/outputs, and both
    output layouts Ultralytics produces ([1, 4+nc, N] and [1, N, 4+nc]).
    """

    model_based = True
    _MAX_DETECTIONS = 100

    def __init__(self, cfg: ModelCfg):
        model_dir = Path(cfg.dir)
        meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
        self.model_version: str = meta["model_version"]
        self.classes = [ClassInfo(**c) for c in meta["classes"]]
        self.score_threshold = cfg.score_threshold
        self.iou_threshold = cfg.iou_threshold

        Interpreter = _interpreter_class()
        self.interpreter = Interpreter(model_path=str(model_dir / "model.tflite"), num_threads=cfg.num_threads)
        self.interpreter.allocate_tensors()
        self.inp = self.interpreter.get_input_details()[0]
        self.out = self.interpreter.get_output_details()[0]
        _, self.in_h, self.in_w, _ = self.inp["shape"]
        log.info("model %s loaded: %d classes, input %dx%d %s",
                 self.model_version, len(self.classes), self.in_w, self.in_h, self.inp["dtype"].__name__)

    def analyze(self, rgb: np.ndarray) -> Analysis:
        start = time.perf_counter()
        green, bright = greenness(rgb)

        img, ratio, (pad_x, pad_y) = _letterbox(rgb, (self.in_h, self.in_w))
        x = img.astype(np.float32)[None] / 255.0
        if self.inp["dtype"] in (np.int8, np.uint8):
            scale, zero = self.inp["quantization"]
            info = np.iinfo(self.inp["dtype"])
            x = np.clip(np.round(x / scale + zero), info.min, info.max).astype(self.inp["dtype"])
        self.interpreter.set_tensor(self.inp["index"], x)
        self.interpreter.invoke()
        raw = self.interpreter.get_tensor(self.out["index"])
        if raw.dtype in (np.int8, np.uint8):
            scale, zero = self.out["quantization"]
            raw = (raw.astype(np.float32) - zero) * scale

        detections = self._decode(raw[0], ratio, pad_x, pad_y, rgb.shape[1], rgb.shape[0])
        return Analysis(detections, green, bright, self.model_version, (time.perf_counter() - start) * 1000)

    def _decode(self, pred: np.ndarray, ratio: float, pad_x: int, pad_y: int, frame_w: int, frame_h: int) -> list[Detection]:
        nc = len(self.classes)
        if pred.shape[0] == 4 + nc:
            pred = pred.T  # -> (N, 4 + nc)
        elif pred.shape[1] != 4 + nc:
            raise ValueError(f"model output {pred.shape} doesn't match {nc} classes in meta.json")

        scores = pred[:, 4:].max(axis=1)
        keep = scores >= self.score_threshold
        if not keep.any():
            return []
        pred, scores = pred[keep], scores[keep]
        class_ids = pred[:, 4:].argmax(axis=1)

        xywh = pred[:, :4].copy()
        # Ultralytics' TFLite export emits boxes normalised to 0-1;
        # other exporters emit input pixels. Handle both.
        if xywh.max() <= 2.0:
            xywh[:, [0, 2]] *= self.in_w
            xywh[:, [1, 3]] *= self.in_h
        boxes = np.empty_like(xywh)
        boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2

        # Class-aware NMS: shift each class into its own coordinate range
        # so boxes of different classes never suppress each other.
        offset = class_ids[:, None].astype(np.float32) * (max(self.in_w, self.in_h) + 1)
        kept = _nms(boxes + offset, scores, self.iou_threshold)[: self._MAX_DETECTIONS]

        out: list[Detection] = []
        for i in kept:
            x1, y1, x2, y2 = boxes[i]
            x1 = int(np.clip((x1 - pad_x) / ratio, 0, frame_w - 1))
            x2 = int(np.clip((x2 - pad_x) / ratio, 0, frame_w - 1))
            y1 = int(np.clip((y1 - pad_y) / ratio, 0, frame_h - 1))
            y2 = int(np.clip((y2 - pad_y) / ratio, 0, frame_h - 1))
            out.append(Detection(self.classes[int(class_ids[i])].name, float(scores[i]), (x1, y1, x2, y2)))
        return out


def load_detector(cfg: ModelCfg) -> Detector:
    model_dir = Path(cfg.dir)
    if not (model_dir / "model.tflite").exists() or not (model_dir / "meta.json").exists():
        log.warning("no model in %s -- using the greenness heuristic until one is installed", model_dir.resolve())
        return GreennessOnly()
    try:
        return TFLiteDetector(cfg)
    except Exception:  # noqa: BLE001 -- a broken model must not take the device down
        log.exception("could not load the model in %s -- falling back to the greenness heuristic", model_dir)
        return GreennessOnly()
