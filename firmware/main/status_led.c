/**
 * Status LED — Visual feedback via GPIO-driven LED.
 *
 * Runs a background FreeRTOS task that toggles the LED based on the
 * current state. Patterns are chosen for easy visual identification
 * from across the room:
 *
 *   CONNECTING:  200ms on / 200ms off  (fast blink)
 *   CONNECTED:   solid on
 *   ERROR:       3× 100ms flash, then 700ms off  (urgent triple-pulse)
 */

#include "status_led.h"
#include "config.h"

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <stdatomic.h>

static atomic_int s_state = LED_STATE_CONNECTING;

static void led_on(void)  { gpio_set_level(STATUS_LED_GPIO, 1); }
static void led_off(void) { gpio_set_level(STATUS_LED_GPIO, 0); }

static void status_led_task(void *arg) {
    while (1) {
        led_state_t state = atomic_load(&s_state);

        switch (state) {
            case LED_STATE_CONNECTING:
                led_on();
                vTaskDelay(pdMS_TO_TICKS(200));
                led_off();
                vTaskDelay(pdMS_TO_TICKS(200));
                break;

            case LED_STATE_CONNECTED:
                led_on();
                vTaskDelay(pdMS_TO_TICKS(500));
                break;

            case LED_STATE_ERROR:
                /* Triple flash */
                for (int i = 0; i < 3; i++) {
                    led_on();
                    vTaskDelay(pdMS_TO_TICKS(100));
                    led_off();
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
                vTaskDelay(pdMS_TO_TICKS(700));
                break;
        }
    }
}

void status_led_init(void) {
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << STATUS_LED_GPIO),
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_conf);
    led_off();

    xTaskCreatePinnedToCore(status_led_task, "led_task", 2048, NULL, 2, NULL, 0);
}

void status_led_set(led_state_t state) {
    atomic_store(&s_state, state);
}
