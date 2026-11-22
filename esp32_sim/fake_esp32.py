"""Fake ESP32: pretends to be the bench node, so the laptop side can be built without hardware.

It simulates the breadboard you are building (with an optional mistake), and sends exactly what
the real ESP32 will send: raw ADC counts per pin, and a check request when you "press the button".

    python fake_esp32.py ../circuits/demo.cir

Commands:
    b        build the next part           u   remove the last part
    f        list mistakes you can plant   f N plant mistake N
    x        remove the planted mistake    c   press the button (ask for a check)
    s        show the board                q   quit
    --script "b c b c f 3 c x c"  runs commands automatically
"""

import argparse
import json
import random
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "laptop"))
from benchcheck.adc import pull_readings  # noqa: E402
from benchcheck.circuit import Circuit, solve_z  # noqa: E402
from benchcheck.adc import ADC_MAX_VOLTS  # noqa: E402
from benchcheck.faults import apply_fault, candidate_faults  # noqa: E402

T_READINGS = "benchcheck/readings"
T_REQUEST = "benchcheck/check_request"
T_RESULT = "benchcheck/check_result"
T_STATUS = "benchcheck/status"

PERIOD_S = 0.2  # 5 readings a second
NOISE_V = 0.01  # left after the median filter
LED_SYMBOL = {"green": "GREEN", "red": "RED", "yellow": "YELLOW"}


class FakeEsp32:
    def __init__(self, circuit_path, host, port):
        self.circuit = Circuit.from_file(circuit_path)
        self.pins = json.loads(Path(circuit_path).with_suffix(".wiring.json").read_text())["pins"]
        self.built = 0
        self.fault = None
        self.stale = {}
        self.seq = 0
        self.start = time.monotonic()
        self.got_result = threading.Event()
        self.lock = threading.Lock()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="benchcheck-esp32-sim")
        self.client.on_connect = lambda c, u, f, rc, p: c.subscribe([(T_RESULT, 1), (T_STATUS, 1)])
        self.client.on_message = self.on_message
        self.client.connect(host, port)
        self.client.loop_start()
        threading.Thread(target=self.publish_loop, daemon=True).start()

    def t_ms(self):
        return int((time.monotonic() - self.start) * 1000)

    def board_parts(self):
        parts = self.circuit.step_parts(self.built)
        return apply_fault(parts, self.fault) if self.fault else parts

    def read_adc(self):
        """What the ESP32 would measure on each pin right now."""
        with self.lock:
            volts, ohms = solve_z(self.board_parts(), self.circuit.nodes)
        raw, mv, up, dn = {}, {}, {}, {}
        for pin, node in self.pins.items():
            true = volts[node]
            # a floating pin holds a stale, steady value; only the pull test gives it away
            v = self.stale.setdefault(pin, random.uniform(0, ADC_MAX_VOLTS)) if true is None else true
            v = max(0.0, min(ADC_MAX_VOLTS, v + random.gauss(0, NOISE_V)))  # the S3 ADC reads 0-3.1 V at 12 dB
            raw[pin] = round(v / ADC_MAX_VOLTS * 4095)
            mv[pin] = round(v * 1000)
            pu, pd = pull_readings(true, ohms[node])   # the same pin with the pull-up, then pull-down, on
            up[pin], dn[pin] = round(pu * 1000), round(pd * 1000)
        return raw, mv, up, dn

    def publish_loop(self):
        while True:
            self.seq += 1
            raw, mv, up, dn = self.read_adc()
            msg = {"device": "esp32-sim", "seq": self.seq, "t_ms": self.t_ms(), "raw": raw, "mv": mv, "up": up, "dn": dn}
            self.client.publish(T_READINGS, json.dumps(msg), qos=0)
            time.sleep(PERIOD_S)

    def on_message(self, client, userdata, msg):
        data = json.loads(msg.payload)
        if msg.topic == T_RESULT:
            print(f"\n  LED: {LED_SYMBOL[data['led']]}  ({data['message']})")
            self.got_result.set()
        elif msg.topic == T_STATUS:
            if data["complete"]:
                print("\n  Laptop: circuit complete")
            else:
                print(f"\n  Laptop expects step {data['step']}/{data['total_steps']}: {data['instruction']}")

    def press_button(self):
        self.got_result.clear()
        msg = {"device": "esp32-sim", "seq": self.seq, "t_ms": self.t_ms()}
        self.client.publish(T_REQUEST, json.dumps(msg), qos=1)
        print("  Button pressed")

    def command(self, line):
        words = line.split()
        if not words:
            return True
        cmd = words[0]
        with self.lock:
            if cmd == "b":
                if self.built < self.circuit.num_steps:
                    self.built += 1
                    print(f"  Built part {self.built}: {self.circuit.parts[self.built - 1].instruction}")
                else:
                    print("  Every part is already on the board")
            elif cmd == "u":
                self.built = max(0, self.built - 1)
                self.fault = None
                print(f"  Removed the last part ({self.built} parts on the board, mistake cleared)")
            elif cmd == "f":
                faults = candidate_faults(self.circuit.step_parts(self.built))
                if len(words) == 1:
                    for i, f in enumerate(faults):
                        print(f"  {i:>3}  {f.label}")
                else:
                    self.fault = faults[int(words[1])]
                    print(f"  Planted mistake: {self.fault.label}")
            elif cmd == "x":
                self.fault = None
                print("  Mistake removed")
            elif cmd == "s":
                print(f"  {self.built} parts on the board; mistake: {self.fault.label if self.fault else 'none'}")
            elif cmd == "q":
                return False
        if cmd == "c":
            self.press_button()
        return True

    def run_script(self, script):
        tokens = script.split()
        i = 0
        time.sleep(1)
        while i < len(tokens):
            line = tokens[i]
            if tokens[i] == "f" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                line, i = f"f {tokens[i + 1]}", i + 1
            print(f"> {line}")
            self.command(line)
            if line == "c":
                self.got_result.wait(timeout=5)
            else:
                time.sleep(2.5)  # let a full window of new readings arrive
            i += 1
        time.sleep(0.5)

    def run_interactive(self):
        print(__doc__.split("Commands:")[1])
        try:
            while self.command(input("> ")):
                pass
        except (EOFError, KeyboardInterrupt):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("circuit")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--script", help='commands to run automatically, e.g. "b c b c"')
    args = ap.parse_args()
    esp = FakeEsp32(args.circuit, args.host, args.port)
    if args.script:
        esp.run_script(args.script)
    else:
        esp.run_interactive()
    esp.client.loop_stop()


if __name__ == "__main__":
    main()
