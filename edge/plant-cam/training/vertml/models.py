"""Model builders and ONNX export, all permissively licensed (torchvision, BSD-3).

Stage A  ssdlite320_mobilenet_v3_large   leaf / flower / fruit / runner boxes
Stage B  mobilenet_v3_small | mobilenet_v3_large | efficientnet_b0   crop classifiers

Exports follow plant-cam's model-package contract (plant_cam/runtime):
the detector is wrapped so its single output is dets_v1 -- [N, 6] rows of
x1, y1, x2, y2 (0-1 of the input), score, class -- and classifiers output
logits. Each package is model.onnx + meta.json, ready for deploy-model.sh.

Why not Ultralytics YOLO: its code and weights are AGPL-3.0, which would
put obligations on the whole subsystem. torchvision's models and these
scripts carry no such terms (check your *dataset* licences separately --
see DATASETS.md).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torchvision import models as tvm
from torchvision.models.detection import ssdlite320_mobilenet_v3_large
from torchvision.models.detection.ssdlite import SSDLiteClassificationHead

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
CLASSIFIERS = ("mobilenet_v3_small", "mobilenet_v3_large", "efficientnet_b0")


def build_classifier(arch: str, num_classes: int, pretrained: bool = True) -> nn.Module:
    if arch not in CLASSIFIERS:
        raise ValueError(f"arch must be one of {CLASSIFIERS}")
    weights = "DEFAULT" if pretrained else None
    model: Any = getattr(tvm, arch)(weights=weights)
    head = model.classifier[-1]
    model.classifier[-1] = nn.Linear(head.in_features, num_classes)
    return model  # type: ignore[no-any-return]


def build_detector(num_classes: int, pretrained: bool = True, size: int = 320) -> nn.Module:
    """num_classes excludes background (torchvision adds it as class 0)."""
    if pretrained:
        model = ssdlite320_mobilenet_v3_large(weights="DEFAULT")
        # Keep the COCO-trained backbone and box regressor, replace the class head.
        in_channels = [m[0][0].in_channels for m in model.head.classification_head.module_list]
        anchors = model.anchor_generator.num_anchors_per_location()
        norm = nn.BatchNorm2d
        model.head.classification_head = SSDLiteClassificationHead(
            in_channels, anchors, num_classes + 1, lambda c: norm(c, eps=0.001, momentum=0.03)
        )
    else:
        model = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None, num_classes=num_classes + 1)
    model.transform.min_size = (size,)
    model.transform.max_size = size
    model.transform.fixed_size = (size, size)
    return model


class DetectorExport(nn.Module):
    """[1, 3, S, S] float 0-1  ->  dets_v1 [N, 6] (boxes normalised to 0-1).

    torchvision's SSD transform normalises and resizes internally, and its
    postprocess already applies per-class NMS; plant-cam applies its own
    score threshold and NMS on top (harmless, and model-agnostic)."""

    def __init__(self, model: nn.Module, size: int) -> None:
        super().__init__()
        self.model = model
        self.size = float(size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.model(x)[0]
        boxes = out["boxes"] / self.size
        scores = out["scores"].unsqueeze(1)
        labels = (out["labels"] - 1).to(torch.float32).unsqueeze(1)  # drop background index
        return torch.cat([boxes, scores, labels], dim=1)


def export_onnx(module: nn.Module, example: torch.Tensor, path: Path, dynamic_batch: bool) -> None:
    module.eval()
    axes = {"input": {0: "N"}, "output": {0: "N"}} if dynamic_batch else {"output": {0: "N"}}
    torch.onnx.export(
        module, (example,), str(path), input_names=["input"], output_names=["output"],
        dynamic_axes=axes, opset_version=17, dynamo=False,
    )


def write_meta(folder: Path, *, model_id: str, version: str, task: str, labels: list[str], size: int,
               normalise: bool, output: str, file: str = "model.onnx", trained_on: list[str] | None = None,
               extra: dict[str, Any] | None = None) -> None:
    meta: dict[str, Any] = {
        "model_id": model_id,
        "model_version": version,
        "task": task,
        "file": file,
        "labels": labels,
        "input": {
            "size": [size, size],
            "layout": "NCHW",
            "dtype": "float32",
            "scale": 255.0,
            "mean": list(IMAGENET_MEAN) if normalise else [0.0, 0.0, 0.0],
            "std": list(IMAGENET_STD) if normalise else [1.0, 1.0, 1.0],
            "resize": "stretch",
        },
        "output": {"format": output},
        "licence": "model code: torchvision (BSD-3-Clause); weights: see trained_on and DATASETS.md",
        "trained_on": trained_on or [],
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    meta.update(extra or {})
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def export_classifier(model: nn.Module, folder: Path, *, model_id: str, version: str, labels: list[str],
                      size: int = 224, trained_on: list[str] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    export_onnx(model, torch.zeros(1, 3, size, size), folder / "model.onnx", dynamic_batch=True)
    write_meta(folder, model_id=model_id, version=version, task="classify", labels=labels, size=size,
               normalise=True, output="logits", trained_on=trained_on)
    return folder


def export_detector(model: nn.Module, folder: Path, *, model_id: str, version: str, labels: list[str],
                    size: int = 320, trained_on: list[str] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    export_onnx(DetectorExport(model, size), torch.zeros(1, 3, size, size), folder / "model.onnx",
                dynamic_batch=False)
    # The SSD transform normalises internally, so the package feeds 0-1 pixels.
    write_meta(folder, model_id=model_id, version=version, task="detect", labels=labels, size=size,
               normalise=False, output="dets_v1", trained_on=trained_on)
    return folder
