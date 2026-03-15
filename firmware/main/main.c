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

#include <fcntl.h>
#include <inttypes.h>
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

/* ── TX mode: UDP frame sender (esp_timer-based) ──────────────────── */

#if BOARD_ROLE_TX

/** TX rate logging interval in seconds. */
#define TX_LOG_INTERVAL_S  10

/** State shared between timer callback and rate-logging task. */
static int s_tx_sock = -1;
static struct sockaddr_in s_tx_dest;
static uint32_t s_tx_seq = 0;
static volatile uint32_t s_tx_count = 0;       /* Packets sent since last log. */
static volatile uint32_t s_tx_errors = 0;      /* Send errors since last log. */

/**
 * esp_timer callback — fires every TX_INTERVAL_US (10,000 µs for 100Hz).
 * Runs in the esp_timer task context (not ISR), so lwIP sendto() is safe.
 */
static void tx_timer_callback(void *arg) {
    s_tx_seq++;
    int err = sendto(s_tx_sock, &s_tx_seq, sizeof(s_tx_seq), 0,
                     (struct sockaddr *)&s_tx_dest, sizeof(s_tx_dest));
    if (err < 0) {
        s_tx_errors++;
    } else {
        s_tx_count++;
    }
}

/**
 * Rate-logging task: prints actual TX rate every TX_LOG_INTERVAL_S seconds.
 */
static void tx_rate_log_task(void *arg) {
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(TX_LOG_INTERVAL_S * 1000));
        uint32_t sent = s_tx_count;
        uint32_t errs = s_tx_errors;
        s_tx_count = 0;
        s_tx_errors = 0;
        float rate = (float)sent / TX_LOG_INTERVAL_S;
        ESP_LOGI(TAG, "TX rate: %.1f pps (sent=%"PRIu32" errors=%"PRIu32" seq=%"PRIu32")",
                 rate, sent, errs, s_tx_seq);
    }
}

/**
 * Initialize TX mode: create UDP socket, start esp_timer, start rate logger.
 */
static void tx_start(void) {
    /* Set up destination address. */
    s_tx_dest.sin_family = AF_INET;
    s_tx_dest.sin_port = htons(TX_UDP_PORT);
    inet_aton(TX_TARGET_IP, &s_tx_dest.sin_addr);

    /* Create non-blocking UDP socket. */
    s_tx_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s_tx_sock < 0) {
        ESP_LOGE(TAG, "Failed to create UDP socket: errno %d", errno);
        return;
    }

    /* Enable broadcast if target is broadcast address. */
    int broadcast = 1;
    setsockopt(s_tx_sock, SOL_SOCKET, SO_BROADCAST, &broadcast, sizeof(broadcast));

    /* Make socket non-blocking so timer callback never stalls. */
    int flags = fcntl(s_tx_sock, F_GETFL, 0);
    fcntl(s_tx_sock, F_SETFL, flags | O_NONBLOCK);

    /* Create periodic esp_timer for precise TX interval. */
    const esp_timer_create_args_t timer_args = {
        .callback = tx_timer_callback,
        .name = "tx_timer",
    };
    esp_timer_handle_t tx_timer;
    ESP_ERROR_CHECK(esp_timer_create(&timer_args, &tx_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(tx_timer, TX_INTERVAL_US));

    ESP_LOGI(TAG, "TX timer started: %d Hz (%d µs) to %s:%d",
             TX_RATE_HZ, TX_INTERVAL_US, TX_TARGET_IP, TX_UDP_PORT);

    /* Start rate-logging task on core 0 (low priority). */
    xTaskCreatePinnedToCore(tx_rate_log_task, "tx_log", 2048, NULL, 2, NULL, 0);
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
    /* TX mode: start esp_timer-driven UDP sender. */
    ESP_LOGI(TAG, "Starting TX mode (%d Hz)", TX_RATE_HZ);
    tx_start();
#else
    /* RX mode: initialize MQTT, then start CSI collection. */
    ESP_LOGI(TAG, "Starting RX mode (CSI → MQTT)");
    ESP_ERROR_CHECK(mqtt_client_init());
    ESP_ERROR_CHECK(csi_handler_init());
#endif

    ESP_LOGI(TAG, "Initialization complete");
}
