"""Laptop node: receives readings over MQTT, checks each build step, sends the result back.

    python bench_server.py                        # the circuit you used last (the demo the first time)
    python bench_server.py ../circuits/demo.cir   # a specific circuit

It plans the circuit itself at start-up (test points and breadboard layout) and writes the
wiring file next to it. The designer page can hand it a new circuit at any time.
"""

import argparse
import hashlib
import json
import re
import threading
import time
import traceback
from collections import deque
from pathlib import Path

import paho.mqtt.client as mqtt

from benchcheck.adc import CAL_SAMPLES, SUPPLY_VOLTS, Pulls, pull_curves
from benchcheck.calibration import Calibration
from benchcheck.checker import Checker, compose_message
from benchcheck.circuit import Circuit, format_ohms
from benchcheck.layout import LAYOUT_VERSION
from benchcheck.planner import DEFAULT_TOLERANCE, plan, report
from benchcheck.socket import measure, verdict
from benchcheck.store import Store

T_READINGS = "benchcheck/readings"
T_REQUEST = "benchcheck/check_request"
T_RESULT = "benchcheck/check_result"
T_STATUS = "benchcheck/status"
T_CONTROL = "benchcheck/control"
T_CIRCUIT = "benchcheck/circuit"
T_LIVE = "benchcheck/live"
T_DESIGN_PREVIEW = "benchcheck/design/preview"
T_DESIGN_USE = "benchcheck/design/use"
T_DESIGN_PLAN = "benchcheck/design/plan"
T_SOCKET = "benchcheck/socket"
T_LAYOUT_RESULT = "benchcheck/layout_result"
T_CALIBRATION = "benchcheck/calibration"

WINDOW = 10  # readings (about 2 s at 5 per second) used for each check
STALE_SECONDS = 1.5  # a check needs a reading newer than this (the ESP32 sends about 5 a second)
SOCKET_WINDOW = 4  # readings for the resistor test socket (under a second)
FLOAT_UP_MV = 2900    # with the pull-up on, a floating pin reads at the ADC ceiling...
FLOAT_DOWN_MV = 200   # ...and with the pull-down on, at 0. A pin the circuit drives does not do both.

HERE = Path(__file__).parent


class BenchServer:
    def __init__(self, circuit_path, host, port):
        self.lock = threading.RLock()  # the design thread swaps circuits while MQTT messages arrive
        self.load(Path(circuit_path))
        self.progress_path = HERE / "data" / "progress.json"
        self.resume()
        self.calibration = Calibration.load(HERE / "calibration.json")
        (HERE / "data").mkdir(exist_ok=True)
        self.store = Store(HERE / "data" / "bench.db")
        self.window = deque(maxlen=WINDOW)
        self.pull_window = deque(maxlen=WINDOW)   # per reading: {node: (rise with pull-up, fall with pull-down)}
        self.last_reading = 0.0   # when the last reading arrived (time.monotonic)
        # The ESP32's internal pull resistors, measured with the test socket's known resistors.
        # One model per device: the simulators have plain 45k pulls, a real board does not.
        self.pulls_path = HERE / "data" / "pulls.json"
        self.pulls = {}
        self.device = None   # the device whose readings are arriving
        if self.pulls_path.exists():
            saved = json.loads(self.pulls_path.read_text())
            if "up" in saved or "ohms" in saved:   # the file from before there was one model per device
                saved = {"esp32s3": saved}
            self.pulls = {d: Pulls.from_json(m) for d, m in saved.items()}
        self.cal = None   # during a calibration run: {resistor ohms, or None for empty: [(plain, up, down), ...]}
        self.preview_seq = 0
        self.float_aware = False

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="benchcheck-laptop")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(host, port)

    # ---------- circuit ----------
    def load(self, path, socket=None, place=None, keep_step=False, keep_wires=None):
        """Read and plan a circuit file, and start its build at step 1 (or stay at the current step
        when only the breadboard placement changed).

        keep_wires: without the socket, leave the measuring wires where they are with it (see plan).
        None keeps what the wiring file says, as long as the socket setting does not change."""
        wiring_path = path.with_suffix(".wiring.json")
        tolerance, saved_socket, saved_place, saved_keep = DEFAULT_TOLERANCE, True, None, False
        if wiring_path.exists():
            saved = json.loads(wiring_path.read_text())
            tolerance, saved_socket = saved.get("tolerance", tolerance), saved.get("socket", True)
            saved_keep = bool(saved.get("keep_wires"))
            if saved.get("layout_version") == LAYOUT_VERSION:  # an older layout pinned every node: re-plan
                saved_place = saved.get("place")
        if keep_wires is None:
            keep_wires = saved_keep and socket in (None, saved_socket)
        socket = saved_socket if socket is None else bool(socket)
        keep_wires = bool(keep_wires) and not socket
        circuit = Circuit.from_file(path)
        p = plan(circuit, tolerance, socket, place if place is not None else saved_place, keep_wires)
        if p["layout_error"] and place is None and saved_place:
            p = plan(circuit, tolerance, socket, None, keep_wires)  # a saved placement that no longer fits the circuit
        if p["layout_error"]:
            raise ValueError(f"Cannot lay this circuit out on the breadboard: {p['layout_error']}")
        if not p["pins"]:
            raise ValueError("Nothing in this circuit can be measured")
        wiring_path.write_text(json.dumps({"circuit": path.name, "tolerance": tolerance, "pins": p["pins"],
                                           "socket": socket, "socket_pin": p["socket_pin"], "keep_wires": keep_wires,
                                           "place": p["layout"]["place"], "layout_version": LAYOUT_VERSION}, indent=2) + "\n")
        design_path = path.with_suffix(".design.json")
        with self.lock:
            self.path, self.circuit, self.plan = path, circuit, p
            self.design = json.loads(design_path.read_text()) if design_path.exists() else None
            self.pins, self.table, self.tolerance = p["pins"], p["table"], tolerance
            self.layout, self.placements = p["layout"], p["placements"]
            self.checker = Checker(self.table, self.pins, tolerance, p["status"])
            self.checker.part_holes = {n: tuple(h) for n, h in p["layout"]["parts"].items()}
            self.last_result = None   # the last check that did not pass, kept so the socket can update it
            self.socket = p["layout"]["socket"]
            self.socket_window = deque(maxlen=SOCKET_WINDOW)
            self.socket_last = None
            if not keep_step or not hasattr(self, "step") or self.step > circuit.num_steps + 1:
                self.step = 1
            if hasattr(self, "window"):
                self.window.clear()
                self.pull_window.clear()
        print(f"\nLoaded {path.name}: {circuit.num_steps} steps, test points "
              + ", ".join(f"{n} on GPIO {pin}" for pin, n in self.pins.items()))

    # ---------- build progress ----------
    def circuit_hash(self):
        return hashlib.sha1(self.path.read_bytes()).hexdigest()

    def save_progress(self):
        self.progress_path.write_text(json.dumps({"circuit": self.path.name, "hash": self.circuit_hash(), "step": self.step,
                                                  "confirmed": sorted(self.checker.value_confirmed)}))

    def resume(self):
        """At start-up, carry on where the last run stopped, if it was on this same circuit."""
        if not self.progress_path.exists():
            return
        saved = json.loads(self.progress_path.read_text())
        if saved["circuit"] == self.path.name and saved["hash"] == self.circuit_hash() \
                and 1 <= saved["step"] <= self.circuit.num_steps + 1:
            self.step = saved["step"]
            self.checker.value_confirmed |= set(saved["confirmed"])
            print(f"Resuming {self.path.name} at step {self.step}. Restart in the dashboard begins again.")

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"Connected to broker ({reason_code}). Waiting for the ESP32...")
        client.subscribe([(T_READINGS, 0), (T_REQUEST, 1), (T_CONTROL, 1),
                          (T_DESIGN_PREVIEW, 0), (T_DESIGN_USE, 1)])
        self.publish_circuit()
        self.publish_status()
        self.publish_calibration()

    def on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            print(f"Ignored a message on {msg.topic} that is not JSON")
            return
        try:
            self.handle(msg.topic, data)
        except Exception:  # one bad message must not take the laptop node down
            print(f"Error while handling {msg.topic}:")
            traceback.print_exc()

    def handle(self, topic, data):
        if topic == T_READINGS:
            self.on_reading(data)
        elif topic == T_REQUEST:
            self.on_check_request(data)
        elif topic == T_CONTROL and data.get("cmd") == "set_step":
            self.set_step(int(data["step"]))
        elif topic == T_CONTROL and data.get("cmd") == "set_layout":
            self.set_layout(data.get("place") or {})
        elif topic == T_CONTROL and data.get("cmd") == "calibrate":
            self.calibrate(data.get("action"))
        elif topic == T_CONTROL and data.get("cmd") == "set_socket":
            self.set_socket(bool(data.get("on")))
        elif topic in (T_DESIGN_PREVIEW, T_DESIGN_USE):
            # Planning a big design takes up to a second; do it off the MQTT thread.
            self.preview_seq += 1
            threading.Thread(target=self.on_design, args=(topic == T_DESIGN_USE, data, self.preview_seq),
                             daemon=True).start()

    # ---------- designer ----------
    def on_design(self, use, data, seq):
        req = data.get("req")
        try:
            circuit = Circuit.from_text(data.get("cir", ""), "design")
            if not use:
                r = report(circuit, plan(circuit, self.tolerance, data.get("socket", True)))
                if seq == self.preview_seq:  # a newer edit arrived meanwhile: skip the stale answer
                    self.client.publish(T_DESIGN_PLAN, json.dumps({"req": req, **r}))
                return
        except Exception as e:  # this thread must answer: the designer waits for it
            if not isinstance(e, ValueError):
                traceback.print_exc()
            self.client.publish(T_DESIGN_PLAN, json.dumps({"req": req, "ok": False, "error": str(e)}))
            return

        name = re.sub(r"[^a-z0-9_-]+", "-", str(data.get("name", "")).lower()).strip("-") or "design"
        path = self.path.parent / f"{name}.cir"
        old = path.read_text() if path.exists() else None
        path.write_text(data["cir"])
        try:
            self.load(path, data.get("socket", True))
        except Exception as e:
            if not isinstance(e, ValueError):
                traceback.print_exc()
            if old is None:
                path.unlink()
            else:
                path.write_text(old)
            self.client.publish(T_DESIGN_PLAN, json.dumps({"req": req, "ok": False, "used": False, "error": str(e)}))
            return
        if data.get("design") is not None:
            path.with_suffix(".design.json").write_text(json.dumps(data["design"], indent=1) + "\n")
            self.design = data["design"]
        self.client.publish(T_DESIGN_PLAN, json.dumps({"req": req, "ok": True, "used": True, "name": name,
                                                       **report(self.circuit, self.plan)}))
        self.publish_circuit()
        self.publish_status()

    # ---------- readings and checks ----------
    def to_volts(self, data, pin):
        """Our own calibration if we measured one; else the ESP32's factory-calibrated millivolts."""
        mv = data.get("mv") or {}
        if not self.calibration.custom and pin in mv:
            return mv[pin] / 1000
        return self.calibration.to_volts(data["raw"][pin])

    def on_reading(self, data):
        raw = data.get("raw", {})
        with self.lock:
            self.last_reading = time.monotonic()
            device = data.get("device", "esp32s3")
            if device != self.device:
                self.device = device
                self.publish_calibration()
            if self.socket and self.socket["pin"] in raw:
                m = self.on_socket(self.to_volts(data, self.socket["pin"]))
                self.calibrate_pulls(data, m)
                self.collect_calibration(data, m)
            if not all(p in raw for p in self.pins):
                return
            # A pin that swings rail to rail with the ESP32's pull-up and pull-down is not connected
            # to anything: its plain reading is stale charge, so it becomes None ("floating").
            up, dn = data.get("up"), data.get("dn")
            self.float_aware = bool(up) and bool(dn)
            volts = {}
            for pin, node in self.pins.items():
                floating = (self.float_aware and up.get(pin, 0) >= FLOAT_UP_MV and dn.get(pin, 9999) <= FLOAT_DOWN_MV)
                volts[node] = None if floating else self.to_volts(data, pin)
            self.window.append(volts)
            mv = data.get("mv") or {}
            self.pull_window.append({
                node: None if volts[node] is None or not self.float_aware or pin not in mv
                else ((up[pin] - mv[pin]) / 1000, (mv[pin] - dn[pin]) / 1000)
                for pin, node in self.pins.items()})
            self.publish_live(volts)
        self.store.add_reading(data.get("seq"), raw, volts)

    def mean_pulls(self):
        """Average pull-up rise and pull-down fall per node over the window (None if not measured)."""
        out = {}
        for node in self.pins.values():
            got = [r[node] for r in self.pull_window if r.get(node) is not None]
            out[node] = None if len(got) < len(self.pull_window) / 2 or not got else (
                sum(g[0] for g in got) / len(got), sum(g[1] for g in got) / len(got))
        return out

    def calibrate_pulls(self, data, m):
        """Measure the ESP32's pull resistors with the socket, whose resistors are known.

        Empty socket: the pin sits at 3.3 V behind the reference, so the pull-down reading gives the
        pull-down resistance. With a kit resistor in it, the node is a known divider and the pull-up
        reading gives the pull-up resistance.
        """
        pin, ref = self.socket["pin"], self.socket["reference_ohms"]
        up, dn = (data.get("up") or {}).get(pin), (data.get("dn") or {}).get(pin)
        model = self.pull_model()
        if up is None or dn is None or m is None or model.calibrated:
            return   # a calibration run measured the full curves: this one-number estimate would be a step back
        found = None
        if m["state"] == "empty" and 500 < dn < 3000:
            v = dn / 1000
            found = (1, ref * v / (SUPPLY_VOLTS - v))
        elif m["state"] == "value" and m.get("kit") and up < 3000:
            x = m["nearest"]
            vth, rth, v = SUPPLY_VOLTS * x / (ref + x), ref * x / (ref + x), up / 1000
            if v - vth > 0.03:
                found = (0, rth * (SUPPLY_VOLTS - v) / (v - vth))
        if not found or not 15e3 < found[1] < 150e3:
            return
        i, ohms = found
        pull_ohms, known = list(model.ohms), model.known
        old = pull_ohms[i]
        pull_ohms[i] = ohms if not known[i] else 0.9 * old + 0.1 * ohms
        if not known[i]:
            known[i] = True
            if not known[1 - i]:
                pull_ohms[1 - i] = ohms     # best guess for the other one until it is measured
            print(f"  Measured the pull-{'up' if i == 0 else 'down'} resistor of {self.device}: {ohms / 1000:.1f} k")
        self.pulls[self.device] = Pulls.ohmic(*pull_ohms, known)
        if abs(pull_ohms[i] - old) > 0.02 * old:
            self.save_pulls()

    def pull_model(self):
        """The pulls of the device whose readings are arriving (a plain 45k until measured)."""
        return self.pulls.get(self.device) or Pulls.ohmic()

    def save_pulls(self):
        self.pulls_path.write_text(json.dumps({d: m.to_json() for d, m in self.pulls.items()}))

    # ---------- calibration run: the pulls' real curves ----------
    def calibrate(self, action):
        """start: collect socket readings per resistor. save: build the curves from them. cancel: drop them."""
        with self.lock:
            if action == "start":
                self.cal = {} if self.socket else None
            elif action == "cancel":
                self.cal = None
            elif action == "save" and self.cal is not None:
                up, down = pull_curves(self.calibration_samples(), self.socket["reference_ohms"])
                if len(up) >= 3 and len(down) >= 3:
                    self.pulls[self.device] = Pulls(up, down, True, time.strftime("%Y-%m-%d %H:%M"))
                    self.save_pulls()
                    self.cal = None
                    print(f"  Calibrated the pulls of {self.device}. Pull-up: "
                          + ", ".join(f"{i * 1e6:.0f} uA at {v:.2f} V" for v, i in up) + ". Pull-down: "
                          + ", ".join(f"{i * 1e6:.0f} uA at {v:.2f} V" for v, i in down))
        self.publish_calibration()

    def calibration_samples(self):
        """Median (plain, up, down) per resistor that has enough readings."""
        mid = lambda xs: sorted(xs)[len(xs) // 2]
        return {x: tuple(mid([r[k] for r in rows]) for k in range(3))
                for x, rows in self.cal.items() if len(rows) >= CAL_SAMPLES}

    def collect_calibration(self, data, m):
        """During a run, file each steady socket reading under the resistor that is in the socket."""
        if self.cal is None or m is None or m["state"] == "settling" or (m["state"] == "value" and not m.get("kit")):
            return
        pin = self.socket["pin"]
        mv, up, dn = ((data.get(k) or {}).get(pin) for k in ("mv", "up", "dn"))
        if None in (mv, up, dn):
            return
        if len(self.socket_window) < self.socket_window.maxlen:
            return   # just after a swap: wait until every reading in the window is of this resistor
        rows = self.cal.setdefault(m["nearest"] if m["state"] == "value" else None, [])
        if len(rows) < 2 * CAL_SAMPLES:
            rows.append((mv / 1000, up / 1000, dn / 1000))
            self.publish_calibration(m)

    def publish_calibration(self, m=None):
        with self.lock:
            if not self.socket:
                self.cal = None   # the socket was switched off in the middle of a run
            model = self.pull_model()
            msg = {"device": self.device, "active": self.cal is not None, "calibrated": model.calibrated,
                   "when": model.when, "socket": bool(self.socket)}
            if self.cal is not None:
                name = lambda x: "empty socket" if x is None else format_ohms(x)
                done = self.calibration_samples()
                up, down = pull_curves(done, self.socket["reference_ohms"])
                msg.update(done=[name(x) for x in sorted(done, key=lambda x: x or 0)],
                           ready=len(up) >= 3 and len(down) >= 3,
                           current=None if m is None else name(m["nearest"] if m["state"] == "value" else None))
        self.client.publish(T_CALIBRATION, json.dumps(msg), qos=1, retain=True)

    def on_socket(self, volts):
        """Read the resistor test socket and judge it against the part this step needs."""
        self.socket_window.append(volts)
        m = measure(list(self.socket_window), self.socket["reference_ohms"])
        part, suspect = self.socket_target(m)
        v = verdict(m, part, bool(suspect))
        msg = {"step": self.step, "part": part.name if part else None, **m, **v}
        key = (msg["result"], msg.get("nearest"), msg["part"])
        if v["result"] == "ok" and part.name not in self.checker.value_confirmed:
            self.checker.value_confirmed.add(part.name)
            print(f"  Socket: {part.name} measured as {m['label']}, value confirmed")
            self.publish_status()
        if suspect:
            self.update_suspects(part, m, v["result"] == "ok")
        if key != self.socket_last or self.socket_window.maxlen == len(self.socket_window):
            self.client.publish(T_SOCKET, json.dumps(msg), qos=0)
            self.socket_last = key
        return m

    def socket_target(self, m):
        """The part a resistor in the socket is judged against, and whether it is a suspect.

        Normally that is the part of the current step. While the last check left value suspects,
        the list is walked from the top and the first suspect the reading says something about
        wins: the reading equals its correct value (the part is fine) or the wrong value in its
        label (found it). Anything else is judged against the step's part as usual.
        """
        part = self.circuit.parts[self.step - 1] if self.step <= self.circuit.num_steps else None
        part = part if part is not None and part.kind == "R" else None
        if m["state"] == "value" and m.get("kit") and self.last_result:
            for s in self.last_result["suspects"]:
                if s["kind"] != "value":
                    continue
                named = next((p for p in self.circuit.parts if p.name == s["part"] and p.kind == "R"), None)
                if named and m["nearest"] in (named.value, s["wrong_ohms"]):
                    return named, s
        return part, None

    def update_suspects(self, part, m, fine):
        """Publish the kept result again, with the suspect the socket just settled marked."""
        r = self.last_result
        if fine:
            todo = [s for s in r["suspects"] if s["part"] == part.name and s["kind"] == "value" and not s.get("ruled_out")]
            for s in todo:
                s["ruled_out"] = True
        else:
            todo = [s for s in r["suspects"] if s["part"] == part.name and s["kind"] == "value"
                    and s["wrong_ohms"] == m["nearest"] and not s.get("found")]
            for s in todo:
                s["found"] = True
                s.pop("ruled_out", None)   # an earlier reading may have cleared it
            if todo:
                r["problem"]["headline"] = (f"Found it: that resistor is {format_ohms(m['nearest'])}, "
                                            f"{part.name} needs {format_ohms(part.value)}")
        if not todo:
            return
        r["updated"] = True
        r["message"] = compose_message(r)
        self.client.publish(T_RESULT, json.dumps(r), qos=1)

    def publish_live(self, volts):
        """Latest voltage at every test point, compared with the current step (for the dashboard)."""
        step = min(self.step, self.circuit.num_steps)
        expected = self.table[step]["correct"]
        points = {}
        for pin, node in self.pins.items():
            exp = expected[node]
            v = volts[node]
            if exp is None:
                ok = (v is None) if self.float_aware else None       # should still be unconnected
            else:
                ok = v is not None and abs(v - exp) <= self.tolerance
            points[node] = {"pin": pin, "volts": None if v is None else round(v, 3), "floating": v is None,
                            "expected": None if exp is None else round(exp, 3), "ok": ok}
        self.client.publish(T_LIVE, json.dumps({"step": step, "points": points}), qos=0)

    def publish_circuit(self):
        """The whole circuit, wiring and expected voltages, so the dashboard can draw it."""
        with self.lock:
            parts = [{"step": i + 1, "name": p.name, "kind": p.kind, "a": p.a, "b": p.b,
                      "value": format_ohms(p.value) if p.kind == "R" else f"{p.value:g} V",
                      "instruction": p.instruction}
                     for i, p in enumerate(self.circuit.parts)]
            expected = {str(step): {n: None if v is None else round(v, 3) for n, v in row["correct"].items()}
                        for step, row in self.table.items()}
            msg = {"name": self.path.stem, "parts": parts, "nodes": self.circuit.nodes, "pins": self.pins,
                   "tolerance": self.tolerance, "expected": expected, "layout": self.layout,
                   "placements": self.placements, "design": self.design,
                   # after a pass at step N: what each placed part has been confirmed to be
                   "part_status": {str(k): v for k, v in self.plan["status"].items()}}
        self.client.publish(T_CIRCUIT, json.dumps(msg), qos=1, retain=True)

    def on_check_request(self, data):
        with self.lock:
            complete = self.step > self.circuit.num_steps
            # If the ESP32 has stopped sending, the window holds old readings: do not check those.
            stale = time.monotonic() - self.last_reading > STALE_SECONDS
            if stale:
                self.window.clear()
                self.pull_window.clear()
            # On a finished circuit Test checks the whole board again: a part pulled out or knocked
            # loose afterwards shows up like any other mistake, with suspects.
            result = self.checker.check(min(self.step, self.circuit.num_steps), list(self.window),
                                        self.float_aware, self.mean_pulls(), self.pull_model())
            if stale:
                msg = "No fresh readings from the ESP32: check its power and Wi-Fi"
                result["message"] = result["problem"]["headline"] = msg
            # The ESP32 only reads a result that arrives in one 4 kB piece: leave out what repeats other fields.
            result.pop("same_reading", None)
            result.pop("likely_faults")
            result["seq"] = data.get("seq")
            if result["result"] == "pass":
                if complete:
                    result["message"] = "Circuit already complete. " + result["message"]
                else:
                    self.step += 1
            result["next_step"] = self.step
            self.last_result = None if result["result"] == "pass" else json.loads(json.dumps(result))
            self.print_result(result)
            self.store.add_check(result)
            self.client.publish(T_RESULT, json.dumps(result), qos=1)
            if not complete:
                self.publish_status()

    def set_layout(self, place):
        """Move the ESP32 or a node on the breadboard: re-plan the layout, keep the build step."""
        step = self.step
        confirmed = set(self.checker.value_confirmed)   # socket checks stay valid: the parts did not change
        try:
            self.load(self.path, place=place, keep_step=True)
            self.checker.value_confirmed |= confirmed
        except ValueError as e:
            msg = str(e).replace("Cannot lay this circuit out on the breadboard: ", "")
            print(f"Layout move refused: {msg}")
            self.client.publish(T_LAYOUT_RESULT, json.dumps({"ok": False, "error": msg}))
            return
        print(f"Breadboard layout moved (still at step {step}): {self.layout['place']}")
        self.client.publish(T_LAYOUT_RESULT, json.dumps({"ok": True, "place": self.layout["place"]}))
        self.publish_circuit()
        self.publish_status()

    def set_socket(self, on):
        """Turn the resistor test socket on or off from the dashboard: re-plan, keep the build step."""
        pins, confirmed = dict(self.pins), set(self.checker.value_confirmed)
        try:
            self.load(self.path, socket=on, keep_step=True, keep_wires=not on)
        except ValueError as e:
            print(f"Could not turn the test socket {'on' if on else 'off'}: {e}")
            self.client.publish(T_LAYOUT_RESULT, json.dumps({"ok": False, "error": str(e)}))
        else:
            if self.pins == pins:
                self.checker.value_confirmed |= confirmed
            else:  # the measuring wires moved (the socket needs a pin that was measuring): build again
                self.step = 1
            print(f"Resistor test socket turned {'on' if on else 'off'} (at step {self.step})")
        self.publish_circuit()
        self.publish_status()

    def set_step(self, step):
        with self.lock:
            self.step = max(1, min(step, self.circuit.num_steps + 1))
            self.last_result = None
            if self.step == 1:  # starting over: socket checks were for the old board
                self.checker.value_confirmed.clear()
        print(f"Step set by hand to {self.step}")
        self.publish_status()

    def publish_status(self):
        with self.lock:
            complete = self.step > self.circuit.num_steps
            instruction = "" if complete else self.circuit.parts[self.step - 1].instruction
            status = {"circuit": self.path.stem, "step": self.step, "total_steps": self.circuit.num_steps,
                      "instruction": instruction, "complete": complete,
                      "placement": "" if complete else self.placements[self.step - 1],
                      "value_checked": sorted(self.checker.value_confirmed)}
        self.client.publish(T_STATUS, json.dumps(status), qos=1, retain=True)
        self.save_progress()
        if complete:
            print("\nCircuit complete. Every step passed.")
        else:
            print(f"\nStep {status['step']}/{status['total_steps']}: {instruction}")
            print(f"  {status['placement']}")

    def print_result(self, r):
        print(f"  Check step {r['step']}: {r['result'].upper()} ({r['led']}): {r['message']}")
        for node, p in r["points"].items():
            exp = "  float" if p["expected"] is None else f"{p['expected']:6.3f}V"
            mark = {True: "ok", False: "WRONG", None: "not checked"}[p["ok"]]
            got = " float " if p["measured"] is None else f"{p['measured']:6.3f}V"
            print(f"    {node:<5} GPIO {p['pin']}: measured {got}  expected {exp}  {mark}")

    def run(self):
        self.client.loop_forever()


CIRCUITS = HERE.parent / "circuits"


def last_circuit():
    """The circuit file used most recently (its wiring file is rewritten on every load)."""
    used = sorted(CIRCUITS.glob("*.wiring.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for wiring in used:
        cir = wiring.with_name(wiring.name.replace(".wiring.json", ".cir"))
        if cir.exists():
            print(f"No circuit given: using {cir.name}, the one used last. Pick another in the designer.")
            return str(cir)
    print("No circuit given: starting with the demo. Draw your own in the designer.")
    return str(CIRCUITS / "demo.cir")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("circuit", nargs="?", help="circuit file; default: the one used last")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    args = ap.parse_args()
    try:
        BenchServer(args.circuit or last_circuit(), args.host, args.port).run()
    except ValueError as e:
        raise SystemExit(str(e))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
