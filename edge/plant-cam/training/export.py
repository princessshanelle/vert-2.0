"""Exports a trained model to INT8 TFLite and packages it with the
meta.json the Pi needs, ready for deploy/deploy-model.sh.

    python export.py --weights runs/plant-v1/weights/best.pt \
        --data datasets/vert-plant/data.yaml --version plant-v1

INT8 needs representative images to calibrate against; Ultralytics
takes them from --data. Run this on Linux, WSL2 or Colab -- the
TensorFlow toolchain behind the export is unreliable on native Windows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
from pathlib import Path

from ultralytics import YOLO

from common import load_classes

# Which file in Ultralytics' *_saved_model/ folder to ship, per precision.
_PRECISION_FILES = {"int8": "*_int8.tflite", "fp16": "*_float16.tflite", "fp32": "*_float32.tflite"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weights", required=True, type=Path)
    p.add_argument("--data", required=True)
    p.add_argument("--version", required=True, help="e.g. plant-v1; becomes the folder name and model_version")
    p.add_argument("--imgsz", type=int, default=320)
    p.add_argument("--precision", choices=sorted(_PRECISION_FILES), default="int8")
    p.add_argument("--out", type=Path, default=Path("exported"))
    args = p.parse_args()

    model = YOLO(str(args.weights))
    classes = load_classes()
    model_names = [model.names[i] for i in sorted(model.names)]
    if model_names != [c["name"] for c in classes]:
        raise SystemExit(f"model classes {model_names} don't match classes.yaml -- rebuild the dataset and retrain")

    model.export(format="tflite", imgsz=args.imgsz, int8=args.precision == "int8",
                 half=args.precision == "fp16", data=args.data)
    saved = args.weights.parent / f"{args.weights.stem}_saved_model"
    matches = sorted(saved.glob(_PRECISION_FILES[args.precision]))
    if not matches:
        raise SystemExit(f"export produced no {_PRECISION_FILES[args.precision]} in {saved}: {[f.name for f in saved.glob('*.tflite')]}")

    version = f"{args.version}-{dt.date.today():%Y%m%d}"
    out = args.out / version
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(matches[0], out / "model.tflite")
    meta = {
        "model_version": version,
        "trained_from": str(args.weights),
        "input_size": args.imgsz,
        "precision": args.precision,
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "classes": [
            {"name": c["name"], "kind": c["kind"], "weight": c.get("weight", 1.0), "note": c.get("note", "")}
            for c in classes
        ],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    size_mb = (out / "model.tflite").stat().st_size / 1e6
    print(f"\npackaged {out} ({size_mb:.1f} MB)")
    print(f"copy to the Pi:   scp -r {out} pi@<pi-ip>:~/")
    print(f"then on the Pi:   sudo /opt/plant-cam/deploy/deploy-model.sh ~/{version}")


if __name__ == "__main__":
    main()
