# Benchmarks (measured)

Hardware: Raspberry Pi 4 Model B Rev 1.5, 4 GB, Debian 13 (Trixie) aarch64,
Python 3.13, OV5647 camera. **The same Pi was running the whole VERT
Docker stack** (RabbitMQ, Postgres, TimescaleDB, MinIO, gateway,
automation, inference, frontend) during every measurement. Ambient room
temperature, no fan. Measured 2026-10-12.

Reproduce: `make bench` (pipeline) and `.venv/bin/python tools/bench_models.py ...` (models).

## Whole pipeline on the real camera (`plant_cam benchmark`, 10 analyses)

| Stage | Classical only: mean / p95 ms | All four models: mean / p95 ms |
|---|---|---|
| capture (main frame) | 349 / 503 | 317 / 398 |
| full-sensor still (2592x1944, for Stage B crops) | 733 / 815 | 743 / 779 |
| classical (canopy, indices) | 99 / 110 | 108 / 123 |
| detector (SSDLite320, FP32) | -- | 203 / 262 |
| leaf_classifier (MobileNetV3-Small) | -- | 71 / 167 |
| strawberry_fruit | 132 / 158 | 149 / 211 |
| **one analysis** | **1313** | **1592** |
| CPU while analysing | 34 % of one core (8.5 % of the Pi) | 55 % of one core (13.8 % of the Pi) |
| RSS | 154 MB | 255 MB |

At the default cadence (one analysis per 10 min) that is <0.1 % average
CPU. The leaf classifier's worst case (no Stage A model, 24 canopy tiles)
is ~24 x 25 ms = 0.6 s more. "All four models" used real architectures
with random weights: latency is representative, accuracy is not.

Live service (camera thread + lores live-view buffer, stream off): ~5 % of
one core. Thermal: 62-67 C during these runs (soft limit 70 C).

## Models (ONNX Runtime 1.31, 2 intra-op threads unless noted)

### Stage A detectors, 1 image

| Model (licence) | input | FP32 ms | INT8 (ORT QDQ) ms |
|---|---|---|---|
| **SSDLite320-MobileNetV3-Large** (BSD-3, torchvision) -- chosen | 320 | 136 (4 thr: 116) | 127 (4 thr: 147) |
| NanoDet-Plus-m (Apache-2.0) | 320 | 113 (4 thr: 95) + box decoding on CPU | -- |
| YOLOX-nano (Apache-2.0) | 416 | 167 (4 thr: 150) | -- |

### Stage B classifiers, 224x224, per crop (batch 8)

| Model | FP32 ms/crop | INT8 (ORT) ms/crop |
|---|---|---|
| **MobileNetV3-Small** -- chosen | 25.1 | 21.5 |
| MobileNetV3-Large | 66.0 | 58.6 |
| EfficientNet-B0 | 135.4 | 136.1 |

### Runtime: LiteRT (XNNPACK) vs ONNX Runtime, same model (EfficientNet-Lite0, 224)

| Runtime | 1 thread | 2 threads | 4 threads |
|---|---|---|---|
| ONNX Runtime FP32 | 158 | 95 | 77 |
| LiteRT FP32 | 104 | 84 | 49 |
| **LiteRT INT8** | **58** | **33** | 32 |

## Decisions and why

* **SSDLite320 for Stage A.** NanoDet-Plus is ~20 % faster but its
  training stack is heavier to run and test; SSDLite trains and exports
  with plain torchvision, which is what makes the retraining path testable
  end to end here (tests/integration/test_retrain_path.py). 136 ms every
  10 minutes is not worth a harder toolchain. Revisit if analysis moves to
  seconds-level cadence.
* **MobileNetV3-Small for Stage B**, Large as an option if accuracy needs it.
* **Two inference threads.** Four threads buy 15-25 % latency but compete with
  the broker and databases on this shared Pi; the systemd unit also caps
  the service at 2 CPUs (`CPUQuota=200%`).
* **INT8:** with ONNX Runtime, quantization gains 0-15 % on this Cortex-A72
  (no int8 dot-product instructions) and was *slower* for SSDLite at 4
  threads, so FP32 ONNX is the default. LiteRT's XNNPACK INT8 kernels are a
  different story (2.6x faster than ONNX Runtime FP32 on the same model):
  the fastest deployable form is a LiteRT INT8 export
  (`export.py --tflite-int8`, needs TensorFlow on the training machine).
  The runtime handles both; pick per model after measuring accuracy.
* The model is not the bottleneck: the full-sensor still (~0.75 s) is. If
  Stage B accuracy allows, `camera.still_size: null` crops from the
  1296x972 main stream instead and saves it.
