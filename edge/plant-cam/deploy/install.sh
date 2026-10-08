#!/usr/bin/env bash
# Installs (or upgrades) plant-cam on a Raspberry Pi running Raspberry Pi
# OS Bookworm 64-bit. Run from the edge/plant-cam directory:
#
#   sudo ./deploy/install.sh
#
# Safe to re-run: config and models are never overwritten.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "warning: $(uname -m) detected -- ai-edge-litert needs 64-bit Raspberry Pi OS (aarch64)" >&2
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
ROOT=/opt/plant-cam

apt-get update
apt-get install -y python3-venv python3-picamera2 python3-opencv python3-numpy python3-yaml python3-requests rsync

id plantcam &>/dev/null || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin --groups video plantcam

mkdir -p "$ROOT/app" "$ROOT/models" "$ROOT/deploy" /etc/plant-cam
rsync -a --delete "$SRC/plant_cam" "$ROOT/app/"
install -m 755 "$SRC/deploy/deploy-model.sh" "$ROOT/deploy/deploy-model.sh"

if [[ ! -d "$ROOT/venv" ]]; then
  python3 -m venv --system-site-packages "$ROOT/venv"
fi
"$ROOT/venv/bin/pip" install --upgrade pip
"$ROOT/venv/bin/pip" install -r "$SRC/requirements-pi.txt"

if [[ ! -f /etc/plant-cam/config.yaml ]]; then
  install -m 640 -o root -g plantcam "$SRC/config.example.yaml" /etc/plant-cam/config.yaml
  echo ">> edit /etc/plant-cam/config.yaml (device id/secret, broker, gateway), then:"
  echo ">>   sudo systemctl restart plant-cam"
fi

chown -R plantcam:plantcam "$ROOT/models"
install -m 644 "$SRC/deploy/plant-cam.service" /etc/systemd/system/plant-cam.service
systemctl daemon-reload
systemctl enable plant-cam
systemctl restart plant-cam
echo "installed. logs: journalctl -u plant-cam -f"
