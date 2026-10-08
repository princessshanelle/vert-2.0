const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, ShadingType, BorderStyle, AlignmentType, LevelFormat, PageBreak
} = require("docx");

const PAGE_WIDTH = 12240, PAGE_HEIGHT = 15840; // US Letter

function h1(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_1, spacing: { before: 400, after: 200 } }); }
function h2(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_2, spacing: { before: 300, after: 150 } }); }
function p(text, opts = {}) { return new Paragraph({ children: [new TextRun({ text, ...opts })], spacing: { after: 150 } }); }
function bullet(text) { return new Paragraph({ text, numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 } }); }

function code(lines) {
  return new Paragraph({
    children: (Array.isArray(lines) ? lines : [lines]).map((l, i) =>
      new TextRun({ text: l, font: "Consolas", size: 18, break: i === 0 ? 0 : 1 })
    ),
    shading: { type: ShadingType.CLEAR, fill: "F0F0F0" },
    spacing: { before: 100, after: 200 },
    border: { top: { style: BorderStyle.SINGLE, size: 2, color: "CCCCCC" }, bottom: { style: BorderStyle.SINGLE, size: 2, color: "CCCCCC" }, left: { style: BorderStyle.SINGLE, size: 2, color: "CCCCCC" }, right: { style: BorderStyle.SINGLE, size: 2, color: "CCCCCC" } }
  });
}

function cell(text, opts = {}) {
  return new TableCell({
    width: { size: opts.width || 2000, type: WidthType.DXA },
    shading: opts.header ? { type: ShadingType.CLEAR, fill: "1B5E20" } : undefined,
    children: [new Paragraph({ children: [new TextRun({ text, bold: !!opts.header, color: opts.header ? "FFFFFF" : "000000", size: 20 })] })]
  });
}

function table(headerRow, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({ children: headerRow.map((t, i) => cell(t, { header: true, width: widths[i] })) }),
      ...rows.map(r => new TableRow({ children: r.map((t, i) => cell(t, { width: widths[i] })) }))
    ]
  });
}

const doc = new Document({
  numbering: {
    config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }]
  },
  sections: [{
    properties: { page: { size: { width: PAGE_WIDTH, height: PAGE_HEIGHT } } },
    children: [
      new Paragraph({ children: [new TextRun({ text: "VERT", bold: true, size: 64, color: "1B5E20" })], spacing: { after: 100 } }),
      new Paragraph({ children: [new TextRun({ text: "Versatile Eco-farming Robotic Technology", italics: true, size: 28 })], spacing: { after: 100 } }),
      new Paragraph({ children: [new TextRun({ text: "Project Overview & Architecture — Internal Engineering Document", size: 22, color: "555555" })], spacing: { after: 100 } }),
      new Paragraph({ children: [new TextRun({ text: "Prepared: September 2026 — Revision 3: full frontend, plant-health heuristic, and reminders/alerts APIs implemented", size: 20, color: "777777" })], spacing: { after: 400 } }),

      h1("1. Introduction"),
      p("VERT (Versatile Eco-farming Robotic Technology) is a local-network-first smart greenhouse platform. It integrates environmental sensors (temperature/humidity, pH), cameras (USB and XIAO ESP32-C3/S3 camera modules), microcontrollers (ESP32, XIAO boards, Raspberry Pi), and actuators (relay-driven grow lights, water pumps, window/vent motors) into one coherent system."),
      p("The platform gives growers a dashboard for live monitoring, manual control (e.g. triggering irrigation), and rule-based automation (e.g. opening vents when temperature crosses a threshold). It also incorporates AI-based inference over camera feeds and sensor history to assess plant health."),
      p("VERT is designed to run entirely on a local network, with no dependency on internet connectivity, and to be approachable for non-technical users — both in how devices are added to the system and in how the dashboard is used day to day."),
      p("Every design choice in this document is held to one standard: does this make life easier for the person actually walking the greenhouse rows, or the family checking on their plants from another room? A clever architecture that a grower has to fight is not a good architecture. Where a trade-off had to be made, this document says which way it was made and why."),

      h1("2. Goals"),
      bullet("Robust, maintainable, extensible messaging between devices and backend services."),
      bullet("Plug-and-play device onboarding: adding a new sensor or actuator should not require editing configuration files or redeploying services."),
      bullet("An award-winning, but user-customizable, UI/UX — comparable in spirit to Home Assistant, tailored specifically to greenhouse operations."),
      bullet("Full functionality on an isolated local network, with no internet dependency."),
      bullet("Multi-language support from day one: English, French, and Nigerian Pidgin, with a clear path to add further local languages."),
      bullet("A built-in alerting, notification, and manual-reminder system."),
      bullet("Every device, and every person, is who they say they are — verified by the system itself, not assumed."),

      h1("3. System Architecture"),
      p("VERT is organized into five layers. Each layer only depends on the one below it, which keeps the system maintainable as devices, services, and UI evolve independently."),
      table(
        ["Layer", "Responsibility", "Technology"],
        [
          ["Device layer", "Sensors and actuators; publish readings, receive commands", "ESP32, XIAO ESP32-C3/S3, Raspberry Pi, USB camera host"],
          ["Broker layer", "Message transport, routing, delivery guarantees, per-device authentication", "RabbitMQ + MQTT plugin + HTTP auth backend"],
          ["Backend services", "Accounts, device registry, ingestion, automation, AI inference", "Go (gateway, automation), Python (inference)"],
          ["Storage", "Accounts/registry, time-series data, images", "PostgreSQL, TimescaleDB, MinIO/S3"],
          ["Frontend", "Dashboard, control, configuration, alerts", "Vue 3 PWA"]
        ],
        [2400, 4600, 3200]
      ),
      new Paragraph({ text: "", spacing: { after: 150 } }),
      p("Data flow, in words:", { bold: true }),
      bullet("A device publishes a reading or lifecycle event over MQTT to RabbitMQ's MQTT plugin, authenticating with credentials issued to it individually (Section 5)."),
      bullet("RabbitMQ maps the MQTT topic onto an AMQP topic exchange (amq.topic), based on the routing-key convention described in Section 4, and checks — on every single message, not just at login — that the routing key belongs to that device."),
      bullet("The Go gateway service consumes from purpose-specific queues bound to those exchanges: one queue for telemetry ingestion, one for device lifecycle events."),
      bullet("Telemetry updates the device registry in PostgreSQL today; writing readings through to TimescaleDB is the next piece of this consumer to land (Section 15)."),
      bullet("The Go automation service separately consumes every telemetry reading, evaluates it against the rules a grower has configured, and — when a rule's condition has held true for as long as it specifies — publishes a command back to a device and records a plain-language alert (Section 6)."),
      bullet("The Python inference service consumes camera-frame-ready events, runs plant-health analysis, and republishes the result as an ordinary telemetry reading — indistinguishable on the wire from a physical sensor."),
      bullet("Camera frames themselves go to MinIO/S3 over a separate, authenticated HTTP upload — never through the message broker, which is sized for small control-plane messages, not images (Section 7)."),
      bullet("The Vue dashboard calls the gateway's and automation service's HTTP APIs to read and change state, and will (see roadmap) receive live updates over WebSocket."),

      h1("4. Messaging Design (RabbitMQ)"),
      h2("4.1 Exchanges"),
      table(
        ["Exchange", "Type", "Purpose"],
        [
          ["iot.telemetry", "topic", "Sensor and AI-derived readings"],
          ["iot.commands", "topic", "Dashboard/automation → device commands"],
          ["iot.events", "topic", "Device lifecycle and alerts: registration, online/offline, rule-triggered alerts"],
          ["iot.ai", "topic", "Camera-frame-ready events for the inference service"]
        ],
        [2600, 1400, 6200]
      ),
      new Paragraph({ text: "", spacing: { after: 150 } }),
      h2("4.2 Topic naming convention"),
      p("Routing keys are structured and self-describing, so adding a new device type never requires new parsing logic — only a new binding pattern. Every topic is namespaced under vert. so the broker's own default exchange traffic, and anything else that might one day share this RabbitMQ instance, can never collide with it."),
      code("vert.<zone>.<device_type>.<device_id>.<kind>"),
      p("Examples:"),
      code(["vert.zoneA.temp-humidity.dht22-01.reading", "vert.zoneA.window.window01.cmd", "vert.zoneA.camera.cam01.frame", "vert.zoneA.temp-humidity.dht22-01.alert"]),

      h2("4.3 Message envelope"),
      p("Every device and service publishes against the same JSON envelope. Consumers reject or upgrade unknown schema_version values rather than guessing at payload shape."),
      code([
        "{",
        "  \"schema_version\": \"1.0\",",
        "  \"device_id\": \"dht22-01\",",
        "  \"device_type\": \"temp-humidity\",",
        "  \"zone\": \"zoneA\",",
        "  \"ts\": \"2026-09-13T10:22:00Z\",",
        "  \"seq\": 1042,",
        "  \"payload\": { \"temp_c\": 23.4, \"humidity_pct\": 61.2 }",
        "}"
      ]),

      h2("4.4 Reliability"),
      bullet("Dead-letter exchanges on every queue, so a message a consumer cannot process (bad schema, downstream outage) is captured instead of silently dropped or blocking the queue."),
      bullet("Publisher confirms on the command path, so the backend knows a command (e.g. “close window”) was actually accepted by the broker."),
      bullet("Per-queue TTL and max-length on telemetry queues, so a slow consumer cannot cause unbounded memory growth."),
      bullet("Quorum queues (not classic mirrored queues) if/when RabbitMQ is clustered for high availability — not yet configured (Section 15)."),

      h2("4.5 Device onboarding (implemented)"),
      p("A device is provisioned before it ever powers on, and is never anonymous:"),
      bullet("An admin calls POST /api/v1/devices/provision with a device_id and device_type. The gateway generates a high-entropy secret, stores only its hash, and returns the secret exactly once — there is no “forgot password” for a device, only re-provisioning, which issues a new secret and immediately invalidates the old one."),
      bullet("That device_id/secret pair is the device's identity everywhere: MQTT username/password, and HTTP Basic Auth for camera frame uploads. One identity, one credential, checked the same way regardless of protocol."),
      bullet("When the device first connects and announces its capabilities (what it reads and/or writes), the gateway's lifecycle consumer records that in the device registry and assigns it into a zone that a person sets from the dashboard/API."),
      bullet("A new sensor type requires a new capability descriptor and a small UI renderer — never a change to the ingestion pipeline or a RabbitMQ configuration change."),

      h1("5. Device Trust: How Per-Device Credentials Are Enforced"),
      p("This is the part of the system that stands between “a device says it's dht22-01” and “RabbitMQ has actually verified that.” It matters because these credentials guard real actuators — a spoofed device could otherwise tell a pump or a window motor to do something the grower never asked for."),
      p("RabbitMQ's own user database (used by the gateway, automation, and inference services — see Section 9) has no idea what a “device” is. Instead, RabbitMQ is configured with two chained authentication backends:"),
      bullet("Internal (built into RabbitMQ): recognizes VERT's own backend services, declared once in infra/rabbitmq/definitions.json."),
      bullet("HTTP: for any username the internal backend doesn't recognize — which, by construction, is every device — RabbitMQ calls back to the gateway to ask."),
      p("The gateway answers four questions RabbitMQ asks it, on every connection and every single message a device sends or receives (services/gateway/internal/api/rabbitmq_auth.go):"),
      table(
        ["Question RabbitMQ asks", "Gateway's answer"],
        [
          ["Is this username/password a real device credential?", "Look up the device's stored secret hash and compare in constant time — no bcrypt here; a device secret is already a high-entropy random token, not a human password, so a fast comparison is the correct tool, not a shortcut."],
          ["Can this device use this vhost?", "Yes, always — there is only one vhost."],
          ["Can this device touch this exchange or queue?", "Only the shared vert topic exchange, and only the one MQTT-plugin-managed queue that belongs to its own connection."],
          ["Can this device publish or subscribe to this exact routing key?", "Only if the device_id segment of the routing key (vert.<zone>.<device_type>.<device_id>.<kind>) matches the authenticated username — a pure string check, so this costs no database round trip on the hot path of every message."]
        ],
        [3200, 6600]
      ),
      new Paragraph({ text: "", spacing: { after: 150 } }),
      p("The practical result: a compromised or misconfigured sensor cannot see another device's readings, cannot impersonate another device's commands, and a wrong password is rejected by RabbitMQ itself — not merely “not trusted by the application logic.” docs/TESTING.md, Step 5, shows how to verify this by trying a deliberately wrong secret."),

      h1("6. Automation / Rules Engine (implemented)"),
      p("The automation service (services/automation) is its own consumer, independent from ingestion, so a bug evaluating one grower's rule can never back up or crash the pipe that keeps every other reading flowing."),
      h2("6.1 Rule shape"),
      p("A rule has a condition and an action, both plain JSON, editable through the automation service's own API (POST/PATCH/DELETE /api/v1/rules) without redeploying anything:"),
      code([
        "condition: { \"metric\": \"temp_c\", \"op\": \">\", \"value\": 40, \"for_minutes\": 10 }",
        "action:    { \"device_id\": \"window01\", \"command\": \"open\" }"
      ]),
      p("A rule applies either to one specific device, or to every device in a zone — whichever the person creating it chooses."),
      h2("6.2 Sustained breach, not a single reading"),
      p("The engine tracks, per rule and per device, when a breach started. It only fires once the condition has held continuously for for_minutes, and it will not fire again for the same breach episode — the condition has to clear and then recur before it can trigger a second time. This is a deliberate hysteresis: nobody wants a vent commanded to “open” on every single reading for as long as it's hot outside; one action, one alert, per episode."),
      h2("6.3 What happens when a rule fires"),
      bullet("A command envelope is published to iot.commands, addressed to the action's target device, in the same envelope shape as everything else on the bus."),
      bullet("An alert row is written to PostgreSQL, and the same alert is published to iot.events — the persisted copy is the source of truth; the published copy is what a future live notification feature (Section 13) would subscribe to."),
      bullet("The alert's message is composed in plain language for whoever reads it next, often on a phone between other jobs — for example: “Zone A too hot: dht22-01 has read temp_c 42.0 for over 10 minutes, so VERT is telling window01 to open.” Not a log line; a sentence a tired person can understand at a glance."),
      h2("6.4 Who can manage rules"),
      p("Viewing rules is open to any signed-in account. Creating, enabling/disabling, or deleting a rule is restricted to admin and operator accounts — a viewer account (e.g. a family member checking in) can see what's automated without being able to change what the pumps and vents do."),

      h1("7. Object Storage: Camera Frames in MinIO/S3 (implemented)"),
      p("Images never touch the message broker — RabbitMQ is sized for small, frequent control-plane messages, not JPEGs. A camera device instead:"),
      bullet("Uploads the raw frame over a plain, authenticated HTTP POST directly to the gateway (the same per-device credential from Section 5, sent as HTTP Basic Auth)."),
      bullet("The gateway streams it into a MinIO/S3 bucket (auto-created on first boot, so nobody has to manually configure a bucket before the first photo can be taken) and records the object key, device, size, and content type in PostgreSQL."),
      bullet("The device then publishes a small MQTT event referencing the upload, which the inference service consumes to run plant-health analysis (Section 3)."),
      p("For viewing, the gateway hands the dashboard short-lived, presigned URLs (15-minute expiry) rather than making the bucket or its objects public — GET /api/v1/devices/{deviceID}/frames returns a list of recent photos, each with a link that opens directly in a browser. Nobody should have to understand S3 credentials to see a picture of their own plant."),

      h1("8. AI Plant-Health Heuristic (Inference Service, implemented)"),
      p("services/inference is deliberately honest about what it is: there is no trained model, no dataset, and no training pipeline behind it yet. What exists instead is a real, documented computer-vision technique — the Excess Green Index (ExG = 2G − R − B), long used in agricultural imaging to separate live vegetation from soil, pots, and background — applied plainly, with its limits stated rather than dressed up as something it isn't."),
      h2("8.1 What it actually computes"),
      bullet("The photo is resized (longest edge 512px) and every pixel's Excess Green value is computed; the share of pixels that clear a vegetation threshold becomes green_fraction, which is the health_score."),
      bullet("Mean brightness is also checked. A photo that is too dark or too blown-out cannot be read honestly, so the service reports a low confidence and says so in plain language, rather than quietly giving a misleading score."),
      bullet("The output is never a bare number: health_score, a label (thriving / steady / stressed / needs_attention), a confidence, and a short, warm, human-readable note are all returned together, e.g. “Less green than usual in this shot. Not an emergency, but worth a closer look when you have a moment.”"),
      h2("8.2 Why this is the right amount of “complete” for now"),
      p("A grower checking on a camera device today gets an honest, immediate, free signal instead of nothing — better than a placeholder that always says “unknown,” and not dressed up as more certain than it is. The entire surface a future trained model needs to replace is one function, analyze(image_bytes) -> HealthResult, in services/inference/vision.py: nothing in the message consumer, the storage client, or main.py needs to change on the day a real classifier is trained and swapped in."),
      h2("8.3 How a frame gets analyzed"),
      bullet("A camera device uploads its JPEG over HTTP (Section 7) and separately publishes a small MQTT event referencing it on vert.<zone>.camera.<id>.frame."),
      bullet("The inference service consumes that event, fetches the image directly from MinIO (reusing the platform's S3 credentials today — scoping this down to a read-only credential is tracked in Section 17), and runs analyze()."),
      bullet("The result is republished as an ordinary telemetry reading, under the camera's own device_id, with device_type \"plant-health-model\" — the automation engine and dashboard treat it exactly like a sensor reading, because to them it is one."),
      bullet("The gateway's telemetry consumer remembers it as that device's “latest reading” (Section 11), which is what the dashboard and device detail page actually display."),

      h1("9. Language Choices"),
      table(
        ["Component", "Language", "Rationale"],
        [
          ["Gateway (accounts, devices, frames, RabbitMQ auth webhooks)", "Go", "Native goroutines make per-connection and per-consumer concurrency cheap; mature RabbitMQ (amqp091-go) and MinIO (minio-go) libraries; compiles to a static binary that deploys easily to a Raspberry Pi; simple enough for an average developer to maintain."],
          ["Automation (rules engine)", "Go", "Shares the gateway's concurrency and deployment advantages; a rules engine is a small, latency-sensitive consumer — exactly Go's sweet spot — and is deliberately its own binary rather than a package inside the gateway, so a rule-evaluation bug can never take ingestion down with it."],
          ["AI inference service", "Python", "Unmatched ML/vision ecosystem (numpy, Pillow, and eventually PyTorch/TensorFlow when a trained model replaces today's heuristic); rapid iteration matters more than raw concurrency here."],
          ["Frontend", "Vue 3", "Approachable component model, strong PWA tooling (vite-plugin-pwa), first-class i18n support via vue-i18n."]
        ],
        [3200, 1200, 5800]
      ),

      h1("10. Authentication & Accounts"),
      bullet("User accounts: email + bcrypt-hashed password, JWT issued on register/login, 30-day expiry, shared secret between the gateway and automation service so one login covers both APIs."),
      bullet("The very first account created on a fresh instance becomes an admin automatically — nobody standing up their own greenhouse's VERT instance should need a hidden bootstrap step or a shared default password just to get past the front door. Every account after that defaults to operator."),
      bullet("Roles: admin, operator, viewer. Enforced today for device provisioning, zone management, and automation rule management; broader enforcement across every endpoint is still ahead (Section 17)."),
      bullet("Device credentials are entirely separate from user accounts, as described in Section 5 — a device is never able to log in as a person, and a person's password is never a device's password."),

      h1("11. Frontend / UX (implemented)"),
      p("The dashboard is a Vue 3 Progressive Web App: installable to a home screen, and able to keep working offline via a service-worker-cached shell, which matters on a network with no internet uplink. As of this revision it is a complete, working application, not a single-page proof of concept — every backend capability described above has a real screen behind it."),
      h2("11.1 What's there today"),
      table(
        ["Screen", "What it does"],
        [
          ["Dashboard", "Devices grouped by zone, live status dot, last-seen time, and a one-click path to provisioning the very first device when there are none yet."],
          ["Device detail", "Status, zone (reassignable inline for admins/operators), the device's latest reading rendered as either plain stat tiles or — for a camera — the plant-health note from Section 8, plus a photo gallery of everything it has uploaded."],
          ["Zones", "Create zones and see how many devices live in each, matching however the greenhouse is actually laid out."],
          ["Add device (admin only)", "Provisions a device (Section 5) and shows the one-time secret with a copy button and an unmissable warning that it will not be shown again."],
          ["Automation", "Create, pause/enable, and delete rules (Section 6) with a plain form — pick a zone or a device, a reading, a comparison, a duration, and what to tell which device to do."],
          ["Alerts", "Every alert the automation engine has raised, newest and unacknowledged first, with a one-tap “Got it.” A quiet greenhouse shows a reassuring empty state, not a blank table."],
          ["Reminders", "Personal, per-account reminders with a due date and optional recurrence — separate from system alerts, because “I should reorder pH strips” and “the automation engine noticed something” are different kinds of things (Section 13)."],
          ["Settings", "Account info, role, and the language switcher, plus a short, plain-language “about VERT” note."]
        ],
        [2200, 8000]
      ),
      new Paragraph({ text: "", spacing: { after: 150 } }),
      h2("11.2 The design split that makes it maintainable"),
      bullet("The VERT team owns the visual design system — spacing, color, typography, motion — which is what makes the product feel like one coherent thing rather than a pile of forms."),
      bullet("Users customize layout and data, not the design language: which zone a device sits in, what a rule watches, what a reminder says. This avoids users assembling something visually inconsistent, while still making the dashboard theirs."),
      bullet("Device capability descriptors (Section 4.5) drive which card and detail layout renders for a given device, so the UI extends automatically as new device types are added."),
      h2("11.3 What's still ahead here"),
      bullet("A true drag-and-drop, user-arranged dashboard layout (today's grouping is automatic, by zone, not yet user-rearrangeable) — Section 17."),
      bullet("Live updates over WebSocket/SSE — today the dashboard reflects whatever was true the moment a page loaded, via ordinary HTTP polling on navigation, not a live push the instant a rule fires."),

      h1("12. Internationalization"),
      p("Only the PWA is translated. The backend, firmware, and message envelopes remain English/machine-readable identifiers only — translation is purely a presentation-layer concern."),
      bullet("Implemented with vue-i18n, using flat JSON locale files keyed by feature (e.g. dashboard.title, alerts.acknowledge), never by literal source strings."),
      bullet("Locales shipped today: English (en), French (fr), and Nigerian Pidgin (pcm) — all three now cover every screen listed in Section 11, not just login and the original dashboard, verified to have identical key sets across all three files."),
      bullet("Adding a new local language (e.g. Hausa, Yoruba) means adding one new locale JSON file and registering its code — no component code changes."),
      bullet("Nigerian Pidgin has no widely used ISO 639-1 code (pcm is the closest ISO 639-3 code) and no reliable machine-translation quality yet; that locale requires human native-speaker review rather than automated translation (see roadmap)."),

      h1("13. Alerts, Notifications, and Reminders"),
      p("These are three distinct concepts with different triggers and lifecycles, unified in one notification center in the UI:"),
      bullet("Alerts: system-generated, tied to automation rule thresholds. These are real — the automation engine (Section 6) writes them to PostgreSQL and publishes them to iot.events, with a plain-language message rather than a log line, and the Alerts screen (Section 11) lets anyone signed in acknowledge one, not just admins/operators."),
      bullet("Notifications: the delivery mechanism that reaches a user who isn't currently looking at the dashboard. On a LAN-only, no-internet deployment, standard Web Push (which assumes reachability to a browser vendor's push service) does not work unmodified — this is still unresolved (Section 17) and the options remain a self-hosted local push server, or an SMS fallback where cellular coverage exists."),
      bullet("Reminders: user-created, scheduled, not device-triggered (e.g. “calibrate the pH sensor every two weeks”). As of this revision these have a full CRUD API (gateway: /api/v1/reminders) and a real screen (Section 11) — create, mark done, delete, optional recurrence. What's not yet built is a scheduler that proactively surfaces a reminder as it comes due (today it's a list you check, not something that taps you on the shoulder)."),

      h1("14. Local-Network-Only Operation"),
      p("VERT is designed to run fully airgapped. The broker, databases, object storage, gateway, automation, and dashboard all run on local hardware (a Raspberry Pi 4/5 or small NUC is sufficient for a single-greenhouse deployment); devices and browsers reach them by LAN IP or local hostname."),
      p("Requirements for this to work smoothly:"),
      bullet("Local time sync: run a local NTP server (or let the on-site server be the time source) rather than relying on internet NTP, so envelope timestamps stay consistent."),
      bullet("Local discovery: mDNS/Avahi (e.g. vert.local) instead of public DNS, so device and dashboard discovery doesn't depend on external resolution."),
      bullet("Local certificate authority: a self-signed local CA distributed once to devices/browsers, rather than depending on an internet-issued certificate authority such as Let's Encrypt — not yet set up (Section 17)."),
      bullet("On-site AI inference: models run on local hardware rather than calling a hosted cloud vision API, which is consistent with keeping the whole system offline-capable."),

      h1("15. Repository Structure"),
      p("The scaffolded project (see accompanying source tree) is organized as follows:"),
      code([
        "docker-compose.yml         Full local stack: broker, databases, object storage, gateway, automation, inference, frontend",
        "infra/                     RabbitMQ (topology + per-device auth wiring) and Postgres bootstrap configuration",
        "services/gateway/          Go: accounts, device registry & provisioning, camera frame storage, reminders, AMQP ingestion",
        "services/automation/       Go: the rules engine — threshold rules → device commands + alerts",
        "services/inference/        Python: the plant-health heuristic (Section 8), consumes camera frames",
        "frontend/                  Vue 3 PWA — full app: dashboard, zones, provisioning, automation, alerts, reminders, settings",
        "firmware/dht22-node/       ESP32 + DHT22 sample device",
        "firmware/xiao-esp32s3-camera/  XIAO ESP32-S3 Sense camera sample device",
        "scripts/simulate_device.py A pretend sensor, for testing before any hardware exists",
        "docs/TESTING.md            Full local walkthrough, start to finish"
      ]),
      bullet("services/gateway: exposes /api/v1/auth/*, /api/v1/me, /api/v1/devices (+ /provision, /{id}/frames, /{id}/zone), /api/v1/zones, /api/v1/reminders, and the /internal/rabbitmq/auth/* webhooks described in Section 5; runs the telemetry and lifecycle AMQP consumers described in Section 3, including the “latest reading” persistence behind Section 11's device detail screen."),
      bullet("services/automation: exposes /api/v1/rules and /api/v1/alerts; runs the telemetry consumer and rule-evaluation loop described in Section 6."),
      bullet("services/inference: consumes vert.*.camera.*.frame events, fetches the frame from MinIO, and republishes the Excess Green Index result described in Section 8 as a plant-health reading."),
      bullet("frontend: every screen listed in Section 11.1, a Pinia store per resource (auth, devices, rules, alerts, reminders), and the i18n setup described in Section 12."),
      bullet("firmware/dht22-node and firmware/xiao-esp32s3-camera: publish readings/frames and a one-time capability announcement over MQTT, authenticated with the credential issued at provisioning (Section 5)."),

      h1("16. Testing This Locally"),
      p("docs/TESTING.md is a full, copy-pasteable walkthrough: bring up docker compose, register the first (automatically admin) account, create a zone, provision a device, simulate a sensor reading with scripts/simulate_device.py (no hardware required), watch an automation rule fire and produce a plain-language alert, upload a camera frame, trigger the plant-health heuristic on it, and see the result on the device's page. It also covers what a rejected wrong-secret connection looks like, and the most common first-run problems, and notes throughout where the web UI can be used instead of the raw API calls."),
      p("This section exists because a document that describes an architecture is not the same thing as a system someone has actually watched work — the walkthrough is what closes that gap."),

      h1("17. Known Gaps / What's Missing"),
      p("This is the current, honest state of the scaffold. Items are ordered roughly by how soon they block real usage. Automation, object storage, per-device upload authentication, the plant-health heuristic, and a complete frontend were open in earlier revisions of this document; they are now implemented and listed here as done, for the record."),
      table(
        ["Area", "Status", "Notes"],
        [
          ["Automation / rules engine", "Implemented", "See Section 6. Threshold + sustained-duration rules, scoped to a device or a zone, with hysteresis so one breach produces one command and one alert."],
          ["Object storage for images", "Implemented (MinIO/S3)", "See Section 7. Bucket auto-created on boot; frames recorded in PostgreSQL; dashboard reads get short-lived presigned URLs, not public links."],
          ["Device upload / connection authentication", "Implemented", "See Section 5. Enforced by RabbitMQ itself via the gateway's HTTP auth backend for MQTT, and HTTP Basic Auth checked against the same credential for frame uploads."],
          ["Plant-health image analysis", "Implemented (heuristic baseline)", "See Section 8. A real, documented computer-vision technique (Excess Green Index), honestly not a trained model -- analyze() is a stable, swappable interface for when one exists."],
          ["Frontend application", "Implemented", "See Section 11. Every backend capability above has a working screen: dashboard, device detail, zones, provisioning, automation rules, alerts, reminders, settings -- fully translated in all three locales."],
          ["“Latest reading” per device", "Implemented", "The gateway remembers each device's most recent payload (sensor value or AI health result) so the dashboard has something real to show; full history is the next item."],
          ["Time-series persistence", "Not implemented", "Only the latest value is kept (see above); full history is not yet written to TimescaleDB, so there is no trend chart yet."],
          ["TLS / local certificate authority", "Not set up", "Needed for secure LAN traffic per Section 14; no CA or cert issuance process exists yet."],
          ["RabbitMQ high availability", "Not set up", "Single-node broker; no clustering or quorum-queue configuration for production resilience."],
          ["Offline notification delivery", "Unresolved", "Standard Web Push does not work without internet reachability to the browser vendor; a local push server or SMS fallback still needs to be built (Section 13)."],
          ["Reminder scheduler", "CRUD implemented, no proactive alerts", "Reminders can be created, completed, and deleted from a real screen (Section 11), but nothing yet taps the user on the shoulder as a due date arrives -- today it's a list you check, not a notification."],
          ["Dashboard layout customization UI", "Not built", "Devices are grouped automatically by zone; a drag-and-drop, user-arranged layout described in Section 11.3 is a design goal, not yet implemented."],
          ["Live dashboard updates", "Not built", "The frontend reflects whatever was true when a page loaded (ordinary HTTP, refetched on navigation); a WebSocket or SSE channel for live readings/alerts is planned but not built."],
          ["Object-storage credential scoping", "Not done", "The inference service currently reuses the platform's MinIO admin credentials to fetch frames (Section 8.3); a read-only, bucket-scoped credential is a good first hardening task."],
          ["Role-based access control", "Partial", "Enforced for device provisioning, zone management, and rule management (Sections 6, 10); not yet enforced across every endpoint."],
          ["Automated testing", "None", "No unit, integration, or firmware tests exist yet for any service -- vision.py's heuristic was verified manually against synthetic images, not with a test suite."],
          ["CI/CD", "None", "No build/test/deploy pipeline has been set up."],
          ["Backup and restore", "Not addressed", "No strategy yet for backing up PostgreSQL, TimescaleDB, or the MinIO bucket."],
          ["Security review", "Not done", "No formal review of authentication, transport security, or device trust boundaries — Section 5 describes the design intent, not an audit result."],
          ["Rate limiting", "Not implemented", "The HTTP APIs and MQTT connections have no throttling against abuse or a runaway/misbehaving device."],
          ["French / Pidgin translation review", "Needs native-speaker review", "All three locale files now have identical key coverage across the whole app (Section 12), but were drafted directly rather than reviewed by native speakers -- especially important for Pidgin."],
          ["Mobile packaging beyond PWA", "Not planned yet", "If native app-store distribution is ever needed, that is a separate effort from the PWA."],
          ["Load / scale testing", "Not done", "The system has not been tested with a realistic number of concurrent devices or message volume."]
        ],
        [2600, 2300, 5300]
      ),

      new Paragraph({ children: [new PageBreak()] }),
      h1("Appendix: Glossary"),
      bullet("Envelope — the shared JSON message shape every device/service publishes against."),
      bullet("Capability descriptor — a small JSON document a device announces on registration, describing what it reads and/or writes."),
      bullet("Zone — a logical grouping of devices within the greenhouse (e.g. “zoneA”), used for both message routing, automation rule scope, and dashboard organization."),
      bullet("Provisioning — the admin action that creates a device's identity and one-time-visible secret before it ever connects."),
      bullet("Breach episode — one continuous stretch of an automation rule's condition being true; the engine fires at most once per episode (hysteresis), not once per reading."),
      bullet("Presigned URL — a temporary, signed link to a private object-storage file, so the dashboard can display a photo without the bucket or its contents being public."),
      bullet("Excess Green Index (ExG) — a computer-vision formula (2G − R − B) used to tell vegetation pixels apart from soil/background; the basis of today's plant-health heuristic (Section 8)."),
      bullet("Latest reading — the most recent payload a device (or an AI result publishing under that device's id) reported, remembered by the gateway so the dashboard has something current to show, distinct from full time-series history."),
      bullet("Quorum queue — RabbitMQ's replicated queue type, recommended over classic mirrored queues for clustered high availability."),
    ]
  }]
});

Packer.toBuffer(doc).then(buf => require("fs").writeFileSync(process.argv[2], buf));
