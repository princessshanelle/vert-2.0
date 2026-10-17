// VERT greenhouse controller: one ESP32 DevKit V1 as five VERT devices.
//
//   climate (temp-humidity)  DHT22                    temp_c, humidity_pct
//   soil    (soil-moisture)  1-4 capacitive probes    soil_moisture_pct (average), soil_<n>_pct, soil_<n>_raw
//   ph      (ph)             analog pH module         ph, ph_mv
//   pump    (pump)           relay                    pump_on, pump_run_s, pump_backup   commands: on, off
//   light   (light)          relay (grow-light bulb)  light_on                           commands: on, off
//
// Each device has its own provisioned secret and its own MQTT session,
// exactly like the single-sensor nodes in firmware/: the broker lets a
// device publish and subscribe only on its own topics
//   vert/<zone>/<type>/<id>/{reading,announce,offline,cmd}
// so one board carrying five identities needs five sessions. Readings use
// the envelope in services/gateway/internal/mq/envelope.go; announce and
// the offline last-will use the gateway's lifecycle format (plain JSON
// with "kind").
//
// Safety is local: the relays start OFF, the pump always stops after
// PUMP_MAX_RUN_S no matter what asked for it, and if the Pi is unreachable
// for BACKUP_AFTER_S the board waters by itself from the soil readings
// (the old standalone sketch's 30 %/70 % logic) until the Pi is back.
#include <Arduino.h>
#include <algorithm>
#include <vector>
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h>

#include "config.h"

// ---------------------------------------------------------------------------
// One VERT identity = one MQTT session.
// ---------------------------------------------------------------------------
struct Link {
  Link(const char* id_, const char* secret_, const char* type_, std::vector<const char*> reads_,
       std::vector<const char*> commands_, void (*onCommand_)(const char*))
      : id(id_), secret(secret_), type(type_), reads(reads_), commands(commands_), onCommand(onCommand_), mqtt(net) {}

  const char* id;
  const char* secret;
  const char* type;
  std::vector<const char*> reads;
  std::vector<const char*> commands;
  void (*onCommand)(const char* command);  // nullptr for sensors

  WiFiClient net;
  PubSubClient mqtt;
  char tReading[112], tAnnounce[112], tOffline[112], tCmd[112];
  char will[128];
  uint32_t seq = 0;
  uint32_t nextTry = 0;
  uint32_t backoffMs = 2000;
};

void pumpCommand(const char* c);
void lightCommand(const char* c);

Link climate{CLIMATE_ID, CLIMATE_SECRET, "temp-humidity", {"temp_c", "humidity_pct"}, {}, nullptr};
Link soil{SOIL_ID, SOIL_SECRET, "soil-moisture", {"soil_moisture_pct"}, {}, nullptr};
Link phLink{PH_ID, PH_SECRET, "ph", {"ph"}, {}, nullptr};
Link pump{PUMP_ID, PUMP_SECRET, "pump", {"pump_on"}, {"on", "off"}, pumpCommand};
Link light{LIGHT_ID, LIGHT_SECRET, "light", {"light_on"}, {"on", "off"}, lightCommand};
Link* const LINKS[] = {&pump, &light, &climate, &soil, &phLink};  // actuators connect first

DHT dht(DHT_PIN, DHT22);
const uint8_t SOIL_PIN_LIST[] = SOIL_PINS;
static_assert(sizeof(SOIL_PIN_LIST) >= SOIL_COUNT, "SOIL_PINS lists fewer pins than SOIL_COUNT");

// ---------------------------------------------------------------------------
// Relays and state
// ---------------------------------------------------------------------------
bool pumpOn = false;
bool pumpByBackup = false;
uint32_t pumpStartedAt = 0;
uint32_t pumpStoppedAt = 0;
bool pumpEverRan = false;
bool pumpDirty = true;

bool lightOn = false;
bool lightDirty = true;

float soilAvg = NAN;          // NAN when no probe reads sensibly
uint32_t lastBrokerAt = 0;    // last time the pump's session was up

void setRelay(uint8_t pin, bool on) {
  digitalWrite(pin, (on != RELAY_ACTIVE_LOW) ? HIGH : LOW);
}

void startPump(bool byBackup, const char* why) {
  if (pumpOn) return;
  pumpOn = true;
  pumpByBackup = byBackup;
  pumpStartedAt = millis();
  pumpEverRan = true;
  setRelay(PUMP_RELAY_PIN, true);
  pumpDirty = true;
  Serial.printf("pump ON (%s)\n", why);
}

void stopPump(const char* why) {
  if (!pumpOn) return;
  pumpOn = false;
  pumpByBackup = false;
  pumpStoppedAt = millis();
  setRelay(PUMP_RELAY_PIN, false);
  pumpDirty = true;
  Serial.printf("pump OFF (%s)\n", why);
}

void setLight(bool on) {
  if (on == lightOn) return;
  lightOn = on;
  setRelay(LIGHT_RELAY_PIN, on);
  lightDirty = true;
  Serial.printf("light %s\n", on ? "ON" : "OFF");
}

void pumpCommand(const char* c) {
  if (!strcmp(c, "on") || !strcmp(c, "start")) startPump(false, "command");
  else if (!strcmp(c, "off") || !strcmp(c, "stop")) stopPump("command");
  else Serial.printf("pump: unknown command \"%s\" ignored\n", c);
  pumpDirty = true;  // always answer with the current state
}

void lightCommand(const char* c) {
  if (!strcmp(c, "on")) setLight(true);
  else if (!strcmp(c, "off")) setLight(false);
  else Serial.printf("light: unknown command \"%s\" ignored\n", c);
  lightDirty = true;
}

bool backupActive() {
  return !pump.mqtt.connected() && millis() - lastBrokerAt >= (uint32_t)BACKUP_AFTER_S * 1000UL;
}

// The pump's hard limit, then the backup logic. Runs every loop, Wi-Fi or not.
void servicePump() {
  if (pumpOn && millis() - pumpStartedAt >= (uint32_t)PUMP_MAX_RUN_S * 1000UL) {
    stopPump("max run time reached");
  }
  if (backupActive()) {
    if (isnan(soilAvg)) {
      if (pumpOn && pumpByBackup) stopPump("backup: no soil reading");
      return;
    }
    bool rested = !pumpEverRan || millis() - pumpStoppedAt >= (uint32_t)PUMP_MIN_REST_S * 1000UL;
    if (!pumpOn && soilAvg < BACKUP_SOIL_ON && rested) startPump(true, "backup: soil dry, Pi unreachable");
    if (pumpOn && pumpByBackup && soilAvg >= BACKUP_SOIL_OFF) stopPump("backup: soil wet enough");
  } else if (pumpOn && pumpByBackup) {
    stopPump("Pi back: rules in charge again");
  }
}

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------
void initLink(Link& l) {
  snprintf(l.tReading, sizeof l.tReading, "vert/%s/%s/%s/reading", ZONE, l.type, l.id);
  snprintf(l.tAnnounce, sizeof l.tAnnounce, "vert/%s/%s/%s/announce", ZONE, l.type, l.id);
  snprintf(l.tOffline, sizeof l.tOffline, "vert/%s/%s/%s/offline", ZONE, l.type, l.id);
  // Any zone: commands follow the device if an admin moves it to another zone.
  snprintf(l.tCmd, sizeof l.tCmd, "vert/+/%s/%s/cmd", l.type, l.id);
  snprintf(l.will, sizeof l.will, "{\"kind\":\"offline\",\"device_id\":\"%s\"}", l.id);
  l.mqtt.setServer(MQTT_HOST, MQTT_PORT);
  l.mqtt.setBufferSize(768);
  l.mqtt.setKeepAlive(30);
  l.mqtt.setSocketTimeout(4);
  if (l.onCommand) {
    Link* self = &l;
    l.mqtt.setCallback([self](char*, uint8_t* body, unsigned int n) {
      JsonDocument doc;
      if (deserializeJson(doc, body, n)) {
        Serial.printf("[%s] unreadable command ignored\n", self->id);
        return;
      }
      // Gateway and automation send the envelope {"payload": {"command": ...}}.
      const char* c = doc["payload"]["command"] | (doc["command"] | "");
      Serial.printf("[%s] command \"%s\"\n", self->id, c);
      self->onCommand(c);
    });
  }
}

void announce(Link& l) {
  JsonDocument doc;
  doc["kind"] = "announce";
  doc["device_id"] = l.id;
  doc["device_type"] = l.type;
  doc["ip"] = WiFi.localIP().toString();
  JsonObject caps = doc["capabilities"].to<JsonObject>();
  JsonArray reads = caps["reads"].to<JsonArray>();
  for (auto r : l.reads) reads.add(r);
  if (!l.commands.empty()) {
    JsonArray cmds = caps["commands"].to<JsonArray>();
    for (auto c : l.commands) cmds.add(c);
  }
  caps["board"] = "esp32-controller";
  char buf[512];
  size_t n = serializeJson(doc, buf, sizeof buf);
  l.mqtt.publish(l.tAnnounce, (const uint8_t*)buf, n, true);  // retained
}

// At most one connection attempt per call: a broker that's down must not
// stall the loop (and with it the pump's safety timer) for 5 x 4 s.
void serviceConnections() {
  if (WiFi.status() != WL_CONNECTED) return;
  for (Link* l : LINKS) {
    if (l->mqtt.connected() || (int32_t)(millis() - l->nextTry) < 0) continue;
    if (l->mqtt.connect(l->id, l->id, l->secret, l->tOffline, 1, false, l->will)) {
      Serial.printf("[%s] connected\n", l->id);
      l->backoffMs = 2000;
      announce(*l);
      if (l->onCommand) l->mqtt.subscribe(l->tCmd, 1);
      if (l == &pump) pumpDirty = true;
      if (l == &light) lightDirty = true;
    } else {
      int rc = l->mqtt.state();
      Serial.printf("[%s] MQTT connect failed rc=%d%s\n", l->id, rc,
                    rc == 4 || rc == 5 ? " -- check this device's ID and secret in config.h" : "");
      l->nextTry = millis() + l->backoffMs;
      l->backoffMs = min<uint32_t>(l->backoffMs * 2, 60000);
    }
    return;
  }
}

void publishReading(Link& l, JsonDocument& payload) {
  if (!l.mqtt.connected()) return;
  JsonDocument doc;
  doc["schema_version"] = "1.0";
  doc["device_id"] = l.id;
  doc["device_type"] = l.type;
  doc["zone"] = ZONE;
  doc["seq"] = l.seq++;
  doc["payload"] = payload;
  char buf[768];
  size_t n = serializeJson(doc, buf, sizeof buf);
  l.mqtt.publish(l.tReading, (const uint8_t*)buf, n);
}

// ---------------------------------------------------------------------------
// Sensors
// ---------------------------------------------------------------------------
template <typename F>
int median(F read, int samples) {
  int v[15];
  samples = min(samples, 15);
  for (int i = 0; i < samples; i++) {
    v[i] = read();
    delayMicroseconds(300);
  }
  std::sort(v, v + samples);
  return v[samples / 2];
}

float soilPct(int raw) {
  float pct = 100.0f * (float)(raw - SOIL_RAW_DRY) / (float)(SOIL_RAW_WET - SOIL_RAW_DRY);
  return constrain(pct, 0.0f, 100.0f);
}

void readClimate() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    Serial.println("DHT22 read failed (wiring/power?) -- skipped");
    return;
  }
  JsonDocument p;
  p["temp_c"] = roundf(t * 10) / 10;
  p["humidity_pct"] = roundf(h * 10) / 10;
  publishReading(climate, p);
}

void readSoilAndPh() {
  JsonDocument p;
  float sum = 0;
  int ok = 0;
  int raws[SOIL_COUNT];
  for (int i = 0; i < SOIL_COUNT; i++) {
    uint8_t pin = SOIL_PIN_LIST[i];
    int raw = median([pin] { return (int)analogRead(pin); }, 9);
    raws[i] = raw;
    char key[16];
    snprintf(key, sizeof key, "soil_%d_raw", i + 1);
    p[key] = raw;
    // Pinned to a rail = unplugged or shorted: leave it out of the average.
    if (raw < 100 || raw > 4000) continue;
    float pct = soilPct(raw);
    snprintf(key, sizeof key, "soil_%d_pct", i + 1);
    p[key] = roundf(pct * 10) / 10;
    sum += pct;
    ok++;
  }
  soilAvg = ok ? sum / ok : NAN;
  if (ok) p["soil_moisture_pct"] = roundf(soilAvg * 10) / 10;  // no fake value without a probe
  p["soil_probes_ok"] = ok;
  publishReading(soil, p);

  int mv = median([] { return (int)analogReadMilliVolts(PH_PIN); }, 15);
  JsonDocument q;
  q["ph_mv"] = mv;
  if (mv > 50 && mv < 3200) {
    float ph = 7.0f - ((float)mv - PH_NEUTRAL_MV) / PH_MV_PER_UNIT;
    q["ph"] = roundf(constrain(ph, 0.0f, 14.0f) * 100) / 100;
  }
  publishReading(phLink, q);

  // For calibration: watch these on the serial monitor (115200 baud).
  Serial.printf("soil raw:");
  for (int i = 0; i < SOIL_COUNT; i++) Serial.printf(" %d", raws[i]);
  Serial.printf("  avg %.1f%%  |  pH %d mV\n", soilAvg, mv);
}

void publishPump() {
  JsonDocument p;
  p["pump_on"] = pumpOn ? 1 : 0;
  p["pump_run_s"] = pumpOn ? (millis() - pumpStartedAt) / 1000 : 0;
  p["pump_backup"] = backupActive() ? 1 : 0;
  p["pump_max_run_s"] = PUMP_MAX_RUN_S;
  publishReading(pump, p);
  pumpDirty = false;
}

void publishLight() {
  JsonDocument p;
  p["light_on"] = lightOn ? 1 : 0;
  publishReading(light, p);
  lightDirty = false;
}

// ---------------------------------------------------------------------------
// Wi-Fi
// ---------------------------------------------------------------------------
void serviceWifi() {
  static uint32_t lastAttempt = 0;
  static bool wasConnected = false;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wasConnected) Serial.printf("Wi-Fi connected: %s\n", WiFi.localIP().toString().c_str());
  wasConnected = up;
  if (!up && millis() - lastAttempt > 15000) {
    lastAttempt = millis();
    Serial.printf("Connecting to Wi-Fi \"%s\" (2.4 GHz)...\n", WIFI_SSID);
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

// ---------------------------------------------------------------------------
void setup() {
  // Relays OFF before anything else: a reboot must never start the pump.
  pinMode(PUMP_RELAY_PIN, OUTPUT);
  pinMode(LIGHT_RELAY_PIN, OUTPUT);
  setRelay(PUMP_RELAY_PIN, false);
  setRelay(LIGHT_RELAY_PIN, false);

  Serial.begin(115200);
  Serial.println("\nVERT esp32-controller starting");

  analogReadResolution(12);
  for (int i = 0; i < SOIL_COUNT; i++) analogSetPinAttenuation(SOIL_PIN_LIST[i], ADC_11db);
  analogSetPinAttenuation(PH_PIN, ADC_11db);
  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  for (Link* l : LINKS) initLink(*l);
  lastBrokerAt = millis();  // backup starts BACKUP_AFTER_S after boot if the Pi never answers
}

void loop() {
  serviceWifi();
  serviceConnections();
  for (Link* l : LINKS) l->mqtt.loop();
  if (pump.mqtt.connected()) lastBrokerAt = millis();

  static uint32_t lastClimate = 0, lastSoil = 0, lastPump = 0, lastLight = 0;
  uint32_t now = millis();
  if (now - lastClimate >= 5000) {  // DHT22 needs >= 2 s between reads
    lastClimate = now;
    readClimate();
  }
  if (now - lastSoil >= 10000 || lastSoil == 0) {
    lastSoil = now;
    readSoilAndPh();
  }

  servicePump();

  if (pumpDirty || now - lastPump >= (pumpOn ? 5000UL : 30000UL)) {
    lastPump = now;
    publishPump();
  }
  if (lightDirty || now - lastLight >= 60000) {
    lastLight = now;
    publishLight();
  }
}
