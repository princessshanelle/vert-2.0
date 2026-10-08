# VERT — Versatile Eco-farming Robotic Technology

A local-network-first smart greenhouse platform: sensors and actuators talk
MQTT to a RabbitMQ broker, backend services turn that into a live dashboard,
automated care, and AI plant-health inference — all without requiring
internet access. It's built so that the people actually tending the plants
never have to think about any of that: they plug in a sensor, and it shows
up; they set a rule once, and it just keeps watching.

See [`VERT-Project-Overview.docx`](./VERT-Project-Overview.docx) for the full
architecture writeup, and **[`docs/TESTING.md`](./docs/TESTING.md) for a
step-by-step walkthrough of running the whole stack locally** — start there
if you want to see it working before reading any more code.

## Layout

```
docker-compose.yml         Full local stack: broker, databases, object storage, gateway, automation, inference, frontend
infra/                     RabbitMQ (topology, per-device auth wiring) + Postgres bootstrap config
services/gateway/          Go: accounts, device registry & provisioning, camera frame storage, AMQP ingestion
services/automation/       Go: the rules engine — sustained-threshold rules → device commands + alerts
services/inference/        Python: plant-health heuristic (Excess Green Index), consumes camera frames
frontend/                  Vue 3 PWA — dashboard, zones, provisioning, automation, alerts, reminders, settings
firmware/dht22-node/       ESP32 + DHT22 sample device
firmware/xiao-esp32s3-camera/  XIAO ESP32-S3 Sense camera sample device
firmware/soil-moisture-node/   ESP32 + capacitive soil moisture sensor
firmware/ph-node/          ESP32 + PH-4502C pH module (reservoir water pH)
edge/plant-cam/            Raspberry Pi camera with on-device plant-health AI
edge/plant-cam/            Raspberry Pi camera: on-device plant-health detection (YOLO TFLite) + live view, plus its training pipeline
scripts/simulate_device.py A pretend sensor, for testing before any hardware exists
docs/TESTING.md            Full local walkthrough: accounts → provisioning → a rule firing → a photo stored
```

## Quick start

```bash
docker compose up --build
```

| Service | Address | Notes |
|---|---|---|
| Gateway API | http://localhost:8080 | accounts, devices, zones, frames |
| Automation API | http://localhost:8081 | rules |
| RabbitMQ management UI | http://localhost:15672 | `vert_gateway` / `vert_gateway_dev_password` |
| MinIO console | http://localhost:9001 | `vert_minio_admin` / `vert_minio_dev_password` |
| Frontend | http://localhost:5173 | the Vue PWA |

Then follow **[`docs/TESTING.md`](./docs/TESTING.md)** — it walks through
creating an account, provisioning a device, watching a simulated sensor
trip an automation rule, and uploading a camera frame, all with copyable
commands.

To flash a real device: provision it first (see TESTING.md, Step 4) to get
its `DEVICE_ID`/`DEVICE_SECRET`, copy `src/config.h.example` to
`src/config.h` under `firmware/dht22-node` or `firmware/xiao-esp32s3-camera`,
fill in your Wi-Fi and those credentials, then `pio run --target upload`.

## What's implemented vs. what's still ahead

Implemented and testable today: accounts (first user is admin automatically),
per-device credentials enforced at the RabbitMQ connection itself (not just
checked in application code), device provisioning, zones, camera frames
stored in MinIO/S3 with access-controlled viewing links, an automation
engine that turns a sustained sensor reading into both a device command and
a plain-language alert, a plant-health heuristic that actually looks at
uploaded photos (Excess Green Index — see below), and a complete frontend:
dashboard, device detail (with photo gallery and health notes), zone
management, device provisioning, automation rules, alerts, and personal
reminders — every screen in English, French, and Nigerian Pidgin.

Everything else from the original roadmap (TLS/local CA, RabbitMQ
clustering, offline notification delivery, live WebSocket updates, a
drag-and-drop dashboard layout editor, RBAC enforcement beyond the role
checks already in place, full time-series history, tests, CI/CD, a real
*trained* plant-health model, and more) is tracked in
[`VERT-Project-Overview.docx`](./VERT-Project-Overview.docx) Section 17 —
nothing there has quietly become obsolete, it's simply not built yet.

## The plant-health heuristic, honestly

`services/inference` does not use a trained model — there is no dataset or
training pipeline behind it (yet). What it does use is a real, documented
computer-vision technique: the **Excess Green Index** (`2G − R − B`), a
long-standing method in agricultural imaging for telling live vegetation
apart from soil and background. It looks at every uploaded photo, reports
a health score, a friendly label, a confidence (lower if the photo is too
dark or too bright to read honestly), and a plain-language note — never a
bare number. See `services/inference/vision.py` and Section 8 of the
project overview for exactly what it does and doesn't claim to know.

## Message contract

Every device and service exchanges JSON envelopes shaped like:

```json
{
  "schema_version": "1.0",
  "device_id": "dht22-01",
  "device_type": "temp-humidity",
  "zone": "zoneA",
  "ts": "2026-09-11T10:22:00Z",
  "seq": 1042,
  "payload": { "temp_c": 23.4, "humidity_pct": 61.2 }
}
```

Routing keys follow `vert.<zone>.<device_type>.<device_id>.<kind>`.
See `services/gateway/internal/mq/` for the canonical Go definition,
`services/automation/internal/mq/` and `services/inference/envelope.py`
for the two mirrors.

## How a device is trusted

A device is never anonymous and never shares a credential with any other
device or with any human account:

1. An admin calls `POST /api/v1/devices/provision` (gateway) with a
   `device_id` and `device_type`. The response includes a secret,
   **shown exactly once**.
2. That `device_id`/secret pair becomes the device's MQTT username/password
   *and* its HTTP Basic Auth credential for camera frame uploads — one
   identity, checked the same way everywhere.
3. RabbitMQ itself enforces this: it has no idea what a "device" is, but
   for any username it doesn't recognize as one of VERT's own backend
   services, it asks the gateway's `/internal/rabbitmq/auth/*` webhooks
   whether the username/password is valid, and whether the specific topic
   being published or subscribed to belongs to that device. A device can
   never see or spoof another device's messages — this is checked by
   RabbitMQ on every single message, not just at login.

See `services/gateway/internal/api/rabbitmq_auth.go` and
`infra/rabbitmq/rabbitmq.conf` for exactly how that's wired up, and
`docs/TESTING.md` Step 5 for how to see a wrong secret get rejected.
