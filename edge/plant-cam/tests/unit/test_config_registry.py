from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from plant_cam import config as config_mod
from plant_cam.core.pipeline import Pipeline
from plant_cam.core.profile import ProfileError, load_profile
from plant_cam.core.registry import (
    Analyzer,
    AnalyzerParams,
    PluginError,
    analyzer_names,
    create_analyzer,
    publisher_names,
    register_analyzer,
    validate_params,
)
from plant_cam.core.types import AnalyzerResult, Context, Frame, HistoryStore
from tests.conftest import frame_of, load_scene

MINIMAL = """
device: {id: plantcam-01, zone: zoneA, secret: from-file}
broker: {host: 192.168.1.151}
gateway: {url: "http://192.168.1.151:8080/"}
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text))
    return p


def test_minimal_config_and_defaults(tmp_path: Path):
    cfg = config_mod.load(_write(tmp_path, MINIMAL), environ={})
    assert cfg.gateway.url == "http://192.168.1.151:8080"
    assert cfg.topic_base == "vert/zoneA/ai-camera/plantcam-01"
    assert cfg.schedule.interval_s == 600
    assert [p.name for p in cfg.publishers] == ["snapshot", "mqtt"]


def test_env_overrides_and_secret(tmp_path: Path):
    env = {
        "PLANTCAM__BROKER__PORT": "8883",
        "PLANTCAM__SCHEDULE__WINDOWS": "['07:00-11:00']",
        "PLANTCAM__BROKER__TLS__ENABLED": "true",
        "VERT_DEVICE_SECRET": "from-env",
    }
    cfg = config_mod.load(_write(tmp_path, MINIMAL), environ=env)
    assert cfg.broker.port == 8883
    assert cfg.broker.tls.enabled is True
    assert cfg.schedule.windows == ["07:00-11:00"]
    assert cfg.secret() == "from-env"
    assert "from-env" not in repr(cfg)  # secrets never end up in logs


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("device: {id: a.b, zone: z, secret: s}\nbroker: {host: h}\ngateway: {url: 'http://g'}", "device.id"),
        ("device: {id: a, zone: z, secret: s}\nbroker: {host: h}\ngateway: {url: 'g'}", "gateway.url"),
        (MINIMAL + "schedule: {windows: ['7am-9am']}", "schedule.windows"),
        (MINIMAL + "rois: [{name: Tray, box: [0, 0, 1, 1]}]", "rois.0.name"),
        (MINIMAL + "rois: [{name: t, box: [0.5, 0, 0.2, 1]}]", "rois.0"),
        (MINIMAL + "camerra: {}", "camerra"),
        ("device: [", "not valid YAML"),
    ],
)
def test_config_errors_are_actionable(tmp_path: Path, text: str, message: str):
    with pytest.raises(config_mod.ConfigError, match=message.replace(".", r"\.")):
        config_mod.load(_write(tmp_path, text), environ={})


def test_missing_secret_is_refused(tmp_path: Path):
    with pytest.raises(config_mod.ConfigError, match="secret"):
        config_mod.load(_write(tmp_path, MINIMAL.replace("secret: from-file", "")), environ={})


def test_schedule_windows_including_overnight(make_cfg):
    import datetime as dt

    s = make_cfg(schedule={"windows": ["07:00-11:00", "22:00-02:00"]}).schedule
    at = lambda h, m=0: dt.datetime(2026, 10, 12, h, m)  # noqa: E731
    assert s.in_window(at(7)) and s.in_window(at(10, 59)) and not s.in_window(at(11))
    assert s.in_window(at(23)) and s.in_window(at(1)) and not s.in_window(at(3))


def test_profile_validation(tmp_path: Path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "profile.yaml").write_text("id: bad\nversion: '1'\npersistence: {activate_after: 0}\n")
    with pytest.raises(ProfileError, match="activate_after"):
        load_profile("bad", tmp_path)
    with pytest.raises(ProfileError, match="available"):
        load_profile("tomato", tmp_path)


def test_builtin_plugins_registered(profile):
    assert {"classical", "detector", "classifier", "strawberry_fruit"} <= set(analyzer_names())
    assert {"mqtt", "snapshot", "log"} <= set(publisher_names())
    with pytest.raises(PluginError, match="unknown analyzer"):
        create_analyzer("nope", {}, profile)
    with pytest.raises(PluginError, match="threshold"):
        validate_params("analyzer", "classical", {"threshold": 1})


# --- The definition-of-done demonstration: one class + one config entry ----


@register_analyzer("leaf_brightness_demo")
class LeafBrightness(Analyzer):
    class Params(AnalyzerParams):
        scale: float = 1.0

    def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult:
        canopy = ctx.results["classical"].metrics["canopy_coverage_pct"]
        return AnalyzerResult(self.id, metrics={"demo_metric": round(canopy * self.params.scale, 2)})  # type: ignore[attr-defined]


def test_new_analyzer_is_one_class_and_one_config_entry(scenes: Path, make_cfg, profile):
    cfg = make_cfg(analyzers=[{"name": "classical"}, {"name": "leaf_brightness_demo", "params": {"scale": 2}}])
    report, _ = Pipeline(cfg, profile, HistoryStore()).run(frame_of(load_scene(scenes, "healthy_canopy")[0]))
    assert report.metrics["demo_metric"] == pytest.approx(2 * report.metrics["canopy_coverage_pct"], abs=0.02)


def test_failing_analyzer_does_not_stop_the_others(scenes: Path, make_cfg, profile):
    @register_analyzer("explodes_demo")
    class Explodes(Analyzer):
        def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult:
            raise RuntimeError("boom")

    cfg = make_cfg(analyzers=[{"name": "explodes_demo"}, {"name": "classical"}])
    report, _ = Pipeline(cfg, profile, HistoryStore()).run(frame_of(load_scene(scenes, "healthy_canopy")[0]))
    assert report.metrics["canopy_coverage_pct"] > 0
