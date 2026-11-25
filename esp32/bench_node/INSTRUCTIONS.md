# Bench Check: flash the ESP32-S3 bench node

Instructions for whoever (person or AI coding agent) builds and flashes this firmware on a new
computer. Follow the steps in order. Ask the human for anything marked **ASK**.

## What this is

ESP-IDF v6.0 firmware for an **ESP32-S3-DevKitC-1** (EE 250 final project, "Bench Check").
The board joins Wi-Fi, connects to an MQTT broker on the laptop, and:

- every 200 ms reads GPIO 4-9 (ADC1, 12 dB attenuation, about 0-3.1 V), takes the median of 9
  samples per pin, and publishes raw counts plus factory-calibrated millivolts on
  `benchcheck/readings`;
- publishes `benchcheck/check_request` when the **BOOT** button is pressed;
- subscribes to `benchcheck/check_result` and shows the `"led"` colour on the onboard RGB LED.

LED meaning: blue = connecting, off = ready, white = checking, green = pass, red = fail,
yellow = unsure.

`wifi_station.c/.h`, `led.c/.h` and `board_sensors.c/.h` come from the EE 250L Lab 4 starter code
(`led.c` gained a "yellow" entry). `main.c` and `bench.c` are the bench node.

## 1. ESP-IDF v6.0.x

Check whether it is installed:

```bash
idf.py --version
```

If that fails, look for an existing install and source its export script (for example
`~/.espressif/v6.0.2/esp-idf/export.sh` or `~/esp/esp-idf/export.sh`). If there is none, install
v6.0.2 following Espressif's guide for ESP32-S3
(https://docs.espressif.com/projects/esp-idf/en/v6.0.2/esp32s3/get-started/). On macOS/Linux:

```bash
git clone -b v6.0.2 --recursive https://github.com/espressif/esp-idf.git ~/esp/esp-idf
cd ~/esp/esp-idf && ./install.sh esp32s3
. ~/esp/esp-idf/export.sh
```

On Windows, use Espressif's ESP-IDF installer and the "ESP-IDF PowerShell" it creates.
Every new terminal needs `export.sh` (or the ESP-IDF shell) before `idf.py` works.

## 2. Credentials (ASK)

Edit `main/credentials.h`. Ask the human for:

- `WIFI_SSID` and `WIFI_PASSWORD`: a **2.4 GHz WPA2-PSK** network, usually a phone hotspot.
  USC's eduroam (WPA2-Enterprise) does **not** work.
- `MQTT_BROKER_URI`: `mqtt://<laptop IP>:<port>`, the laptop running Mosquitto on the same
  network. macOS: `ipconfig getifaddr en0`. Windows: `ipconfig`, the "IPv4 Address" under the
  Wi-Fi adapter. Port 1883 by default; Finn's Mac uses 1884, and Windows uses 1884 because the
  Mosquitto installer's own Windows service already holds 1883 (see the README's Windows setup).

Do not commit, upload or send `credentials.h` once it holds real values.

## 3. Find the serial port

Plug the board into the computer with a **data** USB-C cable (either connector on the board).

- macOS: `ls /dev/cu.usbmodem* /dev/cu.usbserial*`
- Linux: `ls /dev/ttyACM* /dev/ttyUSB*`
- Windows: Device Manager, "Ports (COM & LPT)", e.g. `COM5`

If nothing appears: it is almost always a charge-only cable. Try another cable, connected
directly (no hub). The red power LED lighting up does not prove the cable carries data.

## 4. Build and flash

From this folder (`bench_node`):

```bash
idf.py set-target esp32s3      # first time only
idf.py build                   # first build downloads espressif/mqtt: needs internet
idf.py -p <PORT> flash monitor
```

Exit the monitor with `Ctrl+]`. If flashing cannot connect, hold **BOOT**, tap **RST**, release
BOOT, and run the flash command again.

## 5. Check that it works

In the monitor log, expect, in order:

1. `Bench Check bench node`
2. Wi-Fi association and an IP address
3. `connected to the broker at mqtt://...`
4. `ready: press BOOT to check the current step`

and the LED going from blue to off. If `Fill in main/credentials.h` appears, step 2 was skipped.

Without the laptop software, the MQTT side can be checked with the Mosquitto clients
(any machine on the same network):

```bash
mosquitto_sub -h <broker IP> -p <port> -t 'benchcheck/#' -v
```

Expect about five `benchcheck/readings` lines a second, and a `benchcheck/check_request` line
when BOOT is pressed. To test the LED:

```bash
mosquitto_pub -h <broker IP> -p <port> -t benchcheck/check_result -m '{"led":"green"}'
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Log says `LED -> ...` but the LED stays dark | Board revision: set `LED_GPIO` in `main/led.h` to 38 (v1.1 and later) or 48 (v1.0), rebuild |
| `MQTT error` in the log | Broker not running, wrong IP or port, broker only listening on localhost (its config needs `listener <port> 0.0.0.0`), macOS firewall blocking mosquitto, or the board and laptop on different networks |
| Never gets an IP | Wrong SSID/password, a 5 GHz-only network, or eduroam |
| Readings stuck at 4095 / 3100 mV | Pin above 3.1 V or floating; that is expected for an unconnected pin |

## Wiring safety

- The DevKitC-1 is too wide to straddle a breadboard. Do not press it into the board so that a
  pin header lands in a power rail: every pin in that rail would be shorted together. Keep it
  beside the breadboard and use jumper wires (female-to-male) from its pins.
- GPIO 4-9 are the measuring inputs. Never put more than 3.3 V on them.
- Use the board's 3V3 and GND pins to power the breadboard rails.

## Files

| File | What |
|---|---|
| `main/main.c` | Start-up: NVS, LED, Wi-Fi, bench, BOOT button |
| `main/bench.c/.h` | ADC sampling, MQTT, readings task, check requests, LED results |
| `main/credentials.h` | Wi-Fi and broker settings (fill in) |
| `main/wifi_station.c/.h`, `main/led.c/.h`, `main/board_sensors.c/.h` | From the Lab 4 starter code |
| `main/idf_component.yml` | Pulls `espressif/mqtt` (ESP-IDF 6 ships MQTT as a managed component) |
| `sdkconfig.defaults` | Target esp32s3, MQTT 3.1.1 |
