"""Deterministic synthetic strawberry scenes with ground truth.

These are not realistic plants -- they're controlled colour/shape cases
that pin down the classical baseline's behaviour, including the known
pitfalls: red-tinted (cold-stressed) leaves, a purple grow-light cast
with a grey reference card, flowers vs white fruit, and an empty tray.
Real greenhouse photos are what the model evaluation (M8) uses.

    python -m tests.fixtures.synth tests/fixtures/images

Writes <name>.png plus <name>.json ground truth:
    {"counts": {"fruit_ripe_count": 3, ...}, "boxes": [{"label": ..., "box": [x1,y1,x2,y2]}],
     "expect": {...scene-specific assertions...}}
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

W, H = 1296, 972


def _substrate(rng: np.random.Generator) -> np.ndarray:
    # dark coir/peat: low saturation brown-grey, textured
    base = np.empty((H, W, 3), np.float32)
    base[:] = (62, 52, 44)  # RGB
    noise = rng.normal(0, 9, (H // 8, W // 8, 1)).astype(np.float32)
    noise = cv2.resize(noise, (W, H), interpolation=cv2.INTER_CUBIC)[..., None]
    fine = rng.normal(0, 5, (H, W, 1)).astype(np.float32)
    return np.clip(base + noise + fine, 0, 255)


def _leaf(
    img: np.ndarray,
    cx: int,
    cy: int,
    size: int,
    angle: float,
    rgb: tuple[int, int, int],
    rng: np.random.Generator,
    yellow: float = 0.0,
    tint: tuple[int, int, int] | None = None,
) -> list[int]:
    """Trifoliate strawberry leaf: three leaflets with a darker midrib."""
    xs, ys = [], []
    for k, rot in enumerate((-50, 0, 50)):
        a = math.radians(angle + rot)
        lx = int(cx + math.sin(a) * size * 0.75)
        ly = int(cy - math.cos(a) * size * 0.75)
        colour = np.array(rgb, np.float32) + rng.normal(0, 6, 3)
        if yellow and rng.random() < yellow * 1.6:
            colour = np.array((205, 190, 60), np.float32) + rng.normal(0, 6, 3)
        if tint is not None and k != 1:
            colour = np.array(tint, np.float32) + rng.normal(0, 6, 3)
        axes = (int(size * 0.42), int(size * 0.62))
        cv2.ellipse(img, (lx, ly), axes, angle + rot, 0, 360, tuple(float(c) for c in colour), -1, cv2.LINE_AA)
        dark = tuple(float(c) * 0.75 for c in colour)
        cv2.line(img, (cx, cy), (lx - int(math.sin(a) * size * 0.5), ly + int(math.cos(a) * size * 0.5)), dark, 2)
        xs += [lx - axes[1], lx + axes[1]]
        ys += [ly - axes[1], ly + axes[1]]
    return [min(xs), min(ys), max(xs), max(ys)]


def _berry(img: np.ndarray, cx: int, cy: int, r: int, stage: str, rng: np.random.Generator) -> list[int]:
    colours = {
        "fruit_ripe": (200, 22, 30),
        "fruit_overripe": (95, 10, 22),
        "fruit_white": (235, 236, 220),
        "fruit_green": (150, 190, 90),
    }
    pts = np.array(
        [
            [cx - r, cy - r // 3],
            [cx + r, cy - r // 3],
            [cx + r // 2, cy + r],
            [cx, cy + int(r * 1.4)],
            [cx - r // 2, cy + r],
        ],
        np.int32,
    )
    if stage == "fruit_turning":
        cv2.fillPoly(img, [pts], (240, 200, 205), cv2.LINE_AA)  # pale pink shoulders
        lower = pts.copy()
        lower[:2, 1] = cy + r // 4
        cv2.fillPoly(img, [lower], (215, 40, 50), cv2.LINE_AA)  # red tip
    else:
        cv2.fillPoly(img, [pts], colours[stage], cv2.LINE_AA)
    for _ in range(14):  # achenes
        sx, sy = int(cx + rng.uniform(-0.6, 0.6) * r), int(cy + rng.uniform(-0.1, 1.0) * r)
        cv2.circle(img, (sx, sy), 1, (230, 200, 80), -1)
    cv2.ellipse(img, (cx, cy - r // 3), (r, r // 3), 0, 180, 360, (60, 130, 40), -1)  # calyx
    return [cx - r, cy - r // 3 - r // 3, cx + r, cy + int(r * 1.4)]


def _flower(img: np.ndarray, cx: int, cy: int, r: int) -> list[int]:
    for k in range(5):
        a = 2 * math.pi * k / 5
        cv2.circle(
            img,
            (int(cx + math.cos(a) * r * 0.6), int(cy + math.sin(a) * r * 0.6)),
            int(r * 0.5),
            (245, 245, 240),
            -1,
            cv2.LINE_AA,
        )
    cv2.circle(img, (cx, cy), int(r * 0.35), (235, 195, 30), -1, cv2.LINE_AA)
    return [cx - r - r // 10, cy - r - r // 10, cx + r + r // 10, cy + r + r // 10]


def _norm(box: list[int]) -> list[float]:
    return [
        round(max(box[0], 0) / W, 4),
        round(max(box[1], 0) / H, 4),
        round(min(box[2], W) / W, 4),
        round(min(box[3], H) / H, 4),
    ]


def _canopy(img: np.ndarray, rng: np.random.Generator, n: int, **kw: Any) -> None:
    for i in range(n):
        cx = int(160 + (i % 4) * 300 + rng.integers(-40, 40))
        cy = int(200 + (i // 4) * 330 + rng.integers(-40, 40))
        green = (int(rng.integers(45, 75)), int(rng.integers(120, 150)), int(rng.integers(35, 60)))
        _leaf(img, cx, cy, int(rng.integers(95, 125)), float(rng.uniform(-30, 30)), green, rng, **kw)


def scene(name: str) -> tuple[np.ndarray, dict[str, Any]]:
    rng = np.random.default_rng(sum(map(ord, name)))
    img = _substrate(rng)
    gt: dict[str, Any] = {"counts": {}, "boxes": [], "expect": {}}

    def add(label: str, box: list[int]) -> None:
        gt["boxes"].append({"label": label, "box": _norm(box)})

    if name == "healthy_canopy":
        _canopy(img, rng, 8)
        gt["expect"] = {"canopy_min": 25, "yellow_max": 0.03, "conditions": [], "fruit_total": 0}
    elif name in ("fruit_mix", "purple_cast"):
        _canopy(img, rng, 8)
        placements = [
            ("fruit_ripe", 150, 820),
            ("fruit_ripe", 330, 850),
            ("fruit_ripe", 1150, 820),
            ("fruit_turning", 520, 830),
            ("fruit_turning", 960, 860),
            ("fruit_white", 700, 840),
            ("fruit_overripe", 820, 880),
        ]
        for label, x, y in placements:
            add(label, _berry(img, x, y, 34, label, rng))
        for x, y in ((420, 90), (1000, 110)):
            add("flower", _flower(img, x, y, 40))
        gt["counts"] = {
            "fruit_ripe_count": 3,
            "fruit_turning_count": 2,
            "fruit_white_count": 1,
            "fruit_overripe_count": 1,
            "flower_count": 2,
        }
        gt["expect"] = {"conditions": []}
        if name == "purple_cast":
            cv2.rectangle(img, (1200, 20), (1280, 100), (180, 180, 180), -1)  # grey card
            gt["reference_roi"] = [1205 / W, 25 / H, 1275 / W, 95 / H]
    elif name == "yellowing":
        _canopy(img, rng, 8, yellow=0.4)
        gt["expect"] = {"yellow_min": 0.10, "conditions": []}
    elif name == "red_tint_leaves":
        # cold-stressed / anthocyanin-rich leaves: reddish-purple, no fruit
        _canopy(img, rng, 8, tint=(150, 45, 70))
        gt["expect"] = {"fruit_total": 0, "conditions": [], "red_leaf_min": 0.05}
    elif name == "empty_substrate":
        gt["expect"] = {"canopy_max": 2.0, "label": "steady", "fruit_total": 0}
    else:
        raise KeyError(name)

    out = np.clip(img, 0, 255).astype(np.uint8)
    if name == "purple_cast":
        # pink/purple LED grow light; x0.72 models auto-exposure keeping highlights unclipped
        cast = np.array([1.25, 0.72, 1.35], np.float32) * 0.72
        out = np.clip(out.astype(np.float32) * cast, 0, 255).astype(np.uint8)
    return out, gt


def labelled_photo(seed: int) -> tuple[np.ndarray, list[tuple[str, list[float]]]]:
    """A random small scene with Label Studio-style labels: healthy or mildewed
    leaves, ripe or green fruit, flowers. Boxes normalised 0-1."""
    rng = np.random.default_rng(1000 + seed)
    img = _substrate(rng)
    labels: list[tuple[str, list[float]]] = []
    for i in range(4):
        cx, cy = 200 + i * 300 + int(rng.integers(-30, 30)), 250 + int(rng.integers(-40, 40))
        box = _leaf(img, cx, cy, 110, float(rng.uniform(-20, 20)), (55, 135, 45), rng)
        sick = bool(rng.random() < 0.5)
        if sick:
            for _ in range(4):
                cv2.circle(
                    img, (cx + int(rng.integers(-60, 60)), cy + int(rng.integers(-60, 60))), 22, (235, 238, 232), -1
                )
        labels.append(("powdery_mildew_leaf" if sick else "healthy_leaf", _norm(box)))
    for i in range(3):
        stage = "fruit_ripe" if rng.random() < 0.5 else "fruit_green"
        labels.append((stage, _norm(_berry(img, 250 + i * 380, 780, 40, stage, rng))))
    labels.append(("flower", _norm(_flower(img, 1150, 600, 45))))
    return np.clip(img, 0, 255).astype(np.uint8), labels


def write_labelstudio(folder: Path, stems: list[str]) -> Path:
    """Images + a Label Studio JSON export (rectanglelabels, percent coordinates)."""
    folder.mkdir(parents=True, exist_ok=True)
    tasks = []
    for i, stem in enumerate(stems):
        rgb, labels = labelled_photo(i)
        cv2.imwrite(str(folder / f"{stem}.jpg"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        results = [
            {
                "type": "rectanglelabels",
                "from_name": "label",
                "to_name": "image",
                "value": {
                    "x": b[0] * 100,
                    "y": b[1] * 100,
                    "width": (b[2] - b[0]) * 100,
                    "height": (b[3] - b[1]) * 100,
                    "rectanglelabels": [lab],
                },
            }
            for lab, b in labels
        ]
        tasks.append({"data": {"image": f"/data/upload/1/abc123-{stem}.jpg"}, "annotations": [{"result": results}]})
    export = folder / "export.json"
    export.write_text(json.dumps(tasks), encoding="utf-8")
    return export


SCENES = ("healthy_canopy", "fruit_mix", "purple_cast", "yellowing", "red_tint_leaves", "empty_substrate")


def write(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in SCENES:
        rgb, gt = scene(name)
        cv2.imwrite(str(folder / f"{name}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        (folder / f"{name}.json").write_text(json.dumps(gt, indent=1), encoding="utf-8")


if __name__ == "__main__":
    write(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/images"))
