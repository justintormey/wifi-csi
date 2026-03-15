/**
 * WiFi CSI MQTT Client — Publishes CSI binary packets to Mosquitto on the RPi.
 *
 * Uses the ESP-MQTT component. Publishes at QoS 0 (no ack) since dropping
 * CSI frames is acceptable and latency matters more than reliability.
 */

#include "mqtt_client_csi.h"
#include "config.h"

#include "esp_log.h"
#include "esp_wifi.h"
#include "mqtt_client.h"

#include <stdio.h>
#include <string.h>

static const char *TAG = "mqtt_csi";
static esp_mqtt_client_handle_t s_client = NULL;
static bool s_connected = false;
static char s_topic[64];  /* csi/{floor_id}/{rx_mac} */

/* ── MQTT event handler ────────────────────────────────────────────── */

static void mqtt_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data) {
    esp_mqtt_event_handle_t event = (esp_mqtt_event_handle_t)data;

    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            s_connected = true;
            ESP_LOGI(TAG, "Connected to MQTT broker, topic: %s", s_topic);
            break;
        case MQTT_EVENT_DISCONNECTED:
            s_connected = false;
            ESP_LOGW(TAG, "Disconnected from MQTT broker");
            break;
        case MQTT_EVENT_ERROR:
            ESP_LOGE(TAG, "MQTT error type: %d", event->error_handle->error_type);
            break;
        default:
            break;
    }
}

/* ── Public API ────────────────────────────────────────────────────── */

esp_err_t mqtt_client_init(void) {
    /* Build the MQTT topic: csi/{floor_id}/{rx_mac} */
    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    snprintf(s_topic, sizeof(s_topic), "csi/%d/%02x:%02x:%02x:%02x:%02x:%02x",
             FLOOR_ID, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    /* Build broker URI */
    char broker_uri[64];
    snprintf(broker_uri, sizeof(broker_uri), "mqtt://%s:%d",
             MQTT_BROKER_IP, MQTT_BROKER_PORT);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = broker_uri,
        .session.keepalive = 60,
        .buffer.size = 1024,
    };

    s_client = esp_mqtt_client_init(&mqtt_cfg);
    if (!s_client) {
        ESP_LOGE(TAG, "Failed to init MQTT client");
        return ESP_FAIL;
    }

    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_err_t err = esp_mqtt_client_start(s_client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start MQTT client: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "MQTT client started, broker: %s, topic: %s", broker_uri, s_topic);
    return ESP_OK;
}

void mqtt_client_publish_csi(const uint8_t *data, size_t len) {
    if (!s_connected || !s_client) {
        return;  /* Silently drop — acceptable for CSI streaming. */
    }

    /* QoS 0, no retain. Fire-and-forget. */
    esp_mqtt_client_publish(s_client, s_topic, (const char *)data, (int)len, 0, 0);
}
