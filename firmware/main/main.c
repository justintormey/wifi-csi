/**
 * WiFi CSI Firmware — Main entry point.
 *
 * Initializes NVS, WiFi (STA mode), and dispatches to TX or RX mode
 * based on compile-time configuration (Kconfig / sdkconfig).
 *
 * TX mode: Sends UDP unicast frames at the configured rate (default 100Hz).
 * RX mode: Registers CSI callback, serializes I/Q data, publishes via MQTT.
 */

#include "config.h"
#include "csi_handler.h"
#include "mqtt_client_csi.h"
#include "status_led.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"

#include <string.h>

static const char *TAG = "csi_main";

/* Event group for WiFi connection status. */
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

/* Timestamp (microseconds) when WiFi last disconnected. 0 = connected. */
static int64_t s_disconnect_time_us = 0;

/* ── WiFi event handler ────────────────────────────────────────────── */

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi disconnected, reconnecting...");
        status_led_set(LED_STATE_ERROR);
        if (s_disconnect_time_us == 0) {
            s_disconnect_time_us = esp_timer_get_time();
        }
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        s_disconnect_time_us = 0;
        status_led_set(LED_STATE_CONNECTED);
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/* ── WiFi initialization (STA mode) ───────────────────────────────── */

static void wifi_init_sta(void) {
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_config = {
        .sta = {
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .channel = WIFI_CHANNEL,
        },
    };
    /* Copy SSID and password (strncpy for safety). */
    strncpy((char *)wifi_config.sta.ssid, WIFI_SSID, sizeof(wifi_config.sta.ssid));
    strncpy((char *)wifi_config.sta.password, WIFI_PASSWORD, sizeof(wifi_config.sta.password));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

    /* Enable HT40 for 114 subcarriers. */
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT40));

    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "WiFi STA init complete, connecting to %s (ch %d)...",
             WIFI_SSID, WIFI_CHANNEL);

    /* Block until connected. */
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);
    ESP_LOGI(TAG, "WiFi connected");
}

/* ── TX mode: UDP frame sender ─────────────────────────────────────── */

#if BOARD_ROLE_TX

/**
 * TX task: sends small UDP frames at TX_RATE_HZ to trigger CSI on RX boards.
 * The frame content doesn't matter — the RX boards extract CSI from
 * any received WiFi frame. We send a minimal payload.
 */
static void tx_task(void *arg) {
    struct sockaddr_in dest_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(TX_UDP_PORT),
    };
    inet_aton(TX_TARGET_IP, &dest_addr.sin_addr);

    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Failed to create UDP socket: errno %d", errno);
        vTaskDelete(NULL);
        return;
    }

    /* Enable broadcast if target is broadcast address. */
    int broadcast = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &broadcast, sizeof(broadcast));

    /* Minimal payload — just a sequence counter. */
    uint32_t seq = 0;
    const TickType_t interval = pdMS_TO_TICKS(1000 / TX_RATE_HZ);

    ESP_LOGI(TAG, "TX task started: %d Hz to %s:%d", TX_RATE_HZ, TX_TARGET_IP, TX_UDP_PORT);

    while (1) {
        seq++;
        int err = sendto(sock, &seq, sizeof(seq), 0,
                         (struct sockaddr *)&dest_addr, sizeof(dest_addr));
        if (err < 0) {
            ESP_LOGD(TAG, "TX sendto failed: errno %d", errno);
        }
        vTaskDelay(interval);
    }
}

#endif /* BOARD_ROLE_TX */

/* ── WiFi watchdog task ────────────────────────────────────────────── */

/**
 * Monitors WiFi connection health. If disconnected for longer than
 * WIFI_WATCHDOG_TIMEOUT_S, restarts the board to recover from
 * persistent connection failures.
 */
static void wifi_watchdog_task(void *arg) {
    const int64_t timeout_us = (int64_t)WIFI_WATCHDOG_TIMEOUT_S * 1000000LL;

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));  /* Check every 5s. */

        int64_t disc = s_disconnect_time_us;
        if (disc != 0) {
            int64_t elapsed = esp_timer_get_time() - disc;
            if (elapsed > timeout_us) {
                ESP_LOGE(TAG, "WiFi disconnected for %lld s (limit %d s) — restarting",
                         elapsed / 1000000LL, WIFI_WATCHDOG_TIMEOUT_S);
                esp_restart();
            }
        }
    }
}

/* ── App main ──────────────────────────────────────────────────────── */

void app_main(void) {
    ESP_LOGI(TAG, "WiFi CSI Firmware v0.1.0");
    ESP_LOGI(TAG, "Board: %s | Role: %s | Floor: %d | Channel: %d",
             BOARD_ID,
             BOARD_ROLE_TX ? "TX" : "RX",
             FLOOR_ID,
             WIFI_CHANNEL);

    /* Initialize status LED (starts in CONNECTING pattern). */
    status_led_init();

    /* Initialize NVS (required for WiFi). */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /* Connect to house WiFi. */
    wifi_init_sta();

    /* Log MAC address for sensor registration. */
    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    ESP_LOGI(TAG, "MAC address: %02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    /* Start WiFi watchdog. */
    xTaskCreatePinnedToCore(wifi_watchdog_task, "wifi_wd", 2048, NULL, 3, NULL, 0);

#if BOARD_ROLE_TX
    /* TX mode: just send UDP frames to trigger CSI on receivers. */
    ESP_LOGI(TAG, "Starting TX mode (%d Hz)", TX_RATE_HZ);
    xTaskCreatePinnedToCore(tx_task, "tx_task", 4096, NULL, 5, NULL, 1);
#else
    /* RX mode: initialize MQTT, then start CSI collection. */
    ESP_LOGI(TAG, "Starting RX mode (CSI → MQTT)");
    ESP_ERROR_CHECK(mqtt_client_init());
    ESP_ERROR_CHECK(csi_handler_init());
#endif

    ESP_LOGI(TAG, "Initialization complete");
}
