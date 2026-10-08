"""Converts a LabelMe-annotated dataset (one .json of polygons per image)
into a "source" folder using VERT's classes (see classes.yaml).

Built for the Strawberry Disease Detection Dataset (Afzaal et al., JBNU):

    kaggle datasets download usmanafzaal/strawberry-disease-detection-dataset -p raw/strawberry --unzip
    python import_labelme.py --root raw/strawberry --mapping strawberry --out sources/strawberry

Two things differ from VERT's own labels, and this script handles both:

  * Its outlines trace individual lesions, while VERT boxes whole leaves
    (the Pi's health score counts affected *leaves*). Lesions of the same
    class that sit close together are merged into one box, which
    approximates "one diseased leaf". --no-merge turns this off.
  * Some classes are fruit/flower diseases. They are mapped to the
    nearest VERT class below -- edit STRAWBERRY_MAP to change that; a
    class mapped to None drops every image containing it (dropping just
    the box would teach the model that the disease is background).
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from PIL import Image

from common import IMAGE_EXTS, class_names, print_mapping, safe_stem, write_source

STRAWBERRY_MAP: dict[str, str | None] = {
    "angular_leafspot": "leaf_spot_blight",
    "leaf_spot": "leaf_spot_blight",
    "powdery_mildew_leaf": "mildew_mold",
    "powdery_mildew_fruit": "mildew_mold",
    "gray_mold": "mildew_mold",               # Botrytis -- grey fuzzy mould, mostly on fruit
    "anthracnose_fruit_rot": "leaf_spot_blight",
    "blossom_blight": "leaf_spot_blight",
}

MAPPINGS = {"strawberry": STRAWBERRY_MAP}


def normalise(label: str) -> str:
    return label.strip().lower().replace(" ", "_").replace("-", "_")


def find_image(json_path: Path, image_path: str | None) -> Path | None:
    if image_path:
        candidate = json_path.parent / Path(image_path.replace("\\", "/")).name
        if candidate.exists():
            return candidate
    for ext in IMAGE_EXTS:
        for candidate in (json_path.with_suffix(ext), json_path.with_suffix(ext.upper())):
            if candidate.exists():
                return candidate
    return None


def merge_nearby(boxes: list[list[float]], gap: float) -> list[list[float]]:
    """Unions same-class boxes whose edges come within `gap` pixels of each
    other, repeating until nothing more merges."""
    boxes = [b[:] for b in boxes]
    merged = True
    while merged:
        merged = False
        out: list[list[float]] = []
        for b in boxes:
            for o in out:
                if b[0] - gap <= o[2] and o[0] - gap <= b[2] and b[1] - gap <= o[3] and o[1] - gap <= b[3]:
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", required=True, type=Path, help="folder searched recursively for LabelMe .json files")
    p.add_argument("--mapping", required=True, choices=sorted(MAPPINGS))
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--no-merge", action="store_true", help="keep one box per lesion instead of merging nearby ones")
    p.add_argument("--merge-gap", type=float, default=0.05, help="merge distance, as a fraction of the image's longer side")
    args = p.parse_args()

    mapping = MAPPINGS[args.mapping]
    valid = set(class_names())
    for target in mapping.values():
        if target is not None and target not in valid:
            raise SystemExit(f"mapping target {target!r} is not in classes.yaml")

    seen: dict = collections.defaultdict(collections.Counter)
    items, no_image, dropped_images, lesions, boxes_out = [], 0, 0, 0, 0
    json_files = sorted(args.root.rglob("*.json"))
    if not json_files:
        raise SystemExit(f"no .json files under {args.root} -- is this the LabelMe version of the dataset?")

    for json_path in json_files:
        try:
            doc = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if "shapes" not in doc:
            continue  # not a LabelMe file (e.g. a dataset meta.json)
        image = find_image(json_path, doc.get("imagePath"))
        if image is None:
            no_image += 1
            continue
        # The image file is the only trustworthy size.
        with Image.open(image) as im:
            width, height = im.size

        per_class: dict[str, list[list[float]]] = collections.defaultdict(list)
        drop = False
        for shape in doc["shapes"]:
            source = normalise(shape.get("label", ""))
            target = mapping.get(source)
            seen[target][source] += 1
            if target is None:
                drop = True
                continue
            xs = [float(pt[0]) for pt in shape.get("points", [])]
            ys = [float(pt[1]) for pt in shape.get("points", [])]
            if len(xs) < 2:
                continue
            if shape.get("shape_type") == "circle":  # LabelMe circles are (centre, point on edge)
                r = ((xs[1] - xs[0]) ** 2 + (ys[1] - ys[0]) ** 2) ** 0.5
                xs, ys = [xs[0] - r, xs[0] + r], [ys[0] - r, ys[0] + r]
            x1, x2 = max(0.0, min(xs)), min(float(width), max(xs))
            y1, y2 = max(0.0, min(ys)), min(float(height), max(ys))
            if x2 - x1 >= 2 and y2 - y1 >= 2:
                per_class[target].append([x1, y1, x2, y2])
                lesions += 1
        if drop:
            dropped_images += 1
            continue

        boxes = []
        gap = args.merge_gap * max(width, height)
        for target, raw in per_class.items():
            for x1, y1, x2, y2 in (raw if args.no_merge else merge_nearby(raw, gap)):
                boxes.append((target, (x1 + x2) / 2 / width, (y1 + y2) / 2 / height, (x2 - x1) / width, (y2 - y1) / height))
        boxes_out += len(boxes)
        # The dataset's own split folder (train/val/test) goes in the name:
        # file names repeat across splits in some releases.
        rel = json_path.relative_to(args.root)
        items.append((image, boxes, safe_stem(*rel.parent.parts, image.stem)))

    write_source(args.out, items)
    print(f"wrote {len(items)} images to {args.out}  ({no_image} json files had no image, "
          f"{dropped_images} images dropped for unmapped classes)")
    print(f"{lesions} outlines -> {boxes_out} boxes" + ("" if args.no_merge else " after merging nearby lesions") + "\n")
    print_mapping(seen)


if __name__ == "__main__":
    main()
