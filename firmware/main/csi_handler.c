/**
 * WiFi CSI Handler — CSI callback and binary packet serialization.
 *
 * When a frame is received, the ESP32 CSI callback fires with I/Q data
 * for 114 subcarriers (HT40). This module serializes the data into the
 * binary packet format expected by the Python backend:
 *
 *   Offset  Size  Field
 *   0       8     uint64_t timestamp_us
 *   8       6     uint8_t  tx_mac[6]
 *   14      6     uint8_t  rx_mac[6]
 *   20      1     int8_t   rssi
 *   21      1     uint8_t  floor_id
 *   22      456   int16_t  iq_pairs[228]  (114 × I,Q)
 *   ─────────────────────────────────────────
 *   Total:  478 bytes (little-endian)
 */

#include "csi_handler.h"
#include "config.h"
#include "mqtt_client_csi.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_wifi_types.h"

#include <string.h>

static const char *TAG = "csi_handler";

/* ── Packet serialization ──────────────────────────────────────────── */

/**
 * Serialize CSI data into a 478-byte binary packet.
 * Layout matches backend/collector/csi_packet.py exactly.
 */
static void serialize_csi_packet(
    uint8_t *buf,
    uint64_t timestamp_us,
    const uint8_t *tx_mac,
    const uint8_t *rx_mac,
    int8_t rssi,
    uint8_t floor_id,
    const int8_t *iq_data,
    int iq_len
) {
    /* All fields are little-endian (ESP32-S3 is LE natively). */
    memcpy(buf, &timestamp_us, 8);
    memcpy(buf + 8, tx_mac, 6);
    memcpy(buf + 14, rx_mac, 6);
    buf[20] = (uint8_t)rssi;
    buf[21] = floor_id;

    /* Convert raw int8 I/Q pairs to int16 for compatibility with backend.
     * ESP32 CSI callback provides int8_t pairs; backend expects int16_t. */
    int pairs = (iq_len < CSI_NUM_SUBCARRIERS * 2) ? iq_len : CSI_NUM_SUBCARRIERS * 2;
    for (int i = 0; i < pairs; i++) {
        int16_t val = (int16_t)iq_data[i];
        memcpy(buf + CSI_HEADER_SIZE + i * 2, &val, 2);
    }
    /* Zero-fill any remaining slots if fewer subcarriers than expected. */
    for (int i = pairs; i < CSI_NUM_SUBCARRIERS * 2; i++) {
        int16_t zero = 0;
        memcpy(buf + CSI_HEADER_SIZE + i * 2, &zero, 2);
    }
}

/* ── CSI receive callback ──────────────────────────────────────────── */

static void csi_rx_callback(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf) {
        return;
    }

    uint8_t packet[CSI_PACKET_SIZE];
    uint64_t now_us = (uint64_t)esp_timer_get_time();

    /* Get own MAC for rx_mac field. */
    uint8_t rx_mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, rx_mac);

    serialize_csi_packet(
        packet,
        now_us,
        info->mac,        /* TX MAC from the CSI info */
        rx_mac,
        info->rx_ctrl.rssi,
        (uint8_t)FLOOR_ID,
        info->buf,
        info->len
    );

    /* Publish to MQTT (non-blocking, best-effort). */
    mqtt_client_publish_csi(packet, CSI_PACKET_SIZE);
}

/* ── Public API ────────────────────────────────────────────────────── */

esp_err_t csi_handler_init(void) {
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,   /* L-LTF (legacy long training field) */
        .htltf_en          = true,   /* HT-LTF for HT40 subcarriers */
        .stbc_htltf2_en    = true,   /* STBC HT-LTF2 */
        .ltf_merge_en      = true,   /* Merge multiple LTF */
        .channel_filter_en = false,  /* Raw CSI, no hardware filtering */
        .manu_scale        = false,  /* No manual scaling */
        .shift             = false,  /* No bit shift */
    };

    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_rx_callback, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    ESP_LOGI(TAG, "CSI handler initialized (HT40, %d subcarriers, floor %d)",
             CSI_NUM_SUBCARRIERS, FLOOR_ID);
    return ESP_OK;
}
