"""Dataset formats shared by the importers and trainers. Deliberately plain:

Classification (Stage B), one folder per label:
    datasets/<name>/{train,val,test}/<label>/<image>.jpg

Detection (Stage A):
    datasets/<name>/{train,val,test}/images/<image>.jpg
    datasets/<name>/{train,val,test}/annotations.json
        {"labels": ["leaf", "flower", "fruit", "runner"],
         "images": [{"file": "a.jpg", "boxes": [[x1, y1, x2, y2], ...], "labels": ["fruit", ...]}]}
    boxes normalised 0-1.

Splits are assigned by hashing a *group key* (the source photo), never at
random per crop: crops of one photo must not land in both train and test,
or the test score flatters the model.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
# The crop profiles (label vocabularies). Override when the training folder
# was copied on its own, e.g. to Colab: VERT_PROFILES_DIR=/content/profiles
PROFILES = Path(os.environ.get("VERT_PROFILES_DIR", HERE.parents[1] / "profiles"))
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Which Stage A object each Stage B label set describes.
STAGE_B_PARENT = {"stage_b_leaf": "leaf", "stage_b_fruit": "fruit", "stage_b_flower": "flower"}


def profile_labels(profile: str = "strawberry") -> dict[str, list[str]]:
    raw = yaml.safe_load((PROFILES / profile / "profile.yaml").read_text(encoding="utf-8"))
    return {k: list(v) for k, v in raw["labels"].items()}


def profile_conditions(profile: str = "strawberry") -> dict[str, set[str]]:
    """condition id -> the signal (Stage B label) names that are evidence for it."""
    raw = yaml.safe_load((PROFILES / profile / "profile.yaml").read_text(encoding="utf-8"))
    return {c["id"]: set(c["signals"]) for c in raw["conditions"]}


def split_for(group_key: str, val: float = 0.1, test: float = 0.1) -> str:
    h = int(hashlib.sha1(group_key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if h < test:
        return "test"
    if h < test + val:
        return "val"
    return "train"


class DetectionWriter:
    def __init__(self, root: Path, labels: list[str]) -> None:
        self.root = root
        self.labels = labels
        self.items: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}

    def add(self, split: str, image_bytes: bytes, name: str, boxes: list[list[float]], labels: list[str]) -> None:
        unknown = set(labels) - set(self.labels)
        if unknown:
            raise ValueError(f"labels {unknown} not in {self.labels}")
        folder = self.root / split / "images"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(image_bytes)
        self.items[split].append({"file": name, "boxes": boxes, "labels": labels})

    def close(self) -> dict[str, int]:
        for split, items in self.items.items():
            if items:
                (self.root / split).mkdir(parents=True, exist_ok=True)
                (self.root / split / "annotations.json").write_text(
                    json.dumps({"labels": self.labels, "images": items}), encoding="utf-8"
                )
        return {k: len(v) for k, v in self.items.items()}


def read_detection_split(root: Path, split: str) -> tuple[list[str], list[dict[str, Any]]]:
    doc = json.loads((root / split / "annotations.json").read_text(encoding="utf-8"))
    return doc["labels"], doc["images"]


def class_counts(root: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for split in ("train", "val", "test"):
        d = root / split
        if d.is_dir():
            out[split] = {c.name: sum(1 for f in c.iterdir() if f.suffix.lower() in IMAGE_EXTS)
                          for c in sorted(d.iterdir()) if c.is_dir()}
    return out
