"""Fine-tunes a YOLO nano detector on the dataset build_dataset.py wrote,
then scores it on the held-out test split.

    python train.py --data datasets/vert-plant/data.yaml --name plant-v1

320 px input is the sweet spot for a Raspberry Pi 4: roughly 4x cheaper
than 640 and still enough detail for a leaf filling a decent part of
frame. If your camera sits far from the plants and lesions are tiny,
try --imgsz 416 and check bench.py timings on the Pi.
"""
from __future__ import annotations

import argparse

from ultralytics import YOLO


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True)
    p.add_argument("--name", required=True, help="run name, e.g. plant-v1")
    p.add_argument("--weights", default="yolo11n.pt", help="COCO-pretrained starting point (downloaded automatically)")
    p.add_argument("--imgsz", type=int, default=320)
    p.add_argument("--epochs", type=int, default=150)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--device", default=None, help="0 for the first GPU, cpu to force CPU")
    args = p.parse_args()

    model = YOLO(args.weights)
    model.train(
        data=args.data,
        imgsz=args.imgsz,
        epochs=args.epochs,
        batch=args.batch,
        device=args.device,
        project="runs",
        name=args.name,
        patience=30,        # stop once val mAP hasn't improved for 30 epochs
        # Augmentations matched to a fixed greenhouse camera: lighting and
        # colour vary a lot over a day, geometry barely does. Keep hue
        # shifts small -- yellow vs green IS the signal for chlorosis.
        hsv_h=0.005,
        hsv_s=0.5,
        hsv_v=0.4,
        degrees=10,
        fliplr=0.5,
        flipud=0.0,
        mosaic=1.0,
        close_mosaic=15,
        seed=0,
        plots=True,
    )

    # Ultralytics appends a number if runs/<name> already exists, so ask
    # the trainer where it actually saved rather than guessing.
    best_path = model.trainer.best
    best = YOLO(str(best_path))
    metrics = best.val(data=args.data, split="test", imgsz=args.imgsz, plots=True,
                       project="runs", name=f"{args.name}-test")
    print("\n=== held-out test split ===")
    print(f"mAP50     {metrics.box.map50:.3f}")
    print(f"mAP50-95  {metrics.box.map:.3f}")
    # ap50 only has entries for classes present in the test split, in
    # ap_class_index order -- not indexed by class id.
    for ap, class_id in zip(metrics.box.ap50, metrics.box.ap_class_index):
        print(f"  {best.names[int(class_id)]:20s} AP50 {ap:.3f}")
    missing = set(best.names) - {int(c) for c in metrics.box.ap_class_index}
    if missing:
        print(f"  (no test examples of: {[best.names[i] for i in sorted(missing)]} -- label more)")
    print(f"\nconfusion matrix + PR curves: {metrics.save_dir}  -- look at them before shipping")
    print(f"next: python export.py --weights {best_path} --data {args.data} --version {args.name}")


if __name__ == "__main__":
    main()
