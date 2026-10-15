#!/usr/bin/env bash
# Installs a trained model package on the Pi and switches to it atomically.
# Run on the Pi with the folder training/export.py produced:
#
#   sudo /opt/plant-cam/deploy/deploy-model.sh ~/strawberry-leaf-v1-20261020
#
# The package's meta.json names its slot (model_id prefix up to the last
# "-v<N>", e.g. strawberry-leaf), so it lands in
#   /opt/plant-cam/models/<slot>/<version>/   with   <slot>/current -> <version>
# which is what the crop profile's analyzers load. Rolling back is the same
# command with the previous version's folder (old versions are kept).
set -euo pipefail

SRC="${1:?usage: deploy-model.sh <model folder containing meta.json + model file>}"
MODELS=/opt/plant-cam/models

[[ -f "$SRC/meta.json" ]] || { echo "missing $SRC/meta.json" >&2; exit 1; }
read -r SLOT VERSION FILE < <(python3 - "$SRC/meta.json" <<'EOF'
import json, re, sys
m = json.load(open(sys.argv[1]))
slot = m.get("slot") or re.sub(r"-v\d+.*$", "", m["model_id"])
print(slot, m["model_version"], m.get("file", "model.onnx"))
EOF
)
[[ -f "$SRC/$FILE" ]] || { echo "missing $SRC/$FILE (named in meta.json)" >&2; exit 1; }

# Refuse a model the service can't load, before switching to it. The app
# lives in /opt/plant-cam/app (the service's working directory), not in the
# venv, so point Python at it: this script may be run from any directory.
PYTHONPATH=/opt/plant-cam/app /opt/plant-cam/venv/bin/python - "$SRC" <<'EOF'
import sys
from pathlib import Path
from plant_cam.runtime import Model
m = Model(Path(sys.argv[1]), threads=1)
print(f"ok: {m.meta.model_id}@{m.meta.model_version}, {len(m.meta.labels)} labels, backend {m.backend.name}")
EOF

DEST="$MODELS/$SLOT/$VERSION"
mkdir -p "$DEST"
rsync -a "$SRC/" "$DEST/"
chown -R plantcam:plantcam "$MODELS/$SLOT"
ln -sfn "$VERSION" "$MODELS/$SLOT/current.tmp"
mv -T "$MODELS/$SLOT/current.tmp" "$MODELS/$SLOT/current"
systemctl restart plant-cam
echo "now running $SLOT/$VERSION -- check: journalctl -u plant-cam -n 30 | grep 'model loaded'"
