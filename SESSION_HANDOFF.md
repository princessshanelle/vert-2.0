# VERT — Session Handoff

This is a snapshot for continuing work in a new Claude chat. Paste/attach this file and say what you want to pick up.

## What VERT is

A local-network-first smart greenhouse platform. No cloud dependency by design — everything (broker, databases, backend services, dashboard) runs on a local machine via Docker Compose, and devices (ESP32 boards) talk to it over the LAN only.

**Working directory:** `C:\Users\UNESCO\Documents\projects\vert-2.0` (not a git repo as of this session)

## Architecture

| Component | Tech | Role |
|---|---|---|
| `services/gateway` | Go | Accounts/JWT auth, device registry & provisioning, camera frame storage (MinIO), TimescaleDB chart endpoints, on-demand device command channel, RabbitMQ HTTP-auth webhooks |
| `services/automation` | Go | Rules engine — watches telemetry, fires device commands + alerts on sustained threshold breaches |
| `services/inference` | Python | Plant-health heuristic (Excess Green Index — a real CV technique, *not* a trained model) on camera frames |
| `frontend` | Vue 3 PWA | Dashboard with live sparkline charts, camera "eye" live-view toggle, zones, device provisioning UI, automation rules UI, alerts, reminders. i18n: English/French/Nigerian Pidgin |
| `firmware/xiao-esp32s3-camera` | Arduino/PlatformIO | XIAO ESP32-S3 camera: periodic capture (default) + on-demand real MJPEG live stream via command |
| `firmware/dht22-node` | Arduino/PlatformIO | Generic ESP32 dev board + DHT22 temp/humidity sensor |
| RabbitMQ (+ MQTT plugin) | — | **All** device traffic (in and out) flows through the built-in `amq.topic` exchange — this was a major bug we found and fixed; devices can never reach custom exchanges like `iot.telemetry` |
| Postgres | — | Accounts, devices, zones, rules, alerts, reminders, frames metadata |
| TimescaleDB | — | Real time-series history for dashboard charts (`readings` hypertable) |
| MinIO | — | Camera frame object storage, presigned URLs for viewing |

**Topic convention:** `vert.<zone>.<device_type>.<device_id>.<kind>` (kind = `reading`/`frame`/`announce`/`offline`/`cmd`/`alert`)

**Device trust:** each device is provisioned (admin-only) and gets a one-time-shown secret, used as both MQTT username/password and HTTP Basic Auth for frame uploads. RabbitMQ enforces this itself via HTTP callbacks to the gateway (`/internal/rabbitmq/auth/*`), checked on every single message, not just at connect.

Full design rationale lives in `VERT-Project-Overview.docx` in the project root (kept updated across the project's build-out) and `docs/TESTING.md`.

## What's built and verified working (as of this session)

- Full `docker compose up` stack: gateway, automation, inference, frontend, broker, postgres, timescaledb, minio — all healthy, with automatic reconnect logic on all three custom Go/Python services (survives host sleep, RabbitMQ restarts, etc.) plus `restart: unless-stopped`.
- Real per-device credential enforcement (verified: wrong secret gets rejected by RabbitMQ itself).
- Real-time dashboard charts backed by TimescaleDB (`/api/v1/devices/{id}/metrics`, `/readings`), with live-polling sparklines on device cards and full charts w/ time-range + metric tabs on the device detail page.
- Manual device command channel (`POST /api/v1/devices/{id}/command`), separate from automation's rule-triggered commands.
- **Camera fully verified end-to-end with real hardware**: provisioned, online, uploading photos every 60s, AI health analysis running on real photos, MinIO presigned URLs working (fixed a "public vs internal MinIO endpoint" bug), and a genuine **on-demand live MJPEG stream** (not just snapshots) — compiled, flashed, and confirmed via `curl` (866KB of real JPEG frames captured) *and* watched live in the actual browser dashboard via the eye-icon toggle.
- Frontend PWA verified rendering correctly (had to debug a stale-service-worker/browser-cache red herring along the way — not a real bug).

## Currently blocked: DHT22 board can't connect (unresolved)

This is the live issue when this session ended. Chain of real bugs found and fixed, **in order**, to get here:

1. ~~`firmware/dht22-node/platformio.ini` had no `upload_port` set~~ → fixed, set to `COM4` (confirmed via Device Manager).
2. ~~CP2102 USB-UART driver was in `CM_PROB_FAILED_INSTALL` state in Device Manager~~ → user downloaded/installed the Silicon Labs CP210x Universal Windows Driver → now shows clean "OK" status on COM4.
3. ~~`config.h` had `MQTT_PORT 8080`~~ (that's the gateway's HTTP port, not MQTT) → fixed to `1883`.
4. ~~`config.h`'s `DEVICE_SECRET` was stale~~ (I'd re-provisioned `dht22-01` earlier in the session to test with a simulator script, which invalidates the old secret) → updated to the currently-valid secret.
5. ~~`MQTT_HOST`/`UPLOAD_HOST` hardcoded to a stale IP `192.168.1.194`~~ → the host machine's DHCp lease had changed → updated to `192.168.1.25` (whatever was current at the time).
6. Windows Firewall network profile for the Wi-Fi connection was `Public` (blocks inbound LAN traffic) instead of `Private` → user was asked to fix via Settings → **not confirmed complete**.
7. **The actual root cause, finally found**: the Windows host (running Docker) was connected to Wi-Fi network **"Super Wi-Fi 5G"**, while both ESP32 boards have `WIFI_SSID "Airbox-7113"` hardcoded in their `config.h`. Two different Wi-Fi networks — no IP fix can ever work across that; the board literally cannot route to the host. This explains every `MQTT connect failed, rc=-2` (TCP-level connect failure, not a credentials rejection).
8. User chose to fix this by reconnecting the **Windows PC** to `Airbox-7113` (rather than reflashing both boards for the other network) — **this was in progress when this handoff was written; not yet confirmed done.**

### Next steps for the new chat, in order

1. Confirm the PC is actually on `Airbox-7113` now: `Get-NetConnectionProfile | Select-Object InterfaceAlias, Name, NetworkCategory` — check both the network `Name` and that `NetworkCategory` is `Private`, not `Public` (this reverted once already during the session).
2. Get the host's current IPv4 on that network: `ipconfig` → find the `Wi-Fi` adapter's address. (It may or may not still be `192.168.1.194` — DHCP could reassign anything.)
3. If the IP differs from what's currently in the two firmware `config.h` files, update both:
   - `firmware/dht22-node/src/config.h` → `MQTT_HOST`
   - `firmware/xiao-esp32s3-camera/src/config.h` → `MQTT_HOST` and `UPLOAD_HOST`
4. Reflash DHT22: `cd firmware/dht22-node; & "$env:USERPROFILE\.platformio\penv\Scripts\platformio.exe" run --target upload`
   - Board is on **COM4**. Close any open Serial Monitor first — it locks the port and causes `PermissionError`/`Access is denied`.
   - This board sometimes needs manual bootloader entry (hold BOOT, tap EN, hold BOOT ~1s more, release, then upload immediately) if you see `Wrong boot mode detected (0x17)` — but it has also auto-reset fine without this on other attempts. Try without first.
5. Verify real data is flowing (not stale test data):
   ```
   docker compose exec postgres psql -U vert -d vert -c "select now(), id, status, last_seen_at, last_reading from devices;"
   ```
   `last_seen_at` should be within the last few seconds and keep advancing on repeat checks. **Known stale test values to recognize and ignore if seen**: `{"temp_c": 23.0, "humidity_pct": 55.0}` and `{"temp_c": 35.4, "humidity_pct": 90.1}` are both from earlier simulator/test runs in this session, not real hardware.
6. Also sanity-check the camera (`xiao-esp-32-s3`) still connects after any network changes — it was working perfectly before this networking detour but shares the same "host IP baked into config.h" fragility.
7. Once confirmed live, check the dashboard (http://localhost:5173) shows the DHT22's sparkline updating in real time — this part of the frontend is already built and verified with test data, should just work once real data arrives.

## Useful environment specifics

- PlatformIO CLI (not on PATH, use full path): `C:\Users\UNESCO\.platformio\penv\Scripts\platformio.exe`
- DHT22/ESP32 board: **COM4**, generic `esp32dev`, CP2102 USB-UART chip (driver now installed)
- Camera board: XIAO ESP32-S3, native USB, auto-resets reliably (no BOOT+EN needed), was on **COM3**
- Docker containers: `vert-20-*` (gateway, automation, inference, frontend, broker, postgres, timescaledb, minio)
- First VERT account (admin): `you@example.com` / `a-real-password`
- Provisioned devices, both in zone "Plant Area": `dht22-01` (temp-humidity), `xiao-esp-32-s3` (camera)
- Current `dht22-01` device secret (as flashed): `K2KRUV2Q5MSM2H2CZXQKEHKPFGIOVJB7EBOHUMOOT4OFIMULXRGA`
- Current `xiao-esp-32-s3` device secret (as flashed): `666YX65VTQKHLVW44HR5A4BEQXPF2IJMF2J7TZNXKADSYPKFZVAQ`
  *(These are dev-only LAN secrets, already documented project-wide as "change before this ever leaves a trusted network." Re-provisioning either device invalidates its old secret — remember to update `config.h` and reflash if you do.)*
- Frontend dev/build: `cd frontend; npm run build` then `docker compose up -d --build frontend` (port 5173 is already bound by the Docker container, so PlatformIO's `preview_start` dev-server shortcut won't work without freeing it first)
- Gateway/automation rebuild pattern: `cd services/gateway; gofmt -w .; go build ./...` then `docker compose up -d --build gateway` (same pattern for `automation`)

## Known remaining gaps (from the project overview doc, still true)

TLS/local CA, RabbitMQ clustering/HA, offline push notifications, reminder due-date scheduler (CRUD exists, no proactive alerts), drag-and-drop dashboard layout, WebSocket/live push updates (dashboard currently polls), broader RBAC enforcement, automated tests, CI/CD, backup/restore, formal security review, rate limiting, native-speaker review of French/Pidgin translations, object-storage credential scoping for the inference service (currently reuses admin MinIO creds).
