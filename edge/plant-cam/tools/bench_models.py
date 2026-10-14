"""Latency of ONNX models on this machine, with the same ONNX Runtime
session settings plant-cam uses (plant_cam/runtime OnnxBackend).

    .venv/bin/python tools/bench_models.py MODEL.onnx [MODEL.onnx ...] --threads 1 2 4 --batch 1 8

Prints mean/p95 latency per (model, threads, batch) and the CPU time used.
Use it to pick an architecture, and to decide INT8 vs FP32 on real hardware.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


def session(path: Path, threads: int) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def bench_tflite(path: Path, threads: int, runs: int) -> dict[str, float]:
    from ai_edge_litert.interpreter import Interpreter  # XNNPACK is its default CPU delegate

    it = Interpreter(model_path=str(path), num_threads=threads)
    it.allocate_tensors()
    inp = it.get_input_details()[0]
    x = (np.random.default_rng(0).random(inp["shape"]) * 255).astype(inp["dtype"])
    for _ in range(3):
        it.set_tensor(inp["index"], x)
        it.invoke()
    lat, c0 = [], time.process_time()
    t0 = time.perf_counter()
    for _ in range(runs):
        t = time.perf_counter()
        it.set_tensor(inp["index"], x)
        it.invoke()
        lat.append((time.perf_counter() - t) * 1000)
    wall = time.perf_counter() - t0
    lat.sort()
    return {
        "mean_ms": round(statistics.mean(lat), 1),
        "p95_ms": round(lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))], 1),
        "per_item_ms": round(statistics.mean(lat), 1),
        "cpu_cores_busy": round((time.process_time() - c0) / wall, 2),
        "input": "x".join(map(str, inp["shape"])),
    }


def bench(path: Path, threads: int, batch: int, runs: int) -> dict[str, float]:
    if path.suffix == ".tflite":
        return bench_tflite(path, threads, runs) if batch == 1 else {}
    s = session(path, threads)
    inp = s.get_inputs()[0]
    fixed_batch = isinstance(inp.shape[0], int) and inp.shape[0] > 0
    if fixed_batch and batch != inp.shape[0]:
        return {}  # e.g. the detector is exported for batch 1 only
    shape = [d if isinstance(d, int) and d > 0 else batch for d in inp.shape]
    dtype = np.uint8 if "uint8" in inp.type else np.float32
    x = (np.random.default_rng(0).random(shape) * (255 if dtype == np.uint8 else 1)).astype(dtype)
    for _ in range(3):
        s.run(None, {inp.name: x})
    lat, c0 = [], time.process_time()
    t0 = time.perf_counter()
    for _ in range(runs):
        t = time.perf_counter()
        s.run(None, {inp.name: x})
        lat.append((time.perf_counter() - t) * 1000)
    wall = time.perf_counter() - t0
    lat.sort()
    return {
        "mean_ms": round(statistics.mean(lat), 1),
        "p95_ms": round(lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))], 1),
        "per_item_ms": round(statistics.mean(lat) / batch, 1),
        "cpu_cores_busy": round((time.process_time() - c0) / wall, 2),
        "input": "x".join(map(str, shape)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+", type=Path)
    ap.add_argument("--threads", nargs="+", type=int, default=[2])
    ap.add_argument("--batch", nargs="+", type=int, default=[1])
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    rows = []
    print(f"{'model':<46}{'thr':>4}{'batch':>6}{'mean ms':>9}{'p95 ms':>8}{'ms/item':>9}{'cores':>7}")
    for m in args.models:
        for th in args.threads:
            for b in args.batch:
                r = bench(m, th, b, args.runs)
                if not r:
                    continue
                name = f"{m.parent.name}/{m.name}"
                rows.append({"model": name, "threads": th, "batch": b, **r})
                print(
                    f"{name:<46}{th:>4}{b:>6}{r['mean_ms']:>9}{r['p95_ms']:>8}{r['per_item_ms']:>9}{r['cpu_cores_busy']:>7}"
                )
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
