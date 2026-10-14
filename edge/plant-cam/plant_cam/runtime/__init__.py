"""Model packages and inference backends.

A model package is a folder (what training/export.py produces and
deploy/deploy-model.sh installs):

    model.onnx | model.tflite
    meta.json

meta.json is the contract, so the Pi never needs to know which network
architecture produced a model:

    {
      "model_id": "strawberry-leaf-cls", "model_version": "v1-20261020",
      "task": "classify" | "detect",
      "file": "model.onnx",
      "labels": ["healthy_leaf", "powdery_mildew_leaf", ...],
      "input": {"size": [224, 224], "layout": "NCHW", "dtype": "float32",
                "scale": 255.0, "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
                "resize": "stretch" | "letterbox"},
      "output": {"format": "logits" | "probs" | "dets_v1" | "yolov8"},
      "licence": "Apache-2.0", "trained_on": [...]
    }

Detection format dets_v1: one output of shape [N, 6] (or [1, N, 6]) with
rows x1, y1, x2, y2, score, class -- boxes normalised 0-1 of the input
image -- before NMS. The export wrapper does the architecture-specific
decoding; the Pi does score filtering and class-aware NMS.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("plant-cam.runtime")

Array = npt.NDArray[Any]


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    size: tuple[int, int]  # w, h
    layout: Literal["NCHW", "NHWC"] = "NCHW"
    dtype: Literal["float32", "uint8", "int8"] = "float32"
    scale: float = 255.0
    mean: tuple[float, float, float] = (0.0, 0.0, 0.0)
    std: tuple[float, float, float] = (1.0, 1.0, 1.0)
    resize: Literal["stretch", "letterbox"] = "stretch"


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    format: Literal["logits", "probs", "dets_v1", "yolov8"]


class ModelMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model_id: str
    model_version: str
    task: Literal["classify", "detect"]
    file: str = "model.onnx"
    labels: list[str]
    input: InputSpec
    output: OutputSpec
    licence: str = "unknown"
    trained_on: list[str] = Field(default_factory=list)


class ModelError(Exception):
    pass


class Backend(Protocol):
    name: str

    def run(self, x: Array) -> list[Array]: ...


class OnnxBackend:
    name = "onnxruntime"

    def __init__(self, path: Path, threads: int) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        # Pinned deliberately: the Pi also runs the broker and databases.
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")  # don't burn idle cores
        self.session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def run(self, x: Array) -> list[Array]:
        return list(self.session.run(None, {self.input_name: x}))


class LiteRTBackend:
    name = "litert"

    def __init__(self, path: Path, threads: int) -> None:
        interpreter_cls: Any = None
        for module in ("ai_edge_litert.interpreter", "tflite_runtime.interpreter"):
            try:
                interpreter_cls = __import__(module, fromlist=["Interpreter"]).Interpreter
                break
            except ImportError:
                continue
        if interpreter_cls is None:
            raise ModelError("no LiteRT runtime installed (pip install ai-edge-litert) for a .tflite model")
        # XNNPACK is LiteRT's default CPU delegate.
        self.interp = interpreter_cls(model_path=str(path), num_threads=threads)
        self.interp.allocate_tensors()
        self.inp = self.interp.get_input_details()[0]
        self.outs = self.interp.get_output_details()

    def run(self, x: Array) -> list[Array]:
        if self.inp["dtype"] in (np.int8, np.uint8) and x.dtype == np.float32:
            scale, zero = self.inp["quantization"]
            info = np.iinfo(self.inp["dtype"])
            x = np.clip(np.round(x / scale + zero), info.min, info.max).astype(self.inp["dtype"])
        self.interp.set_tensor(self.inp["index"], x)
        self.interp.invoke()
        out = []
        for o in self.outs:
            y = self.interp.get_tensor(o["index"])
            if y.dtype in (np.int8, np.uint8) and o["quantization"][0]:
                scale, zero = o["quantization"]
                y = (y.astype(np.float32) - zero) * scale
            out.append(y)
        return out


class Model:
    """A loaded package: preprocessing per meta.json plus a backend."""

    def __init__(self, folder: Path, threads: int, backend: str = "auto") -> None:
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            raise ModelError(f"no meta.json in {folder}")
        try:
            self.meta = ModelMeta.model_validate(json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise ModelError(f"invalid {meta_path}: {exc}") from exc
        path = folder / self.meta.file
        if not path.exists():
            raise ModelError(f"{meta_path} names {self.meta.file}, which is missing")
        threads = max(1, min(threads, os.cpu_count() or 1))
        if path.suffix == ".tflite" or backend == "litert":
            self.backend: Backend = LiteRTBackend(path, threads)
        else:
            self.backend = OnnxBackend(path, threads)
        self.folder = folder
        log.info(
            "model loaded",
            extra={
                "fields": {
                    "model_id": self.meta.model_id,
                    "version": self.meta.model_version,
                    "backend": self.backend.name,
                    "threads": threads,
                    "labels": len(self.meta.labels),
                }
            },
        )

    def prepare(self, images: list[npt.NDArray[np.uint8]]) -> tuple[Array, list[tuple[float, float, float]]]:
        """RGB uint8 images -> one batch tensor, plus (scale, pad_x, pad_y)
        per image for undoing a letterbox."""
        import cv2

        spec = self.meta.input
        w, h = spec.size
        batch, geom = [], []
        for img in images:
            ih, iw = img.shape[:2]
            if spec.resize == "letterbox":
                r = min(w / iw, h / ih)
                nw, nh = round(iw * r), round(ih * r)
                canvas = np.full((h, w, 3), 114, np.uint8)
                px, py = (w - nw) // 2, (h - nh) // 2
                canvas[py : py + nh, px : px + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
                batch.append(canvas)
                geom.append((r, float(px), float(py)))
            else:
                interp = cv2.INTER_AREA if iw > w else cv2.INTER_LINEAR
                batch.append(cv2.resize(img, (w, h), interpolation=interp))
                geom.append((1.0, 0.0, 0.0))
        x: Array = np.stack(batch)
        if spec.dtype == "float32":
            x = x.astype(np.float32) / spec.scale
            x = (x - np.array(spec.mean, np.float32)) / np.array(spec.std, np.float32)
        if spec.layout == "NCHW":
            x = np.ascontiguousarray(x.transpose(0, 3, 1, 2))
        return x, geom

    def run(self, x: Array) -> list[Array]:
        return self.backend.run(x)


def softmax(logits: Array) -> Array:
    z = logits - logits.max(axis=-1, keepdims=True)
    e = np.exp(z)
    out: Array = e / e.sum(axis=-1, keepdims=True)
    return out


def nms(boxes: Array, scores: Array, iou_threshold: float) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        iw = (np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest])).clip(0)
        ih = (np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest])).clip(0)
        inter = iw * ih
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return keep
