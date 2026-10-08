#!/usr/bin/env bash
# Installs a trained model on the Pi and switches to it atomically.
# Run on the Pi, with the folder training/export.py produced copied over:
#
#   sudo /opt/plant-cam/deploy/deploy-model.sh ~/plant-v3-20261015
#
# Rolling back is the same command with the previous version's folder
# (old versions stay in /opt/plant-cam/models/).
set -euo pipefail

SRC="${1:?usage: deploy-model.sh <model-folder containing model.tflite + meta.json>}"
MODELS=/opt/plant-cam/models
NAME="$(basename "$SRC")"

for f in model.tflite meta.json; do
  [[ -f "$SRC/$f" ]] || { echo "missing $SRC/$f" >&2; exit 1; }
done

rsync -a "$SRC/" "$MODELS/$NAME/"
chown -R plantcam:plantcam "$MODELS/$NAME"
ln -sfn "$MODELS/$NAME" "$MODELS/current.tmp"
mv -T "$MODELS/current.tmp" "$MODELS/current"
systemctl restart plant-cam
echo "now running $NAME -- check: journalctl -u plant-cam -n 20"
