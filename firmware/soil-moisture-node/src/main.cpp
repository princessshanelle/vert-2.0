// VERT sample device: ESP32 + capacitive soil moisture sensor.
//
// Publishes readings on:
//   vert.<ZONE>.<DEVICE_TYPE>.<DEVICE_ID>.reading
// as a JSON envelope matching services/gateway/internal/mq/envelope.go.
//
// Announces itself once on boot on:
//   vert.<ZONE>.<DEVICE_TYPE>.<DEVICE_ID>.announce
// so the gateway's lifecycle consumer can register it without any
// manual config-file editing (see docs: device registry / plug-and-play).
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "config.h"

WiFiClient net;
PubSubClient mqtt(net);

uint32_t seq = 0;

// 2s so dipping the probe in water (or watering a pot) shows up on the
// dashboard's live chart almost immediately -- the gateway pushes every
// reading to the browser over a WebSocket, so this interval is the only
// thing standing between a moisture change and the website showing it.
const uint32_t READ_INTERVAL_MS = 2000;

char topicReading[128];
char topicAnnounce[128];

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

void connectMqtt() {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  while (!mqtt.connected()) {
    Serial.println("Connecting to MQTT broker...");
    // Last-Will: gateway marks the device offline automatically if power
    // is lost without a clean disconnect.
    String willTopic = String("vert/") + ZONE + "/" + DEVICE_TYPE + "/" + DEVICE_ID + "/offline";
    // The gateway's lifecycle consumer reads JSON with "kind" (a bare "1"
    // was unreadable, so a node that lost power stayed "online" forever).
    String will = String("{\"kind\":\"offline\",\"device_id\":\"") + DEVICE_ID + "\"}";
    if (mqtt.connect(DEVICE_ID, DEVICE_ID, DEVICE_SECRET, willTopic.c_str(), 1, false, will.c_str())) {
      Serial.println("MQTT connected");
    } else {
      Serial.printf("MQTT connect failed, rc=%d, retrying...\n", mqtt.state());
      delay(2000);
    }
  }
}

void publishAnnounce() {
  StaticJsonDocument<256> doc;
  doc["kind"] = "announce";
  doc["device_id"] = DEVICE_ID;
  doc["device_type"] = DEVICE_TYPE;
  JsonObject caps = doc.createNestedObject("capabilities");
  JsonArray reads = caps.createNestedArray("reads");
  reads.add("soil_moisture_pct");

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicAnnounce, (uint8_t*)buf, n, true); // retained
}

void publishReading(float moisturePct) {
  StaticJsonDocument<256> doc;
  doc["schema_version"] = "1.0";
  doc["device_id"] = DEVICE_ID;
  doc["device_type"] = DEVICE_TYPE;
  doc["zone"] = ZONE;
  doc["seq"] = seq++;
  JsonObject payload = doc.createNestedObject("payload");
  payload["soil_moisture_pct"] = moisturePct;

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicReading, (uint8_t*)buf, n);
}

// Maps a raw ADC reading to 0-100%, regardless of whether this sensor's
// dry/wet calibration values happen to run high-to-low or low-to-high --
// constrain() after the map() is what actually matters here: a probe
// left in air can read slightly past SOIL_RAW_DRY, and one fully
// submerged can read slightly past SOIL_RAW_WET, so without the clamp a
// perfectly normal reading could come out as -3% or 104%.
float rawToPercent(int raw) {
  float pct = (float)(raw - SOIL_RAW_DRY) * 100.0f / (float)(SOIL_RAW_WET - SOIL_RAW_DRY);
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12); // ESP32 default, set explicitly so raw values stay 0-4095 regardless of core version

  snprintf(topicReading, sizeof(topicReading), "vert/%s/%s/%s/reading", ZONE, DEVICE_TYPE, DEVICE_ID);
  snprintf(topicAnnounce, sizeof(topicAnnounce), "vert/%s/%s/%s/announce", ZONE, DEVICE_TYPE, DEVICE_ID);

  connectWifi();
  connectMqtt();
  publishAnnounce();
}

void loop() {
  // setup() only ever calls connectWifi() once. Without this check, a
  // dropped Wi-Fi link left the board stuck forever retrying MQTT over a
  // radio link that was never coming back on its own -- see the DHT22
  // node's identical fix for the incident that surfaced this.
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    connectWifi();
  }
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  static uint32_t lastRead = 0;
  if (millis() - lastRead >= READ_INTERVAL_MS) {
    lastRead = millis();
    int raw = analogRead(SOIL_PIN);
    float moisturePct = rawToPercent(raw);
    publishReading(moisturePct);
    Serial.printf("Published: %.1f%% (raw=%d)\n", moisturePct, raw);
  }
}
