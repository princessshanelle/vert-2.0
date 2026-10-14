"""Per-class precision/recall for classifiers and detectors (no extra deps)."""

from __future__ import annotations

from typing import Any


def per_class_report(y: list[int], p: list[int], classes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"classes": {}}
    f1s = []
    for i, c in enumerate(classes):
        tp = sum(1 for a, b in zip(y, p, strict=True) if a == i and b == i)
        fp = sum(1 for a, b in zip(y, p, strict=True) if a != i and b == i)
        fn = sum(1 for a, b in zip(y, p, strict=True) if a == i and b != i)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if tp + fn:
            f1s.append(f1)
        out["classes"][c] = {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3), "support": tp + fn}
    out["accuracy"] = round(sum(1 for a, b in zip(y, p, strict=True) if a == b) / max(len(y), 1), 3)
    out["macro_f1"] = round(sum(f1s) / max(len(f1s), 1), 3)
    return out


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def detection_report(preds: list[list[tuple[int, float, list[float]]]], gts: list[list[tuple[int, list[float]]]],
                     classes: list[str], score_threshold: float = 0.35, iou_threshold: float = 0.5) -> dict[str, Any]:
    """Per-class AP50 (all-point interpolation) plus precision/recall at the
    deployment score threshold. preds/gts: per image, (class, [score,] box)."""
    out: dict[str, Any] = {"classes": {}}
    aps = []
    for ci, name in enumerate(classes):
        scored: list[tuple[float, bool]] = []
        n_gt = 0
        for pr, gt in zip(preds, gts, strict=True):
            g = [b for c, b in gt if c == ci]
            n_gt += len(g)
            used: set[int] = set()
            for _, s, box in sorted((x for x in pr if x[0] == ci), key=lambda x: -x[1]):
                best, best_iou = -1, iou_threshold
                for j, gb in enumerate(g):
                    v = iou(box, gb)
                    if j not in used and v >= best_iou:
                        best, best_iou = j, v
                if best >= 0:
                    used.add(best)
                scored.append((s, best >= 0))
        scored.sort(key=lambda t: -t[0])
        tp = fp = 0
        precisions, recalls = [], []
        for _, hit in scored:
            tp, fp = tp + hit, fp + (not hit)
            precisions.append(tp / (tp + fp))
            recalls.append(tp / n_gt if n_gt else 0.0)
        ap, prev_r = 0.0, 0.0
        for k in range(len(precisions)):
            ap += (recalls[k] - prev_r) * max(precisions[k:])
            prev_r = recalls[k]
        at = [h for s, h in scored if s >= score_threshold]
        prec = sum(at) / len(at) if at else 0.0
        rec = sum(at) / n_gt if n_gt else 0.0
        if n_gt:
            aps.append(ap)
        out["classes"][name] = {"ap50": round(ap, 3), "precision": round(prec, 3), "recall": round(rec, 3),
                                "support": n_gt}
    out["map50"] = round(sum(aps) / max(len(aps), 1), 3)
    return out
