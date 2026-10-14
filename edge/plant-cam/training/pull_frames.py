"""Downloads photos VERT cameras have already uploaded (MinIO bucket
`vert-frames`), to label for training. The XIAO camera has been storing a
photo every minute; that backlog is real greenhouse data.

    python pull_frames.py --out unlabelled/ --device xiao-esp-32-s3 --every 10

Photos from the Pi itself are better still (same camera and angle the
model will see) -- copy those with:

    ssh pi@<pi-ip> /opt/plant-cam/venv/bin/python -m plant_cam export-al --config /etc/plant-cam/config.yaml --out ~/al
    scp -r pi@<pi-ip>:al/ unlabelled/
"""
from __future__ import annotations

import argparse
from pathlib import Path

from minio import Minio


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--endpoint", default="localhost:9000")
    p.add_argument("--access-key", default="vert_minio_admin")
    p.add_argument("--secret-key", default="vert_minio_dev_password")
    p.add_argument("--bucket", default="vert-frames")
    p.add_argument("--device", help="only this device's photos")
    p.add_argument("--every", type=int, default=1,
                   help="keep 1 in N photos -- consecutive minutes are near-duplicates, not new information")
    args = p.parse_args()

    client = Minio(args.endpoint, access_key=args.access_key, secret_key=args.secret_key, secure=False)
    args.out.mkdir(parents=True, exist_ok=True)
    prefix = f"{args.device}/" if args.device else ""
    objects = sorted(client.list_objects(args.bucket, prefix=prefix, recursive=True), key=lambda o: o.last_modified)

    kept = 0
    for i, obj in enumerate(objects):
        if i % args.every:
            continue
        stamp = obj.last_modified.strftime("%Y%m%d-%H%M%S")
        target = args.out / f"{stamp}_{obj.object_name.replace('/', '_')}"
        if not target.exists():
            client.fget_object(args.bucket, obj.object_name, str(target))
        kept += 1
    print(f"{kept} photos in {args.out} (of {len(objects)} stored)")


if __name__ == "__main__":
    main()
