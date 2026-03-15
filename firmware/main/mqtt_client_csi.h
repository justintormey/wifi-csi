/**
 * WiFi CSI MQTT Client — Publishes CSI binary packets to the RPi broker.
 *
 * Topic format: csi/{floor_id}/{rx_mac}
 * Payload: 478-byte binary packet (see csi_handler.c for layout).
 *
 * Features:
 * - QoS 0 (fire-and-forget) for low-latency CSI streaming
 * - Ring buffer (50 packets) to survive brief broker disconnects
 * - Status heartbeat to status/{board_id} every 10 seconds
 * - Auto-reconnect with configurable backoff
 */

#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/** Maximum packets buffered during MQTT disconnects. */
#define MQTT_BUFFER_CAPACITY  50

/** Interval (seconds) between status heartbeat publishes. */
#define MQTT_STATUS_INTERVAL_S  10

/**
 * Initialize the MQTT client and connect to the broker.
 * Must be called after WiFi is connected and IP is obtained.
 */
esp_err_t mqtt_client_init(void);

/**
 * Publish a CSI binary packet to the MQTT broker.
 * Non-blocking, QoS 0 (fire-and-forget).
 *
 * When disconnected, packets are buffered in a ring buffer (up to
 * MQTT_BUFFER_CAPACITY). Oldest packets are dropped when the buffer
 * is full. Buffered packets are flushed on reconnect.
 *
 * @param data  Pointer to the CSI_PACKET_SIZE-byte CSI packet.
 * @param len   Length of the packet.
 */
void mqtt_client_publish_csi(const uint8_t *data, size_t len);

/**
 * Check whether the MQTT client is currently connected to the broker.
 */
bool mqtt_client_is_connected(void);

/**
 * Get the number of packets currently in the disconnect buffer.
 */
int mqtt_client_buffered_count(void);

/**
 * Get the total number of packets published since boot.
 */
uint32_t mqtt_client_packets_published(void);

/**
 * Get the total number of packets dropped (buffer overflow while disconnected).
 */
uint32_t mqtt_client_packets_dropped(void);
