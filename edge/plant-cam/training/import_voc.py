"""Converts a Pascal-VOC-annotated public dataset into a "source" folder
using VERT's classes (see classes.yaml).

Two mappings are built in:

  plantdoc  PlantDoc's 30 crop-specific classes ("Tomato Early blight leaf",
            "Apple leaf", ...) folded into VERT's 5 by keyword. The summary
            printed at the end shows exactly what went where -- check it.
  pest      every object becomes `pest` (for insect datasets such as IP102's
            detection subset, whose object names are species ids)

    python import_voc.py --images PlantDoc/TRAIN --annotations PlantDoc/TRAIN \
        --mapping plantdoc --out sources/plantdoc
"""
from __future__ import annotations

import argparse
import collections
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from common import IMAGE_EXTS, class_names, write_source

# Checked in order; first match wins. Anything matching none is a plain
# leaf of some crop, i.e. healthy.
_PLANTDOC_RULES = [
    (("mite",), "pest"),
    (("mildew", "mold", "mould"), "mildew_mold"),
    (("yellow", "mosaic"), "chlorosis"),
    (("blight", "spot", "scab", "rust", "rot", "septoria"), "leaf_spot_blight"),
]


def map_plantdoc(name: str) -> str:
    lower = name.lower()
    for keywords, target in _PLANTDOC_RULES:
        if any(k in lower for k in keywords):
            return target
    return "healthy_leaf"


MAPPINGS = {"plantdoc": map_plantdoc, "pest": lambda _name: "pest"}


def find_image(images: Path, stem: str, filename: str | None) -> Path | None:
    if filename and (images / filename).exists():
        return images / filename
    for ext in IMAGE_EXTS:
        for candidate in (images / f"{stem}{ext}", images / f"{stem}{ext.upper()}"):
            if candidate.exists():
                return candidate
    return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--images", required=True, type=Path)
    p.add_argument("--annotations", required=True, type=Path, help="folder of VOC .xml files")
    p.add_argument("--mapping", required=True, choices=sorted(MAPPINGS))
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()

    to_vert = MAPPINGS[args.mapping]
    valid = set(class_names())
    mapping_seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    items, skipped = [], 0

    for xml_path in sorted(args.annotations.glob("*.xml")):
        root = ET.parse(xml_path).getroot()
        image = find_image(args.images, xml_path.stem, root.findtext("filename"))
        if image is None:
            skipped += 1
            continue
        # Several public datasets record width/height as 0 or wrong in the
        # XML -- the image file itself is the only trustworthy size.
        with Image.open(image) as im:
            width, height = im.size

        boxes = []
        for obj in root.iter("object"):
            source_name = (obj.findtext("name") or "").strip()
            target = to_vert(source_name)
            mapping_seen[target][source_name] += 1
            if target not in valid:
                continue
            bb = obj.find("bndbox")
            x1, y1, x2, y2 = (float(bb.findtext(k)) for k in ("xmin", "ymin", "xmax", "ymax"))
            x1, x2 = sorted((max(0.0, x1), min(float(width), x2)))
            y1, y2 = sorted((max(0.0, y1), min(float(height), y2)))
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            boxes.append((target, (x1 + x2) / 2 / width, (y1 + y2) / 2 / height, (x2 - x1) / width, (y2 - y1) / height))
        items.append((image, boxes))

    write_source(args.out, items)
    print(f"wrote {len(items)} images to {args.out} ({skipped} annotations had no matching image)\n")
    for target, sources in sorted(mapping_seen.items()):
        print(f"{target}  ({sum(sources.values())} boxes)")
        for name, n in sources.most_common():
            print(f"    {n:6d}  {name}")


if __name__ == "__main__":
    main()
