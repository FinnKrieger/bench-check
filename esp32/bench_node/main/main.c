/* AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README. */
/* Bench Check bench node: ESP32-S3-DevKitC-1.
 *
 *   NVS -> LED (blue while connecting) -> Wi-Fi -> bench (ADC + MQTT + readings) -> BOOT button
 *
 * LED: blue = not connected yet, off = ready, white = checking, then green / red / yellow.
 */

#include <string.h>

#include "esp_log.h"
#include "nvs_flash.h"

#include "bench.h"
#include "board_sensors.h"
#include "credentials.h"
#include "led.h"
#include "wifi_station.h"

static const char *TAG = "main";

void app_main(void)
{
    ESP_LOGI(TAG, "Bench Check bench node");
    if (strcmp(WIFI_SSID, "YOUR_WIFI_SSID") == 0 || strstr(MQTT_BROKER_URI, "YOUR_LAPTOP_IP")) {
        ESP_LOGE(TAG, "Fill in main/credentials.h (Wi-Fi and the laptop's IP) and rebuild.");
    }

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    ESP_ERROR_CHECK(led_init());
    led_set_color_name("blue");

    if (wifi_station_connect() != ESP_OK) {
        ESP_LOGE(TAG, "no Wi-Fi yet; still retrying in the background");
    }
    ESP_ERROR_CHECK(bench_start());
    ESP_ERROR_CHECK(button_init(bench_request_check));
    ESP_LOGI(TAG, "ready: press BOOT to check the current step");
}
