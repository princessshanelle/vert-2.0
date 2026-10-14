"""INT8 post-training quantization (ONNX Runtime static, QDQ format).

    python -m vertml.quantize --model exported/leaf-v1/model.onnx --calib datasets/leaf/val --out model.int8.onnx

Calibration uses ~100-300 real crops/frames (the validation split is
ideal). Only Conv/MatMul/Gemm are quantized; the detector's
post-processing (NMS, box decoding) stays float, so outputs keep their meaning.

Measure before you ship: on a Raspberry Pi 4 (Cortex-A72, no int8 dot
product instructions) INT8 is not automatically faster than FP32 -- see
docs/BENCHMARKS.md for numbers on this hardware, and keep the FP32 export
if INT8 isn't a clear win or costs accuracy.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from PIL import Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class FolderReader:
    """CalibrationDataReader over a folder of images, preprocessed per meta.json."""

    def __init__(self, folder: Path, meta: dict, input_name: str, limit: int = 200) -> None:
        files = [p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXTS]
        random.Random(0).shuffle(files)
        self.files = files[:limit]
        if not self.files:
            raise SystemExit(f"no calibration images under {folder}")
        self.meta = meta
        self.input_name = input_name
        self._it: Iterator[Path] = iter(self.files)

    def _prep(self, path: Path) -> np.ndarray:
        spec = self.meta["input"]
        w, h = spec["size"]
        img = np.asarray(Image.open(path).convert("RGB").resize((w, h)), np.float32) / spec.get("scale", 255.0)
        img = (img - np.array(spec.get("mean", [0, 0, 0]), np.float32)) / np.array(spec.get("std", [1, 1, 1]), np.float32)
        return np.ascontiguousarray(img.transpose(2, 0, 1)[None])

    def get_next(self) -> dict[str, np.ndarray] | None:
        path = next(self._it, None)
        return None if path is None else {self.input_name: self._prep(path)}


def quantize(model: Path, calib: Path, out: Path, limit: int = 200) -> Path:
    import onnx
    from onnxruntime.quantization import CalibrationMethod, QuantFormat, QuantType, quantize_static
    from onnxruntime.quantization.shape_inference import quant_pre_process

    meta = json.loads((model.parent / "meta.json").read_text())
    pre = out.with_suffix(".pre.onnx")
    quant_pre_process(str(model), str(pre), skip_symbolic_shape=True)
    input_name = onnx.load(str(pre)).graph.input[0].name
    quantize_static(
        str(pre), str(out), FolderReader(calib, meta, input_name, limit),
        quant_format=QuantFormat.QDQ, activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
        per_channel=True, op_types_to_quantize=["Conv", "MatMul", "Gemm"],
        calibrate_method=CalibrationMethod.MinMax,
    )
    pre.unlink(missing_ok=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, type=Path, help="model.onnx inside an exported package")
    ap.add_argument("--calib", required=True, type=Path, help="folder of representative images")
    ap.add_argument("--out", type=Path, help="default: <package>/model.int8.onnx")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    out = quantize(args.model, args.calib, args.out or args.model.with_name("model.int8.onnx"), args.limit)
    print(f"wrote {out} -- benchmark it on the Pi (tools/bench_models.py) before switching meta.json 'file' to it")


if __name__ == "__main__":
    main()
