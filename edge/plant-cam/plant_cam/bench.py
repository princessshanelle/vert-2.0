"""`plant_cam benchmark`: per-stage latency, analyses/s, CPU, memory and
temperature, on fixture images or the live camera. No broker needed.

Reports mean and p95 per stage (capture, each analyzer, aggregate), the
process CPU used per analysis (as % of one core and of the whole Pi),
peak RSS, and CPU temperature before/after.
"""

from __future__ import annotations

import json
import os
import resource
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from .capture import CaptureLoop, FolderSource, make_source
from .config import Config
from .core.pipeline import Pipeline
from .core.profile import Profile
from .core.types import HistoryStore
from .system.thermal import ThermalMonitor, _rss_mb


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]


def benchmark(cfg: Config, profile: Profile, images: str | None, iterations: int, out_json: str | None) -> int:
    thermal = ThermalMonitor(cfg.thermal)
    temp_before = thermal.temperature()
    if images:
        source: Any = FolderSource(images, cfg.camera.main_size, cfg.camera.lores_size, 1000.0, loop=True)
    else:
        source = make_source(cfg.camera)
    loop = CaptureLoop(source, cfg.camera.stall_timeout_s)
    loop.start()
    if not loop.wait_open(30):
        print("camera/source did not open")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        cfg = cfg.model_copy(update={"store": cfg.store.model_copy(update={"dir": tmp})})
        pipeline = Pipeline(cfg, profile, HistoryStore())
        stages: dict[str, list[float]] = {}
        cpu: list[float] = []
        wall: list[float] = []
        hires_ms: list[float] = []
        # warm-up: first inference pays for graph optimisation and page faults
        pipeline.run(loop.capture_frame(), allow_heavy=True)
        for _ in range(iterations):
            c0, t0 = time.process_time(), time.perf_counter()
            frame = loop.capture_frame()
            stages.setdefault("capture", []).append((time.perf_counter() - t0) * 1000)
            if frame.get_hires is not None:
                h0 = time.perf_counter()
                frame.hires()
                hires_ms.append((time.perf_counter() - h0) * 1000)
            report, _ = pipeline.run(frame, allow_heavy=True)
            for k, v in report.latency_ms.items():
                stages.setdefault(k, []).append(v)
            wall.append(time.perf_counter() - t0)
            cpu.append(time.process_time() - c0)
        loop.stop()
        pipeline.close()

    ncpu = os.cpu_count() or 1
    rows: dict[str, dict[str, float]] = {
        k: {"mean_ms": round(statistics.mean(v), 1), "p95_ms": round(_p95(v), 1)} for k, v in stages.items()
    }
    if hires_ms:
        rows["hires_still"] = {"mean_ms": round(statistics.mean(hires_ms), 1), "p95_ms": round(_p95(hires_ms), 1)}
    total_wall = sum(wall)
    result: dict[str, Any] = {
        "iterations": iterations,
        "source": images or cfg.camera.source,
        "models": pipeline.models(),
        "stages": rows,
        "analysis_mean_ms": round(1000 * statistics.mean(wall), 1),
        "analyses_per_s": round(iterations / total_wall, 2) if total_wall else 0,
        "cpu_pct_of_one_core": round(100 * sum(cpu) / total_wall, 1) if total_wall else 0,
        "cpu_pct_of_pi": round(100 * sum(cpu) / total_wall / ncpu, 1) if total_wall else 0,
        "rss_mb": round(_rss_mb(), 1),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "temp_before_c": temp_before,
        "temp_after_c": thermal.temperature(),
    }
    print(f"\nbenchmark: {iterations} analyses on {result['source']}  models: {result['models'] or 'none'}")
    print(f"{'stage':<24}{'mean ms':>10}{'p95 ms':>10}")
    for stage, row in rows.items():
        print(f"{stage:<24}{row['mean_ms']:>10}{row['p95_ms']:>10}")
    print(f"\nper analysis: {result['analysis_mean_ms']} ms   ({result['analyses_per_s']} analyses/s)")
    print(f"CPU while analysing: {result['cpu_pct_of_one_core']}% of one core = {result['cpu_pct_of_pi']}% of the Pi")
    print(f"memory: {result['rss_mb']} MB now, {result['peak_rss_mb']} MB peak")
    print(f"temperature: {temp_before} -> {result['temp_after_c']} C")
    if out_json:
        Path(out_json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0
