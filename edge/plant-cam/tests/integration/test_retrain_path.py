"""The documented retraining workflow, run for real (tiny, on CPU):

    label (Label Studio export) -> labelstudio.py convert -> train_classifier.py
    -> train_detector.py -> export.py -> deploy into models_dir -> plant-cam loads
    and runs both stages -> plant_cam evaluate reports precision/recall

The models are trained for a couple of epochs from random weights, so
their *accuracy* means nothing here; what's tested is that every step of
the path works and hands the next step what it expects.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from plant_cam.core.pipeline import Pipeline
from plant_cam.core.types import HistoryStore
from plant_cam.evaluate import evaluate_folder
from tests.conftest import frame_of, load_scene
from tests.fixtures import synth

pytestmark = pytest.mark.integration

TRAINING = Path(__file__).resolve().parents[2] / "training"
PY = TRAINING / ".venv" / "bin" / "python"


def _stems_covering_splits(n_per_split: int) -> list[str]:
    import sys

    sys.path.insert(0, str(TRAINING))
    from vertml.data import split_for

    want = {"train": n_per_split * 3, "val": n_per_split, "test": n_per_split}
    stems, i = [], 0
    while any(want.values()):
        stem = f"photo{i:04d}"
        s = split_for(stem)
        if want[s]:
            want[s] -= 1
            stems.append(stem)
        i += 1
    return stems


def _run(*args: str, cwd: Path) -> str:
    proc = subprocess.run([str(PY), *args], cwd=cwd, capture_output=True, text=True, timeout=1500)
    assert proc.returncode == 0, f"{args[0]} failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    return proc.stdout


@pytest.mark.skipif(not PY.exists(), reason="training venv not set up (see training/README.md)")
def test_label_train_export_deploy_evaluate(tmp_path: Path, make_cfg, profile, scenes: Path):
    work = tmp_path / "training"
    shutil.copytree(TRAINING, work, ignore=shutil.ignore_patterns(".venv", "runs", "exported", "datasets"))
    shutil.copytree(TRAINING.parent / "profiles", tmp_path / "profiles")  # as in a checkout: training/../profiles
    export = synth.write_labelstudio(tmp_path / "photos", _stems_covering_splits(3))

    out = _run(
        "labelstudio.py",
        "convert",
        "--export",
        str(export),
        "--images",
        str(tmp_path / "photos"),
        "--out",
        "datasets/own",
        cwd=work,
    )
    assert "powdery_mildew_leaf" in out
    _run(
        "train_classifier.py",
        "--data",
        "datasets/own/stage_b_leaf",
        "--group",
        "stage_b_leaf",
        "--name",
        "leaf-test",
        "--epochs",
        "2",
        "--size",
        "96",
        "--batch",
        "16",
        "--workers",
        "0",
        "--no-pretrained",
        cwd=work,
    )
    _run(
        "train_detector.py",
        "--data",
        "datasets/own/objects",
        "--name",
        "det-test",
        "--epochs",
        "1",
        "--batch",
        "4",
        "--workers",
        "0",
        "--no-pretrained",
        cwd=work,
    )
    report = json.loads((work / "runs" / "leaf-test" / "report.json").read_text())
    assert set(report["classes"]) == {"healthy_leaf", "powdery_mildew_leaf"}
    assert "map50" in json.loads((work / "runs" / "det-test" / "report.json").read_text())

    _run("export.py", "--checkpoint", "runs/leaf-test/best.pt", "--version", "t1", cwd=work)
    _run("export.py", "--checkpoint", "runs/det-test/best.pt", "--version", "t1", cwd=work)

    # "deploy": what deploy-model.sh does -- <models_dir>/<slot>/<version>, current -> version
    cfg = make_cfg()
    for pkg in (work / "exported").iterdir():
        meta = json.loads((pkg / "meta.json").read_text())
        dest = Path(cfg.models_dir) / meta["slot"] / meta["model_version"]
        shutil.copytree(pkg, dest)
        (dest.parent / "current").symlink_to(meta["model_version"])

    pipe = Pipeline(cfg, profile, HistoryStore())
    assert set(pipe.models()) == {"detector", "leaf_classifier"}
    report_, _ = pipe.run(frame_of(load_scene(scenes, "fruit_mix")[0]))
    assert "detector:strawberry-detector" in report_.model_version
    assert report_.latency_ms["detector"] > 0 and report_.latency_ms["leaf_classifier"] > 0
    assert any(k.startswith("cond_powdery_mildew") for k in report_.metrics)  # the new model makes it evaluable

    # the Pi-side measurement on the held-out labelled photos
    result_json = tmp_path / "eval.json"
    assert evaluate_folder(cfg, profile, work / "datasets" / "own" / "eval", str(result_json)) == 0
    result = json.loads(result_json.read_text())
    assert result["images"] == 3 and "powdery_mildew" in result["conditions"]
    assert {"precision", "recall"} <= set(next(iter(result["boxes"].values())))
