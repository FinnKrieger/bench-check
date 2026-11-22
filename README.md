# Bench Check

EE 250 final project, Fall 2026. Hansi Lee and Finn Krieger.

An autograder for breadboard circuits. You describe the circuit in a file, the laptop solves it,
picks the test points, and checks each build step from voltages the ESP32 measures, over MQTT.
See `docs/final-project-plan.md` for the full plan and `docs/message-contract.md` for the MQTT
messages.

## Folders

| Folder | What |
|---|---|
| `circuits/` | Circuit files (`demo.cir`) and the wiring files generated from them |
| `laptop/` | Laptop node: solver, test-point picker, checker, MQTT service, event store |
| `esp32_sim/` | Fake ESP32 for the terminal (scripted tests) |
| `dashboard/` | Web pages: dashboard (`index.html`), circuit designer (`designer.html`), breadboard simulator (`breadboard.html`) |
| `esp32/bench_node/` | ESP32-S3 firmware (ESP-IDF 6.0): readings, BOOT button, RGB LED |
| `docs/` | Plan and message contract |

## Setup (once)

```bash
brew install mosquitto
cd Final_Project
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## How a build works

1. **Design the circuit.** Draw it in the designer (http://localhost:8250/designer.html) or write a
   circuit file by hand (see below).
2. **The laptop plans the board.** It solves every step, simulates the likely mistakes, picks the
   test points, and lays the whole circuit out on the breadboard (`laptop/benchcheck/layout.py`):
   node rows spread down the board with room for a 1/4 W resistor (4 to 8 holes long), nodes ordered
   so resistors stay short, every resistor straight along a row or into a rail, and an exact hole for
   every leg and ESP32 wire. `plan_circuit.py` prints this layout.
3. **Plug in the ESP32 first.** Power wires and probe wires, exactly as the dashboard's
   breadboard picture shows, before any part.
4. **Build and test, one part at a time.** Place the highlighted part, take your hand off the
   board, press Test. Green moves you on; red says what is off and lists up to three suspects,
   closest first, each with one thing to try.

A failed check has a `problem` (what is off, with numbers, e.g. "Node C reads 1.20 V, expected
0.28 V") and up to three `suspects`, each with a short action ("Read R5's colour bands, or pull it
and put it in the test socket"). The closest match by distance comes first; the new part only wins
between matches that read the same to within 2 mV. If the voltages are all right and only the
pull test disagrees, the result is yellow ("unsure") instead of red, and the dashboard offers
"Continue anyway". If a resistor you pull and put in the test socket reads as a suspect's correct
value, that suspect is struck through; if it reads as the wrong value in the suspect's label, it
is marked as found.

Test only checks readings from the last second or two. If the ESP32 has stopped sending, the
result is yellow ("No fresh readings from the ESP32: check its power and Wi-Fi") and the step does
not change. Skip step on the last step completes the circuit; on a finished circuit Test checks
the whole board again.

The checker can give false alarms. The pull test is rough (the ESP32's pull resistors are not
precise, and readings near 0 V and 3.3 V are the least accurate), so a yellow result on a node
far from the supply is often nothing. If the board matches the picture, Skip step is the
intended way past one: later steps check the same parts again.

Checking after every part is what lets the laptop name the mistake: it only has to tell apart
single mistakes. With the whole circuit built at once, several mistakes mix and can only be
reported as "something is wrong".

## Run with the fake ESP32

Use four terminals, all starting in `Final_Project`.

1. Start the MQTT broker:
   ```bash
   mosquitto -c mosquitto.conf
   ```
2. Start the laptop node (it plans the circuit itself; `plan_circuit.py` prints the full report):
   ```bash
   cd laptop
   ../.venv/bin/python bench_server.py              # the circuit used last, or the demo
   ../.venv/bin/python bench_server.py ../circuits/demo.cir   # or a specific circuit
   ```
3. Start the fake ESP32 and build the circuit by typing commands:
   ```bash
   cd esp32_sim
   ../.venv/bin/python fake_esp32.py ../circuits/demo.cir
   ```
   `b` places the next part, `c` presses the button, `f` lists mistakes and `f 9` plants one,
   `x` removes it, `u` removes the last part, `q` quits.

   Or run a whole build automatically:
   ```bash
   ../.venv/bin/python fake_esp32.py ../circuits/demo.cir --script "b c b c b f 9 c x c b c"
   ```

4. Serve the dashboard and open http://localhost:8250 in a browser:
   ```bash
   python3 dashboard/serve.py
   ```
   The page connects straight to the broker over WebSockets (port 9001). Test-point dots turn
   green or red live as you build (yellow: voltage fine, pull test off, with a note); the part to
   place next is blue, and after a failed check the suspects get a dashed red outline and the
   rows of the problem nodes turn light red. Check now, Back a step, Skip step and Restart do the same as
   the button on the board and the step controls. The page loads mqtt.js from a CDN, so the
   laptop needs internet the first time it opens it.

The laptop terminal shows every check with measured and expected voltages. Readings and checks
are saved in `laptop/data/bench.db` (SQLite); readings older than 7 days are deleted when the
laptop node starts.

The build step and the parts confirmed in the test socket are saved in `laptop/data/progress.json`
after every change. If you restart the laptop node on the same, unchanged circuit file, it
resumes at that step and says so; Restart in the dashboard begins again. A new circuit from the
designer, or an edited circuit file, starts at step 1.

### Simulated breadboard (instead of the fake ESP32 terminal)

With the dashboard server from step 4 running, open http://localhost:8250/breadboard.html. It is
a drawn breadboard wired to a simulated ESP32: it solves the board you build and sends the same
readings the real ESP32 will, so skip step 3 when you use it (two simulated boards at once would
mix their readings).

- Pick a part from the parts bin, then click a hole for each leg. The ESP32 3.3 V and GND wires
  need one click each. Hovering a hole lights up every hole connected to it.
- The five holes in a numbered column are connected, and so is each rail (+ on top, − at the
  bottom). Each circuit node gets its own tinted column, labelled underneath; any hole in it works.
  The ESP32's probe wires are the numbered yellow flags, already plugged in.
- Make mistakes the way you would on a real board: a leg in the wrong column, both legs in one
  column, the wrong value, or "Loose" on a part to make it flicker in and out of contact.
- "Press the check button" is the board's button. The LED shows the laptop's answer.
- The board is saved in the browser, so a reload keeps it.

To point either page at a broker whose WebSocket port is not 9001, add `?ws=<port>` to the URL.

If port 1883 is already taken (another broker, or a VM forwarding it), run Mosquitto on another
port and pass `--port` to `bench_server.py` and `fake_esp32.py`. The dashboard only uses 9001.

## The breadboard picture

**ESP32 placement:** plug the ESP32-S3-DevKitC-1 into the right half with its left pin row in
column j and its 3V3 pin at row 1 (antenna up, USB down). Each pin then owns holes f-i of its row
(GPIO 4-7 at rows 4-7, GPIO 8 at row 12, GPIO 9 at row 15, GND at row 22), and the other pin row
hangs off the board's right edge, clear of the rails. The laptop lays the circuit out around that:
3V3 and GND wires cross empty rows 1 and 22 to the left rails, the laptop tries every way to hand out
the six GPIO pins and keeps the one with the fewest wire crossings, then the shortest wires (re-run
after every layout move). Every measuring wire runs from hole f of the pin's row into hole e of its
node, so it never crosses a part. The resistor test socket sits below the circuit.

The dashboard and the sim draw the bench's real breadboard: upright, rows 1-60 from the top, the
a-e half with its + and - rails on the left, the f-j half and its rails on the right. "4c" means
row 4, hole c. The laptop only uses the a-e half and the left rails. Each node owns one tinted row;
any free hole in it works.

**How the circuit is spread out.** Node rows use the whole length of the board, not just the
ESP32's rows: 6 rows apart when every resistor joins neighbouring nodes, 4 apart when some resistor
skips over a node (that one is then 8 holes long). The first node is at row 6 (a row or two lower if
that would land on a power-wire row); if you move the ESP32 down, the nodes stay beside the ADC pins
instead of being forced below it. Only when the nodes cannot fit any other way does the spacing shrink.
A resistor between two nodes lies along one hole letter between their rows, and the laptop picks
letters so resistors never touch and an empty letter is left between neighbours when it can. A
resistor passing over another node's row uses up that row's hole in its letter. Hole e of a node's row
is kept for its measuring wire, and hole a for a resistor to a rail. A resistor to a rail goes from
the free hole nearest the rail a few rows down the rail, so it is long enough to look right.
The laptop adds a jumper wire only in two cases: a node with more legs than holes gets a second
row beside it, and a resistor that would be longer than 8 holes gets a "helper" row 4 rows from
its far end, joined to its node by a jumper. Every jumper is placed in step 1, with the power wires.
The test socket goes below the circuit (or above it when there is no room).

**Zoom and moving things.** Above the picture: *Follow step* (on by default) zooms to the rows
that matter right now: the ESP32 and its wires in step 1, the test socket while a resistor waits to
be checked, then the holes for that resistor. *Whole board*, *+* and *−* zoom by hand; the mouse
wheel zooms and dragging pans. *Edit layout* lets you drag the ESP32 or a node (by its label) to
another row. The laptop re-plans the wires and pins, keeps the build step, and refuses a move that
cannot work (a power-wire row, the socket row, two nodes in neighbouring rows, off the board),
saying why. The placement is saved in the circuit's wiring file (a placement saved by an older layout version is
dropped and re-planned).

## Resistor test socket

Wrong resistor values are the most common breadboard mistake, and often the hardest for the board
checks to see (no current flows through a new part yet, or a smaller resistor in parallel hides
it). So every resistor is measured before it goes on the board.

Wiring (part of step 1): a 10k reference resistor from the + rail into the socket row, and a
spare ESP32 ADC1 pin into the socket row (the dashboard shows both). The resistor under test goes
from the socket row to the − rail. The designer's "Use the resistor test socket" switch turns it
off: then no 10k is reserved and the pin can measure the circuit instead. The dashboard's "Resistor check" card has the same switch for the middle of a build: it leaves the measuring wires and the build step as they are, and only the socket and its 10k go away. The pin reads V = 3.3 × R / (4.7k + R), so R = 4.7k × V / (3.3 − V);
the laptop matches it to the nearest kit value (100 Ω to 100k) and compares it with the part the
current step needs:

- "✓ That's a 1k: right for R3. Now place it on the board", or
- "✗ That's a 10k (brown-black-orange, or brown-black-black-red on a 5-band). R3 needs 1k
  (brown-black-red, or brown-black-black-brown on a 5-band)".

Colour bands are shown for a 4-band and for a 5-band resistor, since the kit has both.

A value confirmed in the socket counts as checked: those mistakes drop out of the fault matching,
and the dashboard colours the part green once nothing else about it is in doubt. In the
breadboard sim, the "Resistor test socket" panel drops a resistor in or out with one click.

The ESP32's ADC is least accurate close to 0 V and close to 3.3 V, which is why the reference is
4.7k (kit values land between 0.07 V and 3.15 V). Check the socket against a multimeter during
calibration, especially 100 Ω and 100k.

## Impedance sensing

Voltage alone cannot see a wrong resistor while no current flows through it: a 100 ohm and a 4.7k
from the supply to an otherwise empty node both read 3.3 V. So the ESP32 also reads every pin with
its internal pull-up and then pull-down (about 45k) switched on. How far the reading shifts shows
how hard the circuit drives that node, its Thevenin resistance:

    pull-down reading = V * Rp / (Rp + R)        pull-up reading = V + (3.3 - V) * R / (R + Rp)

The laptop gets every node's resistance from the same nodal matrix that gives the voltages (the
diagonal of its inverse), predicts both shifts for the correct circuit and for every simulated
mistake, and compares them with the measured ones (`solve_z` in `circuit.py`, `pull_shifts` in
`adc.py`). A floating pin is the extreme case: it swings from rail to rail.

The pull resistors are not precise, so the laptop measures them with the test socket: the empty
socket (3.3 V behind a known 10k) gives the pull-down, a confirmed kit resistor in it gives the
pull-up. The values are kept in `laptop/data/pulls.json`.

That file holds one pull model per device, keyed by the `device` name in the readings: `esp32s3`
is the real board, `esp32-sim` the fake ESP32 and `breadboard-sim` the simulator page. The
simulators have plain 45k pulls, so each device is checked with its own model and a simulator
session never changes the real board's calibration. The model in use is always the one of the
device whose readings are arriving.

**Calibrating an ESP32.** Those formulas treat each pull as a resistor, and on a real S3 that is
only roughly true. Measured on our board, the pull-up pushes about 70 uA at any pin voltage up to
1.5 V; a 45k resistor would push 67 uA at 0.3 V but only 40 uA at 1.5 V. So a node behind 20k rose
a third more than predicted and the check raised a false alarm. The fix is to describe each pull
by a curve instead of one number: the current it pushes (or sinks) at each pin voltage.

The "Calibrate this ESP32" button in the dashboard's Resistor check card measures that curve. You
put resistors you know in the test socket one at a time (at least three values, plus the empty
socket once). With resistor x in the socket the pin sits at a known voltage behind a known
resistance (x parallel to the 10k reference), so the pull's current is simply the shift in the
reading divided by that resistance. Each resistor gives one point on the pull-up curve and one on
the pull-down curve; the laptop joins the points with straight lines and, for any node, finds the
pin voltage where the node and the pull balance (`Pulls` and `pull_curves` in `adc.py`). It is
done once per ESP32, not per circuit, and saved under the device's name in
`laptop/data/pulls.json`. Calibrate again after swapping in a different ESP32. Without a
calibration everything works as before, with the one-number estimate above. The laptop publishes
the state of the current device on `benchcheck/calibration`.

In the fuzz test this raises the share of planted mistakes that are caught and named from 54% to
76%. Its limit: a 45k pull cannot resolve small resistances, so a part in parallel with a much
smaller one (1k next to 100 ohm) stays hidden; that needs a current sensor.

## Designing a circuit

Open http://localhost:8250/designer.html (the laptop node must be running).

- **Resistor:** pick a value, click the dot for one end, then the other.
- **Wire:** click two dots to join them. Any dot along a wire joins it (a T-junction).
- **Ground:** click a dot to connect it to GND. The ESP32's 3.3 V supply is fixed on the left.
- **Erase**, **Undo** (Ctrl+Z) and **Clear** do what they say.
- **Build order:** one part per step, in the order listed. With "Always use the smartest build
  order" on (the default), the laptop searches for the order that lets each step check the most
  (no steps that check nothing, fewest mistakes hidden at their own step) and applies it when it
  beats the current one, saying what improved. Pressing an arrow switches to your own order.
- **Crowded nodes:** a node with more connections than a column has holes gets a second column
  next to it, joined by a jumper wire that is plugged in at step 1 with the probe wires.

While you draw, the laptop plans the design in the background and the page shows the voltage at
every node, the chosen test points, whether it fits the breadboard, which parts are only checked
one step late, and any mistakes the test points can never see.

**Use this circuit** sends it to the laptop: it saves `circuits/<name>.cir` (plus the drawing in
`<name>.design.json`), re-plans it, and restarts the build at step 1. The dashboard and the
breadboard sim switch over by themselves. **Load the laptop's circuit** brings the current
circuit back into the designer to edit it.

To start the laptop with a saved design later: `bench_server.py ../circuits/<name>.cir`.

## Running on the real board (ESP32-S3-DevKitC-1)

The firmware is an ESP-IDF 6.0 project in `esp32/bench_node`, built on the EE 250L Lab 4 starter
code (Wi-Fi, onboard RGB LED, BOOT button). It reads GPIO 4-10 (ADC1) five times a second and sends
raw counts, factory-calibrated millivolts, and a pull-up / pull-down reading per pin that tells the
laptop for certain whether the pin is connected to anything; the laptop decides which pin is which. The BOOT
button is the Test button; the onboard LED shows the result (blue = connecting, off = ready,
white = checking, then green / red / yellow).

1. **Network.** Put the laptop and the ESP32 on the same 2.4 GHz WPA2 network (a phone hotspot;
   eduroam does not work). Find the laptop's IP with `ipconfig getifaddr en0`.
2. **Broker.** Start Mosquitto so the board can reach it (port 1884 here, because a UTM VM holds
   1883 on this Mac): `mosquitto -c mosquitto-1884.conf`. Allow incoming connections for
   mosquitto if macOS asks.
3. **Firmware.** Copy `esp32/bench_node/main/credentials.example.h` to `credentials.h` in the same
   folder (git ignores it) and fill in the Wi-Fi name, password, and `mqtt://<laptop IP>:1884`, then:
   ```bash
   . ~/.espressif/v6.0.2/esp-idf/export.sh
   cd esp32/bench_node
   idf.py build flash monitor
   ```
   The log should end with "connected to the broker" and "ready: press BOOT". If the LED stays
   dark, check `LED_GPIO` in `main/led.h` (48 on board v1.0, 38 on v1.1 and later).
4. **Laptop.** `bench_server.py ../circuits/demo.cir --port 1884`, then open the dashboard.
   The dashboard's breadboard picture shows exactly where every wire goes, including which GPIO
   is which probe and the resistor test socket.

ADC limits that the laptop already accounts for: the S3 reads 0-3.1 V at 12 dB attenuation, so a
3.3 V node is expected to read 3.1 V, and the socket uses a 10k reference so every kit value stays
below that ceiling. Before trusting results, do the ADC test (plan task 0.5): compare a few
readings with a multimeter. If the factory calibration is off, put measured points in
`laptop/calibration.json` (raw count, volts); the laptop then uses those instead of the
millivolts the board sends.

## Testing

`tests/fuzz_circuits.py` generates hundreds of circuits (ladders, series chains, bridges,
parallel parts, random networks, shuffled build orders, and hand-picked edge cases), then for each
one plans it, checks the breadboard layout is the same circuit and tidy (no parts overlapping or lying
over holes in use, nothing on a power-wire or socket row, resistors 4-9 holes long), builds it correctly (every step
must pass), and plants every simulated mistake at every step:

```bash
.venv/bin/python tests/fuzz_circuits.py -n 400
```

Each planted mistake must either fail with the true mistake in the "reads the same" group, read as
unstable (a floating test point), or pass only if the planner had flagged it as invisible at that
step. Anything else is reported as a problem. Last run: 211 circuits, 37,000 planted mistakes,
0 problems. About half of all simulated mistakes cannot be seen at the step they are made (no
current flows through the new part yet); the dashboard says so on a pass, and that part is the
first suspect at the next step.

`tests/test_real_board.py` is a small regression test from real data (`tests/data/`): ten
readings from the real board at step 8 of the 9-resistor circuit, and that board's calibration.
It checks that the step passes with the calibration and is a false alarm without it, and that the
finished circuit names a missing R3.

```bash
.venv/bin/python tests/test_real_board.py
```

## Circuit files

One part per line, in build order. Resistors (`R...`) and DC sources (`V...`) only. The first
part must be the supply, `V1 vcc gnd 3.3` (the ESP32's 3.3 V pin). A resistor needs a value above
zero and two different nodes; the laptop refuses a file that breaks these rules.

```
V1  vcc  gnd  3.3    # Connect the ESP32's 3.3 V and GND pins to the rails
R1  vcc  a    1k     # R1 (1k) from the 3.3 V rail to row A
```

The laptop node plans the circuit and writes its wiring file each time it loads one.
`laptop/plan_circuit.py <file>` only prints the plan and writes nothing.

## Calibration

Until the ADC test is done, the laptop assumes an ideal ADC (0 to 4095 counts = 0 to 3.3 V).
To use real measurements, create `laptop/calibration.json`:

```json
{"points": [[0, 0.0], [512, 0.52], [2048, 1.71], [4095, 3.17]]}
```

Each pair is (ESP32 raw reading, multimeter volts).

## External libraries

- paho-mqtt (MQTT client)
- numpy (solving the circuit equations)
- Mosquitto (MQTT broker)
