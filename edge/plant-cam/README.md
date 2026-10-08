# plant-cam: on-device plant-health AI for a Raspberry Pi

A Raspberry Pi 4 with a Pi camera that watches the plants, runs a YOLO
nano model (TFLite INT8) on the device, and reports to VERT like any other
device. It uses the same provisioning, the same MQTT topics and envelope,
the same frame-upload endpoint, and the same `:81/stream` live view as the
XIAO camera.

```
camera thread ── latest frame ──┬──► MJPEG :81/stream (boxes + status overlay) ──► dashboard live view
 (picamera2)                    ├──► detector (every 30 s) ──► smoothed reading ──► MQTT vert/<zone>/ai-camera/<id>/reading
                                │                         └──► annotated evidence JPEG ──► POST /api/v1/devices/<id>/frames
                                └──► raw photo every 10 min ──► /var/lib/plant-cam/dataset (for training)
MQTT: announce (IP + capabilities) on every connect · JSON last-will on .../offline · commands on .../cmd
```

What VERT does with the readings, with no backend changes:
- `label`, `note`, `health_score` and `confidence` drive the dashboard's existing **plant-health card**.
- Every numeric field (`health_score`, `affected_pct`, `pest_count`, `chlorosis_count`, …) is charted from TimescaleDB, and can be used in **automation rules**, for example "pest_count > 0 for 10 min → alert".
- Evidence photos appear in the device's photo gallery.

With no model installed, it falls back to the same Excess Green heuristic
that `services/inference` uses, so the device is useful from day one. To
train a real model, see **[training/README.md](training/README.md)**.

## Commands it accepts

| Command | Effect |
|---|---|
| `start_stream` | Turns on `:81/stream`. It switches off again after 5 min with no viewers. |
| `stop_stream` | Turns the stream off. |
| `analyze_now` | Runs an analysis immediately instead of waiting up to 30 s. |

Send them from the dashboard eye icon, or with
`POST /api/v1/devices/<id>/command {"command": "analyze_now"}`.

## Set up the Pi

**Hardware and OS:** Raspberry Pi 4 (2 GB or more), any Pi camera module,
and **Raspberry Pi OS Bookworm 64-bit**. The 64-bit OS is required, because
the TFLite runtime has no 32-bit wheels. Make sure the Pi joins the **same
Wi-Fi/LAN as the VERT host**.

1. **Provision the device** (admin token, as in docs/TESTING.md):
   ```bash
   curl -s -X POST http://<vert-host>:8080/api/v1/devices/provision \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"device_id":"plantcam-01","device_type":"ai-camera"}'
   ```
   Save the secret. It is shown only once. Then assign the device to a zone in the dashboard.

2. **Check the camera works**: `rpicam-hello --timeout 3000` (or `libcamera-hello` on older images).

3. **Install**:
   ```bash
   git clone <this repo> && cd vert-2.0/edge/plant-cam     # or scp the edge/plant-cam folder over
   sudo ./deploy/install.sh
   sudo nano /etc/plant-cam/config.yaml   # device id + secret, zone, broker/gateway host IP
   sudo systemctl restart plant-cam
   journalctl -u plant-cam -f
   ```
   A healthy start looks like this:
   ```
   camera open (picamera2, 1280x720 @ 10 fps)
   stream server listening on :81
   connected
   steady (54%, 0 detections, 12 ms): Looking alright overall. ...
   ```

4. **Check it from the VERT host**: `curl http://<pi-ip>:81/healthz`. The device should appear on the dashboard, and the eye icon should show the live view with the overlay.

Updating the code later means running `git pull && sudo ./deploy/install.sh` again. Your config and models are kept.

## Run it on a laptop first (no Pi needed)

```bash
cd edge/plant-cam
python -m venv .venv && .venv\Scripts\activate        # Windows (source .venv/bin/activate elsewhere)
pip install -r requirements-dev.txt
copy config.example.yaml config.yaml                  # set camera.source: opencv (webcam) or folder,
                                                      # and point dataset/spool/model at local paths
python -m plant_cam --config config.yaml
```

Then open http://localhost:81/stream after sending `start_stream` (or set
`stream.always_on: true` while developing).

## Robustness, briefly

- **systemd** restarts it on crash. A **watchdog** restarts it if the main loop hangs. If the camera produces no frames for 30 s, it exits deliberately and gets a clean restart.
- **Broker down:** paho keeps reconnecting with backoff. Readings are spooled to disk and replayed with their original timestamps.
- **Bad model file:** it logs the error and falls back to the greenness heuristic rather than crashing.
- **DHCP gives the Pi a new IP:** it re-announces on every reconnect, so the dashboard's live-view link follows.
- **Models are versioned folders** behind a `current` symlink, so swapping and rolling back are atomic. Every reading includes `model_version`.

## Layout

```
plant_cam/        the app: camera, detector, health, bus (MQTT), uploader, stream, app (main loop), bench
deploy/           install.sh, plant-cam.service, deploy-model.sh
training/         step-by-step model training: classes.yaml -> dataset -> YOLO11n -> TFLite INT8
config.example.yaml
```

## Live stream for marketing

`:81/stream` is meant for the LAN only, the same as the XIAO's. **Don't
port-forward the Pi.** For a public "monitored by AI" feed, relay the
stream through a media server in the Docker stack (e.g. MediaMTX serving
WebRTC/HLS), so the public never reaches the device or its credentials.
That is a planned next step and isn't built yet.
