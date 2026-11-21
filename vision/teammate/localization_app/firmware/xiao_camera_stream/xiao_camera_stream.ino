#include <WiFi.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_random.h"

#define CAMERA_MODEL_XIAO_ESP32S3
#include "camera_pins.h"
#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#else
#include "wifi_secrets.example.h"
#endif

constexpr framesize_t FRAME_SIZE = FRAMESIZE_QVGA;
constexpr int JPEG_QUALITY = 12;  // Lower is higher quality
constexpr char BOUNDARY[] = "--xiao-camera\r\n";
httpd_handle_t streamServer = nullptr;
uint32_t streamSession;
uint32_t sequence = 0;

const char *sensorName(uint16_t pid) {
  switch (pid) {
    case OV2640_PID: return "OV2640";
    case OV3660_PID: return "OV3660";
    case OV5640_PID: return "OV5640";
    default: return "unknown";
  }
}

bool startCamera() {
  const bool psram = psramFound();
  Serial.printf("PSRAM: %s (%lu bytes)\n", psram ? "ready" : "unavailable; using one DRAM buffer",
                static_cast<unsigned long>(ESP.getPsramSize()));
  camera_config_t config{};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = CAMERA_DATA_PINS[0];
  config.pin_d1 = CAMERA_DATA_PINS[1];
  config.pin_d2 = CAMERA_DATA_PINS[2];
  config.pin_d3 = CAMERA_DATA_PINS[3];
  config.pin_d4 = CAMERA_DATA_PINS[4];
  config.pin_d5 = CAMERA_DATA_PINS[5];
  config.pin_d6 = CAMERA_DATA_PINS[6];
  config.pin_d7 = CAMERA_DATA_PINS[7];
  config.pin_xclk = CAMERA_XCLK;
  config.pin_pclk = CAMERA_PCLK;
  config.pin_vsync = CAMERA_VSYNC;
  config.pin_href = CAMERA_HREF;
  config.pin_sccb_sda = CAMERA_SDA;
  config.pin_sccb_scl = CAMERA_SCL;
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAME_SIZE;
  config.jpeg_quality = JPEG_QUALITY;
  config.fb_count = psram ? 2 : 1;
  config.fb_location = psram ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = psram ? CAMERA_GRAB_LATEST : CAMERA_GRAB_WHEN_EMPTY;
  esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", static_cast<unsigned>(error));
    return false;
  }
  sensor_t *sensor = esp_camera_sensor_get();
  camera_fb_t *frame = esp_camera_fb_get();
  if (!sensor || !frame) {
    if (frame) esp_camera_fb_return(frame);
    Serial.println("Camera failed its first capture");
    esp_camera_deinit();
    return false;
  }
  Serial.printf("Sensor PID 0x%x (%s), %ux%u, format %d (JPEG=%d)\n",
                sensor->id.PID, sensorName(sensor->id.PID),
                static_cast<unsigned>(frame->width), static_cast<unsigned>(frame->height),
                frame->format, PIXFORMAT_JPEG);
  esp_camera_fb_return(frame);
  return true;
}

esp_err_t streamFrames(httpd_req_t *request) {
  esp_err_t status = httpd_resp_set_type(request, "multipart/x-mixed-replace; boundary=xiao-camera");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  Serial.println("Stream client connected");
  while (status == ESP_OK && WiFi.status() == WL_CONNECTED) {
    camera_fb_t *frame = esp_camera_fb_get();
    if (!frame) {
      Serial.println("Capture failed; closing stream");
      return ESP_FAIL;
    }
    char headers[160];
    int length = snprintf(headers, sizeof(headers),
                          "Content-Type: image/jpeg\r\nContent-Length: %u\r\n"
                          "X-Stream-Session: %08lx\r\nX-Frame-Id: %lu\r\n\r\n",
                          static_cast<unsigned>(frame->len),
                          static_cast<unsigned long>(streamSession),
                          static_cast<unsigned long>(++sequence));
    if (length < 0 || static_cast<size_t>(length) >= sizeof(headers)) {
      esp_camera_fb_return(frame);
      Serial.println("JPEG header exceeds buffer; closing stream");
      return ESP_FAIL;
    }
    status = httpd_resp_send_chunk(request, BOUNDARY, sizeof(BOUNDARY) - 1);
    if (status == ESP_OK) status = httpd_resp_send_chunk(request, headers, length);
    if (status == ESP_OK)
      status = httpd_resp_send_chunk(request, reinterpret_cast<const char *>(frame->buf), frame->len);
    if (status == ESP_OK) status = httpd_resp_send_chunk(request, "\r\n", 2);
    esp_camera_fb_return(frame);
    delay(1);
  }
  Serial.printf("Stream ended: 0x%x\n", static_cast<unsigned>(status));
  return status == ESP_OK ? ESP_FAIL : status;
}

bool startServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.send_wait_timeout = 3;
  esp_err_t status = httpd_start(&streamServer, &config);
  if (status == ESP_OK) {
    httpd_uri_t stream{};
    stream.uri = "/stream";
    stream.method = HTTP_GET;
    stream.handler = streamFrames;
    status = httpd_register_uri_handler(streamServer, &stream);
    if (status == ESP_OK) return true;
    httpd_stop(streamServer);
    streamServer = nullptr;
  }
  Serial.printf("HTTP server failed: 0x%x\n", static_cast<unsigned>(status));
  return false;
}

bool ready = false;
bool announced = false;
unsigned long lastRetry = 0;

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("KAIST XIAO camera startup");
  if (!startCamera()) return;
  if (strcmp(WIFI_SSID, "YOUR_2_4_GHZ_SSID") == 0 || WIFI_SSID[0] == '\0') {
    Serial.println("Set local wifi_secrets.h before uploading.");
    return;
  }
  streamSession = esp_random();
  WiFi.onEvent([](arduino_event_id_t, arduino_event_info_t info) {
    const auto reason = static_cast<wifi_err_reason_t>(info.wifi_sta_disconnected.reason);
    Serial.printf("Wi-Fi disconnected: reason %u (%s)\n",
                  static_cast<unsigned>(reason), WiFi.disconnectReasonName(reason));
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.onEvent([](arduino_event_id_t) {
    Serial.println("Wi-Fi associated; waiting for DHCP");
  }, ARDUINO_EVENT_WIFI_STA_CONNECTED);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastRetry = millis();
  ready = true;
}

void loop() {
  if (ready) {
    if (WiFi.status() == WL_CONNECTED && WiFi.localIP() != IPAddress(0, 0, 0, 0)) {
      if (!streamServer && millis() - lastRetry >= 1000) {
        lastRetry = millis();
        startServer();
      }
      if (streamServer && !announced) {
        Serial.printf("Ready: http://%s/stream\n", WiFi.localIP().toString().c_str());
        announced = true;
      }
    } else if (announced || millis() - lastRetry >= 15000) {
      Serial.printf("Wi-Fi waiting for link/IP (status %d)\n", WiFi.status());
      WiFi.reconnect();
      lastRetry = millis();
      announced = false;
    }
  }
  delay(250);
}
