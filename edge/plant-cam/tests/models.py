"""Tiny hand-built ONNX models for tests: real model packages (model.onnx +
meta.json) exercised through the real runtime, with behaviour we can
predict exactly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def _save(folder: Path, graph: onnx.GraphProto, meta: dict[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, str(folder / "model.onnx"))
    (folder / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return folder


def colour_classifier(
    folder: Path,
    labels: list[str],
    weights: np.ndarray,
    bias: np.ndarray,
    model_id: str = "test-cls",
    size: int = 32,
    pool: str = "avg",
) -> Path:
    """logits = mean (or max) of R,G,B over the crop, 0-1, @ weights + bias."""
    assert weights.shape == (3, len(labels))
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 3, size, size])
    y = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["N", len(labels)])
    nodes = [
        helper.make_node("GlobalAveragePool" if pool == "avg" else "GlobalMaxPool", ["input"], ["pooled"]),
        helper.make_node("Flatten", ["pooled"], ["flat"], axis=1),
        helper.make_node("Gemm", ["flat", "W", "B"], ["logits"]),
    ]
    graph = helper.make_graph(
        nodes,
        "colour_cls",
        [x],
        [y],
        initializer=[
            numpy_helper.from_array(weights.astype(np.float32), "W"),
            numpy_helper.from_array(bias.astype(np.float32), "B"),
        ],
    )
    meta = {
        "model_id": model_id,
        "model_version": "t1",
        "task": "classify",
        "file": "model.onnx",
        "labels": labels,
        "licence": "test",
        "input": {"size": [size, size], "layout": "NCHW", "dtype": "float32", "scale": 255.0},
        "output": {"format": "logits"},
    }
    return _save(folder, graph, meta)


def fixed_detector(
    folder: Path,
    labels: list[str],
    dets: list[tuple[float, float, float, float, float, int]],
    model_id: str = "test-det",
    size: int = 64,
) -> Path:
    """Always returns the given dets_v1 rows (x1, y1, x2, y2, score, class), whatever the image."""
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, size, size])
    y = helper.make_tensor_value_info("dets", TensorProto.FLOAT, [len(dets), 6])
    zero = numpy_helper.from_array(np.zeros((1,), np.float32), "zero")
    nodes = [
        helper.make_node("ReduceMean", ["input"], ["m"], keepdims=0),
        helper.make_node("Mul", ["m", "zero"], ["z"]),
        helper.make_node("Add", ["D", "z"], ["dets"]),
    ]
    graph = helper.make_graph(
        nodes,
        "fixed_det",
        [x],
        [y],
        initializer=[
            numpy_helper.from_array(np.array(dets, np.float32), "D"),
            zero,
        ],
    )
    meta = {
        "model_id": model_id,
        "model_version": "t1",
        "task": "detect",
        "file": "model.onnx",
        "labels": labels,
        "licence": "test",
        "input": {"size": [size, size], "layout": "NCHW", "dtype": "float32", "scale": 255.0, "resize": "stretch"},
        "output": {"format": "dets_v1"},
    }
    return _save(folder, graph, meta)


def mildew_leaf_classifier(folder: Path) -> Path:
    """Crops containing any near-white patch -> powdery_mildew_leaf, else healthy_leaf.
    (Max-pooled: brightest R, G, B in the crop; leaves and substrate never get near white.)"""
    labels = ["healthy_leaf", "powdery_mildew_leaf"]
    w = np.array([[0.0, 4.0], [0.0, 4.0], [0.0, 4.0]])  # rows R, G, B
    b = np.array([0.0, -4.0 * 2.1])  # white (sum ~2.8) -> +2.8; leaf/soil (sum < 1.5) -> < -2.4
    return colour_classifier(folder, labels, w, b, model_id="test-leaf", pool="max")
