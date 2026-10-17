#!/usr/bin/env bash
# Installs (or upgrades) plant-cam on a Raspberry Pi running Raspberry Pi
# OS Bookworm or Trixie, 64-bit. Run from the edge/plant-cam directory:
#
#   sudo ./deploy/install.sh
#
# Safe to re-run: config, secret, models and state are never overwritten.
# Runtime dependencies come from apt (picamera2, OpenCV, numpy, pydantic,
# paho-mqtt), so nothing is compiled and no pip wheel can replace the numpy
# that picamera2 was built against. The one exception is onnxruntime (below).
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

id plantcam &>/dev/null || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin --groups video plantcam

mkdir -p "$ROOT/app" "$ROOT/models" "$ROOT/deploy" /etc/plant-cam
rsync -a --delete "$SRC/plant_cam" "$SRC/profiles" "$ROOT/app/"
install -m 755 "$SRC/deploy/deploy-model.sh" "$ROOT/deploy/deploy-model.sh"

if [[ ! -d "$ROOT/venv" ]]; then
  python3 -m venv --system-site-packages "$ROOT/venv"
fi
# Make `/opt/plant-cam/venv/bin/python -m plant_cam ...` work from any directory
# (calibrate, evaluate, export-al in the docs), not only from the service's
# working directory.
"$ROOT/venv/bin/python" -c 'import site; print(site.getsitepackages()[0])' \
  | xargs -I{} sh -c 'echo /opt/plant-cam/app > "{}/plant-cam-app.pth"'
# onnxruntime always comes from PyPI, never apt: Debian Trixie's
# python3-onnxruntime (1.21.0+dfsg) dies with "Illegal instruction" on a Pi 4
# (Cortex-A72), while PyPI's aarch64 wheel picks its CPU kernels at runtime.
# --ignore-installed puts it in the venv even if the apt package exists (the
# venv copy then wins); --no-deps keeps pip from replacing apt's numpy, and the
# other dependencies are installed only if missing.
"$ROOT/venv/bin/pip" install --quiet --ignore-installed --no-deps --index-url https://pypi.org/simple "onnxruntime>=1.20"
"$ROOT/venv/bin/pip" install --quiet --index-url https://pypi.org/simple coloredlogs flatbuffers packaging protobuf sympy
if ! "$ROOT/venv/bin/python" -c "import onnxruntime" 2>/dev/null; then
  echo "error: onnxruntime does not load on this CPU. Use the LiteRT model instead:" >&2
  echo "  $ROOT/venv/bin/pip install ai-edge-litert   (then deploy with meta.tflite.json as meta.json)" >&2
  exit 1
fi
# Optional: LiteRT for .tflite models (only if you deploy one).
#   "$ROOT/venv/bin/pip" install ai-edge-litert

if [[ ! -f /etc/plant-cam/config.yaml ]]; then
  install -m 640 -o root -g plantcam "$SRC/config.example.yaml" /etc/plant-cam/config.yaml
  echo ">> edit /etc/plant-cam/config.yaml (device id, zone, broker/gateway address)"
fi
if [[ ! -f /etc/plant-cam/secret.env ]]; then
  install -m 600 -o root -g root /dev/null /etc/plant-cam/secret.env
  cat > /etc/plant-cam/secret.env <<'SECRET'
# Paste the secret shown when this device was provisioned straight after the
# "=" -- the whole ~52-character code, no quotes, no brackets, no spaces.
VERT_DEVICE_SECRET=
SECRET
  echo ">> put the provisioned device secret in /etc/plant-cam/secret.env"
fi
SECRET_LEN=$(sed -n 's/^VERT_DEVICE_SECRET=//p' /etc/plant-cam/secret.env | tr -d '\r' | head -1 | wc -c)
if (( SECRET_LEN > 1 && SECRET_LEN < 41 )); then
  echo "warning: the secret in /etc/plant-cam/secret.env is only $((SECRET_LEN - 1)) characters;" >&2
  echo "         provisioned secrets are ~52. The broker will refuse it (\"Bad user name or password\")." >&2
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
