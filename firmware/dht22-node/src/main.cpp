// VERT sample device: ESP32 + DHT22 temperature/humidity sensor.
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
#include <DHT.h>
#include <ArduinoJson.h>

#include "config.h"

DHT dht(DHT_PIN, DHT22);
WiFiClient net;
PubSubClient mqtt(net);

uint32_t seq = 0;

// DHT22 datasheet minimum sampling period is ~2s; 5s leaves margin for a
// missed/retried read while still giving the dashboard's real-time chart
// something to actually draw every few seconds instead of the 30s
// cadence this used to be (indoor temp/humidity barely move within 30s,
// which was what made the chart look "stuck" even when it was working).
const uint32_t READ_INTERVAL_MS = 5000;

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
  reads.add("temp_c");
  reads.add("humidity_pct");

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicAnnounce, (uint8_t*)buf, n, true); // retained
}

void publishReading(float tempC, float humidity) {
  StaticJsonDocument<256> doc;
  doc["schema_version"] = "1.0";
  doc["device_id"] = DEVICE_ID;
  doc["device_type"] = DEVICE_TYPE;
  doc["zone"] = ZONE;
  doc["seq"] = seq++;
  JsonObject payload = doc.createNestedObject("payload");
  payload["temp_c"] = tempC;
  payload["humidity_pct"] = humidity;

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicReading, (uint8_t*)buf, n);
}

void setup() {
  Serial.begin(115200);
  dht.begin();

  snprintf(topicReading, sizeof(topicReading), "vert/%s/%s/%s/reading", ZONE, DEVICE_TYPE, DEVICE_ID);
  snprintf(topicAnnounce, sizeof(topicAnnounce), "vert/%s/%s/%s/announce", ZONE, DEVICE_TYPE, DEVICE_ID);

  connectWifi();
  connectMqtt();
  publishAnnounce();
}

void loop() {
  // setup() only ever calls connectWifi() once. Without this check, a
  // dropped Wi-Fi link (an AP hiccup, roaming, interference -- exactly
  // what happened during testing: the broker logged this device's TCP
  // connection closing with no error on either end) left the board stuck
  // forever in connectMqtt()'s retry loop, calling mqtt.connect() over a
  // radio link that was never coming back on its own. That's what made
  // the dashboard go static even with all of the real-time plumbing
  // working correctly.
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi dropped, reconnecting...");
    connectWifi();
  }
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  static uint32_t lastRead = 0;
  if (millis() - lastRead >= READ_INTERVAL_MS) {
    lastRead = millis();
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    if (isnan(h) || isnan(t)) {
      Serial.println("DHT22 read failed, skipping this cycle");
      return;
    }
    publishReading(t, h);
    Serial.printf("Published: %.1fC %.1f%%\n", t, h);
  }
}
