"""Classical baseline + strawberry phenology on fixture scenes (real and synthetic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from plant_cam.core.pipeline import Pipeline
from plant_cam.core.profile import Profile
from plant_cam.core.types import HistoryStore
from tests.conftest import frame_of, load_scene

COUNT_KEYS = ("fruit_ripe_count", "fruit_turning_count", "fruit_white_count", "fruit_overripe_count", "flower_count")


def _run(make_cfg, profile: Profile, rgb, gt=None, lux=None):
    rois, pre = [], {}
    if gt and gt.get("reference_roi"):
        rois = [{"name": "card", "box": gt["reference_roi"], "kinds": ["reference"]}]
        pre = {"reference_roi": "card"}
    cfg = make_cfg(rois=rois, preprocess=pre)
    report, _ = Pipeline(cfg, profile, HistoryStore()).run(frame_of(rgb, lux=lux))
    return report


@pytest.mark.parametrize("name", ["fruit_mix", "purple_cast"])
def test_fruit_and_flower_counts_match_ground_truth(scenes: Path, make_cfg, profile, name):
    rgb, gt = load_scene(scenes, name)
    report = _run(make_cfg, profile, rgb, gt)
    got = {k: report.metrics[k] for k in COUNT_KEYS}
    assert got == {k: float(gt["counts"][k]) for k in COUNT_KEYS}
    assert report.metrics["fruit_counts_model"] == 0.0
    assert "fruit_green_count" not in report.metrics  # model-only: not guessed from colour


def test_reference_card_cancels_grow_light_cast(scenes: Path, make_cfg, profile):
    rgb, gt = load_scene(scenes, "purple_cast")
    corrected = _run(make_cfg, profile, rgb, gt)
    uncorrected = _run(make_cfg, profile, rgb)
    assert corrected.metrics["ref_gain_g"] > 1.2  # green was suppressed by the light
    assert {k: corrected.metrics[k] for k in COUNT_KEYS} != {k: uncorrected.metrics[k] for k in COUNT_KEYS}


def test_red_tinted_leaves_are_not_fruit_and_not_disease(scenes: Path, make_cfg, profile):
    rgb, gt = load_scene(scenes, "red_tint_leaves")
    report = _run(make_cfg, profile, rgb, gt)
    assert sum(report.metrics[k] for k in COUNT_KEYS if k.startswith("fruit")) == 0
    assert report.metrics["red_leaf_ratio"] >= gt["expect"]["red_leaf_min"]
    assert report.conditions == []
    assert report.label in ("thriving", "steady")
    assert "not a disease" in report.note


def test_healthy_canopy(scenes: Path, make_cfg, profile):
    rgb, gt = load_scene(scenes, "healthy_canopy")
    m = _run(make_cfg, profile, rgb, gt).metrics
    assert m["canopy_coverage_pct"] >= gt["expect"]["canopy_min"] * 0.6
    assert m["yellow_ratio"] <= gt["expect"]["yellow_max"]
    for k in ("exg_mean", "vari_mean", "gli_mean"):
        assert -1 <= m[k] <= 1


def test_yellowing_raises_yellow_ratio_and_lowers_visual_score(scenes: Path, make_cfg, profile):
    yellow = _run(make_cfg, profile, load_scene(scenes, "yellowing")[0])
    healthy = _run(make_cfg, profile, load_scene(scenes, "healthy_canopy")[0])
    assert yellow.metrics["yellow_ratio"] >= 0.10
    assert yellow.visual_score < healthy.visual_score
    assert yellow.conditions == []  # colour statistics alone never raise a condition


def test_empty_tray_says_no_plants(scenes: Path, make_cfg, profile):
    report = _run(make_cfg, profile, load_scene(scenes, "empty_substrate")[0])
    assert report.metrics["canopy_coverage_pct"] < 2.0
    assert report.label == "steady"
    assert report.confidence <= 0.3
    assert "Couldn't make out any plants" in report.note


def test_green_cast_wall_is_not_canopy(scenes: Path, make_cfg, profile):
    """Real OV5647 frame: a grey ceiling under a strong green cast at night."""
    rgb, gt = load_scene(scenes, "real_ov5647_ceiling_night")
    report = _run(make_cfg, profile, rgb)
    assert report.metrics["canopy_coverage_pct"] < gt["expect"]["canopy_max"]


def test_low_light_is_reported_as_too_dark(scenes: Path, make_cfg, profile):
    rgb, _ = load_scene(scenes, "healthy_canopy")
    report = _run(make_cfg, profile, rgb, lux=33.0)  # the camera's own lux estimate
    assert report.metrics["exposure_ok"] == 0.0
    assert report.confidence <= 0.3
    assert "Too dark" in report.note


def test_growth_tracking_uses_history(scenes: Path, make_cfg, profile):
    import datetime as dt

    from plant_cam.core.types import Frame, FrameMeta

    history = HistoryStore()
    pipe = Pipeline(make_cfg(), profile, history)
    rgb, _ = load_scene(scenes, "healthy_canopy")
    t0 = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
    first, _ = pipe.run(Frame(load_scene(scenes, "empty_substrate")[0], FrameMeta(1, t0, "t")))
    assert "canopy_growth_pct_24h" not in first.metrics
    second, _ = pipe.run(Frame(rgb, FrameMeta(2, t0 + dt.timedelta(hours=24), "t")))
    assert second.metrics["canopy_growth_pct_24h"] == pytest.approx(second.metrics["canopy_coverage_pct"], abs=0.01)


def test_rois_get_their_own_metrics(scenes: Path, make_cfg, profile):
    rgb, _ = load_scene(scenes, "fruit_mix")
    cfg = make_cfg(
        rois=[
            {"name": "canopy_top", "box": [0, 0, 1, 0.6], "kinds": ["canopy"]},
            {"name": "fruit_strip", "box": [0, 0.75, 1, 1], "kinds": ["fruit"]},
        ]
    )
    report, _ = Pipeline(cfg, profile, HistoryStore()).run(frame_of(rgb))
    assert report.metrics["roi_canopy_top_canopy_coverage_pct"] > 20
    assert report.metrics["fruit_ripe_count"] == 3  # counted in the fruit strip only
    assert report.metrics["flower_count"] == 0  # flowers are up in the canopy ROI, not the fruit strip
