"""Merges every folder under sources/ into one YOLO dataset, remapping
class ids by *name* to classes.yaml's order (so a Label Studio export
whose classes.txt is ordered differently still lines up).

A source folder looks like:  images/  labels/  classes.txt
(what import_voc.py writes, and what Label Studio's "YOLO" export unzips to).

Validation and test images come only from your own greenhouse photos
(the --holdout source, default `own`) when you have them: a score on
PlantDoc photos says little about how the model does on *your* plants.
Public data then goes entirely to training.

    python build_dataset.py --sources sources --out datasets/vert-plant
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import os
import shutil
from pathlib import Path

import yaml

from common import IMAGE_EXTS, class_names


def split_for(name: str, val_pct: int, test_pct: int) -> str:
    # Hash-based, so an image keeps its split as more data is added --
    # a test image never quietly leaks into training on the next rebuild.
    bucket = int(hashlib.sha1(name.encode()).hexdigest(), 16) % 100
    if bucket < test_pct:
        return "test"
    if bucket < test_pct + val_pct:
        return "val"
    return "train"


def link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", type=Path, default=Path("sources"))
    p.add_argument("--out", type=Path, default=Path("datasets/vert-plant"))
    p.add_argument("--holdout", default="own", help="source folder whose images supply val/test")
    p.add_argument("--val-pct", type=int, default=20)
    p.add_argument("--test-pct", type=int, default=20)
    p.add_argument("--repeat", type=int, default=2,
                   help="copies of each holdout-source training image, so a few hundred of your own "
                        "photos aren't drowned out by thousands of public ones")
    args = p.parse_args()

    names = class_names()
    sources = sorted(d for d in args.sources.iterdir() if (d / "images").is_dir())
    if not sources:
        raise SystemExit(f"no source folders with images/ under {args.sources}")
    have_holdout = any(s.name == args.holdout for s in sources)
    if not have_holdout:
        print(f"note: no '{args.holdout}' source yet -- val/test will be drawn from all sources. "
              "Fine for a first model; replace with your own photos as soon as you have them.")

    if args.out.exists():
        shutil.rmtree(args.out)
    for split in ("train", "val", "test"):
        (args.out / "images" / split).mkdir(parents=True)
        (args.out / "labels" / split).mkdir(parents=True)

    stats = collections.defaultdict(collections.Counter)
    for src in sources:
        src_names = (src / "classes.txt").read_text(encoding="utf-8").split()
        remap = {i: names.index(n) for i, n in enumerate(src_names) if n in names}
        unknown = [n for n in src_names if n not in names]
        if unknown:
            print(f"{src.name}: dropping boxes of classes not in classes.yaml: {unknown}")
        # A TRAIN_ONLY marker (written by import_classified.py) keeps
        # pre-augmented near-duplicates out of val/test entirely.
        train_only = (src / "TRAIN_ONLY").exists()
        is_holdout = not train_only and ((src.name == args.holdout) or not have_holdout)

        for image in sorted((src / "images").iterdir()):
            if image.suffix.lower() not in IMAGE_EXTS:
                continue
            label = src / "labels" / f"{image.stem}.txt"
            if not label.exists():
                stats[src.name]["skipped_unlabelled"] += 1
                continue
            lines = []
            for line in label.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 5 and int(parts[0]) in remap:
                    lines.append(" ".join([str(remap[int(parts[0])]), *parts[1:]]))
                    stats["boxes"][names[remap[int(parts[0])]]] += 1

            split = split_for(f"{src.name}/{image.name}", args.val_pct, args.test_pct) if is_holdout else "train"
            copies = args.repeat if (split == "train" and src.name == args.holdout) else 1
            for r in range(copies):
                stem = f"{src.name}__{image.stem}" + (f"__r{r}" if r else "")
                link_or_copy(image, args.out / "images" / split / f"{stem}{image.suffix.lower()}")
                (args.out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            stats[src.name][split] += 1

    if not any(stats[s.name]["val"] for s in sources):
        raise SystemExit("no validation images -- every source is train-only. Add PlantDoc, the strawberry set, "
                         "or your own photos so there is something honest to score the model on.")

    data_yaml = {
        "path": str(args.out.resolve()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": dict(enumerate(names)),
    }
    (args.out / "data.yaml").write_text(yaml.safe_dump(data_yaml, sort_keys=False), encoding="utf-8")

    for src in sources:
        tag = " (train-only)" if (src / "TRAIN_ONLY").exists() else ""
        print(f"{src.name}{tag}: {dict(stats[src.name])}")
    print(f"boxes per class: {dict(stats['boxes'])}")
    thin = [n for n in names if stats["boxes"][n] < 150]
    if thin:
        print(f"warning: under ~150 boxes for {thin} -- expect weak results on these until you label more")
    print(f"\nwrote {args.out / 'data.yaml'}")


if __name__ == "__main__":
    main()
