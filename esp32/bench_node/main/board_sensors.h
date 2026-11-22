/* EE 250L Lab 04 Starter Code — Fall 2026
 *
 * board_sensors.h — SUPPLIED.  The two things on the DevKitC-1 that produce
 * data: the SoC's internal temperature sensor, and the BOOT button.
 *
 * No TODOs here.
 */

#pragma once

#include "esp_err.h"

/* The BOOT button.  It is wired to GPIO 0 with a pull-up on the board, so the
 * pin reads HIGH at rest and LOW while the button is held: "active low".
 * GPIO 0 is also a strapping pin — holding it down through a reset puts the
 * chip into download mode — which is why you press it *after* boot, not
 * during. */
#define BUTTON_GPIO 0

/**
 * @brief Install and enable the SoC's internal temperature sensor.
 */
esp_err_t temp_sensor_init(void);

/**
 * @brief Read the internal temperature in degrees Celsius.
 *
 * Note what this measures: the temperature of the *silicon die*, not of the
 * room. It idles well above ambient (mid-30s is normal) and it climbs when
 * the Wi-Fi transmitter is busy. It is a perfectly good telemetry signal for
 * this lab, and a perfectly bad thermometer.
 *
 * @param out_celsius written on success.
 */
esp_err_t temp_sensor_read_celsius(float *out_celsius);

/** Called from the button task after a debounced press. */
typedef void (*button_callback_t)(void);

/**
 * @brief Configure GPIO 0 as an input and start the task that watches it.
 *
 * @param cb invoked once per press, from the button task (NOT from an ISR),
 *           so it is free to publish MQTT messages, log, or block briefly.
 */
esp_err_t button_init(button_callback_t cb);
