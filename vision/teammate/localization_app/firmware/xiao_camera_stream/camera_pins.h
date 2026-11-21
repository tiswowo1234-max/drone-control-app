#pragma once

// Espressif CameraWebServer camera_pins.h, CAMERA_MODEL_XIAO_ESP32S3.
#if !defined(CAMERA_MODEL_XIAO_ESP32S3) || !defined(ARDUINO_XIAO_ESP32S3)
#error Select XIAO_ESP32S3 in Arduino IDE.
#endif

constexpr int CAMERA_DATA_PINS[] = {15, 17, 18, 16, 14, 12, 11, 48};  // D0..D7
constexpr int CAMERA_XCLK = 10;
constexpr int CAMERA_PCLK = 13;
constexpr int CAMERA_VSYNC = 38;
constexpr int CAMERA_HREF = 47;
constexpr int CAMERA_SDA = 40;
constexpr int CAMERA_SCL = 39;
