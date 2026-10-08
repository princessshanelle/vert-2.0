"""Small helpers shared by the training scripts."""
from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def load_classes(path: Path = HERE / "classes.yaml") -> list[dict]:
    classes = yaml.safe_load(path.read_text(encoding="utf-8"))["classes"]
    names = [c["name"] for c in classes]
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate class names in {path}")
    return classes


def class_names() -> list[str]:
    return [c["name"] for c in load_classes()]


Box = tuple[str, float, float, float, float]


def write_source(out: Path, items: list[tuple], train_only: bool = False) -> None:
    """Writes a "source" folder in the layout build_dataset.py merges:
    images/, labels/ (YOLO txt), classes.txt. Each item is
    (image_path, boxes) or (image_path, boxes, new_stem) -- the latter when
    file names repeat across a dataset's class folders. Boxes are
    (class_name, cx, cy, w, h), normalised.

    train_only marks a source whose images must never be used to score the
    model (e.g. pre-augmented sets, where copies of one photo would land
    in both train and test and inflate the score)."""
    import shutil

    names = class_names()
    if out.exists():
        shutil.rmtree(out)
    (out / "images").mkdir(parents=True)
    (out / "labels").mkdir(parents=True)
    (out / "classes.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    if train_only:
        (out / "TRAIN_ONLY").write_text("never used for val/test -- see common.write_source\n", encoding="utf-8")
    for item in items:
        image, boxes = item[0], item[1]
        stem = item[2] if len(item) > 2 else image.stem
        shutil.copy2(image, out / "images" / f"{stem}{image.suffix.lower()}")
        lines = [f"{names.index(c)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in boxes]
        (out / "labels" / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def safe_stem(*parts: str) -> str:
    """A file-name-safe stem built from e.g. (class folder, file stem)."""
    import re

    return "__".join(re.sub(r"[^A-Za-z0-9._-]+", "_", p).strip("_") for p in parts if p)


def print_mapping(seen: dict) -> None:
    """Prints which source class went to which VERT class -- always worth reading."""
    for target, sources in sorted(seen.items(), key=lambda kv: str(kv[0])):
        print(f"{target or 'DROPPED (unmapped)'}  ({sum(sources.values())})")
        for name, n in sources.most_common():
            print(f"    {n:6d}  {name}")
