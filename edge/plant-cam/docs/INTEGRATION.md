# Integration with the VERT stack

plant-cam is an ordinary VERT device (`device_type: ai-camera`): provisioned
like any sensor, authenticated per device by RabbitMQ through the
gateway, readings on the standard envelope, photos through the gateway's
frame store, commands from the dashboard. What the dashboard does with it:

* **Overview** (`frontend/src/views/Dashboard.vue`): a briefing written from
  live readings (active `conditions`, `risk_*`, ripe fruit, climate against
  the strawberry targets, devices gone quiet), then one band per zone with
  the health dial, findings, disease-pressure meters and a ripeness strip
  (`fruit_*_count`).
* **Plant-health panel** on the camera's page (`PlantHealthPanel.vue`): note,
  each finding with status, confidence, persistence and `first_seen`, the
  `env_*` air it was judged against, and `model_version`.
* **Analytics** (`views/Analytics.vue`): `health_score`, every `cond_*_conf`,
  `risk_botrytis`, ripe fruit, canopy and lux over 24 h / 7 d / 30 d, next to
  the zone's climate (time in target range, derived VPD and dew-point margin,
  hour-of-day humidity pattern), each with a table view. Long ranges use the
  gateway's bucketed history (`GET .../readings?metric=&minutes=&bucket=<s>`,
  avg + min + max per bucket).
* charts for every numeric field, the photo gallery, the live view
  (`start_stream` -> `http://<ip>:81/stream`) and automation rules on any
  numeric field, as before.

The strawberry targets the dashboard judges climate against live in
`frontend/src/lib/agronomy.js` (`TARGETS`), next to the VPD and dew-point maths.

## Alerts

Alerts come from automation rules. Useful ones:

| Rule | Meaning |
|---|---|
| `cond_powdery_mildew_active > 0` for 0 min | mildew seen in N consecutive analyses (profile `persistence`) |
| `cond_grey_mould_active > 0` for 0 min | grey mould (Botrytis) on fruit/flowers |
| `risk_botrytis >= 0.7` for 60 min | prolonged humid/wet weather at mild temperatures |
| `fruit_ripe_count >= 10` for 0 min | time to pick |
| `degrade_level >= 2` for 30 min | the Pi is running hot |

`cond_*_active` already includes persistence, so `for_minutes` can be 0.

## Backend change made: zone readings ACL (gateway)

To weigh disease risk with the zone's temperature/humidity, plant-cam
subscribes to `vert/<its zone>/+/+/reading`. The broker's per-device ACL
used to allow only a device's own topics, so the gateway's topic check
(`services/gateway/internal/api/rabbitmq_auth.go`) gained one read-only
exception:

> a device of type `ai-camera` may **read** (never write) `reading`
> topics in **its own, concrete zone** (no wildcard zone, no commands).

Backward compatible: every previous allow/deny decision is unchanged
(table test in `rabbitmq_auth_test.go`). Without it plant-cam logs
"broker refused the zone sensor subscription", reports `env_available: 0`
and carries on with visual analysis only.

## Applied: gateway/automation never give up on RabbitMQ

Found during end-to-end testing: after a broker restart that takes longer
than 30 s (RabbitMQ on this Pi takes ~33 s), the gateway and automation
services stopped trying to reconnect -- the processes stayed up, so Docker
never restarted them, and no device's readings were processed until
someone restarted them by hand. Nothing was lost (RabbitMQ's durable
queues held everything), but nothing reached the dashboard either.

Fixed in `services/{gateway,automation}/cmd/*/main.go`:

* if RabbitMQ is still unreachable after the 30 s connect retry, log it,
  wait 10 s and try again, for as long as the process runs;
* if a consumer stops while the connection stays up (e.g. the broker
  closes its channel or its queue is deleted), the connection is closed
  so the same loop reconnects and re-declares the topology.

Verified on the live stack: the e2e test stops the broker, restarts it
(~33 s) and now requires both services to resume on their own; deleting
`q.automation.rules` under a running automation service brings it back
within a second, queue and bindings re-declared.

## Other findings (not changed)

* The gateway trusts the envelope's `device_id` rather than the topic's
  when storing a reading, so a device could overwrite another device's
  `last_reading` by lying in the body (the ACL does stop it publishing on
  another device's topic). plant-cam itself trusts topic ids for zone readings.
* `services/inference` publishes `device_type: "plant-health-model"` under a
  routing key with segment `plant`; harmless today, inconsistent.
* `GET /api/v1/zones` (and other list endpoints) return `null` for an empty list.
