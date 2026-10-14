"""Import public strawberry datasets into the training layouts (vertml/data.py).

Read DATASETS.md first: these licences restrict use (non-commercial /
academic or unclear). Importing does not change that.

  Afzaal et al. 2021 "Strawberry Disease Detection Dataset" (LabelMe polygons,
  7 disease classes) -> Stage B crop folders. Each lesion polygon is cropped
  with generous context (Stage B sees leaf-/fruit-scale crops, not lesions):

    python import_public.py afzaal --root raw/strawberry --out datasets/afzaal

  StrawDI_Db1 (fruit instance masks) -> Stage A boxes for `fruit` only:

    python import_public.py strawdi --root raw/StrawDI_Db1 --out datasets/strawdi

Public photos differ from your greenhouse (camera, light, distance, variety):
use them to pre-train, then fine-tune and *test* on your own labelled images.
"""

from __future__ import annotations

import argparse
import io
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from vertml.data import IMAGE_EXTS, DetectionWriter, profile_labels, split_for

# Afzaal class names (as written in the LabelMe files) -> profile labels.
AFZAAL = {
    "angular leafspot": ("stage_b_leaf", "angular_leaf_spot"),
    "leaf spot": ("stage_b_leaf", "common_leaf_spot"),
    "powdery mildew leaf": ("stage_b_leaf", "powdery_mildew_leaf"),
    "anthracnose fruit rot": ("stage_b_fruit", "anthracnose_fruit"),
    "gray mold": ("stage_b_fruit", "grey_mould_fruit"),
    "powdery mildew fruit": ("stage_b_fruit", "powdery_mildew_fruit"),
    "blossom blight": ("stage_b_flower", "blossom_blight"),
}


def afzaal(root: Path, out: Path, context: float) -> None:
    labels = profile_labels()
    seen: Counter[str] = Counter()
    for js in sorted(root.rglob("*.json")):
        doc = json.loads(js.read_text(encoding="utf-8"))
        img_path = js.with_name(doc.get("imagePath", js.stem + ".jpg"))
        if not img_path.exists():
            continue
        img = Image.open(img_path).convert("RGB")
        w, h = img.size
        split = split_for(js.stem)  # all crops of one photo share a split
        for k, shape in enumerate(doc.get("shapes", [])):
            name = str(shape.get("label", "")).strip().lower()
            if name not in AFZAAL:
                seen[f"DROPPED {name}"] += 1
                continue
            group, label = AFZAAL[name]
            assert label in labels[group]
            pts = np.array(shape["points"], dtype=float)
            x1, y1 = pts.min(0)
            x2, y2 = pts.max(0)
            side = max(x2 - x1, y2 - y1) * context
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            box = (max(cx - side / 2, 0), max(cy - side / 2, 0), min(cx + side / 2, w), min(cy + side / 2, h))
            if box[2] - box[0] < 24 or box[3] - box[1] < 24:
                continue
            dest = out / group / split / label
            dest.mkdir(parents=True, exist_ok=True)
            img.crop(tuple(int(v) for v in box)).save(dest / f"afzaal_{js.stem}_{k}.jpg", quality=92)
            seen[f"{group}/{label}"] += 1
    for k, v in sorted(seen.items()):
        print(f"{v:6d}  {k}")
    print(f"\nwrote {out}/<group>/<split>/<label>/ -- healthy classes come from your own images (see README)")


def strawdi(root: Path, out: Path) -> None:
    writer = DetectionWriter(out, profile_labels()["stage_a"])
    n = 0
    for split_dir in ("train", "val", "test"):
        img_dir, mask_dir = root / split_dir / "img", root / split_dir / "label"
        if not img_dir.is_dir():
            continue
        for img_path in sorted(p for p in img_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS):
            mask_path = mask_dir / (img_path.stem + ".png")
            if not mask_path.exists():
                continue
            mask = np.asarray(Image.open(mask_path))
            if mask.ndim == 3:
                mask = mask[..., 0]
            h, w = mask.shape
            boxes = []
            for inst in np.unique(mask):
                if inst == 0:
                    continue
                ys, xs = np.nonzero(mask == inst)
                boxes.append([xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h])
            buf = io.BytesIO()
            Image.open(img_path).convert("RGB").save(buf, "JPEG", quality=92)
            writer.add({"val": "val", "test": "test"}.get(split_dir, "train"), buf.getvalue(),
                       f"strawdi_{img_path.stem}.jpg", boxes, ["fruit"] * len(boxes))
            n += 1
    print(f"{n} images -> {out}  {writer.close()}  (fruit boxes only: leaves/flowers come from your own labels)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", choices=["afzaal", "strawdi"])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--context", type=float, default=2.5, help="afzaal: crop side = lesion size x this")
    args = ap.parse_args()
    if args.dataset == "afzaal":
        afzaal(args.root, args.out, args.context)
    else:
        strawdi(args.root, args.out)


if __name__ == "__main__":
    main()
