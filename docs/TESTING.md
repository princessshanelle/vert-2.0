# Testing VERT locally

This walks through bringing up the full stack and exercising every piece
that's implemented so far: accounts, device provisioning, per-device
credential checks, a simulated sensor, an automation rule, an alert, a
camera frame stored in MinIO, and the plant-health heuristic that looks
at it. Nothing here needs real hardware or an internet connection --
it's meant to be run on a laptop before anything is wired up in an
actual greenhouse.

**Two ways to follow along:** every step below shows the raw `curl`
call, because that's what proves the API itself works. But once you've
registered an account (Step 2), you can do almost everything else --
creating a zone, provisioning a device, writing an automation rule,
acknowledging an alert, setting a reminder -- from the web UI at
http://localhost:5173 instead. The `curl` commands are the ground truth;
the UI is what a grower actually uses day to day.

If something in this walkthrough doesn't work the way it's described,
that's worth telling us about -- it means either a bug or a gap in the
explanation, and either way the next person deserves better than we got.

## Prerequisites

- Docker Desktop (Compose V2 -- `docker compose version` should print `v2.x`)
- `curl` (or any HTTP client) for the API calls below
- Python 3 with `pip install paho-mqtt`, only if you want to simulate a
  sensor without flashing real firmware (see Step 5)

## 1. Bring up the stack

```bash
docker compose up --build
```

First boot pulls several images and builds three services, so expect a
couple of minutes. You'll know it's ready when you see:

```
gateway_1      | gateway HTTP API listening on :8080
automation_1   | automation HTTP API listening on :8081
```

What's now running, and where you can look at it directly:

| Service | Address | Notes |
|---|---|---|
| Gateway API | http://localhost:8080 | accounts, devices, zones, frames |
| Automation API | http://localhost:8081 | rules |
| RabbitMQ management UI | http://localhost:15672 | login `vert_gateway` / `vert_gateway_dev_password` |
| MinIO console | http://localhost:9001 | login `vert_minio_admin` / `vert_minio_dev_password` |
| Frontend | http://localhost:5173 | the Vue PWA |

## 2. Create your account

The very first account created on a fresh instance is automatically made
an admin -- there's no separate superuser bootstrap step to remember.

```bash
curl -s -X POST http://localhost:8080/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-real-password","display_name":"You","locale":"en"}'
```

Save the `token` field from the response:

```bash
export TOKEN="paste-the-token-here"
```

## 3. Create a zone

Rules and dashboard grouping both key off zones, so make at least one.
The name matters: it must match the `ZONE` a device's firmware (or the
simulator in Step 5) is configured with.

```bash
curl -s -X POST http://localhost:8080/api/v1/zones \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"zoneA"}'
```

Note the returned `id` -- you'll use it in Step 6.

## 4. Provision a device

This is the step that stands in for "someone is about to wire up a new
sensor." It hands back a secret exactly once -- copy it now.

```bash
curl -s -X POST http://localhost:8080/api/v1/devices/provision \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"device_id":"dht22-01","device_type":"temp-humidity"}'
```

```json
{
  "device_id": "dht22-01",
  "secret": "JBSWY3DPEHPK3PXP...",
  "note": "This secret is shown once. Copy it into the device's config.h now -- there is no way to retrieve it again, only to re-provision and issue a new one."
}
```

Put `dht22-01` into the zone you just created:

```bash
curl -s -X PUT http://localhost:8080/api/v1/devices/dht22-01/zone \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"zone_id":"<the-zone-id-from-step-3>"}'
```

If you have the real hardware: copy `dht22-01` and the secret into
`firmware/dht22-node/src/config.h` (as `DEVICE_ID` and `DEVICE_SECRET`),
along with your Wi-Fi and the machine running Docker's LAN IP as
`MQTT_HOST`, and flash it. Otherwise, continue to Step 5.

## 5. Simulate the sensor (no hardware needed)

```bash
pip install paho-mqtt
python scripts/simulate_device.py \
  --device-id dht22-01 --secret "<the-secret-from-step-4>" \
  --zone zoneA --temp 42.0 --interval 5
```

You should see it announce itself and then publish a reading every 5
seconds. Two things to check right away:

- **The device shows up.** `curl -s http://localhost:8080/api/v1/devices -H "Authorization: Bearer $TOKEN"` should now list `dht22-01` as `"status": "online"`.
- **A wrong secret is rejected.** Try the same command with the secret changed by one character -- RabbitMQ should refuse the MQTT connection. This is the per-device credential check actually being enforced, not just present in the code.

## 6. Create an automation rule

This rule watches `dht22-01`'s zone for a temperature over 40°C sustained
for 0 minutes (immediate, so you don't have to wait 10 minutes to see it
fire) and tells a (made-up, for this test) window motor to open.

```bash
curl -s -X POST http://localhost:8081/api/v1/rules \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "zone_id": "<the-zone-id-from-step-3>",
    "name": "Zone A too hot",
    "condition": {"metric": "temp_c", "op": ">", "value": 40, "for_minutes": 0},
    "action": {"device_id": "window01", "command": "open"}
  }'
```

Note: the action's target device (`window01` here) needs to exist too --
provision it the same way you provisioned the sensor in Step 4 (it
doesn't need to be a real device or ever connect for this test; the
automation service only needs its zone/type on record to build the
command's routing key).

```bash
curl -s -X POST http://localhost:8080/api/v1/devices/provision \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"device_id":"window01","device_type":"window"}'
curl -s -X PUT http://localhost:8080/api/v1/devices/window01/zone \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"zone_id":"<the-zone-id-from-step-3>"}'
```

Now (re)start the simulator from Step 5 with `--temp 42.0` if it isn't
still running. Within about 30 seconds (the automation service polls for
rule changes on that cadence) you should see, in the `automation`
container's logs:

```
automation_1  | automation: Zone A too hot: dht22-01 has read temp_c 40.0 for over 0 minutes, so VERT is telling window01 to open.
```

That message is also now a row in the `alerts` table, and was published
as an event on `iot.events` -- both the persisted record and the live
signal a future notification service would pick up. Open
http://localhost:5173/alerts (logged in as the account from Step 2) and
you should see the same message waiting to be acknowledged.

The reading itself is also now the device's "latest reading": `curl -s
http://localhost:8080/api/v1/devices/dht22-01 -H "Authorization: Bearer
$TOKEN"` includes `"last_reading": {"temp_c": 42.0, ...}`, and the
dashboard's device card / detail page shows it as a stat tile.

## 7. Upload a camera frame

You don't need a camera to check that MinIO storage and the per-device
upload credential work. Provision a camera device, then POST any JPEG:

```bash
curl -s -X POST http://localhost:8080/api/v1/devices/provision \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"device_id":"cam01","device_type":"camera"}'

curl -s -X POST http://localhost:8080/api/v1/devices/cam01/frames \
  -u "cam01:<the-secret-just-issued>" \
  -H "Content-Type: image/jpeg" \
  --data-binary @/path/to/any/photo.jpg
```

A successful upload returns the object's storage key as plain text. Then:

```bash
curl -s http://localhost:8080/api/v1/devices/cam01/frames \
  -H "Authorization: Bearer $TOKEN"
```

returns a short-lived, clickable URL for the photo -- open it in a
browser and you should see the image. You can also see the raw object in
the MinIO console (http://localhost:9001) under the `vert-frames` bucket.

Try the upload again with the wrong password: it should come back `401`.

## 8. See the plant-health note

The upload in Step 7 stored the photo, but it's the camera's own MQTT
"frame ready" event that tells the inference service to actually look at
it -- real firmware publishes that itself right after the HTTP upload
succeeds. To trigger it by hand (replacing `<object-key>` with what
Step 7's upload returned):

```bash
pip install paho-mqtt
python3 -c "
import paho.mqtt.publish as publish, json
publish.single(
    'vert/zoneA/camera/cam01/frame',
    json.dumps({
        'schema_version': '1.0', 'device_id': 'cam01', 'device_type': 'camera',
        'zone': 'zoneA', 'seq': 0, 'payload': {'image_ref': '<object-key>'}
    }),
    hostname='localhost', auth={'username': 'cam01', 'password': '<cam01-secret>'}
)
"
```

Within a second or two, the `inference` container's logs should show
something like `cam01 (thriving): Lots of healthy green growth in this
photo -- whatever you're doing, it's working.` -- that's the Excess
Green Index heuristic in services/inference/vision.py actually looking
at the pixels of the photo you uploaded (see the project overview,
Section 6b, for exactly what it is and isn't). Open
http://localhost:5173/devices/cam01 and you'll see the same note on the
device's page, next to the photo. Try a very dark or very bright image
and you should see it say it couldn't get a clear read, rather than
guessing.

## 9. The real firmware

Steps 5-8 above prove the backend independent of hardware. When you're
ready to flash the actual boards:

```bash
cd firmware/dht22-node        # or firmware/xiao-esp32s3-camera
cp src/config.h.example src/config.h
# edit config.h: WIFI_SSID/PASSWORD, MQTT_HOST (your machine's LAN IP,
# not localhost -- the ESP32 is a separate device on the network),
# DEVICE_ID/DEVICE_SECRET from provisioning, ZONE matching a zone name.
pio run --target upload
pio device monitor
```

## Troubleshooting

- **A device (real or simulated) can't connect over MQTT.** Check the
  `broker` container's logs for an auth rejection, and double check the
  secret matches exactly what `/api/v1/devices/provision` returned --
  there's no way to view it again, only to re-provision (which issues a
  new one and invalidates the old).
- **`docker compose up` fails on first run with a connection error from
  gateway or automation.** These retry for about 30 seconds against
  Postgres/MinIO before giving up (RabbitMQ is retried for as long as
  the service runs) -- if it's still failing after
  that, check that the `broker`/`postgres`/`minio` containers actually
  reported healthy (`docker compose ps`).
- **An automation rule never fires.** Confirm the device and the rule
  are in the *same* zone (`GET /api/v1/zones` and `GET /api/v1/devices`
  from the gateway), and that the metric name in the rule's `condition`
  exactly matches a key in the reading's `payload`.
