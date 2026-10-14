from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from plant_cam.capture import CaptureLoop, FolderSource, LatestSlot
from plant_cam.config import ActiveLearningCfg, StreamCfg, ThermalCfg
from plant_cam.core.pipeline import Pipeline
from plant_cam.core.types import HistoryStore, SampleCandidate
from plant_cam.link import backoff_delay
from plant_cam.publishers.payload import PAYLOAD_SCHEMA, build_payload
from plant_cam.publishers.stream import StreamServer
from plant_cam.store import SEQ_BLOCK, SqliteHistory, Store
from plant_cam.store.active_learning import ActiveLearningStore
from plant_cam.system.thermal import ThermalMonitor
from tests.conftest import frame_of, load_scene

# ---- store / outbox --------------------------------------------------------------


def test_outbox_survives_restart_and_acks_delete(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    for i in range(5):
        s.enqueue(s.next_seq(), "t", f"body{i}")
    s.ack([0, 1])
    s.close()
    s2 = Store(tmp_path / "db.sqlite")  # "power cut" and restart
    assert [r[2] for r in s2.pending()] == ["body2", "body3", "body4"]


def test_outbox_cap_drops_oldest(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite", outbox_max_rows=3)
    for i in range(6):
        s.enqueue(i, "t", str(i))
    assert [r[0] for r in s.pending()] == [3, 4, 5]


def test_outbox_age_cap(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite", outbox_max_age_h=0.0001)
    s.enqueue(0, "t", "old")
    time.sleep(0.5)
    s.enqueue(1, "t", "new")
    assert [r[2] for r in s.pending()] == ["new"]


def test_sequence_is_monotonic_across_restarts(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    first = [s.next_seq() for _ in range(3)]
    s.close()
    s2 = Store(tmp_path / "db.sqlite")
    nxt = s2.next_seq()
    assert first == [0, 1, 2] and nxt == SEQ_BLOCK  # rest of the first block skipped, never reused
    more = [s2.next_seq() for _ in range(2 * SEQ_BLOCK)]
    assert more == list(range(SEQ_BLOCK + 1, 3 * SEQ_BLOCK + 1))  # crosses block boundaries contiguously


def test_corrupt_store_is_set_aside(tmp_path: Path):
    p = tmp_path / "db.sqlite"
    p.write_bytes(b"this is not a database" * 100)
    s = Store(p)
    s.enqueue(s.next_seq(), "t", "x")
    assert s.outbox_size() == 1
    assert list(tmp_path.glob("db.corrupt-*"))


def test_sqlite_history_near(tmp_path: Path):
    h = SqliteHistory(Store(tmp_path / "db.sqlite"))
    h.add("k", 1000.0, 1.0)
    h.add("k", 5000.0, 5.0)
    assert h.near("k", 4500.0, 1000.0) == 5.0
    assert h.near("k", 3000.0, 100.0) is None


# ---- payload --------------------------------------------------------------------


def test_payload_is_flat_numeric_plus_card_fields(scenes: Path, make_cfg, profile):
    report, _ = Pipeline(make_cfg(), profile, HistoryStore()).run(frame_of(load_scene(scenes, "fruit_mix")[0]))
    report.metrics["bad"] = math.nan
    p = build_payload(report)
    assert p["payload_schema"] == PAYLOAD_SCHEMA
    for key in ("profile_id", "profile_version", "model_id", "model_version", "label", "note"):
        assert isinstance(p[key], str)
    assert 0 <= p["health_score"] <= 1 and 0 <= p["confidence"] <= 1
    for key in ("fruit_ripe_count", "flower_count", "canopy_coverage_pct", "env_available", "visual_score"):
        assert isinstance(p[key], float)
    assert "bad" not in p
    assert isinstance(p["conditions"], list)
    nested = {k for k, v in p.items() if isinstance(v, dict | list)}
    assert nested == {"conditions"}  # everything else is flat
    json.dumps(p, allow_nan=False)


# ---- capture ---------------------------------------------------------------------


def test_latest_slot_drops_oldest():
    slot = LatestSlot(1)
    for i in range(5):
        slot.put(np.full((2, 2, 3), i, np.uint8))
    img, seq = slot.latest()
    assert seq == 5 and img is not None and img[0, 0, 0] == 4 and slot.dropped == 4


def test_capture_loop_folder_source(scenes: Path):
    src = FolderSource(str(scenes), (1296, 972), (640, 480), fps=30)
    loop = CaptureLoop(src)
    loop.start()
    try:
        assert loop.wait_open(5)
        frame, _ = loop.live.wait_newer(0, timeout=2)
        assert frame is not None and frame.shape[1] <= 640
        a, b = loop.capture_frame(), loop.capture_frame()
        assert a.meta.source != b.meta.source  # each analysis gets the next image
        assert a.hires().shape[1] >= a.image.shape[1]
        assert loop.camera_ok and not loop.wedged
    finally:
        loop.stop()


def test_capture_loop_recovers_from_camera_errors():
    class Flaky:
        name = "flaky"

        def __init__(self) -> None:
            self.opens = 0
            self.reads = 0

        def open(self) -> None:
            self.opens += 1
            if self.opens == 1:
                raise RuntimeError("camera not detected")

        def read_live(self):
            self.reads += 1
            if self.reads == 3:
                raise RuntimeError("frame timeout")
            time.sleep(0.01)
            return np.zeros((4, 4, 3), np.uint8)

        def close(self) -> None:
            pass

    src = Flaky()
    loop = CaptureLoop(src)  # type: ignore[arg-type]
    loop.start()
    deadline = time.monotonic() + 10
    while src.reads < 10 and time.monotonic() < deadline:
        time.sleep(0.05)
    loop.stop()
    assert src.opens >= 3 and src.reads >= 10 and loop.errors == 2


# ---- thermal ------------------------------------------------------------------------


def test_thermal_levels_with_hysteresis(tmp_path: Path):
    temp = tmp_path / "temp"
    hw = tmp_path / "hwmon0"
    hw.mkdir()
    (hw / "name").write_text("rpi_volt\n")
    (hw / "in0_lcrit_alarm").write_text("0\n")
    mon = ThermalMonitor(ThermalCfg(temp_path=str(temp), hwmon_glob=str(tmp_path / "hwmon*")))

    def at(c: float) -> int:
        temp.write_text(str(int(c * 1000)))
        return mon.poll()

    assert [at(c) for c in (60, 71, 76, 81, 79, 72, 69, 64)] == [0, 1, 2, 3, 3, 2, 1, 0]
    assert at(68) == 0
    (hw / "in0_lcrit_alarm").write_text("1\n")
    assert at(50) == 3 and not mon.allow_heavy and mon.stream_fps_cap == 0.0
    assert mon.system_metrics()["undervoltage"] == 1.0


# ---- active learning --------------------------------------------------------------------


def test_active_learning_rate_and_size_caps(tmp_path: Path, scenes: Path, make_cfg, profile):
    report, _ = Pipeline(make_cfg(), profile, HistoryStore()).run(frame_of(load_scene(scenes, "fruit_mix")[0]))
    crop = np.random.default_rng(0).integers(0, 255, (120, 120, 3), dtype=np.uint8)
    al = ActiveLearningStore(ActiveLearningCfg(dir=str(tmp_path / "al"), max_per_hour=5, max_bytes=60_000), "d")
    samples = [SampleCandidate(crop, "uncertain", "leaf_scorch", {"leaf_scorch": 0.5}) for _ in range(10)]
    assert al.save(samples, report) == 5  # rate limit
    files = list((tmp_path / "al").rglob("*.jpg"))
    total = sum(f.stat().st_size for f in (tmp_path / "al").rglob("*") if f.is_file())
    assert total <= 60_000 and files
    meta = json.loads(files[-1].with_suffix(".json").read_text())
    assert meta["predicted"] == "leaf_scorch" and meta["profile"].startswith("strawberry@")
    assert al.export(tmp_path / "out") == len(files)


# ---- misc -------------------------------------------------------------------------------


def test_backoff_has_jitter_and_bounds():
    delays = [backoff_delay(a, 1.0, 60.0) for a in range(12) for _ in range(20)]
    assert min(delays) >= 0.5 and max(delays) <= 60.0
    assert len({round(d, 3) for d in delays}) > 100  # jittered, not a fixed schedule


def test_stream_server_endpoints():
    slot = LatestSlot(1)
    slot.put(np.zeros((48, 64, 3), np.uint8))
    server = StreamServer(
        StreamCfg(port=0, bind="127.0.0.1", idle_timeout_s=0.1),
        slot,
        lambda f: f[:, :, ::-1],
        lambda: {"ok": True},
        lambda: None,
    )
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        assert json.load(urllib.request.urlopen(f"{base}/healthz")) == {"ok": True}
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{base}/snapshot.jpg", timeout=10)
        assert err.value.code == 503  # live view is off until start_stream
        server.request_start()
        body = urllib.request.urlopen(f"{base}/snapshot.jpg").read()
        assert body[:2] == b"\xff\xd8"
        time.sleep(0.2)
        server.tick()
        assert not server.enabled  # switched itself off: nobody watching
    finally:
        server.stop()
