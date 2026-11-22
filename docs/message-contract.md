# Message contract (MQTT)

Broker: Mosquitto on the laptop, port 1883 (MQTT) and 9001 (MQTT over WebSockets, for the
dashboard in the browser). All payloads are JSON. The ESP32 (real or simulated), the laptop and
the dashboard only share what is on this page.

| Topic | From | To | QoS | Retained | When |
|---|---|---|---|---|---|
| `benchcheck/readings` | ESP32 | laptop | 0 | no | about 5 times a second |
| `benchcheck/check_request` | ESP32, dashboard | laptop | 1 | no | when the button (or Check now) is pressed |
| `benchcheck/check_result` | laptop | ESP32, dashboard | 1 | no | after each check |
| `benchcheck/status` | laptop | ESP32, dashboard | 1 | yes | on start and whenever the step changes |
| `benchcheck/control` | dashboard | laptop | 1 | no | when someone changes the step, layout, socket or calibration by hand |
| `benchcheck/circuit` | laptop | dashboard | 1 | yes | when the laptop node starts |
| `benchcheck/live` | laptop | dashboard | 0 | no | after every reading |
| `benchcheck/socket` | laptop | dashboard, sim | 0 | no | after every reading |
| `benchcheck/calibration` | laptop | dashboard | 1 | yes | on start, when the sending device changes, and during a calibration run |
| `benchcheck/design/preview` | designer | laptop | 0 | no | after each edit in the designer |
| `benchcheck/design/use` | designer | laptop | 1 | no | on "Use this circuit" |
| `benchcheck/design/plan` | laptop | designer | 0 | no | answer to a preview or use |

## `benchcheck/readings`

The ESP32 does not know the circuit. It sends raw ADC counts (0 to 4095) for every ADC1 pin it
uses (the test points and the resistor test socket), already median filtered over a burst. The laptop turns counts into volts (calibration) and pins into
circuit nodes (the wiring file).

```json
{"device": "esp32s3", "seq": 412, "t_ms": 82400,
 "raw": {"4": 1600, "5": 2425, "6": 1083, "7": 4095, "8": 4095, "9": 2210},
 "mv": {"4": 1206, "5": 1836, "6": 820, "7": 3100, "8": 3100, "9": 1690}}
```

`device` names the sender: `esp32s3` (the real board), `esp32-sim` (`fake_esp32.py`) or
`breadboard-sim` (the simulator page). The laptop keeps one pull model per device name and uses the
one of the device whose readings are arriving. A reading without `device` counts as `esp32s3`.

`mv` is the ESP32-S3's factory-calibrated voltage for each pin; the laptop uses it unless
`laptop/calibration.json` exists. The S3 reads 0-3.1 V at 12 dB attenuation.

The firmware also sends `up` and `dn`: each pin's reading in millivolts with the chip's internal
pull-up, then pull-down, switched on. An unconnected ADC pin holds stale charge and reads like a
steady voltage, but it swings to the ceiling with the pull-up and to 0 with the pull-down; a pin
the circuit drives does not do both. The laptop treats a pin as floating when `up` >= 2900 and
`dn` <= 200, and then checks every test point, including the ones that should still be
unconnected at the current step. Without `up`/`dn` it falls back to treating jittery readings as
unstable.

```json
{"up": {"4": 3149, "5": 3149, "9": 705}, "dn": {"4": 3149, "5": 0, "9": 0}}
```
(GPIO 4 is driven at 3.1 V, GPIO 5 is floating, GPIO 9 sits at ground through a resistor.)

## `benchcheck/check_request`

```json
{"device": "esp32-sim", "seq": 7, "t_ms": 83010}
```

## `benchcheck/check_result`

`led` is what the ESP32 shows: `green` (pass), `red` (fail), `yellow` (unsure). The ESP32 only
reads a result that arrives in one 4 kB piece, so the laptop leaves out fields that repeat others.

```json
{"seq": 7, "step": 3, "result": "fail", "led": "red",
 "message": "Node A reads 3.01 V, expected 2.27 V. Most likely: R2 is 22k instead of 2.2k",
 "problem": {"kind": "voltage", "nodes": ["a"], "headline": "Node A reads 3.01 V, expected 2.27 V"},
 "suspects": [{"label": "R2 is 22k instead of 2.2k", "part": "R2", "kind": "value",
               "action": "Read R2's colour bands, or pull it and put it in the test socket", "wrong_ohms": 22000}],
 "points": {"a": {"pin": "33", "expected": 2.27, "measured": 3.01, "floating": false, "drive_ok": null, "ok": false}},
 "next_step": 3}
```

- `problem`: what is off. `kind` is `voltage`, `floating`, `extra`, `resistance` (voltages right,
  pull test off: the result is `unsure`), `flicker`, `unstable` or `no_data` (also `unsure`).
  `nodes` are the nodes involved and `headline` the sentence with numbers.
- `suspects`: up to three mistakes to try, closest match first. `kind` is `value`, `open`,
  `ground`, `short` or `moved`; `action` is one thing to try; a `value` suspect carries
  `wrong_ohms`. The test socket can add `ruled_out` or `found`.
- `more_same`: how many other mistakes would read the same as the last suspect (only when set).
- `updated`: true when the laptop publishes the same result again because the test socket settled a
  suspect. The message and suspects are the new ones; the ESP32 can ignore it.
- `no_data`: with no readings, or none newer than 1.5 s, the result is `unsure` with
  "No fresh readings from the ESP32: check its power and Wi-Fi" and the step does not change.

## `benchcheck/status`

```json
{"step": 3, "total_steps": 7, "instruction": "R2 (2.2k) from row A to GND", "complete": false,
 "placement": "Place R2 (2.2k): one leg in 6b, the other in − rail (column 6)"}
```

## `benchcheck/control`

```json
{"cmd": "set_step", "step": 2}
{"cmd": "set_socket", "on": false}
{"cmd": "calibrate", "action": "start"}
{"cmd": "set_layout", "place": {"esp_row": 1, "nodes": {"a": 8}, "socket_row": 40}}
```

`set_step` takes 1 to the number of steps plus one (the circuit is complete). `set_socket` turns
the resistor test socket on or off and keeps the build step. `calibrate` is `start`, `save` or
`cancel`; see `benchcheck/calibration` below.

## `benchcheck/circuit`

Everything the dashboard needs to draw the circuit. `expected` holds the solved voltage at every
node after each build step (`null` where the node is not connected to anything yet).

```json
{"parts": [{"step": 2, "name": "R1", "kind": "R", "a": "vcc", "b": "a", "value": "1k",
            "instruction": "R1 (1k) from the 3.3 V rail to row A"}],
 "nodes": ["a", "b", "c", "vcc"], "pins": {"32": "a", "33": "c", "34": "b", "35": "vcc"},
 "tolerance": 0.15, "expected": {"2": {"a": 3.3, "b": null, "c": null, "vcc": 3.3}},
 "layout": {"supply": "vcc", "node_columns": {"a": 4, "b": 8, "c": 12},
            "power": {"3v3": "+:1", "gnd": "-:1"},
            "probes": {"32": "d:8", "33": "c:4", "34": "d:12", "35": "+:2"},
            "parts": {"V1": ["+:1", "-:1"], "R1": ["+:4", "a:4"], "R2": ["e:4", "-:4"]}},
 "placements": ["Plug in the ESP32: ...", "Place R1 (1k): one leg in + rail (column 6), the other in 6a"]}
```

Holes: `+:6` / `-:6` are the + and − rails at column 6; `b:6` is hole b of column 6. Each
column's five holes a–e are connected; the + rail is on top and the − rail at the bottom.

## `benchcheck/live`

The latest reading at each test point after calibration, compared with the current step.

```json
{"step": 4, "points": {"a": {"pin": "32", "volts": 2.28, "expected": 2.27, "ok": true}}}
```

## `benchcheck/design/preview` and `benchcheck/design/use`

The circuit as circuit-file text. `use` also carries a name and the drawing, which the laptop
saves next to the circuit file.

```json
{"req": 12, "cir": "V1 vcc gnd 3.3\nR1 vcc a 1k\n...", "name": "bridge", "design": {"parts": [], "wires": [], "grounds": []}}
```

## `benchcheck/design/plan`

The laptop's plan for the latest request (`req` matches). After a `use`, `used` says whether the
laptop switched to it.

```json
{"req": 12, "ok": true, "error": null, "used": true, "name": "bridge", "steps": 6,
 "nodes": {"a": 2.13, "b": 1.17, "vcc": 3.3}, "pins": {"32": "a", "33": "b", "34": "vcc"},
 "faults_simulated": 210, "hidden_at_own_step": 24,
 "never_visible": ["step 6: R5 is missing or a leg is not making contact"],
 "checked_late": [{"step": 2, "part": "R1"}]}
```

## `benchcheck/socket`

The resistor test socket, judged against the part the current step needs. `result` is `empty`,
`settling`, `ok`, `wrong` or `info` (no resistor step right now).

```json
{"step": 4, "part": "R3", "state": "value", "volts": 2.245, "ohms": 10003, "nearest": 10000,
 "kit": true, "label": "10k", "bands": "brown-black-orange, or brown-black-black-red on a 5-band", "result": "wrong",
 "message": "✗ That's a 10k (brown-black-orange, or brown-black-black-red on a 5-band). R3 needs 1k (brown-black-red, or brown-black-black-brown on a 5-band)"}
```

`benchcheck/status` also carries `value_checked`: the parts whose value the socket confirmed.

## `benchcheck/calibration`

The ESP32's pull-up and pull-down are measured with the test socket (see the README), once per
device. This message describes the device whose readings are arriving. During a run (`active`)
it also lists the resistors measured so far.

```json
{"device": "esp32s3", "active": true, "calibrated": false, "when": null, "socket": true,
 "done": ["empty socket", "1k", "4.7k"], "ready": false, "current": "4.7k"}
```

`ready` is true when `save` would work. The laptop keeps the models in `laptop/data/pulls.json`,
one per device name.

## Restart

The laptop saves the step and the socket-confirmed parts in `laptop/data/progress.json`, with the
circuit file's name and a hash of its text. On start-up it resumes there if the circuit is
unchanged. `benchcheck/status` is published as usual.
