"""Stage A/B through the real ONNX runtime, Stage C persistence, scoring."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from plant_cam.core.pipeline import Pipeline
from plant_cam.core.profile import Profile
from plant_cam.core.types import HistoryStore
from tests import models
from tests.conftest import frame_of, load_scene


def _mildew_scene(scenes: Path) -> np.ndarray:
    rgb, _ = load_scene(scenes, "healthy_canopy")
    rgb = rgb.copy()
    # white powdery patches over the top-left plants
    for cx, cy in ((150, 180), (200, 230), (120, 260), (460, 170), (500, 230)):
        cv2.circle(rgb, (cx, cy), 55, (235, 238, 232), -1)
    return rgb


@pytest.fixture
def leaf_model(make_cfg) -> Path:
    cfg = make_cfg()
    return models.mildew_leaf_classifier(Path(cfg.models_dir) / "strawberry-leaf" / "current")


def test_missing_models_are_skipped_not_fatal(scenes: Path, make_cfg, profile: Profile):
    pipe = Pipeline(make_cfg(), profile, HistoryStore())
    report, _ = pipe.run(frame_of(load_scene(scenes, "healthy_canopy")[0]))
    assert report.model_id == "none"
    assert pipe.models() == {}
    assert not any(k.startswith("cond_") for k in report.metrics)


def test_broken_model_falls_back(make_cfg, profile: Profile, scenes: Path):
    cfg = make_cfg()
    folder = Path(cfg.models_dir) / "strawberry-leaf" / "current"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text("{not json")
    pipe = Pipeline(cfg, profile, HistoryStore())
    report, _ = pipe.run(frame_of(load_scene(scenes, "healthy_canopy")[0]))
    assert report.label in ("thriving", "steady")


def test_model_with_labels_outside_profile_is_refused(make_cfg, profile: Profile):
    cfg = make_cfg()
    models.colour_classifier(
        Path(cfg.models_dir) / "strawberry-leaf" / "current",
        ["healthy_leaf", "made_up_disease"],
        np.zeros((3, 2)),
        np.zeros(2),
    )
    pipe = Pipeline(cfg, profile, HistoryStore())
    assert "leaf_classifier" not in pipe.models()


def test_stage_b_tiles_find_mildew_and_persistence_gates_the_alert(
    scenes: Path, make_cfg, profile: Profile, leaf_model: Path
):
    pipe = Pipeline(make_cfg(), profile, HistoryStore())
    sick = _mildew_scene(scenes)
    reports = [pipe.run(frame_of(sick, seq=i))[0] for i in range(1, 4)]

    first = reports[0]
    assert "leaf_classifier" in first.model_id
    assert first.metrics["cond_powdery_mildew_conf"] >= 0.45
    assert first.metrics["cond_powdery_mildew_active"] == 0.0  # seen once: not yet an alert
    assert [r.metrics["cond_powdery_mildew_persist"] for r in reports] == [1.0, 2.0, 3.0]
    assert reports[1].newly_active == []
    assert reports[2].newly_active == ["powdery_mildew"]
    assert reports[2].metrics["cond_powdery_mildew_active"] == 1.0
    assert reports[2].health_score < first.health_score
    cond = next(c for c in reports[2].conditions if c.id == "powdery_mildew")
    assert cond.status in ("detected", "suspected")
    assert "mildew" in reports[2].note.lower()

    healthy = load_scene(scenes, "healthy_canopy")[0]
    after = [pipe.run(frame_of(healthy, seq=10 + i))[0] for i in range(2)]
    assert after[0].metrics["cond_powdery_mildew_active"] == 1.0  # one miss: still active (hysteresis)
    assert after[1].metrics["cond_powdery_mildew_active"] == 0.0  # two misses: cleared


def test_too_dark_or_skipped_model_is_not_a_miss(scenes: Path, make_cfg, profile: Profile, leaf_model: Path):
    pipe = Pipeline(make_cfg(), profile, HistoryStore())
    sick = _mildew_scene(scenes)
    for i in range(3):
        pipe.run(frame_of(sick, seq=i))
    healthy = load_scene(scenes, "healthy_canopy")[0]
    for _ in range(3):
        pipe.run(frame_of(healthy, lux=10))  # too dark to see anything
        hot, _ = pipe.run(frame_of(healthy), allow_heavy=False)  # classifier skipped for heat
    assert hot.latency_ms["leaf_classifier"] == 0.0 or "leaf_classifier" in hot.latency_ms
    track = pipe.tracker.state()
    assert any(k.startswith("powdery_mildew|") and v["active"] for k, v in track.items())


def test_alert_crops_are_saved_for_labelling(scenes: Path, make_cfg, profile: Profile, leaf_model: Path):
    pipe = Pipeline(make_cfg(), profile, HistoryStore())
    sick = _mildew_scene(scenes)
    samples = [pipe.run(frame_of(sick, seq=i))[1] for i in range(3)]
    alert = [s for s in samples[2] if s.reason == "alert"]
    assert alert and all(s.label == "powdery_mildew_leaf" and s.image.size for s in alert)


def test_stage_a_detections_drive_stage_b_crops(scenes: Path, make_cfg, profile: Profile, leaf_model: Path):
    cfg = make_cfg()
    # one "leaf" box exactly over the white patch, one over healthy leaves
    models.fixed_detector(
        Path(cfg.models_dir) / "strawberry-detector" / "current",
        ["leaf", "flower", "fruit", "runner"],
        [(0.07, 0.13, 0.17, 0.27, 0.9, 0), (0.6, 0.6, 0.8, 0.8, 0.8, 0), (0.0, 0.0, 0.1, 0.1, 0.1, 1)],
    )
    pipe = Pipeline(cfg, profile, HistoryStore())
    report, _ = pipe.run(frame_of(_mildew_scene(scenes)))
    assert report.metrics["det_leaf_count"] == 2
    assert report.metrics["det_flower_count"] == 0  # below the score threshold
    stage_b = [d for d in report.detections if d.attrs.get("stage") == "B"]
    assert {d.attrs["from"] for d in stage_b} == {"detector"}
    assert sorted(d.label for d in stage_b) == ["healthy_leaf", "powdery_mildew_leaf"]


def test_ripeness_model_counts_replace_classical(scenes: Path, make_cfg, profile: Profile):
    cfg = make_cfg()
    md = Path(cfg.models_dir)
    models.fixed_detector(
        md / "strawberry-detector" / "current",
        ["leaf", "flower", "fruit", "runner"],
        [(0.1, 0.8, 0.15, 0.9, 0.9, 2), (0.5, 0.8, 0.55, 0.9, 0.9, 2), (0.3, 0.05, 0.4, 0.15, 0.9, 1)],
    )
    # red crops -> fruit_ripe, anything else -> fruit_green
    models.colour_classifier(
        md / "strawberry-fruit" / "current",
        ["fruit_green", "fruit_ripe"],
        np.array([[0.0, 8.0], [0.0, -8.0], [0.0, -8.0]]),
        np.zeros(2),
        model_id="test-fruit",
    )
    report, _ = Pipeline(cfg, profile, HistoryStore()).run(frame_of(load_scene(scenes, "fruit_mix")[0]))
    assert report.metrics["fruit_counts_model"] == 1.0
    assert report.metrics["fruit_ripe_count"] + report.metrics["fruit_green_count"] == 2
    assert report.metrics["flower_count"] == 1
    assert "fruit_classifier" in report.model_version
