/* Bench Check bench node.
 *
 * The ESP32 does not know the circuit. Five times a second it reads every ADC1 pin the bench uses
 * (GPIO 4-10), takes the median of a short burst, and publishes the raw counts plus the voltage in
 * millivolts from the chip's factory calibration on benchcheck/readings. The laptop decides which
 * pin is which test point (or the resistor test socket) and judges the readings.
 *
 * An unconnected ADC pin does not read noise: it holds stale charge and looks like a steady, wrong
 * voltage. So each pin is also read with the chip's internal pull-up and then pull-down switched on
 * (about 45 k each). A floating pin swings from rail to rail; a pin driven by the circuit barely
 * moves. Both readings go to the laptop ("up" and "dn"), which decides.
 *
 * The BOOT button publishes benchcheck/check_request; the answer on benchcheck/check_result
 * carries the colour for the onboard LED (green pass, red fail, yellow unsure).
 * See docs/message-contract.md for the payloads.
 */

#include "bench.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_adc/adc_cali.h"
#include "esp_check.h"
#include "esp_adc/adc_cali_scheme.h"
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "mqtt_client.h"

#include "credentials.h"
#include "led.h"

static const char *TAG = "bench";

#define T_READINGS "benchcheck/readings"
#define T_REQUEST  "benchcheck/check_request"
#define T_RESULT   "benchcheck/check_result"

#define PERIOD_MS 200   /* 5 readings a second, as the laptop expects */
#define BURST     9     /* samples per pin per reading; the median rejects spikes */
#define PULL_BURST 3    /* samples for the pull-up / pull-down readings */
#define SETTLE_US 300   /* after switching a pull resistor, before sampling */

/* ADC1 on the ESP32-S3: GPIO n is channel n-1. GPIO 3 is a strapping pin, so the bench uses 4-10. */
static const struct {
    int gpio;
    adc_channel_t channel;
} k_pins[] = {
    {4, ADC_CHANNEL_3}, {5, ADC_CHANNEL_4}, {6, ADC_CHANNEL_5},
    {7, ADC_CHANNEL_6}, {8, ADC_CHANNEL_7}, {9, ADC_CHANNEL_8},
    {10, ADC_CHANNEL_9},
};
#define N_PINS (sizeof(k_pins) / sizeof(k_pins[0]))

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali[N_PINS];
static esp_mqtt_client_handle_t s_mqtt;
static volatile bool s_connected;
static uint32_t s_seq;

/* ---------- ADC ---------- */

static esp_err_t adc_setup(void)
{
    adc_oneshot_unit_init_cfg_t unit = {.unit_id = ADC_UNIT_1};
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&unit, &s_adc), TAG, "ADC unit");
    adc_oneshot_chan_cfg_t chan = {
        .atten = ADC_ATTEN_DB_12,       /* widest range: about 0-3.1 V */
        .bitwidth = ADC_BITWIDTH_12,    /* 0-4095 */
    };
    for (size_t i = 0; i < N_PINS; i++) {
        ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, k_pins[i].channel, &chan), TAG, "channel");
        adc_cali_curve_fitting_config_t cal = {
            .unit_id = ADC_UNIT_1,
            .chan = k_pins[i].channel,
            .atten = ADC_ATTEN_DB_12,
            .bitwidth = ADC_BITWIDTH_12,
        };
        if (adc_cali_create_scheme_curve_fitting(&cal, &s_cali[i]) != ESP_OK) {
            ESP_LOGW(TAG, "no factory calibration for GPIO %d; sending raw counts only", k_pins[i].gpio);
            s_cali[i] = NULL;
        }
    }
    return ESP_OK;
}

static int cmp_int(const void *a, const void *b)
{
    return *(const int *)a - *(const int *)b;
}

/* Median of a burst: one ADC sample can spike, the middle of nine rarely does. */
static int read_median(adc_channel_t ch, int n)
{
    int s[BURST];
    for (int i = 0; i < n; i++) {
        if (adc_oneshot_read(s_adc, ch, &s[i]) != ESP_OK) {
            s[i] = 0;
        }
    }
    qsort(s, n, sizeof(int), cmp_int);
    return s[n / 2];
}

static int to_mv(size_t i, int raw)
{
    int mv = -1;
    if (s_cali[i]) {
        adc_cali_raw_to_voltage(s_cali[i], raw, &mv);
    }
    return mv;
}

/* One pin: the plain reading, then with the internal pull-up, then with the pull-down. */
static void read_pin(size_t i, int *raw, int *mv, int *up_mv, int *dn_mv)
{
    gpio_num_t gpio = k_pins[i].gpio;
    adc_channel_t ch = k_pins[i].channel;
    gpio_set_pull_mode(gpio, GPIO_FLOATING);
    esp_rom_delay_us(SETTLE_US);
    read_median(ch, 2);                      /* throw away: charge left from the previous channel */
    *raw = read_median(ch, BURST);
    *mv = to_mv(i, *raw);
    gpio_set_pull_mode(gpio, GPIO_PULLUP_ONLY);
    esp_rom_delay_us(SETTLE_US);
    *up_mv = to_mv(i, read_median(ch, PULL_BURST));
    gpio_set_pull_mode(gpio, GPIO_PULLDOWN_ONLY);
    esp_rom_delay_us(SETTLE_US);
    *dn_mv = to_mv(i, read_median(ch, PULL_BURST));
    gpio_set_pull_mode(gpio, GPIO_FLOATING);
}

static int append_map(char *msg, size_t size, int n, const char *key, const int *vals)
{
    n += snprintf(msg + n, size - n, ",\"%s\":{", key);
    bool first = true;
    for (size_t i = 0; i < N_PINS; i++) {
        if (vals[i] >= 0) {
            n += snprintf(msg + n, size - n, "%s\"%d\":%d", first ? "" : ",", k_pins[i].gpio, vals[i]);
            first = false;
        }
    }
    return n + snprintf(msg + n, size - n, "}");
}

static void readings_task(void *arg)
{
    static char msg[768];
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(PERIOD_MS));
        if (!s_connected) {
            continue;
        }
        int raw[N_PINS], mv[N_PINS], up[N_PINS], dn[N_PINS];
        for (size_t i = 0; i < N_PINS; i++) {
            read_pin(i, &raw[i], &mv[i], &up[i], &dn[i]);
        }
        /* {"device":"esp32s3","seq":12,"t_ms":2400,"raw":{"4":1234,...},"mv":{...},"up":{...},"dn":{...}} */
        int n = snprintf(msg, sizeof msg, "{\"device\":\"esp32s3\",\"seq\":%lu,\"t_ms\":%lld,\"raw\":{",
                         (unsigned long)++s_seq, (long long)(esp_timer_get_time() / 1000));
        for (size_t i = 0; i < N_PINS; i++) {
            n += snprintf(msg + n, sizeof msg - n, "%s\"%d\":%d", i ? "," : "", k_pins[i].gpio, raw[i]);
        }
        n += snprintf(msg + n, sizeof msg - n, "}");
        n = append_map(msg, sizeof msg, n, "mv", mv);
        n = append_map(msg, sizeof msg, n, "up", up);
        n = append_map(msg, sizeof msg, n, "dn", dn);
        snprintf(msg + n, sizeof msg - n, "}");
        esp_mqtt_client_publish(s_mqtt, T_READINGS, msg, 0, 0, 0);
    }
}

/* ---------- MQTT ---------- */

/* Pull "led":"green" out of a check_result without a JSON library. */
static void apply_result(const char *json)
{
    const char *p = strstr(json, "\"led\"");
    if (!p || !(p = strchr(p + 5, '"'))) {
        return;
    }
    char colour[16] = {0};
    for (size_t i = 0; i < sizeof colour - 1 && p[1 + i] && p[1 + i] != '"'; i++) {
        colour[i] = p[1 + i];
    }
    ESP_LOGI(TAG, "check result -> LED %s", colour);
    led_set_color_name(colour);
}

static void on_mqtt(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_mqtt_event_handle_t ev = data;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "connected to the broker at %s", MQTT_BROKER_URI);
        s_connected = true;
        esp_mqtt_client_subscribe(s_mqtt, T_RESULT, 1);
        led_set_color_name("off");
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "disconnected from the broker; esp-mqtt retries on its own");
        s_connected = false;
        led_set_color_name("blue");
        break;
    case MQTT_EVENT_DATA:
        /* Results are a few hundred bytes; the 4 kB buffer below keeps them in one piece. */
        if (ev->current_data_offset == 0 && ev->data_len == ev->total_data_len &&
            ev->topic_len == strlen(T_RESULT) && strncmp(ev->topic, T_RESULT, ev->topic_len) == 0) {
            char *json = strndup(ev->data, ev->data_len);
            if (json) {
                apply_result(json);
                free(json);
            }
        }
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error: is mosquitto running on the laptop, and is the port in credentials.h right?");
        break;
    default:
        break;
    }
}

void bench_request_check(void)
{
    if (!s_connected) {
        ESP_LOGW(TAG, "button pressed, but not connected to the broker yet");
        return;
    }
    char msg[96];
    snprintf(msg, sizeof msg, "{\"device\":\"esp32s3\",\"seq\":%lu,\"t_ms\":%lld}",
             (unsigned long)s_seq, (long long)(esp_timer_get_time() / 1000));
    esp_mqtt_client_publish(s_mqtt, T_REQUEST, msg, 0, 1, 0);
    led_set_color_name("white");  /* "checking": the result replaces it within a second */
    ESP_LOGI(TAG, "check requested");
}

esp_err_t bench_start(void)
{
    ESP_RETURN_ON_ERROR(adc_setup(), TAG, "ADC setup");
    esp_mqtt_client_config_t cfg = {
        .broker.address.uri = MQTT_BROKER_URI,
        .buffer.size = 4096,
    };
    s_mqtt = esp_mqtt_client_init(&cfg);
    if (!s_mqtt) {
        return ESP_FAIL;
    }
    esp_mqtt_client_register_event(s_mqtt, ESP_EVENT_ANY_ID, on_mqtt, NULL);
    ESP_RETURN_ON_ERROR(esp_mqtt_client_start(s_mqtt), TAG, "MQTT start");
    xTaskCreate(readings_task, "readings", 4096, NULL, 5, NULL);
    return ESP_OK;
}
