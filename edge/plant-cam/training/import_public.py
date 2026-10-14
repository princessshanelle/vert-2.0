"""Import public strawberry datasets into the training layouts (vertml/data.py).

Read DATASETS.md first: these licences restrict use (non-commercial /
academic or unclear). Importing does not change that.

  Afzaal et al. 2021 "Strawberry Disease Detection Dataset" (LabelMe polygons,
  7 disease classes) -> Stage B crop folders. Each lesion polygon is cropped
  with generous context (Stage B sees leaf-/fruit-scale crops, not lesions):

    python import_public.py afzaal --root raw/strawberry --out datasets/afzaal

  StrawDI_Db1 (fruit instance masks) -> Stage A boxes for `fruit` only:

    python import_public.py strawdi --root raw/StrawDI_Db1 --out datasets/strawdi

  PlantifyDr (Kaggle lavaman151/plantifydr-dataset; PlantVillage-derived leaf
  photos, pre-augmented) -> Stage B leaf folders, strawberry classes only
  (healthy_leaf, leaf_scorch). Its own train/valid split is ignored: the
  augmented copies of one leaf are grouped by a flip/rotation-invariant
  perceptual hash and kept in one split, or the test score would flatter:

    python import_public.py plantifydr --root <download>/PlantDiseasesDataset --out datasets/plantifydr

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


# PlantifyDr strawberry class folders (matched case-insensitively on these words) -> stage_b_leaf labels.
PLANTIFYDR = {"healthy": "healthy_leaf", "scorch": "leaf_scorch"}


def _dhashes(path: Path) -> list[int]:
    """64-bit difference hash of the image in all 8 flips/90-degree rotations."""
    g = np.asarray(Image.open(path).convert("L").resize((9, 9), Image.Resampling.BILINEAR), dtype=np.int16)
    out = []
    for k in range(4):
        r = np.rot90(g, k)
        for v in (r, r[:, ::-1]):
            bits = (v[:8, 1:] > v[:8, :-1]).flatten()
            out.append(int("".join("1" if b else "0" for b in bits), 2))
    return out


def near_duplicate_groups(paths: list[Path], max_dist: int = 6) -> dict[Path, str]:
    """path -> group key; images within `max_dist` bits (in any orientation) share one."""
    parent = list(range(len(paths)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    hashes = [_dhashes(p) for p in paths]
    bands: dict[tuple[int, int], list[int]] = {}
    for i, hs in enumerate(hashes):  # index orientation 0 in 4 16-bit bands (LSH)
        for b in range(4):
            bands.setdefault((b, (hs[0] >> (16 * b)) & 0xFFFF), []).append(i)
    for i, hs in enumerate(hashes):
        for h in hs:
            for b in range(4):
                for j in bands.get((b, (h >> (16 * b)) & 0xFFFF), []):
                    if j != i and bin(h ^ hashes[j][0]).count("1") <= max_dist:
                        parent[find(i)] = find(j)
    members: dict[int, list[Path]] = {}
    for i, p in enumerate(paths):
        members.setdefault(find(i), []).append(p)
    return {p: min(m).name for m in members.values() for p in m}


def plantifydr(root: Path, out: Path, crop: str) -> None:
    allowed = profile_labels()["stage_b_leaf"]
    crop_dirs = [d for d in root.rglob("*") if d.is_dir() and d.name.lower() == crop.lower()]
    if not crop_dirs:
        raise SystemExit(f"no folder named {crop!r} under {root}")
    files: dict[str, list[Path]] = {}
    for class_dir in sorted({p.parent for d in crop_dirs for p in d.rglob("*") if p.suffix.lower() in IMAGE_EXTS}):
        name = class_dir.name.lower()
        hits = [label for word, label in PLANTIFYDR.items() if word in name]
        if len(hits) != 1:
            raise SystemExit(f"don't know which label {class_dir} is: add it to PLANTIFYDR in import_public.py")
        assert hits[0] in allowed
        files.setdefault(hits[0], []).extend(p for p in class_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    seen: Counter[str] = Counter()
    for label, paths in sorted(files.items()):
        groups = near_duplicate_groups(sorted(paths))
        for src in paths:
            split = split_for(f"plantifydr/{label}/{groups[src]}")
            dest = out / "stage_b_leaf" / split / label
            dest.mkdir(parents=True, exist_ok=True)
            Image.open(src).convert("RGB").save(dest / f"plantifydr_{src.stem}.jpg", quality=92)
            seen[f"{split:5s} {label}"] += 1
        print(f"{label}: {len(paths)} images in {len(set(groups.values()))} near-duplicate groups")
    for k, v in sorted(seen.items()):
        print(f"{v:6d}  {k}")
    print(f"\nwrote {out}/stage_b_leaf/<split>/<label>/")


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
    ap.add_argument("dataset", choices=["afzaal", "strawdi", "plantifydr"])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--crop", default="Strawberry", help="plantifydr: which plant's folder to import")
    ap.add_argument("--context", type=float, default=2.5, help="afzaal: crop side = lesion size x this")
    args = ap.parse_args()
    if args.dataset == "afzaal":
        afzaal(args.root, args.out, args.context)
    elif args.dataset == "plantifydr":
        plantifydr(args.root, args.out, args.crop)
    else:
        strawdi(args.root, args.out)


if __name__ == "__main__":
    main()
