#!/usr/bin/env bash
# Installs (or upgrades) plant-cam on a Raspberry Pi running Raspberry Pi
# OS Bookworm or Trixie, 64-bit. Run from the edge/plant-cam directory:
#
#   sudo ./deploy/install.sh
#
# Safe to re-run: config, secret, models and state are never overwritten.
# Every runtime dependency comes from apt (picamera2, OpenCV, numpy, pydantic,
# paho-mqtt, onnxruntime), so nothing is compiled and no pip wheel can
# replace the numpy that picamera2 was built against.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "warning: $(uname -m) detected -- onnxruntime needs 64-bit Raspberry Pi OS (aarch64)" >&2
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
ROOT=/opt/plant-cam

apt-get update
apt-get install -y --no-install-recommends \
  python3-venv python3-picamera2 python3-opencv python3-numpy python3-yaml python3-requests \
  python3-pydantic python3-paho-mqtt rsync
# onnxruntime: packaged on Trixie; on Bookworm fall back to pip (aarch64 wheels exist).
if apt-cache show python3-onnxruntime >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends python3-onnxruntime
  NEED_PIP_ORT=0
else
  NEED_PIP_ORT=1
fi

id plantcam &>/dev/null || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin --groups video plantcam

mkdir -p "$ROOT/app" "$ROOT/models" "$ROOT/deploy" /etc/plant-cam
rsync -a --delete "$SRC/plant_cam" "$SRC/profiles" "$ROOT/app/"
install -m 755 "$SRC/deploy/deploy-model.sh" "$ROOT/deploy/deploy-model.sh"

if [[ ! -d "$ROOT/venv" ]]; then
  python3 -m venv --system-site-packages "$ROOT/venv"
fi
if [[ $NEED_PIP_ORT == 1 ]]; then
  "$ROOT/venv/bin/pip" install --quiet "onnxruntime>=1.16"
fi
# Optional: LiteRT for .tflite models (only if you deploy one).
#   "$ROOT/venv/bin/pip" install ai-edge-litert

if [[ ! -f /etc/plant-cam/config.yaml ]]; then
  install -m 640 -o root -g plantcam "$SRC/config.example.yaml" /etc/plant-cam/config.yaml
  echo ">> edit /etc/plant-cam/config.yaml (device id, zone, broker/gateway address)"
fi
if [[ ! -f /etc/plant-cam/secret.env ]]; then
  install -m 600 -o root -g root /dev/null /etc/plant-cam/secret.env
  echo "VERT_DEVICE_SECRET=" > /etc/plant-cam/secret.env
  echo ">> put the provisioned device secret in /etc/plant-cam/secret.env"
fi

chown -R plantcam:plantcam "$ROOT/models"
install -m 644 "$SRC/deploy/plant-cam.service" /etc/systemd/system/plant-cam.service
systemctl daemon-reload
systemctl enable plant-cam

if grep -q '^VERT_DEVICE_SECRET=.\+' /etc/plant-cam/secret.env; then
  "$ROOT/venv/bin/python" -m plant_cam check-config --config /etc/plant-cam/config.yaml --text-logs
  systemctl restart plant-cam
  echo "installed and (re)started. logs: journalctl -u plant-cam -f"
else
  echo "installed. Set the secret, then: sudo systemctl restart plant-cam"
fi
