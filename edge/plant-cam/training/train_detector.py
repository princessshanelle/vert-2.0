"""Fine-tune the Stage A detector (SSDLite320-MobileNetV3, torchvision, BSD-3).

    python train_detector.py --data datasets/objects --name strawberry-detector-v1

Expects the detection layout in vertml/data.py; labels must be the
profile's labels.stage_a (leaf, flower, fruit, runner). Starts from
COCO-pretrained weights (backbone + box regressor) and replaces the class
head. Writes runs/<name>/best.pt and report.json with per-class AP50 and
precision/recall at the Pi's score threshold, on the held-out test split.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision.transforms import functional as F

from vertml.data import profile_labels, read_detection_split
from vertml.metrics import detection_report
from vertml.models import build_detector


class BoxDataset(Dataset):
    def __init__(self, root: Path, split: str, labels: list[str], train: bool) -> None:
        file_labels, self.items = read_detection_split(root, split)
        if file_labels != labels:
            raise SystemExit(f"{root}/{split} labels {file_labels} != expected {labels}")
        self.dir = root / split / "images"
        self.labels = labels
        self.train = train

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, i: int) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        it = self.items[i]
        img = Image.open(self.dir / it["file"]).convert("RGB")
        w, h = img.size
        boxes = torch.tensor([[b[0] * w, b[1] * h, b[2] * w, b[3] * h] for b in it["boxes"]],
                             dtype=torch.float32).reshape(-1, 4)
        labels = torch.tensor([self.labels.index(c) + 1 for c in it["labels"]], dtype=torch.int64)
        x = F.to_tensor(img)
        if self.train:
            if random.random() < 0.5:
                x = x.flip(-1)
                boxes[:, [0, 2]] = w - boxes[:, [2, 0]]
            x = F.adjust_brightness(x, random.uniform(0.6, 1.4))
            x = F.adjust_contrast(x, random.uniform(0.7, 1.3))
            x = F.adjust_saturation(x, random.uniform(0.7, 1.3))
            x = F.adjust_hue(x, random.uniform(-0.02, 0.02))
        return x, {"boxes": boxes, "labels": labels}


def collate(batch: list[Any]) -> tuple[list[torch.Tensor], list[dict[str, torch.Tensor]]]:
    return [b[0] for b in batch], [b[1] for b in batch]


@torch.no_grad()
def evaluate(model: torch.nn.Module, ds: BoxDataset, device: str) -> dict[str, Any]:
    model.eval()
    preds, gts = [], []
    for i in range(len(ds)):
        x, t = ds[i]
        h, w = x.shape[1:]
        out = model([x.to(device)])[0]
        scale = torch.tensor([w, h, w, h], dtype=torch.float32)
        preds.append([(int(c) - 1, float(s), (b.cpu() / scale).tolist())
                      for b, s, c in zip(out["boxes"], out["scores"], out["labels"], strict=True)])
        gts.append([(int(c) - 1, (b / scale).tolist()) for b, c in zip(t["boxes"], t["labels"], strict=True)])
    return detection_report(preds, gts, ds.labels)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--size", type=int, default=320)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--no-pretrained", action="store_true", help="random init (tests; never for real training)")
    ap.add_argument("--profile", default="strawberry")
    args = ap.parse_args()

    labels = profile_labels(args.profile)["stage_a"]
    train = BoxDataset(args.data, "train", labels, train=True)
    val_split = "val" if (args.data / "val" / "annotations.json").exists() else "train"
    val = BoxDataset(args.data, val_split, labels, train=False)
    # drop_last when the remainder would be a single image: SSDLite's 1x1
    # extra layers can't batch-normalise one sample.
    dl = DataLoader(train, args.batch, shuffle=True, collate_fn=collate, num_workers=args.workers,
                    drop_last=len(train) % args.batch == 1)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_detector(len(labels), pretrained=not args.no_pretrained, size=args.size).to(device)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    run = Path("runs") / args.name
    run.mkdir(parents=True, exist_ok=True)

    best, best_epoch = -1.0, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, total, n = time.time(), 0.0, 0
        for xs, ts in dl:
            losses = model([x.to(device) for x in xs], [{k: v.to(device) for k, v in t.items()} for t in ts])
            loss = sum(losses.values())
            opt.zero_grad()
            loss.backward()
            opt.step()
            total, n = total + float(loss), n + 1
        sched.step()
        score = evaluate(model, val, device)["map50"]
        print(f"epoch {epoch:3d}  loss {total / max(n, 1):.3f}  {val_split} mAP50 {score:.3f}  ({time.time() - t0:.0f}s)")
        if score > best or best < 0:
            best, best_epoch = score, epoch
            torch.save({"kind": "detector", "size": args.size, "labels": labels, "profile": args.profile,
                        "state_dict": model.state_dict(), "data": str(args.data)}, run / "best.pt")
        elif epoch - best_epoch >= args.patience:
            print(f"no improvement for {args.patience} epochs; stopping")
            break

    model.load_state_dict(torch.load(run / "best.pt", weights_only=False)["state_dict"])
    split = "test" if (args.data / "test" / "annotations.json").exists() else val_split
    report = evaluate(model, BoxDataset(args.data, split, labels, train=False), device)
    report.update({"split": split, "best_epoch": best_epoch})
    (run / "report.json").write_text(json.dumps(report, indent=2))
    print(f"\n=== {split} split ===  mAP50 {report['map50']:.3f}")
    for c, r in report["classes"].items():
        print(f"  {c:10s} AP50 {r['ap50']:.3f}  P {r['precision']:.3f}  R {r['recall']:.3f}  n {r['support']}")
    print(f"\nnext: python export.py --checkpoint {run / 'best.pt'} --version v1")


if __name__ == "__main__":
    main()
