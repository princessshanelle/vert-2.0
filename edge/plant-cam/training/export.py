"""Package a trained checkpoint for the Pi: model.onnx + meta.json.

    python export.py --checkpoint runs/strawberry-leaf-v1/best.pt --version v1
    python export.py ... --int8 datasets/leaf/val       # also an ONNX INT8 variant (measure first!)
    python export.py ... --tflite-int8 datasets/leaf/val # LiteRT INT8 via onnx2tf (needs TensorFlow; Colab)

Produces exported/<slot>-<version>-<YYYYmmdd>/ where <slot> is where the
crop profile looks for it (strawberry-detector, strawberry-leaf,
strawberry-fruit, strawberry-flower). Copy it to the Pi and run

    sudo /opt/plant-cam/deploy/deploy-model.sh ~/<that folder>

On a Pi 4, LiteRT INT8 measured ~2.6x faster than FP32 for an
EfficientNet-class model while ONNX Runtime INT8 gained little (see
docs/BENCHMARKS.md) -- so if you can run onnx2tf (Colab), --tflite-int8 is
the fastest deployable form; the FP32 ONNX is the always-available one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

import torch

from vertml.models import build_classifier, build_detector, export_classifier, export_detector

SLOTS = {"stage_b_leaf": "strawberry-leaf", "stage_b_fruit": "strawberry-fruit", "stage_b_flower": "strawberry-flower"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", required=True, type=Path)
    ap.add_argument("--version", required=True, help="e.g. v1")
    ap.add_argument("--slot", help="default: from the checkpoint (e.g. strawberry-leaf)")
    ap.add_argument("--out", type=Path, default=Path("exported"))
    ap.add_argument("--int8", type=Path, metavar="CALIB_DIR", help="also write model.int8.onnx")
    ap.add_argument("--tflite-int8", type=Path, metavar="CALIB_DIR", help="also write model_int8.tflite (onnx2tf)")
    args = ap.parse_args()

    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    labels: list[str] = ckpt["labels"]
    slot = args.slot or ("strawberry-detector" if ckpt["kind"] == "detector" else SLOTS[ckpt["group"]])
    version = f"{args.version}-{dt.date.today():%Y%m%d}"
    folder = args.out / f"{slot}-{version}"
    if folder.exists():
        shutil.rmtree(folder)
    trained_on = [str(ckpt.get("data", ""))]

    if ckpt["kind"] == "detector":
        model = build_detector(len(labels), pretrained=False, size=ckpt["size"])
        model.load_state_dict(ckpt["state_dict"])
        export_detector(model.eval(), folder, model_id=slot, version=version, labels=labels, size=ckpt["size"],
                        trained_on=trained_on)
    else:
        model = build_classifier(ckpt["arch"], len(labels), pretrained=False)
        model.load_state_dict(ckpt["state_dict"])
        export_classifier(model.eval(), folder, model_id=slot, version=version, labels=labels, size=ckpt["size"],
                          trained_on=trained_on)
    meta = json.loads((folder / "meta.json").read_text())
    meta["slot"] = slot
    meta["report"] = _report(args.checkpoint)
    (folder / "meta.json").write_text(json.dumps(meta, indent=2))
    _verify(folder)

    if args.int8:
        from vertml.quantize import quantize

        quantize(folder / "model.onnx", args.int8, folder / "model.int8.onnx")
        print("wrote model.int8.onnx -- to use it, set \"file\": \"model.int8.onnx\" in meta.json after benchmarking")
    if args.tflite_int8:
        _tflite_int8(folder, args.tflite_int8)
    print(f"\npackaged {folder}")
    print(f"  copy:   scp -r {folder} pi@<pi-ip>:~/")
    print(f"  deploy: sudo /opt/plant-cam/deploy/deploy-model.sh ~/{folder.name}")


def _report(ckpt: Path) -> dict:
    p = ckpt.parent / "report.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _verify(folder: Path) -> None:
    """Run the exported model once through ONNX Runtime, as the Pi will."""
    import numpy as np
    import onnxruntime as ort

    meta = json.loads((folder / "meta.json").read_text())
    s = ort.InferenceSession(str(folder / "model.onnx"), providers=["CPUExecutionProvider"])
    w, h = meta["input"]["size"]
    out = s.run(None, {s.get_inputs()[0].name: np.random.rand(1, 3, h, w).astype(np.float32)})[0]
    want = 6 if meta["task"] == "detect" else len(meta["labels"])
    if out.shape[-1] != want:
        raise SystemExit(f"exported model output {out.shape} doesn't match meta.json ({want} columns)")


def _tflite_int8(folder: Path, calib: Path) -> None:
    """ONNX -> TFLite full-integer (onnx2tf, Apache-2.0). Calibration images are
    preprocessed exactly as plant-cam does (meta.json input spec)."""
    import numpy as np
    from PIL import Image

    meta = json.loads((folder / "meta.json").read_text())
    spec = meta["input"]
    w, h = spec["size"]
    files = sorted(p for p in calib.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[:200]
    if not files:
        raise SystemExit(f"no calibration images in {calib}")
    arr = np.stack([np.asarray(Image.open(f).convert("RGB").resize((w, h)), np.float32) / spec["scale"] for f in files])
    calib_npy = folder / "calib.npy"
    np.save(calib_npy, arr)  # NHWC, 0-1; mean/std given to onnx2tf below
    work = folder / "tflite"
    cmd = [sys.executable, "-m", "onnx2tf", "-i", str(folder / "model.onnx"), "-o", str(work), "-oiqt", "-qt", "per-channel",
           "-cind", "input", str(calib_npy), json.dumps([spec["mean"]]), json.dumps([spec["std"]])]
    subprocess.run(cmd, check=True)
    calib_npy.unlink()
    produced = next(work.glob("*_full_integer_quant.tflite"), None) or next(work.glob("*_integer_quant.tflite"), None)
    if produced is None:
        raise SystemExit(f"onnx2tf produced no integer model in {work}")
    shutil.copy2(produced, folder / "model_int8.tflite")
    variant = {**meta, "file": "model_int8.tflite"}
    variant["input"] = {**spec, "layout": "NHWC"}
    (folder / "meta.tflite.json").write_text(json.dumps(variant, indent=2))
    shutil.rmtree(work)
    print("wrote model_int8.tflite + meta.tflite.json (to use it: mv meta.tflite.json meta.json)")


if __name__ == "__main__":
    main()
