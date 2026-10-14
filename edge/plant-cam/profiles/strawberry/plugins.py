"""Strawberry-specific analyzers, loaded by profile.yaml (`plugins: plugins.py`).

`strawberry_fruit` reports phenology: flowers, fruit by ripeness stage,
runners. When the Stage A/B models are installed it counts their
detections; otherwise it falls back to a classical colour baseline:

  * ripe / turning / overripe fruit: compact red or pink blobs (solidity
    and size gates keep irregular reddened leaves out)
  * white fruit vs flowers: white blobs, told apart by the yellow centre
    a flower has
  * green fruit and runners: not separable from leaves by colour, so
    model-only (left out of the payload without a model)

It also reports `red_leaf_ratio`: red tones inside the canopy that are
not fruit. That is context for the grower (cold stress, age, variety) and
deliberately never evidence for any condition.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
import numpy.typing as npt

from plant_cam.analyzers.classical import ClassicalParams, resize_max_edge, vegetation_masks
from plant_cam.core.registry import Analyzer, AnalyzerParams, register_analyzer
from plant_cam.core.types import AnalyzerResult, Box, Context, Detection, Frame

Mask = npt.NDArray[np.bool_]

RIPENESS = ("fruit_green", "fruit_white", "fruit_turning", "fruit_ripe", "fruit_overripe")
FRUIT_DISEASE = ("grey_mould_fruit", "anthracnose_fruit")


class StrawberryFruitParams(AnalyzerParams):
    max_edge: int = 640
    # Blob size as a fraction of the ROI's area.
    min_area_frac: float = 0.0004
    max_area_frac: float = 0.08
    min_solidity: float = 0.75
    max_aspect: float = 2.5
    # Ripe strawberries are glossy, strongly saturated red; reddened leaves
    # (cold, age, anthocyanin varieties) are duller and purpler. A blob is
    # fruit only if its mean saturation clears red_blob_sat_mean AND it is
    # not mostly bordered by leaf (fruit hangs against substrate or air;
    # a red leaflet is attached to green ones).
    red_sat_min: int = 120
    red_val_min: int = 45
    red_blob_sat_mean: int = 185
    max_leaf_border: float = 0.45  # share of a blob's surrounding ring that is green leaf
    # Mean signed OpenCV hue of the blob's red pixels (red = 0, negative =
    # towards magenta). Anthocyanin-reddened leaves sit around -5..-10
    # (purple-red); ripe and overripe fruit around -3..+8 (red to orange-red).
    fruit_hue_min: float = -4.5
    pink_sat: tuple[int, int] = (40, 120)
    pink_val_min: int = 120
    overripe_val_max: int = 120  # mean V of a red blob below this = dark, overripe
    ripe_red_frac: float = 0.70
    white_sat_max: int = 50
    white_val_min: int = 170
    flower_yellow_frac: float = 0.02  # share of a white blob's box that is yellow centre


@register_analyzer("strawberry_fruit")
class StrawberryFruit(Analyzer):
    Params = StrawberryFruitParams
    params: StrawberryFruitParams

    def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult:
        start = time.perf_counter()
        result = AnalyzerResult(self.id)
        ripeness = [d for d in ctx.detections(*RIPENESS) if d.attrs.get("stage") == "B"]
        stage_a = ctx.detections("leaf", "flower", "fruit", "runner")
        stage_a = [d for d in stage_a if d.attrs.get("stage") != "B"]
        model_counts = bool(ripeness) or any(d.label == "fruit" for d in stage_a)

        classical, blobs = self._classical(frame, ctx)
        if model_counts:
            for label in RIPENESS:
                result.metrics[f"{label}_count"] = float(sum(1 for d in ripeness if d.label == label))
            rotting = [d for d in ctx.detections(*FRUIT_DISEASE) if d.attrs.get("stage") == "B"]
            result.metrics["fruit_rotting_count"] = float(len(rotting))
            result.metrics["flower_count"] = float(sum(1 for d in stage_a if d.label == "flower"))
            result.metrics["runner_count"] = float(sum(1 for d in stage_a if d.label == "runner"))
            result.metrics["fruit_counts_model"] = 1.0
        else:
            result.metrics.update(classical)
            result.detections.extend(blobs)
            result.metrics["fruit_counts_model"] = 0.0
        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    # ---- classical colour baseline -----------------------------------------

    def _classical(self, frame: Frame, ctx: Context) -> tuple[dict[str, float], list[Detection]]:
        p = self.params
        counts = {
            "fruit_white_count": 0.0,
            "fruit_turning_count": 0.0,
            "fruit_ripe_count": 0.0,
            "fruit_overripe_count": 0.0,
            "flower_count": 0.0,
        }
        blobs: list[Detection] = []
        red_leaf_px = canopy_px = 0
        for roi in ctx.rois_for("fruit"):
            rgb = resize_max_edge(roi.crop(frame.image), p.max_edge)
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
            h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            area = rgb.shape[0] * rgb.shape[1]
            reddish = (h <= 10) | (h >= 165)
            red = reddish & (s >= p.red_sat_min) & (v >= p.red_val_min)
            pink = reddish & (s >= p.pink_sat[0]) & (s < p.pink_sat[1]) & (v >= p.pink_val_min)
            fruit_px = np.zeros_like(red)
            veg = vegetation_masks(rgb, ClassicalParams())
            ring_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            for box, comp in self._components(red | pink, area):
                core = comp & red
                if not core.any() or float(s[core].mean()) < p.red_blob_sat_mean:
                    continue  # dull red: leaf pigment, not fruit
                hue = h[core].astype(np.int16)
                if float(np.where(hue > 90, hue - 180, hue).mean()) < p.fruit_hue_min:
                    continue  # purple-red: anthocyanin in leaves, not fruit
                ring = cv2.dilate(comp.astype(np.uint8), ring_k).astype(bool) & ~comp
                if ring.any() and float(veg["green"][ring].mean()) > p.max_leaf_border:
                    continue  # red patch inside foliage: a reddened leaflet
                comp_red = float(red[comp].sum()) / max(int(comp.sum()), 1)
                mean_v = float(v[comp & red].mean()) if (comp & red).any() else 255.0
                if comp_red >= p.ripe_red_frac:
                    label = "fruit_overripe" if mean_v < p.overripe_val_max else "fruit_ripe"
                else:
                    label = "fruit_turning"
                counts[f"{label}_count"] += 1
                fruit_px |= comp
                blobs.append(Detection(label, 0.5, roi.to_frame_box(box), roi.name, {"stage": "classical"}))

            white = (s <= p.white_sat_max) & (v >= p.white_val_min)
            yellow = (h >= 18) & (h <= 35) & (s >= 100) & (v >= 120)
            for box, _comp in self._components(white, area):
                x1, y1, x2, y2 = (
                    round(box[0] * rgb.shape[1]),
                    round(box[1] * rgb.shape[0]),
                    round(box[2] * rgb.shape[1]),
                    round(box[3] * rgb.shape[0]),
                )
                yellow_frac = float(yellow[y1:y2, x1:x2].mean()) if y2 > y1 and x2 > x1 else 0.0
                label = "flower" if yellow_frac >= p.flower_yellow_frac else "fruit_white"
                counts["flower_count" if label == "flower" else "fruit_white_count"] += 1
                blobs.append(Detection(label, 0.4, roi.to_frame_box(box), roi.name, {"stage": "classical"}))

            # Red tones on or next to leaves that aren't fruit: reported for
            # context only (cold stress, age, variety), never as a condition.
            red_leaf = veg["near_green"] & ~veg["green"] & reddish & (s >= 60) & (v >= 40) & ~fruit_px
            red_leaf = cv2.morphologyEx(red_leaf.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(
                bool
            )
            canopy_px += int(veg["green"].sum() + veg["yellow"].sum() + red_leaf.sum())
            red_leaf_px += int(red_leaf.sum())
        counts["red_leaf_ratio"] = round(red_leaf_px / canopy_px, 4) if canopy_px else 0.0
        return counts, blobs

    def _components(self, mask: Mask, roi_area: int) -> list[tuple[Box, Mask]]:
        p = self.params
        m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        h, w = mask.shape
        out: list[tuple[Box, Mask]] = []
        for i in range(1, n):
            x, y, bw, bh, a = (int(v) for v in stats[i])
            if not (p.min_area_frac * roi_area <= a <= p.max_area_frac * roi_area):
                continue
            if max(bw, bh) / max(min(bw, bh), 1) > p.max_aspect:
                continue
            comp = labels == i
            contours, _ = cv2.findContours(comp.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            hull_area = cv2.contourArea(cv2.convexHull(max(contours, key=cv2.contourArea)))
            if hull_area <= 0 or a / hull_area < p.min_solidity:
                continue
            out.append(((x / w, y / h, (x + bw) / w, (y + bh) / h), comp))
        return out
