"""Converts a *classification* dataset -- photos sorted into one folder per
disease, no boxes -- into a "source" folder of VERT detection labels.

Built for PlantifyDr (and PlantVillage-style sets), where each photo is a
single leaf on a plain background:

    kaggle datasets download lavaman151/plantifydr-dataset -p raw/plantifydr --unzip
    python import_classified.py --root raw/plantifydr --out sources/plantifydr

What it does:
  1. A photo's class is its folder name ("Tomato___Early_blight"), mapped
     to a VERT class by keyword (RULES below). Folders matching no rule are
     skipped and listed -- read the summary and extend RULES if needed.
  2. The box is drawn automatically around the leaf, found as the
     colourful region against the plain grey/black background. When that
     fails (busy background, leaf filling the frame) the box is the whole
     photo minus a small margin -- right for a frame-filling leaf.
  3. At most --per-class photos per VERT class are kept, spread evenly
     over the source folders. These photos look nothing like a greenhouse
     camera's view, so they should *add examples* of each disease, not
     drown out the real-scene photos from PlantDoc and your own camera.
  4. The source is marked train-only: PlantifyDr ships pre-augmented
     copies of each photo, so letting it into val/test would score the
     model on near-copies of its training photos.
"""
from __future__ import annotations

import argparse
import collections
import random
from pathlib import Path

import numpy as np
from PIL import Image

from common import IMAGE_EXTS, class_names, print_mapping, safe_stem, write_source

# Checked in order against the lower-cased folder name; first match wins.
RULES: list[tuple[tuple[str, ...], str]] = [
    (("healthy",), "healthy_leaf"),
    (("mite", "insect", "pest", "aphid", "whitefly", "thrip", "miner", "worm", "beetle"), "pest"),
    (("mildew", "mold", "mould"), "mildew_mold"),
    (("yellow", "mosaic", "greening", "curl", "virus", "chlorosis", "deficien"), "chlorosis"),
    (("blight", "spot", "scab", "rust", "rot", "septoria", "canker", "measles", "esca",
      "scorch", "bacterial", "anthracnose", "blast", "melanose"), "leaf_spot_blight"),
]

# Folder names that hold splits, not classes.
_SPLIT_NAMES = {"train", "training", "valid", "validation", "val", "test", "testing", "images", "data"}


def map_folder(name: str) -> str | None:
    lower = name.lower()
    for keywords, target in RULES:
        if any(k in lower for k in keywords):
            return target
    return None


def class_folder(image: Path, root: Path) -> str:
    """The nearest parent folder that isn't a split name."""
    for parent in image.relative_to(root).parents:
        if parent.name and parent.name.lower() not in _SPLIT_NAMES:
            return parent.name
    return ""


def leaf_box(path: Path, margin: float) -> tuple[float, float, float, float]:
    """(cx, cy, w, h), normalised, around the most colourful region."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((256, 256))
        hsv = np.asarray(im.convert("HSV"), dtype=np.float32)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    # Leaves -- green, yellow or brown -- are saturated; the grey/black/white
    # backgrounds of lab-style photos (and black rotation corners) are not.
    mask = (sat > 45) & (val > 30)
    h, w = mask.shape
    rows = np.where(mask.mean(axis=1) > 0.04)[0]
    cols = np.where(mask.mean(axis=0) > 0.04)[0]
    if len(rows) and len(cols):
        y1, y2, x1, x2 = rows[0] / h, (rows[-1] + 1) / h, cols[0] / w, (cols[-1] + 1) / w
        area = (x2 - x1) * (y2 - y1)
        if 0.08 <= area <= 0.97:
            return (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
    return 0.5, 0.5, 1 - 2 * margin, 1 - 2 * margin


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--per-class", type=int, default=400, help="max photos kept per VERT class")
    p.add_argument("--margin", type=float, default=0.03, help="fallback box margin when the leaf can't be found")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    valid = set(class_names())
    by_folder: dict[str, list[Path]] = collections.defaultdict(list)
    for path in args.root.rglob("*"):
        if path.suffix.lower() in IMAGE_EXTS:
            by_folder[class_folder(path, args.root)].append(path)
    if not by_folder:
        raise SystemExit(f"no images under {args.root}")

    seen: dict = collections.defaultdict(collections.Counter)
    folders_for: dict[str, list[str]] = collections.defaultdict(list)
    for folder, paths in by_folder.items():
        target = map_folder(folder)
        seen[target][folder] += len(paths)
        if target in valid:
            folders_for[target].append(folder)

    # Even spread: each VERT class's quota is shared across its folders,
    # with any folder's unused share passed on to the others.
    rng = random.Random(args.seed)
    items, fallback = [], 0
    for target, folders in sorted(folders_for.items()):
        pools = {f: rng.sample(by_folder[f], len(by_folder[f])) for f in sorted(folders)}
        chosen: list[tuple[str, Path]] = []
        while len(chosen) < args.per_class and any(pools.values()):
            for f in list(pools):
                if pools[f] and len(chosen) < args.per_class:
                    chosen.append((f, pools[f].pop()))
        for folder, path in chosen:
            try:
                box = leaf_box(path, args.margin)
            except OSError:
                continue
            if box[2] >= 1 - 2 * args.margin - 1e-6 and box[3] >= 1 - 2 * args.margin - 1e-6:
                fallback += 1
            # Full relative path in the name: the same file names repeat
            # across split folders (train/valid) and class folders.
            items.append((path, [(target, *box)], safe_stem(*path.relative_to(args.root).parent.parts, path.stem)))

    write_source(args.out, items, train_only=True)
    kept = collections.Counter(b[0] for _, boxes, _ in items for b in boxes)
    print(f"wrote {len(items)} images to {args.out} (train-only); kept per class: {dict(kept)}")
    print(f"{fallback} photos used the whole-frame fallback box\n")
    print("available photos per folder (before the --per-class cap):")
    print_mapping(seen)


if __name__ == "__main__":
    main()
