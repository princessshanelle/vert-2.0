// VERT sample device: ESP32 + PH-4502C pH module, measuring the pH of the
// irrigation reservoir.
//
// Publishes readings on:
//   vert.<ZONE>.<DEVICE_TYPE>.<DEVICE_ID>.reading
// as a JSON envelope matching services/gateway/internal/mq/envelope.go,
// with payload {"ph": 6.21}. The gateway charts it like any other numeric
// reading, and automation rules can watch it ("ph < 5.5 for 10 minutes").
//
// Announces itself on every (re)connect on:
//   vert.<ZONE>.<DEVICE_TYPE>.<DEVICE_ID>.announce
//
// Wiring and calibration: see src/config.h.example.
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <algorithm>

#include "config.h"

WiFiClient net;
PubSubClient mqtt(net);

uint32_t seq = 0;

char topicReading[128];
char topicAnnounce[128];
char topicOffline[128];

// Readings outside this pin-voltage window mean something's wrong with
// the wiring, not the water: ~0 mV is a disconnected Po wire, and the
// top of the range is the ADC saturating (a missing divider, or no probe
// on the BNC socket letting the input float). Publishing a "pH" computed
// from either would put a confident-looking wrong number on the dashboard.
const uint32_t PIN_MV_MIN = 150;
const uint32_t PIN_MV_MAX = 3100;

void connectWifi() {
  Serial.printf("Connecting to WiFi SSID \"%s\"...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (++attempts % 20 == 0) {
      Serial.printf("\nStill not connected after %lu s (WiFi.status()=%d) -- check WIFI_SSID/WIFI_PASSWORD in config.h\n",
                     attempts / 2, WiFi.status());
    }
  }
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
}

void publishAnnounce() {
  JsonDocument doc;
  doc["kind"] = "announce";
  doc["device_id"] = DEVICE_ID;
  doc["device_type"] = DEVICE_TYPE;
  doc["ip"] = WiFi.localIP().toString();
  doc["capabilities"]["reads"].add("ph");

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicAnnounce, (uint8_t*)buf, n, true); // retained
}

void connectMqtt() {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  // The Last-Will is a full lifecycle event, not a bare "1": the gateway
  // parses it as JSON, so this is what actually flips the dashboard dot
  // to offline when the board loses power.
  char will[160];
  snprintf(will, sizeof(will), "{\"kind\":\"offline\",\"device_id\":\"%s\",\"device_type\":\"%s\"}", DEVICE_ID, DEVICE_TYPE);
  Serial.printf("Connecting to MQTT broker %s:%d as \"%s\"...\n", MQTT_HOST, MQTT_PORT, DEVICE_ID);
  while (!mqtt.connected()) {
    if (mqtt.connect(DEVICE_ID, DEVICE_ID, DEVICE_SECRET, topicOffline, 1, false, will)) {
      Serial.println("MQTT connected");
      publishAnnounce(); // on every reconnect, so the gateway re-marks it online
    } else {
      // -2/-4: broker not reachable (MQTT_HOST, network); 5: wrong DEVICE_ID/DEVICE_SECRET.
      Serial.printf("MQTT connect failed, state=%d -- retrying in 2s\n", mqtt.state());
      delay(2000);
    }
  }
}

void publishReading(float ph) {
  JsonDocument doc;
  doc["schema_version"] = "1.0";
  doc["device_id"] = DEVICE_ID;
  doc["device_type"] = DEVICE_TYPE;
  doc["zone"] = ZONE;
  doc["seq"] = seq++;
  doc["payload"]["ph"] = roundf(ph * 100.0f) / 100.0f;

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicReading, (uint8_t*)buf, n);
}

// pH probes are high-impedance and pick up noise from everything nearby
// (pumps especially). 40 samples, drop the 10 highest and 10 lowest,
// average the rest: one spike can't move the result.
uint32_t readPinMilliVolts() {
  const int N = 40, TRIM = 10;
  uint32_t samples[N];
  for (int i = 0; i < N; i++) {
    samples[i] = analogReadMilliVolts(PH_PIN); // factory-calibrated ADC curve, not raw counts
    delay(10);
  }
  std::sort(samples, samples + N);
  uint32_t sum = 0;
  for (int i = TRIM; i < N - TRIM; i++) sum += samples[i];
  return sum / (N - 2 * TRIM);
}

// Straight line through the two calibration points. Works whichever way
// the module's output runs (the PH-4502C's voltage falls as pH rises).
float milliVoltsToPh(uint32_t mv) {
  float slope = (float)(PH_BUFFER_NEUTRAL - PH_BUFFER_ACID) / (float)(PH_CAL_MV_NEUTRAL - PH_CAL_MV_ACID);
  return PH_BUFFER_NEUTRAL + ((float)mv - (float)PH_CAL_MV_NEUTRAL) * slope;
}

void setup() {
  Serial.begin(115200);
  analogSetPinAttenuation(PH_PIN, ADC_11db); // full ~0-3.1V input range

  snprintf(topicReading, sizeof(topicReading), "vert/%s/%s/%s/reading", ZONE, DEVICE_TYPE, DEVICE_ID);
  snprintf(topicAnnounce, sizeof(topicAnnounce), "vert/%s/%s/%s/announce", ZONE, DEVICE_TYPE, DEVICE_ID);
  snprintf(topicOffline, sizeof(topicOffline), "vert/%s/%s/%s/offline", ZONE, DEVICE_TYPE, DEVICE_ID);

  if (PH_CAL_MV_NEUTRAL == PH_CAL_MV_ACID) {
    Serial.println("PH_CAL_MV_NEUTRAL and PH_CAL_MV_ACID are equal -- fix the calibration in config.h");
  }

  connectWifi();
  connectMqtt();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    connectWifi();
  }
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  static uint32_t lastRead = 0;
  if (lastRead == 0 || millis() - lastRead >= READ_INTERVAL_MS) {
    lastRead = millis() | 1; // never 0 again, so the first read happens right at boot
    uint32_t mv = readPinMilliVolts();
    float ph = milliVoltsToPh(mv);

    if (mv < PIN_MV_MIN || mv > PIN_MV_MAX) {
      Serial.printf("pin=%lu mV -- out of range, not publishing. Check the Po wire, the divider, and that the probe is plugged in.\n", mv);
      return;
    }
    if (ph < 0.0f || ph > 14.0f) {
      Serial.printf("pin=%lu mV -> pH %.2f is impossible -- not publishing. Re-check the calibration in config.h.\n", mv, ph);
      return;
    }
    publishReading(ph);
    // The mV is printed every time: it's exactly what calibration needs.
    Serial.printf("Published: pH %.2f (pin=%lu mV)\n", ph, mv);
  }
}
