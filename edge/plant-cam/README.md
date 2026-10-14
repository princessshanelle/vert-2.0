# plant-cam: on-device plant-health vision (strawberries)

A Raspberry Pi 4 + Pi camera that looks at the crop, analyses it on the
device, and reports to VERT like any other device: same provisioning,
same MQTT envelope and topics, same frame upload, same `:81/stream` live
view. Crop knowledge lives in a **crop profile** (`profiles/strawberry/`);
the core knows nothing about strawberries.

```
                   +---------- capture thread (Picamera2) ------------+
                   | lores 640x480 YUV -> 1-slot "latest" buffer ------+--> MJPEG :81/stream (on demand)
                   |                                                   +--> change detection
 schedule /        | main 1296x972 + full still 2592x1944 (on request)
 change / command -+--> analysis worker (newest request only)
                         preprocess (grey card) -> analyzers -> Stage C -> aggregate
                           classical      canopy %, ExG/VARI/GLI, yellow/brown, growth   (no model needed)
                           detector       Stage A: leaf/flower/fruit/runner   (SSDLite320, ONNX/LiteRT)
                           classifier x3  Stage B: crops from the still -> symptoms, ripeness, flowers
                           strawberry_fruit  fruit by ripeness, flowers (model or colour baseline)
                         env monitor (zone temp/RH via MQTT) -> Botrytis / powdery-mildew risk (Stage D)
                         -> publishers: snapshot (gallery) -> mqtt (SQLite outbox -> broker)
                         -> active-learning samples (bounded) for labelling
 main loop: watchdog, thermal degrade, heartbeat, wedge detection
```

Every reading (schema in [docs/SCHEMAS.md](docs/SCHEMAS.md)) carries the
profile and model versions, a 0-1 health score (visual and environmental
parts reported separately), conditions as *detected*/*suspected* with
confidence and persistence, fruit counts by ripeness, flowers, canopy
coverage, risk indices, and the snapshot reference. Nothing is ever
presented as a diagnosis.

With no model installed it runs on the classical baseline from day one.
Models plug in by dropping a package into `/opt/plant-cam/models/` (see
[training/README.md](training/README.md)).

## Install on the Pi

Raspberry Pi OS **Bookworm or Trixie, 64-bit**. Everything comes from apt
(picamera2, OpenCV, numpy, pydantic, paho-mqtt, onnxruntime).

1. **Provision** the device (admin token, see `docs/TESTING.md` at the repo root):
   ```bash
   curl -s -X POST http://<vert-host>:8080/api/v1/devices/provision \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"device_id":"plantcam-01","device_type":"ai-camera"}'
   ```
   Keep the secret (shown once), and assign the device to a zone in the dashboard.
2. **Install**:
   ```bash
   cd vert-2.0/edge/plant-cam
   sudo ./deploy/install.sh
   sudo nano /etc/plant-cam/config.yaml        # device id, zone, broker/gateway address, ROIs
   sudo nano /etc/plant-cam/secret.env         # VERT_DEVICE_SECRET=...
   sudo systemctl restart plant-cam
   journalctl -u plant-cam -f
   ```
3. **Calibrate colour** once, under the light you analyse in (grow lights
   off / daylight), with the camera in its final position:
   ```bash
   sudo systemctl stop plant-cam
   sudo -u plantcam /opt/plant-cam/venv/bin/python -m plant_cam calibrate --config /etc/plant-cam/config.yaml
   sudo systemctl start plant-cam
   ```
   (or send the device the `calibrate` command from the dashboard API.)
4. **Check**: the device shows online on the dashboard with a plant-health
   card; `curl http://<pi-ip>:81/healthz`.

Upgrading: `git pull && sudo ./deploy/install.sh` (config, secret, models, state are kept).

If the Pi also hosts the Docker stack (as here), use `broker.host: 127.0.0.1`
and `gateway.url: http://127.0.0.1:8080`; the live-view address announced
to the dashboard is still the LAN IP.

## Configuration

[config.example.yaml](config.example.yaml) documents every setting. One
typed config (pydantic), validated at startup with messages naming the
offending key; `python -m plant_cam check-config --config ...` checks it
without starting. Any setting can be overridden from the environment:
`PLANTCAM__SCHEDULE__INTERVAL_S=300`. The secret comes from
`VERT_DEVICE_SECRET` (`/etc/plant-cam/secret.env`, mode 600), never from
a committed file.

Things to set for a real greenhouse:
* **`rois`**: where the canopy is and where the fruit hangs. In tabletop or
  gutter systems a top-down camera sees leaves but little fruit; give the
  fruit strip its own ROI (`kinds: [fruit]`), or use a second camera with
  its own device id looking at the fruit side.
* **`schedule.windows`**: analyse only when grow lights are off or white.
  Purple/pink LEDs wreck colour analysis.
* **`preprocess.reference_roi`**: put a grey card in frame and name its ROI
  to cancel residual colour casts on every analysis.

## Robustness

| Event | What happens |
|---|---|
| Broker / network down | readings go to a SQLite outbox (7 days / 2000 rows); replayed in order on reconnect; a row is deleted only after the broker's PUBACK (tested) |
| Broker restart | reconnect with exponential backoff + full jitter; Last Will marks the device offline meanwhile |
| Process killed / power cut | Last Will -> offline; outbox and condition state survive (SQLite WAL) |
| Camera unplugged | capture thread reopens with backoff; heartbeat reports `camera_ok: 0` |
| Camera driver hung | no frame *and* no retry for 45 s -> exit 1 -> systemd restarts |
| Analysis hung | > 300 s -> exit 1 -> systemd restarts; main loop stalls -> systemd watchdog (60 s) |
| Bad / missing model | logged, that analyzer skipped; everything else runs |
| Bad config | refuses to start with an actionable message (`ExecStartPre` check-config) |
| Hot Pi | 70 C: half rate, 2 fps view; 75 C: ML skipped; 80 C or under-voltage: classical only, view off |
| Corrupt state DB | moved aside, fresh one created |
| SIGTERM | finishes the current analysis, flushes, publishes offline, exits 0 |

SD card: logs go to journald; one small SQLite row per analysis; images
only as bounded active-learning samples (2 GB cap).

## Performance

Measured on this Pi while it also runs the whole Docker stack
([docs/BENCHMARKS.md](docs/BENCHMARKS.md)): one full analysis is
1.3 s with the classical baseline, 1.6 s with all four models, and
analysis happens every 10 min by default, so average CPU stays under 0.1 %
(the service is capped at 2 cores and 900 MB by systemd).

```bash
python -m plant_cam benchmark --config /etc/plant-cam/config.yaml              # live camera
python -m plant_cam benchmark --config ... --images DIR --iterations 20 --json out.json
```

## Develop and test

```bash
cd edge/plant-cam
make dev                     # .venv on top of the system picamera2/numpy
make check                   # ruff + mypy --strict + unit + integration tests
make e2e                     # against the real VERT stack on this machine (provisions test devices)
```

Unit tests use synthetic strawberry scenes (`tests/fixtures/synth.py`,
generated) plus real frames from this camera. Integration tests start a
throwaway mosquitto in Docker and also run the whole retraining path.

Run without a camera: `camera.source: folder` (+ `camera.folder`) or `video`.

## Layout

```
plant_cam/          the service
  core/             types, plugin registry, crop-profile loader, pipeline, Stage C, aggregator
  capture/          FrameSource: picamera2, folder, video; capture loop
  analyzers/        classical baseline, Stage A detector, Stage B classifier
  env/              zone sensor monitor + risk indices (Stage D)
  publishers/       mqtt, snapshot, log; payload schema; overlay; MJPEG stream
  runtime/          model packages; ONNX Runtime / LiteRT backends
  store/            SQLite outbox/state/history; active-learning samples
  system/           thermal monitor, systemd notify
profiles/strawberry crop profile: profile.yaml + plugins.py
training/           label -> train -> evaluate -> export (torchvision, BSD-3)
deploy/             install.sh, plant-cam.service, deploy-model.sh
docs/               SCHEMAS, EXTENDING, PROFILES, BENCHMARKS, INTEGRATION
tools/              bench_models.py
```

More: [extending (analyzers, publishers, crops, models)](docs/EXTENDING.md) ·
[crop profiles](docs/PROFILES.md) · [VERT integration and alerts](docs/INTEGRATION.md)
