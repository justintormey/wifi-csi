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
 *
 * The callback writes packets into a ring buffer. A separate FreeRTOS
 * task drains the buffer and publishes via MQTT, so the callback
 * never blocks on network I/O.
 *
 * Rate limiting: at most CSI_MAX_RATE_HZ packets per second are
 * accepted by the callback; excess frames are silently dropped.
 */

#include "csi_handler.h"
#include "config.h"
#include "mqtt_client_csi.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_wifi_types.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <string.h>

static const char *TAG = "csi_handler";

/* ── Rate limiting ────────────────────────────────────────────────── */

/** Maximum packets per second accepted from the CSI callback. */
#define CSI_MAX_RATE_HZ       100

/** Minimum interval between accepted packets (microseconds). */
#define CSI_MIN_INTERVAL_US   (1000000 / CSI_MAX_RATE_HZ)

/** Timestamp (us) of the last accepted CSI packet. */
static int64_t s_last_accept_us = 0;

/** Cached RX MAC address (set once at init, read in callback). */
static uint8_t s_rx_mac[6] = {0};

/* ── Ring buffer ──────────────────────────────────────────────────── */

/**
 * Simple single-producer / single-consumer ring buffer.
 *
 * Producer: csi_rx_callback (WiFi driver task)
 * Consumer: csi_publish_task (dedicated FreeRTOS task)
 *
 * head is written only by the producer; tail only by the consumer.
 * Both are read by both sides. Memory barriers ensure data visibility
 * across the dual-core ESP32-S3 (Xtensa LX7): the producer issues a
 * full fence after writing data and before advancing head; the consumer
 * issues a full fence after reading data and before advancing tail.
 */
static uint8_t s_ring[CSI_RING_BUF_SIZE][CSI_PACKET_SIZE];
static volatile uint32_t s_ring_head = 0;  /* next write slot  */
static volatile uint32_t s_ring_tail = 0;  /* next read slot   */

/** Counters for diagnostics (logged periodically). */
static uint32_t s_packets_accepted  = 0;
static uint32_t s_packets_dropped   = 0;
static uint32_t s_packets_published = 0;

static inline uint32_t ring_next(uint32_t idx) {
    return (idx + 1) % CSI_RING_BUF_SIZE;
}

static inline bool ring_full(void) {
    return ring_next(s_ring_head) == s_ring_tail;
}

static inline bool ring_empty(void) {
    return s_ring_head == s_ring_tail;
}

/* ── Subcarrier verification ──────────────────────────────────────── */

/** Track whether we've already logged the subcarrier warning. */
static bool s_subcarrier_warned = false;

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

    /* ── Rate limiting ── */
    int64_t now_us = esp_timer_get_time();
    if ((now_us - s_last_accept_us) < CSI_MIN_INTERVAL_US) {
        return;  /* Too soon — drop this frame. */
    }
    s_last_accept_us = now_us;

    /* ── Subcarrier count verification ── */
    int num_subcarriers = info->len / 2;  /* len is total int8 I/Q bytes */
    if (num_subcarriers < CSI_NUM_SUBCARRIERS && !s_subcarrier_warned) {
        ESP_LOGW(TAG, "Expected %d subcarriers (HT40) but got %d "
                 "(len=%d). Check WiFi bandwidth config.",
                 CSI_NUM_SUBCARRIERS, num_subcarriers, info->len);
        s_subcarrier_warned = true;
    }

    /* ── Ring buffer write ── */
    if (ring_full()) {
        s_packets_dropped++;
        return;  /* Buffer full — drop oldest-unwritten packet. */
    }

    uint8_t *slot = s_ring[s_ring_head];

    serialize_csi_packet(
        slot,
        (uint64_t)now_us,
        info->mac,
        s_rx_mac,  /* cached at init — no lock acquisition in callback */
        info->rx_ctrl.rssi,
        (uint8_t)FLOOR_ID,
        info->buf,
        info->len
    );

    /* Full memory barrier: ensure all data writes to slot are visible
     * to the consumer core before we advance the head index. */
    __sync_synchronize();

    /* Publish head advance (atomic 32-bit write). */
    s_ring_head = ring_next(s_ring_head);
    s_packets_accepted++;
}

/* ── Publish task ──────────────────────────────────────────────────── */

/**
 * FreeRTOS task that drains the ring buffer and publishes packets
 * via MQTT. Runs on core 0 to avoid contending with WiFi on core 1.
 */
static void csi_publish_task(void *arg) {
    TickType_t last_stats_tick = xTaskGetTickCount();

    while (1) {
        if (ring_empty()) {
            /* Yield briefly — avoids busy-spin while keeping latency low. */
            vTaskDelay(1);
            continue;
        }

        /* Memory barrier: ensure we see the complete data written by the
         * producer before we read from the slot. */
        __sync_synchronize();

        const uint8_t *slot = s_ring[s_ring_tail];
        mqtt_client_publish_csi(slot, CSI_PACKET_SIZE);
        s_packets_published++;

        /* Full memory barrier before advancing tail, so the producer
         * does not overwrite this slot before we finish reading. */
        __sync_synchronize();

        /* Advance tail (atomic 32-bit write). */
        s_ring_tail = ring_next(s_ring_tail);

        /* Log stats every ~10 seconds. */
        TickType_t now_tick = xTaskGetTickCount();
        if ((now_tick - last_stats_tick) >= pdMS_TO_TICKS(10000)) {
            ESP_LOGI(TAG, "CSI stats: accepted=%lu published=%lu dropped=%lu ring_depth=%lu",
                     (unsigned long)s_packets_accepted,
                     (unsigned long)s_packets_published,
                     (unsigned long)s_packets_dropped,
                     (unsigned long)((s_ring_head - s_ring_tail + CSI_RING_BUF_SIZE) % CSI_RING_BUF_SIZE));
            last_stats_tick = now_tick;
        }
    }
}

/* ── Public API ────────────────────────────────────────────────────── */

esp_err_t csi_handler_init(void) {
    /* Cache the RX MAC address once — avoids calling esp_wifi_get_mac()
     * (which acquires the WiFi internal lock) at 100Hz in the callback. */
    esp_wifi_get_mac(WIFI_IF_STA, s_rx_mac);

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

    /* Start the publish task on core 0 (WiFi runs on core 1). */
    BaseType_t ret = xTaskCreatePinnedToCore(
        csi_publish_task, "csi_pub", 4096, NULL, 5, NULL, 0);
    if (ret != pdPASS) {
        ESP_LOGE(TAG, "Failed to create CSI publish task");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "CSI handler initialized (HT40, %d subcarriers, floor %d, "
             "rate limit %d Hz, ring buf %d slots)",
             CSI_NUM_SUBCARRIERS, FLOOR_ID, CSI_MAX_RATE_HZ, CSI_RING_BUF_SIZE);
    return ESP_OK;
}
