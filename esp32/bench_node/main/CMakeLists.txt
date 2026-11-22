# wifi_station, led and board_sensors come from the EE 250L Lab 4 starter code (led.c gained a
# "yellow" entry); main.c and bench.c are the bench node.
idf_component_register(
    SRCS "main.c" "bench.c" "wifi_station.c" "led.c" "board_sensors.c"
    INCLUDE_DIRS "."
    REQUIRES
        nvs_flash esp_wifi esp_netif esp_event esp_timer
        esp_driver_gpio      # BOOT button
        esp_driver_rmt       # onboard WS2812 LED
        esp_driver_tsens     # board_sensors.c also wraps the die temperature sensor
        esp_adc              # oneshot ADC + factory calibration
)
