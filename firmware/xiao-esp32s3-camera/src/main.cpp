// VERT sample device: Seeed XIAO ESP32-S3 Sense camera.
//
// Two independent ways to see what this camera sees, matching how most
// real ag-tech camera products actually behave -- not streaming video
// 24/7 (expensive in power and bandwidth for very little benefit most
// of the day), but not *just* periodic snapshots either:
//
//   1. Periodic (default): every CAPTURE_INTERVAL_MS, POST a JPEG to the
//      gateway and publish a small MQTT event referencing it, which the
//      inference service picks up to run plant-health analysis. This
//      keeps running as long as nobody's asked for anything else.
//
//   2. On demand: the dashboard can send this device a "start_stream"
//      command (see services/gateway's /command endpoint), which starts
//      a local MJPEG server on port 81 -- the same technique Espressif's
//      own CameraWebServer example uses -- for as long as someone's
//      actually looking. "stop_stream" (or simply closing the dashboard
//      tab) returns it to periodic mode. Streaming and periodic capture
//      never run at the same time; they'd fight over the same camera.
//
// Images never go through the message broker directly -- only their
// metadata/reference does (see docs: storage architecture).
#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_http_server.h>
#include "esp_camera.h"
#include "camera_pins.h"
#include "config.h"

WiFiClient net;
PubSubClient mqtt(net);
uint32_t seq = 0;
char topicFrame[128];
char topicAnnounce[128];
char topicCmd[128];

volatile bool streaming = false;
httpd_handle_t streamServer = NULL;

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM; config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM; config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM; config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM; config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_SVGA;
  config.jpeg_quality = 12;
  config.fb_count = psramFound() ? 2 : 1;

  return esp_camera_init(&config) == ESP_OK;
}

// ---- Live MJPEG stream (on demand only -- see file header) ----------

#define STREAM_BOUNDARY "vertframeboundary"
static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" STREAM_BOUNDARY;
static const char* STREAM_PART_BOUNDARY = "\r\n--" STREAM_BOUNDARY "\r\n";
static const char* STREAM_PART_HEADER = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static esp_err_t streamHandler(httpd_req_t* req) {
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;

  char header[64];
  while (streaming) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      res = ESP_FAIL;
      break;
    }
    res = httpd_resp_send_chunk(req, STREAM_PART_BOUNDARY, strlen(STREAM_PART_BOUNDARY));
    if (res == ESP_OK) {
      size_t n = snprintf(header, sizeof(header), STREAM_PART_HEADER, fb->len);
      res = httpd_resp_send_chunk(req, header, n);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
    }
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;
    delay(50); // ~20fps ceiling -- plenty smooth for checking on a plant, gentle on Wi-Fi
  }
  return res;
}

void startStreamServer() {
  if (streamServer) return; // already running
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 81;
  config.ctrl_port = 32768; // must differ from the default HTTP client's control port on this same board

  httpd_uri_t streamURI = { .uri = "/stream", .method = HTTP_GET, .handler = streamHandler, .user_ctx = NULL };
  if (httpd_start(&streamServer, &config) == ESP_OK) {
    httpd_register_uri_handler(streamServer, &streamURI);
    Serial.println("Live view started: http://" + WiFi.localIP().toString() + ":81/stream");
  } else {
    Serial.println("Could not start the live view server");
    streamServer = NULL;
  }
}

void stopStreamServer() {
  if (!streamServer) return;
  httpd_stop(streamServer);
  streamServer = NULL;
  Serial.println("Live view stopped");
}

// ---- MQTT: connect, announce, and take commands ----------------------

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

// Forward-declared: registered as the MQTT callback in setup(), defined
// below publishAnnounce() since it needs to reference streaming state.
void onCommand(char* topic, byte* payload, unsigned int length);

void connectMqtt() {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onCommand);
  Serial.printf("Connecting to MQTT broker %s:%d as \"%s\"...\n", MQTT_HOST, MQTT_PORT, DEVICE_ID);
  while (!mqtt.connected()) {
    String willTopic = String("vert/") + ZONE + "/" + DEVICE_TYPE + "/" + DEVICE_ID + "/offline";
    if (mqtt.connect(DEVICE_ID, DEVICE_ID, DEVICE_SECRET, willTopic.c_str(), 1, true, "1")) {
      Serial.println("MQTT connected");
      mqtt.subscribe(topicCmd);
    } else {
      // PubSubClient's state() codes that matter most here: -4 = the
      // broker never responded (wrong MQTT_HOST/port, or a firewall
      // dropped it silently); 5 = MQTT_CONNECT_UNAUTHORIZED, meaning the
      // broker *was* reached but rejected DEVICE_ID/DEVICE_SECRET --
      // see docs/TESTING.md on re-provisioning if you see a 5 here.
      Serial.printf("MQTT connect failed, state=%d -- retrying in 2s\n", mqtt.state());
      delay(2000);
    }
  }
}

void publishAnnounce() {
  StaticJsonDocument<320> doc;
  doc["kind"] = "announce";
  doc["device_id"] = DEVICE_ID;
  doc["device_type"] = DEVICE_TYPE;
  // The dashboard uses this to link directly to this device's live-view
  // stream (Section 8 of the project overview) -- the broker is not
  // built for video, so the browser talks to the camera itself.
  doc["ip"] = WiFi.localIP().toString();
  JsonObject caps = doc.createNestedObject("capabilities");
  JsonArray reads = caps.createNestedArray("reads");
  reads.add("image_ref");

  char buf[320];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicAnnounce, (uint8_t*)buf, n, true);
}

// Handles {"payload": {"command": "start_stream" | "stop_stream"}},
// the same envelope shape every command on the bus uses (manual, from
// the dashboard, or automation-rule-triggered -- this device doesn't
// need to tell them apart).
void onCommand(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, payload, length) != DeserializationError::Ok) return;

  const char* command = doc["payload"]["command"];
  if (!command) return;

  Serial.printf("Command received: %s\n", command);
  if (strcmp(command, "start_stream") == 0) {
    streaming = true;
    startStreamServer();
  } else if (strcmp(command, "stop_stream") == 0) {
    streaming = false;
    stopStreamServer();
  }
}

// ---- Periodic capture (default mode) ----------------------------------

// Returns the image reference the gateway assigned, or "" on failure.
String uploadFrame(camera_fb_t* fb) {
  HTTPClient http;
  String path = String("/api/v1/devices/") + DEVICE_ID + "/frames";
  http.begin(UPLOAD_HOST, UPLOAD_PORT, path);
  http.addHeader("Content-Type", "image/jpeg");
  // Same identity, same secret, as the MQTT connection below: the
  // gateway checks this against the credential issued at provisioning
  // time before it stores a single byte -- see docs/TESTING.md.
  http.setAuthorization(DEVICE_ID, DEVICE_SECRET);
  int code = http.POST(fb->buf, fb->len);
  String ref = "";
  if (code == 200 || code == 201) {
    ref = http.getString(); // gateway returns the storage reference as plain text
  } else {
    Serial.printf("Frame upload failed, HTTP %d\n", code);
  }
  http.end();
  return ref;
}

void publishFrameEvent(const String& imageRef) {
  StaticJsonDocument<256> doc;
  doc["schema_version"] = "1.0";
  doc["device_id"] = DEVICE_ID;
  doc["device_type"] = DEVICE_TYPE;
  doc["zone"] = ZONE;
  doc["seq"] = seq++;
  JsonObject payload = doc.createNestedObject("payload");
  payload["image_ref"] = imageRef;

  char buf[256];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(topicFrame, (uint8_t*)buf, n);
}

void setup() {
  Serial.begin(115200);
  if (!initCamera()) {
    Serial.println("Camera init failed");
  }

  snprintf(topicFrame, sizeof(topicFrame), "vert/%s/%s/%s/frame", ZONE, DEVICE_TYPE, DEVICE_ID);
  snprintf(topicAnnounce, sizeof(topicAnnounce), "vert/%s/%s/%s/announce", ZONE, DEVICE_TYPE, DEVICE_ID);
  snprintf(topicCmd, sizeof(topicCmd), "vert/%s/%s/%s/cmd", ZONE, DEVICE_TYPE, DEVICE_ID);

  connectWifi();
  connectMqtt();
  publishAnnounce();
}

void loop() {
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  // While someone's watching the live view, periodic capture stands
  // down entirely -- both paths want the same camera hardware and the
  // same Wi-Fi bandwidth, and a person actively looking always wins.
  if (streaming) return;

  static uint32_t lastCapture = 0;
  if (millis() - lastCapture >= CAPTURE_INTERVAL_MS) {
    lastCapture = millis();
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Frame capture failed");
      return;
    }
    String ref = uploadFrame(fb);
    esp_camera_fb_return(fb);
    if (ref.length() > 0) {
      publishFrameEvent(ref);
      Serial.println("Published frame event: " + ref);
    }
  }
}
