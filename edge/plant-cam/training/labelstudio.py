"""Label your own greenhouse photos once, train both stages from it.

1. Generate the labelling interface from the crop profile:
       python labelstudio.py config > labelling.xml
   In Label Studio: Settings -> Labeling Interface -> Code -> paste.

   Draw a box around each object and pick ONE label:
     * plain objects: leaf / flower / fruit / runner
     * or a condition/ripeness label, which implies its object, e.g. a box
       labelled `powdery_mildew_leaf` is a leaf for Stage A and a
       powdery_mildew_leaf crop for Stage B; `fruit_ripe` is a fruit.
   Label healthy leaves `healthy_leaf` (not just `leaf`) wherever you can
   judge them -- Stage B needs "nothing wrong" examples too.

2. Export as "JSON" and convert:
       python labelstudio.py convert --export export.json --images /path/to/images --out datasets/own
   -> datasets/own/objects (Stage A) and datasets/own/{stage_b_leaf,stage_b_fruit,stage_b_flower} (Stage B)
   Also writes datasets/own/eval/: images + ground-truth JSON for
       plant_cam evaluate --labels datasets/own/eval   (on the Pi: measured precision/recall)

3. Active-learning samples from the Pi (plant_cam export-al) import with
   their model prediction as a pre-annotation: python labelstudio.py tasks --al ~/al > tasks.json
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

from vertml.data import STAGE_B_PARENT, DetectionWriter, profile_conditions, profile_labels, split_for


def label_parent(labels: dict[str, list[str]]) -> dict[str, tuple[str, str | None]]:
    """label -> (stage A object, stage B group or None)."""
    out: dict[str, tuple[str, str | None]] = {a: (a, None) for a in labels["stage_a"]}
    for group, parent in STAGE_B_PARENT.items():
        for lab in labels.get(group, []):
            if lab != "background":
                out[lab] = (parent, group)
    return out


def config() -> str:
    labels = profile_labels()
    colours = {"leaf": "#4caf50", "flower": "#eeeeee", "fruit": "#e53935", "runner": "#8d6e63"}
    lines = ['<View>', '  <Image name="image" value="$image" zoom="true"/>', '  <RectangleLabels name="label" toName="image">']
    for lab in label_parent(labels):
        lines.append(f'    <Label value="{lab}" background="{colours.get(lab, "#ff9800")}"/>')
    lines += ['  </RectangleLabels>', '</View>']
    return "\n".join(lines)


def convert(export: Path, images: Path, out: Path) -> None:
    labels = profile_labels()
    parent = label_parent(labels)
    tasks = json.loads(export.read_text(encoding="utf-8"))
    signal_map = profile_conditions()
    det = DetectionWriter(out / "objects", labels["stage_a"])
    counts: Counter[str] = Counter()
    (out / "eval").mkdir(parents=True, exist_ok=True)
    for task in tasks:
        name = Path(str(task["data"]["image"]).split("?")[0]).name.split("-", 1)[-1]
        src = next(iter(images.rglob(name)), None)
        if src is None:
            print(f"skip: {name} not found under {images}", file=sys.stderr)
            continue
        img = Image.open(src).convert("RGB")
        w, h = img.size
        anns = (task.get("annotations") or [{}])[0].get("result", [])
        boxes, objs, gt_boxes, gt_counts = [], [], [], Counter()
        split = split_for(src.stem)
        for k, r in enumerate(anns):
            if r.get("type") != "rectanglelabels":
                continue
            lab = r["value"]["rectanglelabels"][0]
            if lab not in parent:
                counts[f"UNKNOWN {lab}"] += 1
                continue
            v = r["value"]
            box = [v["x"] / 100, v["y"] / 100, (v["x"] + v["width"]) / 100, (v["y"] + v["height"]) / 100]
            obj, group = parent[lab]
            boxes.append(box)
            objs.append(obj)
            gt_boxes.append({"label": lab, "box": box})
            gt_counts[f"{lab}_count" if lab.startswith("fruit_") else f"{obj}_count"] += 1
            counts[lab] += 1
            if group:
                dest = out / group / split / lab
                dest.mkdir(parents=True, exist_ok=True)
                img.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))).save(
                    dest / f"{src.stem}_{k}.jpg", quality=92)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        det.add(split, buf.getvalue(), f"{src.stem}.jpg", boxes, objs)
        if split == "test":  # the Pi-side evaluation set: never trained on
            shutil.copy2(src, out / "eval" / src.name)
            seen = {b["label"] for b in gt_boxes}
            conditions = sorted(cid for cid, sigs in signal_map.items() if seen & sigs)
            (out / "eval" / f"{src.stem}.json").write_text(json.dumps({
                "counts": dict(gt_counts), "boxes": gt_boxes, "conditions": conditions}, indent=1))
    print(f"objects: {det.close()}")
    for k, v in counts.most_common():
        print(f"{v:6d}  {k}")
    low = [k for k, v in counts.items() if v < 30 and not k.startswith("UNKNOWN")]
    if low:
        print(f"\nfewer than 30 examples (expect weak results): {low}")


def tasks(al: Path) -> str:
    out: list[dict[str, Any]] = []
    for side in sorted(al.glob("*.json")):
        meta = json.loads(side.read_text())
        out.append({
            "data": {"image": f"/data/local-files/?d={side.with_suffix('.jpg').name}", "meta": meta},
            "predictions": [{"model_version": meta.get("model_version", ""), "result": [{
                "type": "rectanglelabels", "from_name": "label", "to_name": "image",
                "value": {"x": 0, "y": 0, "width": 100, "height": 100, "rectanglelabels": [meta["predicted"]]},
            }]}],
        })
    return json.dumps(out, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["config", "convert", "tasks"])
    ap.add_argument("--export", type=Path)
    ap.add_argument("--images", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--al", type=Path)
    args = ap.parse_args()
    if args.command == "config":
        print(config())
    elif args.command == "convert":
        if not (args.export and args.images and args.out):
            ap.error("convert needs --export, --images and --out")
        convert(args.export, args.images, args.out)
    else:
        if not args.al:
            ap.error("tasks needs --al")
        print(tasks(args.al))


if __name__ == "__main__":
    main()
