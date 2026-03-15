/**
 * WiFi CSI Handler — CSI callback and binary packet serialization.
 *
 * Registers the ESP32 CSI receive callback. On each frame, extracts
 * 114 subcarriers of I/Q data, serializes into a 478-byte binary packet,
 * and queues it in a ring buffer for the MQTT publish task.
 *
 * Features:
 *   - Ring buffer (configurable depth) decouples callback from MQTT
 *   - Rate limiting caps output at 100 packets/sec
 *   - Logs warning if HT40 isn't delivering 114 subcarriers
 */

#pragma once

#include "esp_err.h"

/** Ring buffer depth — number of CSI packets to buffer. */
#define CSI_RING_BUF_SIZE  32

/**
 * Initialize CSI collection.
 * Configures CSI on the WiFi interface, creates the ring buffer and
 * publish task, and registers the receive callback.
 * Must be called after WiFi is connected and MQTT is initialized.
 */
esp_err_t csi_handler_init(void);
