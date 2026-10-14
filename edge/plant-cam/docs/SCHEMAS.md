# Message schemas

plant-cam speaks the standard VERT device contract (README.md at the repo
root, `services/gateway/internal/mq/envelope.go`). Topics are MQTT form;
RabbitMQ maps `/` to `.` for the backend's AMQP consumers.

| Topic | QoS | Retained | When | Body |
|---|---|---|---|---|
| `vert/<zone>/ai-camera/<id>/reading` | 1 | no | each analysis (via the outbox) | envelope + **plant-health/2** payload |
| `vert/<zone>/ai-camera/<id>/status` | 0 | no | every `heartbeat_s` | envelope + heartbeat payload |
| `vert/<zone>/ai-camera/<id>/announce` | 1 | yes | every (re)connect | lifecycle announce |
| `vert/<zone>/ai-camera/<id>/offline` | 1 | no | Last Will, and on clean shutdown | lifecycle offline |
| `vert/+/ai-camera/<id>/cmd` | 1 | — | subscribed | command |
| `vert/<zone>/+/+/reading` | 0 | — | subscribed (needs the gateway ACL, INTEGRATION.md) | other devices' readings |

## Envelope

The gateway rejects any `schema_version` other than `"1.0"`, so the
envelope stays `1.0`; the payload carries its own `payload_schema`.

```json
{
  "schema_version": "1.0",
  "device_id": "plantcam-01",
  "device_type": "ai-camera",
  "zone": "zoneA",
  "ts": "2026-10-12T09:00:00.123Z",
  "seq": 1042,
  "payload": { ... }
}
```

* `ts` is UTC ISO 8601 with milliseconds, stamped **when the frame was
  analysed**: a reading replayed from the outbox after an outage lands at
  the right point on the charts.
* `seq` is monotonic across restarts (reserved in blocks of 100 in the
  local store; a restart may skip numbers, never reuse them). Readings and
  heartbeats share the counter.
* Delivery is at-least-once: a power cut between the broker's PUBACK and
  the local delete re-sends one reading. Consumers see the same `seq` twice.

## Reading payload: `plant-health/2`

Rules that shaped it:
* **Every top-level number is charted** (TimescaleDB) and usable in
  automation rules, so metrics are flat numeric keys.
* Strings and objects are kept only as the device's latest reading.
* `label`, `note`, `health_score` (**0-1**) and `confidence` keep the shape
  the dashboard's plant-health card already renders.
* Findings are **never diagnoses**: each condition is `detected` or
  `suspected`, with a confidence.

```json
{
  "payload_schema": "plant-health/2",
  "profile_id": "strawberry",
  "profile_version": "1.0.0",
  "model_id": "detector:strawberry-detector,leaf_classifier:strawberry-leaf",
  "model_version": "detector:strawberry-detector@v1-20261020,leaf_classifier:strawberry-leaf@v2-20261102",

  "health_score": 0.71,              // 0-1, = visual_score x (1 - env_weight x env_risk_score)
  "label": "steady",                 // thriving | steady | stressed | needs_attention
  "note": "Possible powdery mildew (white powdery patches...). Check leaf undersides; ...",
  "confidence": 0.78,                // 0.3 when withheld (too dark / no plants in view)
  "visual_score": 0.80,
  "env_risk_score": 0.45,            // absent without zone sensor data

  "canopy_coverage_pct": 61.2,       // classical baseline (always present)
  "canopy_growth_pct_24h": 1.4,      // percentage points vs ~24 h ago (once history exists)
  "yellow_ratio": 0.031, "brown_ratio": 0.008,
  "exg_mean": 0.21, "vari_mean": 0.12, "gli_mean": 0.15,
  "brightness": 0.47, "exposure_ok": 1, "lux": 4120.0,
  "roi_tray1_canopy_coverage_pct": 58.0,      // per ROI, when ROIs are configured

  "flower_count": 4,
  "fruit_green_count": 6,            // with a Stage A/B model only (colour can't tell green fruit from leaves)
  "fruit_white_count": 2, "fruit_turning_count": 1, "fruit_ripe_count": 3, "fruit_overripe_count": 0,
  "fruit_rotting_count": 0,          // model only
  "runner_count": 2,                 // model only
  "fruit_counts_model": 1,           // 1 = counts from models, 0 = classical colour baseline
  "red_leaf_ratio": 0.04,            // context only (cold/age/variety) -- never a condition

  "cond_powdery_mildew_conf": 0.81,  // per condition a loaded model can see (explicit zeros otherwise)
  "cond_powdery_mildew_persist": 3,  // consecutive analyses it has been seen
  "cond_powdery_mildew_active": 1,   // 1 once persistent (profile persistence.activate_after) -- alert on this

  "env_available": 1,
  "env_temp_c": 18.5, "env_rh_pct": 93.0, "env_dew_point_c": 17.4, "env_age_s": 41,
  "risk_botrytis": 0.5, "risk_botrytis_hours": 8.0,
  "risk_powdery_mildew": 0.17, "risk_powdery_mildew_hours": 8.0,

  "degrade_level": 0, "cpu_temp_c": 61.3, "undervoltage": 0, "rss_mb": 255.1, "load_1m": 0.9,
  "outbox_pending": 0, "latency_ms": 1592.2,
  "det_leaf_count": 14, "det_fruit_count": 9,          // Stage A raw counts (model only)
  "leaf_classifier_crops": 14,

  "conditions": [
    {"id": "powdery_mildew", "kind": "disease", "status": "detected", "confidence": 0.81,
     "persistence": 3, "active": true, "roi": "frame", "first_seen": "2026-10-12T08:40:00.000Z"}
  ],
  "image_ref": "plantcam-01/3f1c....jpg"   // when an annotated snapshot was uploaded with this reading
}
```

Keys are present only when they were measured (no fake zeros), except the
`cond_*` triple, which is explicit for every condition a loaded model can
observe, so an automation rule always has a value to clear on.

## Heartbeat payload (`.../status`)

```json
{"kind": "heartbeat", "uptime_s": 3600, "camera_ok": 1, "outbox_pending": 0, "analyses": 6,
 "env_subscribed": 1, "last_analysis_age_s": 412, "degrade_level": 0, "cpu_temp_c": 62.1,
 "undervoltage": 0, "rss_mb": 160.4, "load_1m": 0.7}
```

The gateway's lifecycle consumer ignores it today (no `kind` at envelope
level); it's there for monitoring and future dashboard use.

## Announce

```json
{"kind": "announce", "device_id": "plantcam-01", "device_type": "ai-camera", "ip": "192.168.1.151",
 "capabilities": {"reads": [...], "commands": ["start_stream", "stop_stream", "analyze_now", "calibrate"],
                  "payload_schema": "plant-health/2", "profile": "strawberry@1.0.0",
                  "models": {"detector": "strawberry-detector@v1-20261020"}, "model_version": "..."}}
```

`ip` is the LAN address for the dashboard's live view (`http://<ip>:81/stream`);
when the broker is on the same machine it falls back to the default-route
address, never loopback. Override with `device.advertise_ip`.

## Commands

`{"payload": {"command": "<name>"}}` on `.../cmd`, e.g. from
`POST /api/v1/devices/<id>/command`.

| Command | Effect |
|---|---|
| `analyze_now` | analyse immediately (and upload a snapshot) |
| `start_stream` / `stop_stream` | live MJPEG view on `:81/stream`; it also stops itself after 5 min with no viewers |
| `calibrate` | measure and lock white balance/exposure under the current light |

## Local HTTP (`:81`)

`/stream` (MJPEG, on demand), `/snapshot.jpg` (on demand), `/healthz` (JSON,
always on: broker/camera state, outbox, temperature, last report).
