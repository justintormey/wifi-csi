/**
 * WiFi CSI MQTT Client — Publishes CSI binary packets to Mosquitto on the RPi.
 *
 * Uses the ESP-MQTT component. Publishes at QoS 0 (no ack) since dropping
 * CSI frames is acceptable and latency matters more than reliability.
 *
 * Features:
 * - Ring buffer (50 packets) for brief disconnect survival
 * - Auto-reconnect with 1s initial / 10s max backoff
 * - Status heartbeat to status/{board_id} every 10 seconds
 */

#include "mqtt_client_csi.h"
#include "config.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mqtt_client.h"

#include <stdio.h>
#include <string.h>

static const char *TAG = "mqtt_csi";

/* ── MQTT state ───────────────────────────────────────────────────── */

static esp_mqtt_client_handle_t s_client = NULL;
static volatile bool s_connected = false;
static char s_csi_topic[64];     /* csi/{floor_id}/{rx_mac} */
static char s_status_topic[64];  /* status/{board_id}       */

/* ── Metrics ──────────────────────────────────────────────────────── */

static uint32_t s_packets_published = 0;
static uint32_t s_packets_dropped = 0;
static uint32_t s_packets_buffered_total = 0;

/* ── Ring buffer for disconnect buffering ─────────────────────────── */

typedef struct {
    uint8_t  data[CSI_PACKET_SIZE];
    size_t   len;
} buffered_packet_t;

static buffered_packet_t s_ring[MQTT_BUFFER_CAPACITY];
static int s_ring_head = 0;  /* Next write position */
static int s_ring_tail = 0;  /* Next read position  */
static int s_ring_count = 0; /* Current occupancy   */

static void ring_push(const uint8_t *data, size_t len) {
    if (len > CSI_PACKET_SIZE) len = CSI_PACKET_SIZE;

    if (s_ring_count >= MQTT_BUFFER_CAPACITY) {
        /* Buffer full — drop oldest packet. */
        s_ring_tail = (s_ring_tail + 1) % MQTT_BUFFER_CAPACITY;
        s_ring_count--;
        s_packets_dropped++;
    }

    memcpy(s_ring[s_ring_head].data, data, len);
    s_ring[s_ring_head].len = len;
    s_ring_head = (s_ring_head + 1) % MQTT_BUFFER_CAPACITY;
    s_ring_count++;
    s_packets_buffered_total++;
}

static bool ring_pop(buffered_packet_t *out) {
    if (s_ring_count <= 0) return false;

    memcpy(out, &s_ring[s_ring_tail], sizeof(buffered_packet_t));
    s_ring_tail = (s_ring_tail + 1) % MQTT_BUFFER_CAPACITY;
    s_ring_count--;
    return true;
}

/* ── Flush buffered packets on reconnect ──────────────────────────── */

static void flush_buffer(void) {
    buffered_packet_t pkt;
    int flushed = 0;

    while (ring_pop(&pkt)) {
        esp_mqtt_client_publish(s_client, s_csi_topic,
                                (const char *)pkt.data, (int)pkt.len, 0, 0);
        s_packets_published++;
        flushed++;
    }

    if (flushed > 0) {
        ESP_LOGI(TAG, "Flushed %d buffered packets", flushed);
    }
}

/* ── Status heartbeat ─────────────────────────────────────────────── */

/**
 * Publishes a JSON status message to status/{board_id} every
 * MQTT_STATUS_INTERVAL_S seconds. Includes packet counters, buffer
 * state, connection uptime, and free heap.
 *
 * Runs as a FreeRTOS task.
 */
static void status_task(void *arg) {
    const TickType_t interval = pdMS_TO_TICKS(MQTT_STATUS_INTERVAL_S * 1000);

    while (1) {
        vTaskDelay(interval);

        if (!s_connected || !s_client) continue;

        char payload[256];
        int len = snprintf(payload, sizeof(payload),
            "{"
            "\"board_id\":\"%s\","
            "\"floor\":%d,"
            "\"role\":\"%s\","
            "\"connected\":true,"
            "\"packets_published\":%lu,"
            "\"packets_dropped\":%lu,"
            "\"packets_buffered\":%d,"
            "\"uptime_s\":%lld,"
            "\"free_heap\":%lu"
            "}",
            BOARD_ID,
            FLOOR_ID,
            BOARD_ROLE_TX ? "tx" : "rx",
            (unsigned long)s_packets_published,
            (unsigned long)s_packets_dropped,
            s_ring_count,
            (long long)(esp_timer_get_time() / 1000000LL),
            (unsigned long)esp_get_free_heap_size()
        );

        esp_mqtt_client_publish(s_client, s_status_topic,
                                payload, len, 0, 0);
    }
}

/* ── MQTT event handler ───────────────────────────────────────────── */

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                                int32_t id, void *data) {
    esp_mqtt_event_handle_t event = (esp_mqtt_event_handle_t)data;

    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            s_connected = true;
            ESP_LOGI(TAG, "Connected to MQTT broker, topic: %s", s_csi_topic);
            /* Flush any packets buffered during disconnect. */
            flush_buffer();
            break;
        case MQTT_EVENT_DISCONNECTED:
            s_connected = false;
            ESP_LOGW(TAG, "Disconnected from MQTT broker (buffering up to %d pkts)",
                     MQTT_BUFFER_CAPACITY);
            break;
        case MQTT_EVENT_ERROR:
            ESP_LOGE(TAG, "MQTT error type: %d", event->error_handle->error_type);
            break;
        default:
            break;
    }
}

/* ── Public API ───────────────────────────────────────────────────── */

esp_err_t mqtt_client_init(void) {
    /* Build the CSI topic: csi/{floor_id}/{rx_mac} */
    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    snprintf(s_csi_topic, sizeof(s_csi_topic),
             "csi/%d/%02x:%02x:%02x:%02x:%02x:%02x",
             FLOOR_ID, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    /* Build the status topic: status/{board_id} */
    snprintf(s_status_topic, sizeof(s_status_topic), "status/%s", BOARD_ID);

    /* Build broker URI */
    char broker_uri[64];
    snprintf(broker_uri, sizeof(broker_uri), "mqtt://%s:%d",
             MQTT_BROKER_IP, MQTT_BROKER_PORT);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = broker_uri,
        .session.keepalive = 60,
        .buffer.size = 1024,
        .network.reconnect_timeout_ms = 1000,  /* 1s initial reconnect */
    };

    s_client = esp_mqtt_client_init(&mqtt_cfg);
    if (!s_client) {
        ESP_LOGE(TAG, "Failed to init MQTT client");
        return ESP_FAIL;
    }

    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID,
                                    mqtt_event_handler, NULL);
    esp_err_t err = esp_mqtt_client_start(s_client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start MQTT client: %s", esp_err_to_name(err));
        return err;
    }

    /* Start the status heartbeat task. */
    xTaskCreatePinnedToCore(status_task, "mqtt_status", 3072, NULL, 2, NULL, 0);

    ESP_LOGI(TAG, "MQTT client started — broker: %s, csi_topic: %s, status_topic: %s",
             broker_uri, s_csi_topic, s_status_topic);
    return ESP_OK;
}

void mqtt_client_publish_csi(const uint8_t *data, size_t len) {
    if (!s_client) return;

    if (!s_connected) {
        /* Buffer the packet for replay on reconnect. */
        ring_push(data, len);
        return;
    }

    /* QoS 0, no retain. Fire-and-forget. */
    esp_mqtt_client_publish(s_client, s_csi_topic,
                            (const char *)data, (int)len, 0, 0);
    s_packets_published++;
}

bool mqtt_client_is_connected(void) {
    return s_connected;
}

int mqtt_client_buffered_count(void) {
    return s_ring_count;
}

uint32_t mqtt_client_packets_published(void) {
    return s_packets_published;
}

uint32_t mqtt_client_packets_dropped(void) {
    return s_packets_dropped;
}
