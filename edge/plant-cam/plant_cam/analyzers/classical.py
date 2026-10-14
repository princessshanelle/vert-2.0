"""Classical CV baseline -- no model needed, so the device is useful on day one.

Per canopy ROI (or the whole frame):
  * vegetation mask: green hue AND enough saturation/brightness AND positive
    Excess Green on chromatic coordinates. The saturation gate is what stops
    a grey wall under a green cast from reading as canopy (a real failure
    seen on this camera's first frames).
  * canopy coverage %, and the share of the canopy that is yellow or brown
    (looked for only inside the closed canopy outline, not in the soil).
  * vegetation indices over leaf pixels: ExG, VARI, GLI.
  * growth: change in coverage against the same ROI ~24 h earlier.

These are colour statistics, not diagnoses: this analyzer never emits
condition signals. Red tones in particular are not measured here -- in
strawberries they mean fruit, cold stress or variety, not disease.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
import numpy.typing as npt

from ..core.registry import Analyzer, AnalyzerParams, register_analyzer
from ..core.types import ROI, AnalyzerResult, Context, Frame, Image

Mask = npt.NDArray[np.bool_]


class ClassicalParams(AnalyzerParams):
    max_edge: int = 640
    hue_green: tuple[int, int] = (30, 90)  # OpenCV hue, 0-180
    # 0-255. 85 keeps a grey surface under a strong green cast out of the
    # canopy (measured: 2.5% of such a frame passes) while shaded leaves pass.
    sat_min: int = 85
    val_min: int = 35
    exg_min: float = 0.03  # on chromatic r,g,b (sums to 1)
    hue_yellow: tuple[int, int] = (17, 30)
    yellow_sat_min: int = 90
    yellow_val_min: int = 90
    hue_brown: tuple[int, int] = (5, 17)
    brown_sat_min: int = 60
    brown_val: tuple[int, int] = (30, 160)
    close_px: int = 15  # canopy outline closing: finds lesions inside leaves, not soil between plants
    # Whole yellow leaves count as canopy only within this distance of green
    # leaves (at max_edge scale) -- keeps yellow straw mulch out.
    attach_px: int = 31
    brightness_ok: tuple[float, float] = (0.12, 0.92)
    # Below this (camera lux estimate) colours aren't trustworthy: auto-exposure
    # makes a dark scene look bright, but noise and gain wreck the hue.
    min_lux: float = 150.0
    growth_window_h: float = 24.0
    growth_tolerance_h: float = 3.0


def _in_range(channel: npt.NDArray[np.uint8], lo: int, hi: int) -> Mask:
    return (channel >= lo) & (channel <= hi)


def resize_max_edge(image: Image, max_edge: int) -> Image:
    h, w = image.shape[:2]
    scale = max_edge / max(h, w)
    if scale >= 1:
        return image
    out: Image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return out


def vegetation_masks(rgb: Image, p: ClassicalParams) -> dict[str, Mask]:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    f = rgb.astype(np.float32)
    total = f.sum(axis=2) + 1e-6
    exg = (2 * f[..., 1] - f[..., 0] - f[..., 2]) / total

    green = _in_range(h, *p.hue_green) & (s >= p.sat_min) & (v >= p.val_min) & (exg > p.exg_min)
    green = cv2.morphologyEx(green.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
    near_green = cv2.dilate(
        green.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p.attach_px | 1, p.attach_px | 1))
    ).astype(bool)
    yellow = near_green & ~green & _in_range(h, *p.hue_yellow) & (s >= p.yellow_sat_min) & (v >= p.yellow_val_min)
    yellow = cv2.morphologyEx(yellow.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
    k = max(p.close_px | 1, 3)
    canopy = cv2.morphologyEx(
        (green | yellow).astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    ).astype(bool)

    # Ripe fruit is saturated bright red; keep it out of "brown".
    fruit_red = ((h <= 8) | (h >= 170)) & (s >= 140) & (v >= 90)
    brown = (
        canopy
        & ~green
        & ~yellow
        & ~fruit_red
        & _in_range(h, *p.hue_brown)
        & (s >= p.brown_sat_min)
        & _in_range(v, *p.brown_val)
    )
    return {"green": green, "yellow": yellow, "brown": brown, "canopy": canopy, "near_green": near_green}


def indices(rgb: Image, mask: Mask) -> dict[str, float]:
    if not mask.any():
        return {}
    px = rgb[mask].astype(np.float32)
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    total = r + g + b + 1e-6
    exg = (2 * g - r - b) / total
    vari_den = g + r - b
    ok = np.abs(vari_den) > 1.0
    vari = np.clip((g[ok] - r[ok]) / vari_den[ok], -1, 1) if ok.any() else np.zeros(1, np.float32)
    gli = (2 * g - r - b) / (2 * g + r + b + 1e-6)
    return {
        "exg_mean": round(float(exg.mean()), 4),
        "vari_mean": round(float(vari.mean()), 4),
        "gli_mean": round(float(gli.mean()), 4),
    }


@register_analyzer("classical")
class ClassicalAnalyzer(Analyzer):
    Params = ClassicalParams
    params: ClassicalParams

    def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult:
        start = time.perf_counter()
        p = self.params
        result = AnalyzerResult(self.id)
        ts = frame.meta.ts.timestamp()
        totals = {"px": 0, "plant": 0, "green": 0, "yellow": 0, "brown": 0}
        veg_pixels: list[Image] = []
        brightness: list[float] = []

        rois = ctx.rois_for("canopy")
        for roi in rois:
            rgb = resize_max_edge(roi.crop(frame.image), p.max_edge)
            m = vegetation_masks(rgb, p)
            n_px = rgb.shape[0] * rgb.shape[1]
            green, yellow, brown = int(m["green"].sum()), int(m["yellow"].sum()), int(m["brown"].sum())
            plant = green + yellow + brown
            for k, v in (("px", n_px), ("plant", plant), ("green", green), ("yellow", yellow), ("brown", brown)):
                totals[k] += v
            leaf = m["green"] | m["yellow"]
            if leaf.any():
                veg_pixels.append(rgb[leaf])
            brightness.append(float(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[..., 2].mean() / 255.0))
            if roi.name != "frame":
                result.roi_metrics[roi.name] = self._roi_metrics(roi, plant, n_px, yellow, brown, ts, ctx)

        coverage = 100.0 * totals["plant"] / max(totals["px"], 1)
        plant = max(totals["plant"], 1)
        result.metrics.update(
            {
                "canopy_coverage_pct": round(coverage, 2),
                "yellow_ratio": round(totals["yellow"] / plant, 4),
                "brown_ratio": round(totals["brown"] / plant, 4),
                "brightness": round(sum(brightness) / max(len(brightness), 1), 3),
            }
        )
        if veg_pixels:
            stacked = np.concatenate(veg_pixels)[None, ...]
            result.metrics.update(indices(stacked, np.ones(stacked.shape[:2], bool)))
        growth = self._growth("canopy:all", coverage, ts, ctx)
        if growth is not None:
            result.metrics["canopy_growth_pct_24h"] = growth
        lo, hi = p.brightness_ok
        light_ok = frame.meta.lux is None or frame.meta.lux >= p.min_lux
        result.metrics["exposure_ok"] = 1.0 if light_ok and lo <= result.metrics["brightness"] <= hi else 0.0
        if frame.meta.lux is not None:
            result.metrics["lux"] = round(frame.meta.lux, 1)
        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    def _roi_metrics(
        self, roi: ROI, plant: int, n_px: int, yellow: int, brown: int, ts: float, ctx: Context
    ) -> dict[str, float]:
        coverage = 100.0 * plant / max(n_px, 1)
        out = {
            "canopy_coverage_pct": round(coverage, 2),
            "yellow_ratio": round(yellow / max(plant, 1), 4),
            "brown_ratio": round(brown / max(plant, 1), 4),
        }
        growth = self._growth(f"canopy:{roi.name}", coverage, ts, ctx)
        if growth is not None:
            out["canopy_growth_pct_24h"] = growth
        return out

    def _growth(self, key: str, coverage: float, ts: float, ctx: Context) -> float | None:
        p = self.params
        then = ctx.history.near(key, ts - p.growth_window_h * 3600, p.growth_tolerance_h * 3600)
        ctx.history.add(key, ts, coverage)
        return None if then is None else round(coverage - then, 2)
