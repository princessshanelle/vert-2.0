"""Stage D risk indices: table-driven, against the strawberry profile defaults."""

from __future__ import annotations

import pytest

from plant_cam.config import EnvCfg
from plant_cam.env import EnvMonitor
from plant_cam.env.risk import EnvSample, compute_risks, dew_point_c, hours_meeting, is_wet

H = 3600.0
NOW = 1_800_000_000.0


def series(hours: float, temp: float, rh: float, step_min: float = 5, wet: float | None = None) -> list[EnvSample]:
    n = int(hours * 60 / step_min)
    return [EnvSample(NOW - (n - i) * step_min * 60, temp, rh, wet) for i in range(n)]


def test_dew_point_reference_values():
    assert dew_point_c(20.0, 50.0) == pytest.approx(9.3, abs=0.1)
    assert dew_point_c(20.0, 100.0) == pytest.approx(20.0, abs=0.01)


@pytest.mark.parametrize(
    ("samples", "botrytis", "mildew"),
    [
        # 8 h at 18 C, 96 % RH (free water): Botrytis building (4 h onset, 12 h full); wet excludes mildew
        (series(8, 18, 96), 0.5, 0.0),
        # 8 h at 18 C, 93 % RH: humid but 1.15 C above dew point -- not wet -> Botrytis AND mildew pressure
        (series(8, 18, 93), 0.5, (8 - 6) / 12),
        # 14 h at 20 C, 85 % RH, no free water: classic powdery-mildew weather, Botrytis quiet
        (series(14, 20, 85), 0.0, (14 - 6) / 12),
        # 24 h warm and humid but too hot for both bands
        (series(24, 31, 92), 0.0, 0.0),
        # dry and mild
        (series(24, 20, 50), 0.0, 0.0),
        # leaf-wetness sensor says wet at 70 % RH: Botrytis counts it (wet: or), mildew excludes it
        (series(12, 18, 80, wet=90.0), 1.0, 0.0),
    ],
)
def test_risk_indices(profile, samples, botrytis, mildew):
    risks = compute_risks(samples, profile.risk_indices, profile.wetness, NOW)
    assert risks["botrytis"][0] == pytest.approx(botrytis, abs=0.03)
    assert risks["powdery_mildew"][0] == pytest.approx(mildew, abs=0.03)


def test_window_and_gaps(profile):
    idx = profile.risk_indices["botrytis"]
    old = [EnvSample(NOW - 30 * H + i * 300, 18, 95) for i in range(60)]  # 5 h, but >24 h ago
    assert hours_meeting(old, idx, profile.wetness, NOW) == 0.0
    sparse = [EnvSample(NOW - 10 * H, 18, 95), EnvSample(NOW - 1 * H, 18, 95)]  # a 9 h hole in the data
    assert hours_meeting(sparse, idx, profile.wetness, NOW) == pytest.approx(1.0, abs=0.01)  # 0.5 + 0.5, gap unknown


def test_wetness_estimate_without_sensor(profile):
    w = profile.wetness
    assert is_wet(EnvSample(0, 15, 96), w)
    assert is_wet(EnvSample(0, 15, 94), w)  # within 1 C of dew point
    assert not is_wet(EnvSample(0, 15, 85), w)
    assert is_wet(EnvSample(0, None, None), w) is None


def test_monitor_aggregates_zone_and_ignores_stale_and_self(profile):
    mon = EnvMonitor(EnvCfg(max_age_s=600, sample_every_s=300), profile, own_device_id="plantcam-01")
    t = NOW - 20 * H
    while t < NOW:
        mon.ingest("dht22-01", {"temp_c": 18.0, "humidity_pct": 94.0}, t)
        mon.ingest("dht22-02", {"temp_c": 20.0, "humidity_pct": 92.0}, t)
        t += 300
    mon.ingest("old-sensor", {"temp_c": 40.0, "humidity_pct": 10.0}, NOW - 2 * H)
    mon.ingest("plantcam-01", {"temp_c": 99.0}, NOW)
    mon.ingest("ph-01", {"ph": 6.2}, NOW)
    snap = mon.snapshot(NOW)
    assert snap.temp_c == pytest.approx(19.0)
    assert snap.rh_pct == pytest.approx(93.0)
    assert snap.risks["botrytis"] == 1.0  # ~20 h humid at mild temperature
    assert snap.hours["botrytis"] >= 19


def test_monitor_without_sensors(profile):
    snap = EnvMonitor(EnvCfg(), profile).snapshot(NOW)
    assert not snap.available and snap.risks == {}


def test_monitor_rejects_implausible_humidity(profile):
    mon = EnvMonitor(EnvCfg(), profile)
    mon.ingest("bad", {"temp_c": 20.0, "humidity_pct": 250.0}, NOW)
    assert mon.snapshot(NOW).rh_pct is None
