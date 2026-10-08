# Training the plant-health model

The full loop from photos to a model running on the Pi. Each step is
one command. Steps 1 to 7 run on a training machine (Linux, WSL2 or Google
Colab with a GPU). A Pi 4 is far too slow to train on.

```
 photos ──► label ──► build dataset ──► train ──► check ──► export INT8 ──► deploy to Pi
   ▲                                                                            │
   └────────────── Pi saves new raw photos to /var/lib/plant-cam/dataset ◄──────┘
```

The model is **YOLO11n**, the smallest Ultralytics detector at about 2.6M
parameters. It is fine-tuned from COCO-pretrained weights, trained at
320×320, and exported to INT8 TFLite. On a Pi 4 it takes roughly 150–400 ms
per analysis. That's plenty, since the Pi analyses once every 30 s.

The classes are defined once, in [`classes.yaml`](classes.yaml):
`healthy_leaf`, `chlorosis` (yellowing), `leaf_spot_blight`, `mildew_mold`
and `pest`.

---

## Step 0: Set up the training machine

```bash
cd edge/plant-cam/training
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -c "import torch; print('GPU:', torch.cuda.is_available())"
```

No GPU? Use **Google Colab** or **Kaggle**. Upload
[`vert_training.ipynb`](vert_training.ipynb) (File → Upload notebook),
switch the runtime to a T4 GPU, and choose Run all. The notebook embeds
every script in this folder and runs Steps 1 and 4–7 below. If you edit
any script or `classes.yaml`, regenerate it with `python make_notebook.py`.
A CPU works too, but expect hours per run instead of minutes.

## Step 1: Get a starter dataset (public data)

You need something before your own photos are labelled. **PlantDoc**
is a good fit because its photos were taken in real fields rather than a
lab, and it has bounding boxes.

```bash
git clone https://github.com/pratikkayal/PlantDoc-Object-Detection-Dataset PlantDoc
python import_voc.py --images PlantDoc/TRAIN --annotations PlantDoc/TRAIN --mapping plantdoc --out sources/plantdoc
python import_voc.py --images PlantDoc/TEST  --annotations PlantDoc/TEST  --mapping plantdoc --out sources/plantdoc-test
```

**Read the summary it prints.** It shows which PlantDoc class went into
which VERT class (for example "Tomato leaf yellow virus → chlorosis"). If a
mapping is wrong for your crops, edit `_PLANTDOC_RULES` in `import_voc.py`.

PlantDoc has no insects. For the `pest` class, add an insect detection
set in VOC format, such as the detection subset of IP102:

```bash
python import_voc.py --images IP102/JPEGImages --annotations IP102/Annotations --mapping pest --out sources/ip102
```

Roboflow Universe also has many small greenhouse-pest sets (whitefly,
aphid, thrips). Export them as "Pascal VOC" and import them the same way.

### More public data from Kaggle

Two more Kaggle datasets are supported. The notebook downloads and imports
both automatically; each one can be switched off in its settings cell.

```bash
kaggle datasets download usmanafzaal/strawberry-disease-detection-dataset -p raw/strawberry --unzip
python import_labelme.py --root raw/strawberry --mapping strawberry --out sources/strawberry

kaggle datasets download lavaman151/plantifydr-dataset -p raw/plantifydr --unzip
python import_classified.py --root raw/plantifydr --out sources/plantifydr --per-class 400
```

- **Strawberry Disease Detection** (about 2,500 real-field photos, LabelMe outlines). Each outline traces a single lesion, but VERT boxes whole leaves, so nearby lesions of the same disease are merged into one box. The fruit and flower diseases are mapped to the closest VERT class; edit `STRAWBERRY_MAP` to change that. One caveat: healthy leaves in these photos aren't labelled, so the model sees them as background. If `healthy_leaf` scores drop after adding this set, that's the cause.
- **PlantifyDr** (about 125k photos sorted into one folder per disease, with no boxes). The box is drawn automatically around the leaf, and the notebook shows a sample so you can check. Only `--per-class` photos per VERT class are kept, because these are close-ups of single leaves on plain backgrounds. The source is marked **train-only**: it ships pre-augmented copies, so it is never used to score the model.

> **Be realistic about public data.** It gets you a model that runs and
> roughly works. Your own photos are what make it accurate. Public photos
> differ from your greenhouse in crop, lighting, camera and distance.

## Step 2: Collect your own photos

There are two sources, and the second is better:

```bash
# Backlog the XIAO camera already uploaded (MinIO must be running):
python pull_frames.py --out unlabelled/ --device xiao-esp-32-s3 --every 10

# Photos from the Pi itself (same camera and angle the model will see).
# plant-cam saves one every 10 minutes by default:
scp -r pi@<pi-ip>:/var/lib/plant-cam/dataset/ unlabelled/
```

What to aim for:
- **At least 150–300 boxes per class** for a usable first model, and 1,000+ for a good one.
- Photos across the whole day (morning, noon, evening, cloudy), because lighting changes more than anything else.
- When a plant does get sick, **photograph it deliberately**: closer, from several angles, every day as it develops. Real disease examples are the rarest and most valuable data you'll have.
- Keep some photos with no problems in them. The model needs to learn what "nothing wrong" looks like too.

## Step 3: Label

Use **Label Studio**. It's free and runs locally:

```bash
pip install label-studio
label-studio start            # opens http://localhost:8080 -- stop VERT's gateway first, or use --port 8090
```

1. Create a project. Under Settings → Labeling Interface → Code, paste the output of `python labelstudio_config.py`.
2. Import the images from `unlabelled/`.
3. Draw a box around **each leaf** you can judge, labelled `healthy_leaf` or with its problem. Draw a box around each pest, or each patch of pest damage. Skip leaves that are blurry or mostly hidden.
4. Be consistent. Decide on rules once (for example: "a leaf more than 30% yellow is chlorosis") and write them down. Inconsistent labels hurt more than having fewer labels.
5. Export in **YOLO** format and unzip it into `sources/own/`. It should contain `images/`, `labels/` and `classes.txt`.

To speed this up after the first model exists: in Label Studio, import the
model's predictions as pre-annotations and only fix what's wrong. This is
usually 3–5× faster than labelling from scratch.

## Step 4: Build the dataset

```bash
python build_dataset.py --sources sources --out datasets/vert-plant
```

This merges every folder under `sources/` and remaps class ids by name.
When `sources/own` exists, **validation and test images come only from
your own photos**, so the score you see reflects your greenhouse rather
than PlantDoc. Your training photos are also repeated 2× so they aren't
drowned out by thousands of public ones. It warns you about any class
with too few examples.

## Step 5: Train

```bash
python train.py --data datasets/vert-plant/data.yaml --name plant-v1
```

This takes about 20–60 min on a T4 GPU. Training stops on its own once
validation accuracy stops improving (`patience=30`). The augmentations are
tuned for a fixed greenhouse camera: strong brightness and saturation
changes (lighting varies), but only tiny hue shifts, because green versus
yellow is exactly what the model has to tell apart.

## Step 6: Check it before you ship it

`train.py` finishes by scoring the **held-out test split** and printing
per-class AP50. As a rough guide:

| AP50 on your own test photos | Meaning |
|---|---|
| > 0.7 | Good. Ship it. |
| 0.5–0.7 | Usable, since the Pi smooths readings over 5 analyses. Keep labelling. |
| < 0.5 | That class needs more or cleaner labels before it can be trusted. |

Also open the run's `confusion_matrix.png`. The most common problem is
`chlorosis` being confused with `healthy_leaf` in warm evening light. The
fix is more evening photos, not more epochs.

Look at real predictions too, not just numbers:

```bash
yolo predict model=runs/plant-v1/weights/best.pt source=datasets/vert-plant/images/test imgsz=320
```

## Step 7: Export to TFLite INT8

```bash
python export.py --weights runs/plant-v1/weights/best.pt --data datasets/vert-plant/data.yaml --version plant-v1
```

This creates `exported/plant-v1-<date>/` containing `model.tflite` (about
3 MB) and `meta.json`. `meta.json` holds the class list, weights and
grower-facing notes copied from `classes.yaml`. The Pi reads those to turn
detections into a health score, so **retraining never needs a code change
on the Pi.**

INT8 usually costs about 1–2 points of mAP compared with the full model.
If a class gets noticeably worse, export with `--precision fp16` instead.
It's about 2× slower on the Pi but still well within budget.

## Step 8: Deploy to the Pi

```bash
scp -r exported/plant-v1-20261002 pi@<pi-ip>:~/
ssh pi@<pi-ip>

# Benchmark on real photos first: shows latency and what it detects
/opt/plant-cam/venv/bin/python -m plant_cam.bench \
    --model-dir ~/plant-v1-20261002 --images /var/lib/plant-cam/dataset --out ~/bench-out

# Switch to it (atomic; old versions are kept)
sudo /opt/plant-cam/deploy/deploy-model.sh ~/plant-v1-20261002
journalctl -u plant-cam -n 20      # should say: model plant-v1-20261002 loaded
```

**Rollback:** run `deploy-model.sh` again with the previous version's
folder in `/opt/plant-cam/models/`. Every reading carries
`model_version`, so you can compare models on the dashboard charts.

## Step 9: Repeat

Every few weeks:
1. Pull new photos from the Pi (Step 2), especially from days when the dashboard flagged something.
2. Label the ones where the model was wrong. These teach it the most.
3. Rebuild, retrain as `plant-v2`, compare its test score against v1 on the **same** test split, and ship it only if it's better.
