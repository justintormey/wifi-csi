/**
 * WiFi CSI Handler — CSI callback and binary packet serialization.
 *
 * Registers the ESP32 CSI receive callback. On each frame, extracts
 * 114 subcarriers of I/Q data, serializes into a 478-byte binary packet,
 * and passes it to the MQTT client for publishing.
 */

#pragma once

#include "esp_err.h"

/**
 * Initialize CSI collection.
 * Configures CSI on the WiFi interface and registers the receive callback.
 * Must be called after WiFi is connected.
 */
esp_err_t csi_handler_init(void);
