from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from plant_cam.config import Config
from plant_cam.core.profile import Profile, load_profile
from plant_cam.core.types import Frame, FrameMeta, utc_now
from tests.fixtures import synth

FIXTURES = Path(__file__).parent / "fixtures"
REAL_IMAGES = FIXTURES / "images"


@pytest.fixture(scope="session")
def scenes(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """All synthetic scenes (generated, deterministic) plus the committed real frames."""
    folder = tmp_path_factory.mktemp("scenes")
    synth.write(folder)
    for real in REAL_IMAGES.glob("real_*"):
        shutil.copy2(real, folder / real.name)
    return folder


def load_scene(folder: Path, name: str) -> tuple[np.ndarray, dict[str, Any]]:
    path = next(p for p in folder.iterdir() if p.stem == name and p.suffix != ".json")
    rgb = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    return rgb, json.loads((folder / f"{name}.json").read_text())


@pytest.fixture(scope="session")
def profile() -> Profile:
    return load_profile("strawberry")


@pytest.fixture
def make_cfg(tmp_path: Path) -> Callable[..., Config]:
    def make(**overrides: Any) -> Config:
        raw: dict[str, Any] = {
            "device": {"id": "plantcam-test", "zone": "zoneA", "secret": "s3cret"},
            "broker": {"host": "127.0.0.1", "port": 1883, "backoff_min_s": 0.2, "backoff_max_s": 1.0},
            "gateway": {"url": "http://127.0.0.1:9"},
            "models_dir": str(tmp_path / "models"),
            "store": {"dir": str(tmp_path / "state")},
            "active_learning": {"dir": str(tmp_path / "al")},
            "camera": {"source": "folder", "folder": str(tmp_path), "fps": 30},
            "stream": {"enabled": False},
            "logging": {"json": False},
        }
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(raw.get(key), dict):
                raw[key] = {**raw[key], **value}
            else:
                raw[key] = value
        return Config.model_validate(raw)

    return make


def frame_of(rgb: np.ndarray, seq: int = 1, lux: float | None = None) -> Frame:
    return Frame(image=rgb, meta=FrameMeta(seq=seq, ts=utc_now(), source="test", lux=lux))
