"""Fine-tune a Stage B crop classifier (leaf symptoms, fruit ripeness/disease, flowers).

    python train_classifier.py --data datasets/leaf --group stage_b_leaf --name strawberry-leaf-v1
    python train_classifier.py ... --arch mobilenet_v3_large      # slower on the Pi, sometimes more accurate

Expects datasets/<name>/{train,val,test}/<label>/*.jpg (see vertml/data.py);
folder names must be labels of the crop profile's `labels.<group>`.
Writes runs/<name>/best.pt and runs/<name>/report.json (per-class
precision/recall on the held-out test split). Then: python export.py.

Augmentation is tuned for a fixed greenhouse camera: brightness,
contrast and saturation vary a lot over a day, so they're randomised
strongly; hue is barely touched, because hue (yellowing, purple lesions,
red ripeness) is the signal itself.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, WeightedRandomSampler
from torchvision import datasets, transforms

from vertml.data import profile_labels
from vertml.models import CLASSIFIERS, IMAGENET_MEAN, IMAGENET_STD, build_classifier
from vertml.metrics import per_class_report


def loaders(root: Path, size: int, batch: int, workers: int) -> tuple[dict[str, DataLoader], list[str]]:
    train_tf = transforms.Compose([
        transforms.RandomResizedCrop(size, scale=(0.6, 1.0), ratio=(0.75, 1.33)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomVerticalFlip(),
        transforms.RandomRotation(15),
        transforms.ColorJitter(brightness=0.4, contrast=0.3, saturation=0.3, hue=0.02),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    eval_tf = transforms.Compose([
        transforms.Resize((size, size)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    sets = {s: datasets.ImageFolder(str(root / s), train_tf if s == "train" else eval_tf)
            for s in ("train", "val", "test") if (root / s).is_dir()}
    classes = sets["train"].classes
    for s, ds in sets.items():
        if ds.classes != classes:
            raise SystemExit(f"{s} split has classes {ds.classes}, train has {classes}: give every split every class")
    # Rare classes (real disease) are the ones that matter: balance the sampling.
    counts = torch.bincount(torch.tensor(sets["train"].targets), minlength=len(classes)).float()
    weights = (1.0 / counts.clamp(min=1))[sets["train"].targets]
    out = {"train": DataLoader(sets["train"], batch, sampler=WeightedRandomSampler(weights, len(weights)),
                               num_workers=workers)}
    for s in ("val", "test"):
        if s in sets:
            out[s] = DataLoader(sets[s], batch, shuffle=False, num_workers=workers)
    return out, classes


@torch.no_grad()
def predict(model: nn.Module, loader: DataLoader, device: str) -> tuple[list[int], list[int]]:
    model.eval()
    y, p = [], []
    for x, t in loader:
        p += model(x.to(device)).argmax(1).cpu().tolist()
        y += t.tolist()
    return y, p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--group", required=True, help="profile label group, e.g. stage_b_leaf")
    ap.add_argument("--name", required=True)
    ap.add_argument("--arch", default="mobilenet_v3_small", choices=CLASSIFIERS)
    ap.add_argument("--size", type=int, default=224)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--patience", type=int, default=8)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--no-pretrained", action="store_true", help="random init (tests; never for real training)")
    ap.add_argument("--profile", default="strawberry")
    args = ap.parse_args()

    allowed = profile_labels(args.profile).get(args.group)
    if allowed is None:
        raise SystemExit(f"profile {args.profile} has no labels.{args.group}")
    dl, classes = loaders(args.data, args.size, args.batch, args.workers)
    unknown = set(classes) - set(allowed)
    if unknown:
        raise SystemExit(f"dataset classes {sorted(unknown)} are not in profile labels.{args.group}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_classifier(args.arch, len(classes), pretrained=not args.no_pretrained).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    run = Path("runs") / args.name
    run.mkdir(parents=True, exist_ok=True)

    best, best_epoch = -1.0, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, total = time.time(), 0.0
        for x, t in dl["train"]:
            opt.zero_grad()
            loss = loss_fn(model(x.to(device)), t.to(device))
            loss.backward()
            opt.step()
            total += loss.item() * len(t)
        sched.step()
        score = per_class_report(*predict(model, dl.get("val", dl["train"]), device), classes)["macro_f1"]
        print(f"epoch {epoch:3d}  loss {total / len(dl['train'].dataset):.3f}  val macro-F1 {score:.3f}  "
              f"({time.time() - t0:.0f}s)")
        if score > best:
            best, best_epoch = score, epoch
            torch.save({"kind": "classifier", "arch": args.arch, "size": args.size, "labels": classes,
                        "group": args.group, "profile": args.profile, "state_dict": model.state_dict(),
                        "data": str(args.data)}, run / "best.pt")
        elif epoch - best_epoch >= args.patience:
            print(f"no val improvement for {args.patience} epochs; stopping")
            break

    ckpt = torch.load(run / "best.pt", weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    split = "test" if "test" in dl else "val" if "val" in dl else "train"
    report = per_class_report(*predict(model, dl[split], device), classes)
    report.update({"split": split, "best_epoch": best_epoch, "arch": args.arch})
    (run / "report.json").write_text(json.dumps(report, indent=2))
    print(f"\n=== {split} split ===  accuracy {report['accuracy']:.3f}  macro-F1 {report['macro_f1']:.3f}")
    for c, r in report["classes"].items():
        print(f"  {c:32s} P {r['precision']:.3f}  R {r['recall']:.3f}  n {r['support']}")
    print(f"\nnext: python export.py --checkpoint {run / 'best.pt'} --version v1")


if __name__ == "__main__":
    main()
