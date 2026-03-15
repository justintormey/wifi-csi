/**
 * WiFi CSI MQTT Client — Publishes CSI binary packets to the RPi broker.
 *
 * Topic format: csi/{floor_id}/{rx_mac}
 * Payload: 478-byte binary packet (see csi_handler.c for layout).
 */

#pragma once

#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>

/**
 * Initialize the MQTT client and connect to the broker.
 * Must be called after WiFi is connected and IP is obtained.
 */
esp_err_t mqtt_client_init(void);

/**
 * Publish a CSI binary packet to the MQTT broker.
 * Non-blocking, QoS 0 (fire-and-forget). Dropped frames are acceptable.
 *
 * @param data  Pointer to the 478-byte CSI packet.
 * @param len   Length of the packet (must be CSI_PACKET_SIZE).
 */
void mqtt_client_publish_csi(const uint8_t *data, size_t len);
