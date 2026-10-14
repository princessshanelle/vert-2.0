"""`plant_cam evaluate`: measured precision/recall on labelled images.

Ground truth is one JSON per image (same stem), e.g. `IMG_0042.json`:

    {
      "counts":     {"fruit_ripe_count": 3, "flower_count": 2, ...},
      "boxes":      [{"label": "fruit_ripe", "box": [x1, y1, x2, y2]}, ...],   # 0-1 of the image
      "conditions": ["powdery_mildew"]                                         # present in this image
    }

Any key may be omitted; only what is labelled is scored. Reported:

  * per-class box precision/recall/F1 (greedy IoU >= 0.5 matching, same label)
  * per-count mean absolute error and exact-match rate
  * per-condition image-level precision/recall (found = suspected or detected)

Each image is scored on its own (fresh Stage C state), so persistence
doesn't hide or invent findings. tools/labelstudio_to_gt.py converts a
Label Studio export to this format (see training/README.md).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .capture.files import IMAGE_EXTS, read_rgb
from .config import Config
from .core.pipeline import Pipeline
from .core.profile import Profile
from .core.temporal import ConditionTracker
from .core.types import Box, Frame, FrameMeta, HistoryStore, utc_now

IOU = 0.5


def iou(a: Box | list[float], b: Box | list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match(pred: list[tuple[str, float, Box]], gt: list[tuple[str, list[float]]]) -> dict[str, list[int]]:
    """{label: [tp, fp, fn]} with greedy highest-score-first matching."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    used: set[int] = set()
    for label, _score, box in sorted(pred, key=lambda p: -p[1]):
        best, best_iou = None, IOU
        for i, (gl, gb) in enumerate(gt):
            if i in used or gl != label:
                continue
            v = iou(box, gb)
            if v >= best_iou:
                best, best_iou = i, v
        if best is None:
            out[label][1] += 1
        else:
            used.add(best)
            out[label][0] += 1
    for i, (gl, _) in enumerate(gt):
        if i not in used:
            out[gl][2] += 1
    return out


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def evaluate_folder(cfg: Config, profile: Profile, folder: Path, out_json: str | None) -> int:
    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS and p.with_suffix(".json").exists())
    if not images:
        print(f"no labelled images in {folder} (each image needs a same-named .json)")
        return 1
    pipe = Pipeline(cfg, profile, HistoryStore())
    boxes: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    counts: dict[str, list[tuple[float, float]]] = defaultdict(list)
    conds: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    per_image: list[dict[str, Any]] = []

    for i, path in enumerate(images, 1):
        gt = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        pipe.tracker = ConditionTracker(profile)
        pipe.aggregator.tracker = pipe.tracker
        report, _ = pipe.run(Frame(read_rgb(path), FrameMeta(i, utc_now(), path.name)))

        if "boxes" in gt:
            labels = {b["label"] for b in gt["boxes"]}
            pred = [(d.label, d.score, d.box) for d in report.detections if d.label in labels]
            truth = [(b["label"], b["box"]) for b in gt["boxes"]]
            for label, (tp, fp, fn) in match(pred, truth).items():
                acc = boxes[label]
                acc[0], acc[1], acc[2] = acc[0] + tp, acc[1] + fp, acc[2] + fn
        for key, want in gt.get("counts", {}).items():
            counts[key].append((float(want), float(report.metrics.get(key, 0.0))))
        if "conditions" in gt:
            found = {c.id for c in report.conditions}
            truth_c = set(gt["conditions"])
            for cid in found | truth_c:
                acc = conds[cid]
                if cid in found and cid in truth_c:
                    acc[0] += 1
                elif cid in found:
                    acc[1] += 1
                else:
                    acc[2] += 1
        per_image.append(
            {
                "image": path.name,
                "label": report.label,
                "health_score": report.health_score,
                "found": sorted({c.id for c in report.conditions}),
            }
        )

    result: dict[str, Any] = {
        "images": len(images),
        "profile": f"{profile.id}@{profile.version}",
        "models": pipe.models(),
        "boxes": {k: _prf(*v) for k, v in sorted(boxes.items())},
        "counts": {
            k: {
                "mae": round(sum(abs(a - b) for a, b in v) / len(v), 3),
                "exact": round(sum(1 for a, b in v if a == b) / len(v), 3),
                "n": len(v),
            }
            for k, v in sorted(counts.items())
        },
        "conditions": {k: _prf(*v) for k, v in sorted(conds.items())},
        "per_image": per_image,
    }
    print(f"\nevaluated {len(images)} labelled images; models: {result['models'] or 'none (classical baseline)'}")
    if result["boxes"]:
        print(f"\n{'class':<22}{'precision':>10}{'recall':>8}{'f1':>7}{'tp':>5}{'fp':>5}{'fn':>5}")
        for k, v in result["boxes"].items():
            print(f"{k:<22}{v['precision']:>10}{v['recall']:>8}{v['f1']:>7}{v['tp']:>5}{v['fp']:>5}{v['fn']:>5}")
    if result["counts"]:
        print(f"\n{'count':<22}{'MAE':>8}{'exact':>8}{'n':>5}")
        for k, v in result["counts"].items():
            print(f"{k:<22}{v['mae']:>8}{v['exact']:>8}{v['n']:>5}")
    if result["conditions"]:
        print(f"\n{'condition':<22}{'precision':>10}{'recall':>8}{'tp':>5}{'fp':>5}{'fn':>5}")
        for k, v in result["conditions"].items():
            print(f"{k:<22}{v['precision']:>10}{v['recall']:>8}{v['tp']:>5}{v['fp']:>5}{v['fn']:>5}")
    if out_json:
        Path(out_json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0
