"""Build the self-contained Colab notebook (colab/vert_strawberry_colab.ipynb).

    python colab/make_notebook.py            # from edge/plant-cam/training, after changing training code

The notebook embeds this training folder and the strawberry crop profile as
a base64 tarball, so it needs no repo access: upload it to Colab, pick a GPU
runtime, Run all. tests/test_colab_notebook.py fails when the embedded copy
is older than the code here -- rerun this script and commit the notebook.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import tarfile
from pathlib import Path

TRAINING = Path(__file__).resolve().parents[1]
PROFILES = TRAINING.parent / "profiles"
NOTEBOOK = TRAINING / "colab" / "vert_strawberry_colab.ipynb"


def bundle_files() -> dict[str, bytes]:
    """archive name -> content: the training code and the strawberry profile."""
    files = {}
    for p in sorted([*TRAINING.glob("*.py"), *TRAINING.glob("vertml/*.py"), TRAINING / "requirements.txt",
                     TRAINING / "README.md", TRAINING / "DATASETS.md"]):
        files[f"training/{p.relative_to(TRAINING).as_posix()}"] = p.read_bytes()
    files["profiles/strawberry/profile.yaml"] = (PROFILES / "strawberry" / "profile.yaml").read_bytes()
    return files


def bundle() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in bundle_files().items():  # fixed metadata: same files -> same bytes
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(gzip.compress(buf.getvalue(), compresslevel=9, mtime=0)).decode()


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


def cells(payload: str) -> list[dict]:
    return [
        md("""
# VERT: train the strawberry leaf-health model (Colab)

This notebook trains the plant-cam **leaf classifier** (`strawberry-leaf` slot) on the
[PlantifyDr dataset](https://www.kaggle.com/datasets/lavaman151/plantifydr-dataset) and packages it
for the Raspberry Pi. You don't need to edit anything to get a model.

**How to run it**
1. **Runtime > Change runtime type > T4 GPU** (any GPU works; CPU is very slow), then **Save**.
2. **Runtime > Run all**. It takes about 15 to 30 minutes: roughly 5 to download, 5 to prepare the data and 5 to 15 to train.
3. At the end your browser downloads `strawberry-leaf-<version>.zip`. Section 9 explains how to install it on the Pi.

No Kaggle login is needed for this public dataset. If the download asks for credentials,
add `KAGGLE_USERNAME` and `KAGGLE_KEY` under **Secrets** (the key icon on the left), with notebook access switched on.

**What the model can and can't do.** PlantifyDr's strawberry photos are PlantVillage-style single leaves on a plain
background, in two classes: **healthy** and **leaf scorch**. The model learns exactly that. Expect a near-perfect test score:
it measures these photos, not your greenhouse. Treat the first model as a starting point, and retrain it on labelled
photos from your own camera (the Pi collects them automatically: see `training/README.md`, steps 1 to 4).
"""),
        md("## 1. Settings\nThe defaults are the recommended ones."),
        code("""
ARCH = "efficientnet_b0"   # efficientnet_b0 (most accurate) | mobilenet_v3_large | mobilenet_v3_small (fastest on the Pi)
EPOCHS = 30                # training stops earlier when validation stops improving
BATCH = 64
VERSION = "v1"             # becomes part of the model folder name, e.g. strawberry-leaf-v1-20261014
TFLITE_INT8 = True         # also try a LiteRT INT8 model (~2.6x faster on a Pi 4); optional, skipped if conversion fails
INCLUDE_AFZAAL = False     # also add Afzaal et al.'s field photos of 3 more leaf diseases; read section 4 before enabling
SAVE_TO_DRIVE = False      # also copy the result to Google Drive (MyDrive/vert-models)
"""),
        md("## 2. Check the GPU and unpack the VERT training code\nThe training code is embedded in this notebook, so no repository access is needed."),
        code(f"""
import base64, io, os, subprocess, sys, tarfile
from pathlib import Path

import torch
print("torch", torch.__version__, "| GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE")
if not torch.cuda.is_available():
    print("\\n*** No GPU. Runtime > Change runtime type > T4 GPU, then Runtime > Run all. (CPU works but takes hours.) ***")

WORK = Path("/content/vert") if Path("/content").exists() else Path.cwd() / "vert"
BUNDLE = "{payload}"
WORK.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(BUNDLE)), mode="r:gz") as tar:
    tar.extractall(WORK, filter="data")
TRAIN = WORK / "training"
os.environ["VERT_PROFILES_DIR"] = str(WORK / "profiles")
os.chdir(TRAIN)
DATA = WORK / "data"


def run(*args):
    \"\"\"Run a training script, streaming its output; stop the notebook if it fails.\"\"\"
    print("$", " ".join(map(str, args)))
    proc = subprocess.Popen([sys.executable, *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=TRAIN)
    for line in proc.stdout:
        print(line, end="")
    if proc.wait():
        raise RuntimeError(f"{{args[0]}} failed (exit {{proc.returncode}}): see the output above")


print("training code in", TRAIN)
"""),
        md("## 3. Install the extra packages\nColab already has PyTorch; this adds ONNX export and the Kaggle downloader."),
        code("""
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "onnx>=1.15", "onnxruntime>=1.16", "onnxscript",
                "kagglehub", "pyyaml", "pillow"], check=True)
print("ok")
"""),
        md("""
## 4. Download and prepare the data

The importer keeps only the strawberry folders and maps them to plant-cam's labels (`healthy_leaf`,
`leaf_scorch`). It then re-splits the photos 80/10/10 into train, validation and test. PlantifyDr ships
**augmented copies** of each photo (flipped, rotated), so the importer groups copies of the same leaf
with a perceptual hash and keeps each group in one split. Otherwise the test score would partly measure memorisation.

**About `INCLUDE_AFZAAL`.** The [Afzaal et al. dataset](https://www.kaggle.com/datasets/usmanafzaal/strawberry-disease-detection-dataset)
adds real field photos of powdery mildew, common leaf spot and angular leaf spot. But its photos come from the field
while PlantifyDr's are lab shots, so a mixed model can learn "field photo = diseased" and raise false alarms on healthy
greenhouse leaves. Enable it only if you will check the results on your own photos. Its licence is unclear: see `DATASETS.md`.
"""),
        code("""
import kagglehub

src = Path(kagglehub.dataset_download("lavaman151/plantifydr-dataset"))
print("downloaded to", src)
run("import_public.py", "plantifydr", "--root", src, "--out", DATA)

if INCLUDE_AFZAAL:
    afz = Path(kagglehub.dataset_download("usmanafzaal/strawberry-disease-detection-dataset"))
    run("import_public.py", "afzaal", "--root", afz, "--out", WORK / "afzaal")
    # Only its leaf diseases; the fruit/flower crops would need their own models.
    import shutil
    for split_dir in (WORK / "afzaal" / "stage_b_leaf").glob("*"):
        for label_dir in split_dir.glob("*"):
            shutil.copytree(label_dir, DATA / "stage_b_leaf" / split_dir.name / label_dir.name, dirs_exist_ok=True)
"""),
        md("### Look at the data\nCheck that the counts are what you expect and that the samples look like strawberry leaves."),
        code("""
import random
import matplotlib.pyplot as plt
from PIL import Image

LEAF = DATA / "stage_b_leaf"
labels = sorted(d.name for d in (LEAF / "train").iterdir())
print(f"{'label':24s}" + "".join(f"{s:>8s}" for s in ("train", "val", "test")))
for label in labels:
    print(f"{label:24s}" + "".join(f"{len(list((LEAF / s / label).glob('*.jpg'))):8d}" for s in ("train", "val", "test")))

fig, axes = plt.subplots(len(labels), 6, figsize=(14, 2.5 * len(labels)), squeeze=False)
for row, label in zip(axes, labels):
    files = random.Random(0).sample(sorted((LEAF / "train" / label).glob("*.jpg")), 6)
    for ax, f in zip(row, files):
        ax.imshow(Image.open(f)); ax.set_title(label, fontsize=8); ax.axis("off")
plt.tight_layout(); plt.show()
"""),
        md("## 5. Train\nFine-tunes an ImageNet-pretrained network. Each epoch prints the validation macro-F1, and the best epoch is kept."),
        code("""
NAME = f"strawberry-leaf-{VERSION}"
run("train_classifier.py", "--data", LEAF, "--group", "stage_b_leaf", "--name", NAME, "--arch", ARCH,
    "--epochs", EPOCHS, "--batch", BATCH, "--workers", os.cpu_count() or 2)
"""),
        md("## 6. Results on the held-out test photos\nThe model never saw these photos during training."),
        code("""
import json
import numpy as np

RUN = TRAIN / "runs" / NAME
report = json.loads((RUN / "report.json").read_text())
history = json.loads((RUN / "history.json").read_text())
print(f"test accuracy {report['accuracy']:.3f}   macro-F1 {report['macro_f1']:.3f}   (best epoch {report['best_epoch']})\\n")
for c, r in report["classes"].items():
    print(f"  {c:24s} precision {r['precision']:.3f}  recall {r['recall']:.3f}  ({r['support']} photos)")

fig, (a, b) = plt.subplots(1, 2, figsize=(13, 4.5))
ep = [h["epoch"] for h in history]
a.plot(ep, [h["loss"] for h in history], label="train loss")
a.plot(ep, [h["val_macro_f1"] for h in history], label="val macro-F1")
a.set_xlabel("epoch"); a.legend(); a.set_title("training")
cm = np.array(report["confusion"]); names = report["labels"]
b.imshow(cm, cmap="Greens")
b.set_xticks(range(len(names)), names, rotation=30, ha="right"); b.set_yticks(range(len(names)), names)
for i in range(len(names)):
    for j in range(len(names)):
        b.text(j, i, cm[i, j], ha="center", va="center", color="white" if cm[i, j] > cm.max() / 2 else "black")
b.set_xlabel("predicted"); b.set_ylabel("true"); b.set_title("test confusion matrix")
plt.tight_layout(); plt.show()
"""),
        md("## 7. Export for the Pi\nThis writes `model.onnx` and `meta.json` (plant-cam's model contract), then checks that ONNX Runtime gives the same answers as PyTorch on test photos."),
        code("""
run("export.py", "--checkpoint", RUN / "best.pt", "--version", VERSION)
PKG = max((TRAIN / "exported").glob(f"strawberry-leaf-{VERSION}-*"), key=lambda p: p.stat().st_mtime)

# Parity: the exported ONNX model must agree with the trained PyTorch model.
import onnxruntime as ort
from vertml.models import build_classifier

meta = json.loads((PKG / "meta.json").read_text())
ckpt = torch.load(RUN / "best.pt", map_location="cpu", weights_only=False)
net = build_classifier(ckpt["arch"], len(ckpt["labels"]), pretrained=False); net.load_state_dict(ckpt["state_dict"]); net.eval()
sess = ort.InferenceSession(str(PKG / "model.onnx"), providers=["CPUExecutionProvider"])
size, mean, std = meta["input"]["size"], np.array(meta["input"]["mean"]), np.array(meta["input"]["std"])
test_files = sorted((LEAF / "test").rglob("*.jpg"))
test_files = random.Random(1).sample(test_files, min(64, len(test_files)))
x = np.stack([((np.asarray(Image.open(f).convert("RGB").resize(tuple(size)), np.float32) / 255 - mean) / std).transpose(2, 0, 1)
              for f in test_files]).astype(np.float32)
onnx_out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
with torch.no_grad():
    torch_out = net(torch.from_numpy(x)).numpy()
agree = (onnx_out.argmax(1) == torch_out.argmax(1)).mean()
print(f"ONNX vs PyTorch: {agree:.1%} same predictions, max logit difference {np.abs(onnx_out - torch_out).max():.2e}")
assert agree == 1.0, "exported model disagrees with the trained one"
"""),
        md("### Optional: LiteRT INT8 (faster on the Pi)\nThis runs only if `TFLITE_INT8 = True`. Both models are scored against the true labels on **every** test photo, and INT8 is selected only if its accuracy stays within 0.5 points of FP32. Otherwise the ONNX model stays selected."),
        code("""
def accuracy_on_test(predict_one):
    files = sorted((LEAF / "test").rglob("*.jpg"))
    labels = json.loads((PKG / "meta.json").read_text())["labels"]
    ok = 0
    for f in files:
        x1 = ((np.asarray(Image.open(f).convert("RGB").resize(tuple(size)), np.float32) / 255 - mean) / std).astype(np.float32)
        ok += labels[predict_one(x1)] == f.parent.name
    return ok / len(files), len(files)

TFLITE_OK = False
if TFLITE_INT8:
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "onnx2tf", "onnx_graphsurgeon", "sng4onnx",
                        "onnxsim", "tf_keras", "ai-edge-litert", "psutil"], check=True)
        run("export.py", "--checkpoint", RUN / "best.pt", "--version", VERSION, "--tflite-int8", LEAF / "val")
        PKG = max((TRAIN / "exported").glob(f"strawberry-leaf-{VERSION}-*"), key=lambda p: p.stat().st_mtime)
        from ai_edge_litert.interpreter import Interpreter
        it = Interpreter(model_path=str(PKG / "model_int8.tflite")); it.allocate_tensors()
        inp, out = it.get_input_details()[0], it.get_output_details()[0]

        def int8_predict(x1):  # NHWC, quantised as plant-cam's LiteRT backend does
            xi = x1[None]
            if inp["dtype"] in (np.int8, np.uint8):
                qs, qz = inp["quantization"]; info = np.iinfo(inp["dtype"])
                xi = np.clip(np.round(xi / qs + qz), info.min, info.max)
            it.set_tensor(inp["index"], xi.astype(inp["dtype"])); it.invoke()
            return int(it.get_tensor(out["index"])[0].argmax())

        def fp32_predict(x1):
            return int(sess.run(None, {sess.get_inputs()[0].name: x1.transpose(2, 0, 1)[None]})[0][0].argmax())

        acc32, n = accuracy_on_test(fp32_predict)
        acc8, _ = accuracy_on_test(int8_predict)
        print(f"test accuracy on all {n} photos:  ONNX FP32 {acc32:.2%}   LiteRT INT8 {acc8:.2%}")
        TFLITE_OK = acc8 >= acc32 - 0.005
        if not TFLITE_OK:
            print("INT8 loses too much accuracy: it stays in the folder but is NOT selected. The ONNX model will be used.")
    except Exception as e:
        print(f"LiteRT INT8 conversion skipped ({type(e).__name__}: {e}). The ONNX model is ready to use.")
if TFLITE_OK:
    os.replace(PKG / "meta.tflite.json", PKG / "meta.json")   # the Pi will load model_int8.tflite
    print("meta.json now selects model_int8.tflite (install ai-edge-litert on the Pi; see section 9)")
"""),
        md("## 8. Download the model package"),
        code("""
import shutil
zip_path = Path(shutil.make_archive(str(WORK / PKG.name), "zip", PKG.parent, PKG.name))
print("package:", zip_path.name)
for f in sorted(PKG.iterdir()):
    print(f"  {f.name:24s} {f.stat().st_size / 1e6:6.1f} MB")
print("\\nmodel:", json.loads((PKG / "meta.json").read_text())["file"])
if SAVE_TO_DRIVE:
    from google.colab import drive
    drive.mount("/content/drive")
    dest = Path("/content/drive/MyDrive/vert-models"); dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(zip_path, dest); print("copied to", dest)
try:
    from google.colab import files
    files.download(str(zip_path))
except ImportError:
    print("not on Colab: the zip is at", zip_path)
"""),
        md("""
## 9. Install it on the Raspberry Pi

Copy the zip to the Pi, unzip it and deploy it. Run these on your computer, replacing `<pi-ip>` and the file name:

```bash
scp ~/Downloads/strawberry-leaf-v1-YYYYMMDD.zip pi@<pi-ip>:~/
ssh pi@<pi-ip>
unzip strawberry-leaf-v1-YYYYMMDD.zip
# only if section 7b selected the INT8 model (meta.json says "model_int8.tflite"):
/opt/plant-cam/venv/bin/pip install ai-edge-litert
sudo /opt/plant-cam/deploy/deploy-model.sh ~/strawberry-leaf-v1-YYYYMMDD
journalctl -u plant-cam -n 30 | grep "model loaded"
```

`deploy-model.sh` switches models atomically and keeps the previous version. To roll back, run it again with the
older folder in `/opt/plant-cam/models/strawberry-leaf/`. Every reading carries `model_version`, so the dashboard
shows when the model changed.

**Next:** once the camera is pointed at the plants, the Pi saves one raw frame per hour plus the crops the model was unsure about.
Label a few hundred of them (`training/README.md`, steps 1 and 2), add them to `data/stage_b_leaf`, and retrain.
That is what makes the model accurate in *your* greenhouse.
"""),
    ]


def build() -> dict:
    return {
        "cells": cells(bundle()),
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": [], "gpuType": "T4"},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }


def embedded_bundle(nb: dict) -> str:
    for c in nb["cells"]:
        for line in c["source"]:
            if line.startswith('BUNDLE = "'):
                return line.strip()[len('BUNDLE = "'):-1]
    raise ValueError("notebook has no embedded bundle")


def main() -> None:
    NOTEBOOK.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {NOTEBOOK} ({NOTEBOOK.stat().st_size / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
