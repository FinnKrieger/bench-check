/* AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README. */
/* Bench Check bench node: reads the ADC1 pins and talks to the laptop over MQTT. */
#pragma once

#include "esp_err.h"

/** Set up the ADC, connect to the broker and start sending readings (call after Wi-Fi is up). */
esp_err_t bench_start(void);

/** The board's button: ask the laptop to check the current build step. */
void bench_request_check(void);
