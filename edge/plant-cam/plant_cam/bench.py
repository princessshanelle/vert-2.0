"""Benchmarks and sanity-checks an installed model on real images,
without MQTT or the camera -- run it on the Pi after every new model.

    python -m plant_cam.bench --model-dir models/current --images samples/ --out annotated/
"""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path

import cv2

from .config import ModelCfg
from .detector import load_detector
from .overlay import annotate


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model-dir", default="models/current")
    p.add_argument("--images", required=True)
    p.add_argument("--threads", type=int, default=3)
    p.add_argument("--score", type=float, default=0.35)
    p.add_argument("--out", help="write annotated copies here to eyeball what the model sees")
    args = p.parse_args()

    detector = load_detector(ModelCfg(dir=args.model_dir, num_threads=args.threads, score_threshold=args.score))
    classes = {c.name: c for c in detector.classes}
    paths = sorted(q for q in Path(args.images).glob("*") if q.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not paths:
        raise SystemExit(f"no images in {args.images}")
    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    latencies = []
    for i, path in enumerate(paths):
        rgb = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
        a = detector.analyze(rgb)
        if i > 0:  # first call includes one-off warm-up cost
            latencies.append(a.latency_ms)
        found = ", ".join(f"{d.cls} {d.score:.2f}" for d in a.detections) or "nothing"
        print(f"{path.name}: {found}  ({a.latency_ms:.0f} ms)")
        if out:
            cv2.imwrite(str(out / path.name), annotate(rgb, a.detections, classes, detector.model_version))

    if latencies:
        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95) - 1] if len(latencies) >= 20 else max(latencies)
        print(f"\n{detector.model_version}: mean {statistics.mean(latencies):.0f} ms, p95 {p95:.0f} ms over {len(latencies)} images")


if __name__ == "__main__":
    main()
