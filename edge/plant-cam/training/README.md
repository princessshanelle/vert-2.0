# Training the strawberry models

```
 Pi: active-learning samples + raw frames ──► label (Label Studio) ──► convert ──► train (GPU/Colab)
          ▲                                                                          │
          │      plant_cam evaluate (precision/recall on YOUR held-out photos)  ◄── export ──► deploy-model.sh
          └──────────────────────────── new model running on the Pi ◄────────────────────────────┘
```

Three kinds of model, all optional, all permissively licensed torchvision
architectures (no AGPL; see [DATASETS.md](DATASETS.md) for data licences):

| Slot (profile) | Stage | Architecture | Labels (`profiles/strawberry/profile.yaml`) |
|---|---|---|---|
| `strawberry-detector` | A: find objects | SSDLite320-MobileNetV3 | `labels.stage_a`: leaf, flower, fruit, runner |
| `strawberry-leaf` | B: leaf crops | MobileNetV3-Small | `labels.stage_b_leaf`: healthy_leaf, powdery_mildew_leaf, leaf_scorch, ... |
| `strawberry-fruit` | B: fruit crops | MobileNetV3-Small | `labels.stage_b_fruit`: fruit_green ... fruit_overripe, grey_mould_fruit, ... |
| `strawberry-flower` | B: flower crops | MobileNetV3-Small | `labels.stage_b_flower` |

Without Stage A, the leaf classifier classifies tiles of the canopy, so a
leaf model alone already enables disease findings.

## 0. Set up a training machine

A GPU (Colab/Kaggle T4 is enough) or a Linux PC. A Pi is fine for the
smoke test, far too slow for real training.

```bash
cd edge/plant-cam/training
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

On Colab, upload `edge/plant-cam/training` **and** `edge/plant-cam/profiles`,
then `export VERT_PROFILES_DIR=/content/profiles` if they aren't side by side.

## 1. Collect photos from your greenhouse

The Pi keeps, in `/var/lib/plant-cam/active-learning/` (2 GB cap):
* one **raw frame per hour** (no overlay, only in good light): label these for Stage A;
* crops the model was **unsure** about, crops behind **alerts**, and a small random sample, each with a JSON sidecar.

```bash
ssh pi@<pi> /opt/plant-cam/venv/bin/python -m plant_cam export-al --config /etc/plant-cam/config.yaml --out ~/al
scp -r pi@<pi>:al/ unlabelled/
```

Aim for a few hundred photos across the day and the season, and photograph
sick plants deliberately: real disease examples are the rarest and most
valuable data. Older XIAO-camera uploads: `python pull_frames.py`.

## 2. Label (Label Studio)

```bash
pip install label-studio && label-studio start --port 8090    # :8080 is VERT's gateway
python labelstudio.py config > labelling.xml                    # paste into Settings > Labeling Interface > Code
```

One box per object, one label each. A condition or ripeness label
implies its object (`powdery_mildew_leaf` is a leaf, `fruit_ripe` is a
fruit), so one pass trains both stages. Label healthy leaves
`healthy_leaf`. Write your rules down (e.g. "a leaf with >2 purple spots
is common_leaf_spot") and stick to them: inconsistent labels hurt more
than fewer labels. Active-learning crops can be imported with their
prediction as a pre-annotation: `python labelstudio.py tasks --al unlabelled/al > tasks.json`.

Export as JSON, then:

```bash
python labelstudio.py convert --export export.json --images unlabelled/ --out datasets/own
```

Photos are split train/val/test by photo (never crops of one photo across
splits). `datasets/own/eval/` holds the test photos and their ground
truth, which is what the Pi-side evaluation in step 5 uses.

## 3. (Optional) Pre-train on public data

Read [DATASETS.md](DATASETS.md) first; these are non-commercial or unclear.

```bash
python import_public.py afzaal --root raw/strawberry --out datasets/afzaal   # Kaggle usmanafzaal/strawberry-disease-detection-dataset
python import_public.py strawdi --root raw/StrawDI_Db1 --out datasets/strawdi
```

Merge class folders/annotations as you see fit, but always **validate and
test on your own photos**: public images differ in camera, light, distance
and variety, and a score on them says little about your greenhouse.

## 4. Train

```bash
python train_classifier.py --data datasets/own/stage_b_leaf  --group stage_b_leaf  --name strawberry-leaf-v1
python train_classifier.py --data datasets/own/stage_b_fruit --group stage_b_fruit --name strawberry-fruit-v1
python train_detector.py   --data datasets/own/objects --name strawberry-detector-v1
```

Each prints and saves (`runs/<name>/report.json`) per-class precision and
recall on the held-out test split. Rough guide: a class under ~30 labelled
examples, or with recall < 0.5, isn't ready to alert on. Get more examples
of it, starting with the active-learning crops where the model was wrong.

## 5. Export, check on the Pi, deploy

```bash
python export.py --checkpoint runs/strawberry-leaf-v1/best.pt --version v1
scp -r exported/strawberry-leaf-v1-20261020 pi@<pi>:~/
```

On the Pi:
```bash
# measured precision/recall on your held-out photos, with the new model
mkdir -p /tmp/m/strawberry-leaf && cp -r ~/strawberry-leaf-v1-20261020 /tmp/m/strawberry-leaf/current
PLANTCAM__MODELS_DIR=/tmp/m /opt/plant-cam/venv/bin/python -m plant_cam evaluate \
    --config /etc/plant-cam/config.yaml --labels ~/eval --json ~/eval-v1.json
# latency
/opt/plant-cam/venv/bin/python tools/bench_models.py ~/strawberry-leaf-v1-20261020/model.onnx --batch 8
# switch to it (atomic; previous versions stay for rollback)
sudo /opt/plant-cam/deploy/deploy-model.sh ~/strawberry-leaf-v1-20261020
journalctl -u plant-cam -n 30 | grep "model loaded"
```

Ship a new version only if it beats the current one on the same eval set.
Every reading carries `model_version`, so the dashboard charts show when
a model changed. Rollback = `deploy-model.sh` with the previous folder in
`/opt/plant-cam/models/<slot>/`.

### Faster: LiteRT INT8

On a Pi 4, LiteRT's INT8 kernels ran ~2.6x faster than ONNX Runtime FP32
on the same model (docs/BENCHMARKS.md). If the training machine has
TensorFlow (Colab does): `pip install onnx2tf onnx_graphsurgeon sng4onnx`, then

```bash
python export.py --checkpoint runs/strawberry-leaf-v1/best.pt --version v1 --tflite-int8 datasets/own/stage_b_leaf/val
cd exported/strawberry-leaf-v1-*/ && mv meta.tflite.json meta.json      # use the .tflite
```

and on the Pi: `/opt/plant-cam/venv/bin/pip install ai-edge-litert`.
Evaluate it as above: INT8 can cost accuracy on some classes.

> Status: plant-cam's LiteRT backend is tested on the Pi with a real INT8
> `.tflite` (uint8 NHWC input, XNNPACK). The `--tflite-int8` *conversion*
> step has **not** been run yet: TensorFlow wouldn't install on the Pi used
> for development. Run it once on Colab and check the result with
> `plant_cam evaluate` before relying on it.

## Tested

`tests/integration/test_retrain_path.py` runs this whole path (Label Studio
export -> convert -> train both stages -> export -> deploy -> plant-cam
loads and runs the models -> `plant_cam evaluate`) on synthetic photos,
every `make check`.
