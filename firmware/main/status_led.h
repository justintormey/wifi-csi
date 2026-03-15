/**
 * Status LED — Visual feedback for board state.
 *
 * Blink patterns:
 *   Connecting:  fast blink (200ms on/off)
 *   Connected:   solid on
 *   Error:       triple-flash pattern (3x 100ms blink, then 700ms off)
 */

#pragma once

typedef enum {
    LED_STATE_CONNECTING,
    LED_STATE_CONNECTED,
    LED_STATE_ERROR,
} led_state_t;

/**
 * Initialize the status LED GPIO and start the blink task.
 */
void status_led_init(void);

/**
 * Update the LED blink pattern. Thread-safe.
 */
void status_led_set(led_state_t state);
