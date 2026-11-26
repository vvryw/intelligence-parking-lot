#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include "esp_camera.h"
#include "esp_wifi.h"
#include "wifi_secrets.h"

#ifndef CAMERA_ROLE
#define CAMERA_ROLE "unassigned"
#endif

// AI Thinker / compatible ESP32-CAM with OV2640, commonly used on CAM-MB.
// CAM-MB is the USB adapter; verify the camera board's pinout before upload.
// Pin map reference: Espressif CameraWebServer/camera_pins.h, CAMERA_MODEL_AI_THINKER.
constexpr int CAMERA_POWER_DOWN_PIN = 32;
constexpr int CAMERA_FLASH_PIN = 4;
WebServer server(80);
bool cameraReady = false;
bool serverStarted = false;
bool wasConnected = false;
bool raisedTxPower = false;
unsigned long lastReconnect = 0;

void identifyCamera() {
  const int count = String(CAMERA_ROLE) == "exit" ? 2 : 1;
  Serial.printf("Identifying camera: %s (%d flash pulses)\n", CAMERA_ROLE, count);
  for (int pulse = 0; pulse < count; ++pulse) {
    digitalWrite(CAMERA_FLASH_PIN, HIGH);
    delay(100);
    digitalWrite(CAMERA_FLASH_PIN, LOW);
    delay(350);
  }
}

void captureImage() {
  if (!cameraReady) {
    server.send(503, "text/plain", "Camera sensor did not initialize");
    return;
  }
  camera_fb_t *frame = esp_camera_fb_get();
  if (frame == nullptr) {
    server.send(503, "text/plain", "Camera capture failed");
    return;
  }
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "image/jpeg", reinterpret_cast<const char *>(frame->buf), frame->len);
  esp_camera_fb_return(frame);
}

bool initCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5;
  config.pin_d1 = 18;
  config.pin_d2 = 19;
  config.pin_d3 = 21;
  config.pin_d4 = 36;
  config.pin_d5 = 39;
  config.pin_d6 = 34;
  config.pin_d7 = 35;
  config.pin_xclk = 0;
  config.pin_pclk = 22;
  config.pin_vsync = 25;
  config.pin_href = 23;
  config.pin_sccb_sda = 26;
  config.pin_sccb_scl = 27;
  config.pin_pwdn = CAMERA_POWER_DOWN_PIN;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  const bool hasPsram = psramFound();
  config.frame_size = hasPsram ? FRAMESIZE_SVGA : FRAMESIZE_VGA;
  config.jpeg_quality = 10;
  config.fb_count = 1;
  config.fb_location = hasPsram ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x. Check camera-board model, ribbon, and power.\n", error);
    return false;
  }
  Serial.println("Camera initialized");
  return true;
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(50);
  pinMode(CAMERA_FLASH_PIN, OUTPUT);
  digitalWrite(CAMERA_FLASH_PIN, LOW);
  Serial.println("\nParking camera starting (AI Thinker pin map)");
  Serial.printf("Camera role: %s; MAC: %s\n", CAMERA_ROLE, WiFi.macAddress().c_str());
  // Allow USB identification before the radio starts, even on weak supplies.
  const unsigned long identifyUntil = millis() + 1500;
  while (millis() < identifyUntil) {
    if (Serial.available() && Serial.readStringUntil('\n') == "identify") identifyCamera();
    delay(5);
  }

  // Hold the sensor in power-down until Wi-Fi association is complete.
  pinMode(CAMERA_POWER_DOWN_PIN, OUTPUT);
  digitalWrite(CAMERA_POWER_DOWN_PIN, HIGH);
  if (WIFI_SSID[0] == '\0') {
    Serial.println("Set WIFI_SSID and WIFI_PASSWORD in wifi_secrets.h, then upload again.");
    return;
  }

  server.on("/capture", HTTP_GET, captureImage);
  server.on("/health", HTTP_GET, []() {
    const String body = String("{\"status\":\"") + (cameraReady ? "ok" : "error") +
      "\",\"camera\":\"" + (cameraReady ? "ready" : "unavailable") +
      "\",\"role\":\"" + CAMERA_ROLE + "\",\"mac\":\"" + WiFi.macAddress() + "\"}";
    server.send(cameraReady ? 200 : 503, "application/json", body);
  });
  server.on("/identify", HTTP_POST, []() {
    identifyCamera();
    server.send(200, "text/plain", CAMERA_ROLE);
  });
  server.on("/", HTTP_GET, []() {
    server.send(200, "text/plain", "Parking camera: GET /capture for a JPEG; GET /health for status.");
  });
  WiFi.onEvent([](arduino_event_id_t, arduino_event_info_t info) {
    Serial.printf("Wi-Fi disconnect reason: %u\n", static_cast<unsigned>(info.wifi_sta_disconnected.reason));
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  Serial.println("Starting Wi-Fi radio...");
  if (!WiFi.mode(WIFI_STA)) {
    Serial.println("Wi-Fi radio failed to start");
    return;
  }
  // The IDF API can set the limit before association, without waiting for an Arduino event.
  if (esp_wifi_set_max_tx_power(WIFI_POWER_8_5dBm) != ESP_OK) {
    Serial.println("Could not set Wi-Fi transmit power limit");
  }
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(true);
  Serial.println("Connecting to Wi-Fi...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  if (Serial.available() && Serial.readStringUntil('\n') == "identify") {
    identifyCamera();
  }
  if (WiFi.getMode() == WIFI_MODE_NULL) {
    delay(100);
    return;
  }
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected) {
    if (!serverStarted) {
      Serial.println("Wi-Fi connected; initializing camera...");
      cameraReady = initCamera();
      server.begin();
      serverStarted = true;
    }
    if (!wasConnected) {
      Serial.print("ESP32_CAPTURE_URL=http://");
      Serial.print(WiFi.localIP());
      Serial.println("/capture");
    }
    server.handleClient();
  } else if (millis() - lastReconnect >= 15000) {
    lastReconnect = millis();
    if (!serverStarted && !raisedTxPower) {
      raisedTxPower = true;
      if (esp_wifi_set_max_tx_power(WIFI_POWER_15dBm) == ESP_OK) {
        Serial.println("Wi-Fi retry using a 15 dBm transmit limit");
      }
    }
    Serial.println("Wi-Fi disconnected; retrying...");
    WiFi.reconnect();
  }
  wasConnected = connected;
  delay(2);
}
