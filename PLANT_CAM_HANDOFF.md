# Plant-cam handoff (strawberry plant-health vision): progress as of 2026-10-14

Written so a new Claude chat can continue this work. Start a new chat in
`/home/pi/Documents/vert-2.0` and say: *"Read PLANT_CAM_HANDOFF.md and
continue."* Contains no secrets (see "Credentials" below for where they live).

---

## 1. What the project is

VERT is a smart-greenhouse stack (Docker Compose: gateway (Go), automation (Go),
inference (Python), RabbitMQ with MQTT plugin, Postgres/Timescale, MinIO,
frontend). The task was to build a **strawberry plant-health vision subsystem**
for a Raspberry Pi 4 + Pi camera, by evolving the existing `edge/plant-cam`.

The original brief asked for four gated phases: (1) discovery, read-only;
(2) one batch of clarifying questions with defaults; (3) a plan, approved
before coding; (4) implementation milestone by milestone, running
tests and lint each time. Rules from the brief that still apply:
- Follow existing repo conventions. **Don't refactor or rewrite other components
  unless the user asks; propose changes instead.**
- **Never claim something works without running it.**
- Crop profile (`profiles/strawberry/`). Outputs: health score, detected or
  suspected conditions, fruit ripeness counts, environmental risk fusion,
  active learning, and a tested retraining path.
- Flag YOLO's AGPL licence; prefer Apache/BSD models.
- Definition of done: runs as a service on boot, survives outages, benchmarks
  documented, tests, lint and type-check clean, and a new analyzer is one class
  plus one config entry.

### Decisions the user accepted ("OK to all defaults")
- Evolve `edge/plant-cam` in place; systemd on the same Pi; budget ≤2 threads, 75 °C.
- Apache/BSD models (torchvision SSDLite320 + MobileNetV3-Small), **no Ultralytics**.
- `health_score` stays 0–1; envelope stays `schema_version "1.0"`, payload has `payload_schema`.
- Alerts come from numeric `cond_*` metrics (no gateway alert consumer). **No frontend changes.**
- Gateway ACL change: an ai-camera device may **read** reading topics in its own zone.
- Leaf wetness is estimated from RH and dew point. Outbox holds about 7 days; active learning is capped at 2 GB.
- LAN-only; TLS is supported but off.
- **Leave alone:** `SESSION_HANDOFF.md` (has dev passwords), `edge/plant-cam/training.zip`,
  `patch_minio_block.py`. The user's `docker-compose.yml` MinIO change is their own work.

Still unanswered from Phase 2: strawberry variety and growth stage; whether the camera
is NoIR; which zone the DHT22 sensor is in.

---

## 2. Environment facts (non-obvious)

- This **Pi 4 (4 GB), 192.168.1.151, hosts the whole Docker stack AND the camera.**
- Camera: **OV5647 (v1)**. Currently aimed at a **ceiling** (no plants), so there are
  no real greenhouse photos yet.
- OS: **Debian 13 Trixie**, Python 3.13, system numpy 2.2.4, picamera2 from apt.
- **sudo needs a password.** Claude can't run apt, `install.sh` or systemctl; the user must.
  Docker works without sudo.
- No `gh` CLI, **no git identity, no GitHub credentials** on the Pi.
- Go is not installed; build and test Go with
  `docker run --rm -v $PWD/services/<svc>:/src -w /src golang:1.22-alpine go test ./...`.
- Venvs:
  - `edge/plant-cam/.venv`: dev/runtime (system-site-packages; onnxruntime, opencv-headless,
    pydantic, onnx, ai-edge-litert, tflite2onnx). Create with `make dev`.
  - `edge/plant-cam/training/.venv`: torch 2.14 CPU, torchvision 0.29, onnx, onnxruntime.
- TensorFlow/onnx2tf would not install on the Pi (stopped after 30 min).

---

## 3. What was built (all in `edge/plant-cam`)

### Package `plant_cam/`
| Module | Purpose |
|---|---|
| `core/` | `types`, `registry` (plugin registry: analyzers and publishers by name), `profile` (crop profile loader), `pipeline`, `temporal` (persistence: `activate_after`/`clear_after`), `aggregator` (per-zone score), `services` |
| `capture/` | `base` (CaptureLoop, LatestSlot), `files.py` (folder/video source), `picam.py` (main 1296×972 + lores 640×480 YUV + 2592×1944 still; calibrate; lux estimate) |
| `preprocess/` | colour correction (grey-card ROI cancels grow-light cast) |
| `analyzers/` | `classical.py` (canopy cover, ExG/VARI/GLI, yellow/brown share, 24 h growth, too-dark gate); `ml.py` (`detector`: dets_v1/yolov8 decode; `classifier` with tile fallback when there's no detector) |
| `runtime/` | ONNX Runtime and LiteRT backends; `meta.json` model contract |
| `env/` | EnvMonitor (subscribes to zone sensor readings), `risk.py` (disease-risk indices from RH, temperature and dew point) |
| `store/` | SQLite outbox, seq, kv, history, env; `active_learning.py` (uncertain, alert and random crops; one raw frame per hour in good light; 2 GB cap) |
| `publishers/` | mqtt, snapshot (`uploader.py`), log, `payload.py`, `overlay.py` (`status_text`), `stream.py` (MJPEG) |
| `system/` | `thermal.py` (sysfs, slows down when hot), `watchdog.py` (systemd watchdog) |
| `link.py` | MQTT client: backoff with jitter, LWT, announce, outbox flushed on PUBACK, zone subscription, loopback-safe announced IP, `advertise_ip` |
| `app.py`, `__main__.py` | CLI: `run`, `check-config`, `calibrate`, `benchmark`, `evaluate`, `export-al` |
| `bench.py`, `evaluate.py`, `config.py`, `logjson.py` | benchmark, precision/recall evaluation, pydantic config, JSON logging |

### Profile
- `profiles/strawberry/profile.yaml`: labels for stage A and stage B, thresholds, persistence.
  Uses keys `activate_after` and `clear_after` because YAML reads `on`/`off` as booleans.
- `profiles/strawberry/plugins.py`: `strawberry_fruit` (ripeness by colour, with a
  red-leaf guard: saturation ≥185, hue ≥ −4.5, leaf-border check) and `red_leaf_ratio`.

### Deploy
- `deploy/install.sh` (Trixie, installs from apt; creates the `plantcam` user, `/opt/plant-cam`,
  `/etc/plant-cam`, `/var/lib/plant-cam`).
- `deploy/plant-cam.service`: `Restart=on-failure`, `WatchdogSec=60`, `CPUQuota=200%`,
  `MemoryMax=900M`, `EnvironmentFile=/etc/plant-cam/secret.env`.
- `deploy/deploy-model.sh`: slot-based atomic model switch with rollback.
- `config.example.yaml`, `Makefile` (`dev`, `check`, `e2e`, `bench`, `run`, `install`, `deploy-model`),
  `pyproject.toml`, `tools/bench_models.py`.

### Docs (`edge/plant-cam/docs/`)
`SCHEMAS.md`, `EXTENDING.md`, `PROFILES.md`, `BENCHMARKS.md`, `INTEGRATION.md`, plus `README.md`.

### Training (`edge/plant-cam/training/`), Ultralytics removed
- `vertml/` (models, data, metrics, quantize), `train_classifier.py`, `train_detector.py`,
  `export.py` (ONNX; `--tflite-int8` via onnx2tf is **untested**), `import_public.py`,
  `labelstudio.py`, `pull_frames.py` (kept), `README.md`, `DATASETS.md`, `requirements.txt`.
- Old files deleted: `train.py`, `build_dataset.py`, `common.py`, `classes.yaml`, the
  import_* scripts, the notebook, and `requirements-pi.txt` / `requirements-dev.txt`.

### Tests (`edge/plant-cam/tests/`)
- 60 unit tests, run on synthetic scenes from `tests/fixtures/synth.py` (`make fixtures`
  regenerates the PNGs, which are gitignored; the JSON ground truth is committed). One real
  frame is committed: `real_ov5647_ceiling_night.jpg`.
- 3 integration tests against a real mosquitto, and `test_retrain_path.py`, which runs the
  whole path: Label Studio export → train → export → deploy → evaluate.
- `tests/e2e/test_vert_stack.py` runs against the live stack (`make e2e`), including a
  broker stop/start. **It now requires gateway and automation to recover on their own (the
  old xfail was removed).**
- **Status:** `make check` passes (ruff, mypy strict on 41 files, 64 passed); `make e2e` passes.

---

## 4. Changes outside plant-cam (approved or requested)

1. **Gateway ACL** (approved): `services/gateway/internal/api/rabbitmq_auth.go` lets an
   ai-camera read `vert.<own zone>.*.*.reading`. Supporting changes: `DeviceTypeAndZone`
   in `internal/store/zones.go`, and `router.go` passes `Zones: db`. Covered by
   `rabbitmq_auth_test.go` (12 cases).
2. **RabbitMQ reconnect fix** (the user asked for it on 2026-10-14). Applied in
   `services/gateway/cmd/gateway/main.go` and `services/automation/cmd/automation/main.go`:
   - Bug: after the 30 s connect retry failed (a broker restart here takes about 33 s),
     `runMessaging` returned. The process stayed up but stopped consuming, so Docker never
     restarted it and the dashboard went dead until a manual restart.
   - Fix: log, wait 10 s (ctx-aware), and `continue` retrying forever.
   - Also: each consumer goroutine closes the connection when it exits, so a consumer that
     stops while the connection is up (channel closed, queue deleted) triggers a reconnect
     and re-declares the topology. The log says "mq: a consumer stopped, reconnecting".
   - Verified: gofmt, vet and tests pass; both containers rebuilt; the e2e broker restart
     recovers at about 41 s; deleting `q.automation.rules` recovers within 1 s with the
     queue and bindings re-declared.
   - `services/inference` (Python) was checked: after its retry it raises, the process exits,
     and Docker's `restart: unless-stopped` brings it back. That's acceptable, so it wasn't changed.
3. Docs updated: `docs/TESTING.md` (RabbitMQ is retried indefinitely), the top-level
   `README.md` plant-cam line, and `edge/plant-cam/docs/INTEGRATION.md` ("Applied" section).
   The `docs/proposed/` patch was deleted.

---

## 5. Live stack state (this Pi)

- Containers rebuilt and running: gateway and automation, with the fix.
- Dev admin login: the documented one from `docs/TESTING.md`.
- Zones: `zoneA` (real) and `e2e` (test). Devices: `plantcam-01` (real), `plantcam-e2e`,
  `dht22-e2e` (test leftovers; delete them if you don't want them).
- The plant-cam run config is `edge/plant-cam/config.yaml` (gitignored; stream port 8181).
- plant-cam has run live on the real camera: device online, "too dark" reading at night,
  snapshot in the gallery, `start_stream` works, clean exit on SIGTERM.

### Credentials (not in this file)
- The `plantcam-01` device secret is in `edge/plant-cam/.secrets/plantcam-01.env`
  (gitignored, mode 600).
- `SESSION_HANDOFF.md` contains dev passwords and was already in the initial commit.

---

## 6. Measured numbers (Pi 4, OV5647)

- One analysis: 1.3 s with the classical baseline, 1.6 s with all 4 models; 255 MB RSS;
  about 14 % of the Pi during an analysis. At one analysis per 10 min, average CPU stays under 0.1 %.
- Per model: SSDLite320 detector 136 ms; MobileNetV3-Small classifier 25 ms per crop.
- LiteRT INT8 is about 2.6× faster than ONNX Runtime FP32 (EfficientNet-Lite0: 33 vs 95 ms, 2 threads).
  ONNX Runtime INT8 gains little. The plant-cam LiteRT backend was verified with a real INT8 `.tflite`.
- Evaluation on the synthetic fixtures: classical counts are exact, except the purple-cast
  scene when no grey-card ROI is configured.

---

## 7. Git state: commit and push NOT done yet

- Branch **`plant-cam-vision`** (created from `master`, which has only `a904172 initial commit`).
- All changes are **staged** (`git add -A`), except `patch_minio_block.py`, which is left untracked on purpose.
  This handoff file is new and untracked, so add it too if you want it committed.
- The commit message is saved at **`.git/PLANTCAM_COMMIT_MSG`**.
- Blocked because the Pi has no git identity and no GitHub credentials. Remote:
  `https://github.com/princessshanelle/vert-2.0` (returns 404 anonymously: private, or doesn't exist yet).
- To finish:
  ```bash
  git config user.name "Your Name" && git config user.email "you@your.email"
  git commit -F .git/PLANTCAM_COMMIT_MSG
  sudo apt install -y gh && gh auth login        # sets up the git credential helper
  git push -u origin plant-cam-vision             # or: git push origin plant-cam-vision:master
  ```
- Check the repo is **private** before pushing, because `SESSION_HANDOFF.md` has dev passwords.

---

## 8. Remaining work (in order)

1. **Commit and push** (section 7).
2. **Install as a service** (user, needs sudo):
   `cd edge/plant-cam && sudo ./deploy/install.sh`. Then put `PLANTCAM_*` from
   `.secrets/plantcam-01.env` into `/etc/plant-cam/secret.env` (mode 600) and run
   `sudo systemctl restart plant-cam`. Check it with `journalctl -u plant-cam -f`.
3. **Point the camera at the plants.** In `/etc/plant-cam/config.yaml`, set the ROIs (canopy,
   fruit strip, grey card) and the analysis time windows (when the light is good). Then run
   `python -m plant_cam calibrate` under the analysis light.
4. **Collect and label real photos.** Active learning saves one raw frame per hour plus
   uncertain crops. Label them in Label Studio, train on Colab (`training/README.md`), export,
   and deploy with `deploy-model.sh`.
5. **Run `plant_cam evaluate`** on held-out real photos to get per-class precision/recall.
   This is the last unmet item in the definition of done.
6. Run the `--tflite-int8` export once on Colab (TensorFlow available) and evaluate the result.
7. Optional cleanup: delete the e2e test zone and devices; answer the open Phase 2 questions.

## 9. Caveats to keep stating
- **Dataset licences:** no public strawberry dataset is cleared for commercial use.
  StrawDI is non-commercial; the Afzaal (Kaggle) and PlantVillage licences are unclear or
  conflicting. See `edge/plant-cam/training/DATASETS.md` for sources. `meta.json` records `trained_on`.
- Conditions are reported only as "detected" or "suspected" and need 3 consecutive sightings before an alert.
- Precision/recall on public or synthetic data says little about this greenhouse.
