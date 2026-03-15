idf_component_register(
    SRCS "main.c" "csi_handler.c" "mqtt_client.c"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_event esp_netif mqtt nvs_flash esp_timer
)
