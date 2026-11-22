/* EE 250L Lab 04 Starter Code — Fall 2026
 *
 * board_sensors.c — SUPPLIED.  No TODOs here.
 *
 * Two independent things live in this file because neither is big enough to
 * deserve its own: the internal temperature sensor, and the BOOT button.
 */

#include "board_sensors.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/gpio.h"
#include "driver/temperature_sensor.h"
#include "esp_log.h"

static const char *TAG = "sensors";

/* ===========================================================================
 * Internal temperature sensor
 * ===========================================================================
 * `driver/temperature_sensor.h` is the modern handle-based API.  If you find
 * example code that calls `temp_sensor_set_config()` / `temp_sensor_read_celsius()`
 * on a global, that is the legacy `driver/temp_sensor.h` API, which was
 * removed in ESP-IDF v5 and does not exist here.
 *
 * The hardware trades range against precision: you tell it the window you
 * care about and it picks an internal offset.  -10..80 C covers everything a
 * board on a lab bench will ever see and gives about +/-1 C.
 */

static temperature_sensor_handle_t s_tsens;

esp_err_t temp_sensor_init(void)
{
    /* The DEFAULT macro fills in the clock source and the power-down flag so
     * that adding fields to this struct in a future IDF does not break us. */
    temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);

    esp_err_t err = temperature_sensor_install(&cfg, &s_tsens);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "temperature_sensor_install failed: %s",
                 esp_err_to_name(err));
        return err;
    }

    /* install() allocates; enable() actually powers the sensor up.  Reads
     * before enable() return ESP_ERR_INVALID_STATE. */
    err = temperature_sensor_enable(s_tsens);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "temperature_sensor_enable failed: %s",
                 esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "internal temperature sensor enabled (-10..80 C)");
    return ESP_OK;
}

esp_err_t temp_sensor_read_celsius(float *out_celsius)
{
    if (out_celsius == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_tsens == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    return temperature_sensor_get_celsius(s_tsens, out_celsius);
}

/* ===========================================================================
 * BOOT button
 * ===========================================================================
 * A mechanical switch does not close cleanly: for a few milliseconds the
 * contacts bounce and the pin reads a burst of highs and lows.  Publishing on
 * every edge would send five "pressed" messages for one press.
 *
 * Two ways to handle this:
 *
 *   - GPIO interrupt + a queue + a timer to filter the bounces.  Lower
 *     latency, but the handler must be ISR-safe (no logging, no blocking, no
 *     esp_mqtt_client_publish) so it has to hand off to a task anyway.
 *
 *   - Poll the pin from a task.  Read the level every 20 ms; report a press
 *     only on a HIGH->LOW transition between two consecutive samples.  Because
 *     the samples are 20 ms apart and the bounce lasts a few ms, the bounce is
 *     invisible: it is debounce for free.
 *
 * We poll.  20 ms is imperceptible to a human finger and the task costs
 * essentially nothing.  Prefer the boring mechanism when it is good enough.
 */

#define BUTTON_POLL_MS 20

static button_callback_t s_button_cb;

static void button_task(void *arg)
{
    /* Start from "released" so that a button already held down at boot does
     * not look like a fresh press. */
    int prev_level = 1;

    while (true) {
        int level = gpio_get_level(BUTTON_GPIO);

        /* Active low: a press is the falling edge 1 -> 0. */
        if (prev_level == 1 && level == 0) {
            ESP_LOGI(TAG, "BOOT button pressed");
            if (s_button_cb != NULL) {
                s_button_cb();
            }
        }

        prev_level = level;
        vTaskDelay(pdMS_TO_TICKS(BUTTON_POLL_MS));
    }
}

esp_err_t button_init(button_callback_t cb)
{
    s_button_cb = cb;

    gpio_config_t io = {
        .pin_bit_mask = 1ULL << BUTTON_GPIO,
        .mode         = GPIO_MODE_INPUT,
        /* The board already has an external pull-up on GPIO 0; enabling the
         * internal one too is harmless and makes the code correct on a bare
         * module where the external resistor is absent. */
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE, /* we poll; no ISR */
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gpio_config failed: %s", esp_err_to_name(err));
        return err;
    }

    /* 3072 bytes is comfortable: the callback publishes an MQTT message,
     * which formats a topic string on this stack. */
    BaseType_t ok = xTaskCreate(button_task, "button", 3072, NULL, 5, NULL);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "BOOT button (GPIO %d) polled every %d ms",
             BUTTON_GPIO, BUTTON_POLL_MS);
    return ESP_OK;
}
