"""Regression test from real data: ten readings from the ESP32 at step 8 of the 9-resistor circuit
(tests/data/finn-test.cir), every part correct, and that board's real pull calibration.

    .venv/bin/python tests/test_real_board.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "laptop"))
from benchcheck.adc import Pulls, pull_readings  # noqa: E402
from benchcheck.checker import Checker  # noqa: E402
from benchcheck.circuit import Circuit  # noqa: E402
from benchcheck.planner import plan  # noqa: E402

TOL = 0.15
LOST = "R3 is missing or a leg is not making contact"

circuit = Circuit.from_file(HERE / "data" / "finn-test.cir")
p = plan(circuit, TOL, True)
pins, table = p["pins"], p["table"]
checker = Checker(table, pins, TOL, p["status"])
rows = [json.loads(line) for line in (HERE / "data" / "live8.jsonl").read_text().splitlines()]
real = Pulls.from_json(json.loads((HERE / "data" / "pulls.real.json").read_text()))
plain = Pulls.ohmic()

# what the laptop builds from the board's readings: volts per node, and the mean pull shifts
window = [{n: r["mv"][pin] / 1000 for pin, n in pins.items()} for r in rows]
pulls = {n: (sum((r["up"][pin] - r["mv"][pin]) / 1000 for r in rows) / len(rows),
             sum((r["mv"][pin] - r["dn"][pin]) / 1000 for r in rows) / len(rows)) for pin, n in pins.items()}

problems = []


def expect(name, ok, detail):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        problems.append(f"{name}: {detail}")


r = checker.check(8, window, True, pulls, real)
expect("(a) step 8 passes with the board's calibration", r["result"] == "pass", f"{r['result']}: {r['message']}")

r = checker.check(8, window, True, pulls, plain)
kind = (r["problem"] or {}).get("kind")
expect("(b) step 8 is unsure (resistance) with the plain 45k model", r["result"] == "unsure" and kind == "resistance",
       f"{r['result']}, {kind}")

same = all(plain.readings(v, ohms) == pull_readings(v, ohms) for v, ohms in [(1.2, 3e3), (3.3, 100.0), (0.5, 47e3), (None, None)])
expect("(c) Pulls.ohmic gives the same readings as adc.pull_readings", same, "readings differ")

fault = next(f for f, _ in table[10]["faults"] if f.label == LOST)
volts, ohms = table[10]["drive"][fault.label]
sig = [{n: None if volts[n] is None else min(volts[n], 3.1) for n in pins.values()}] * 10
shifts = {n: None if volts[n] is None else plain.shifts(volts[n], ohms[n]) for n in pins.values()}
r = checker.check(10, sig, True, shifts, plain)
first = r["suspects"][0]["label"] if r["suspects"] else None
expect("(d) finished circuit: R3 missing is a fail that names R3 first", r["result"] == "fail" and first == LOST,
       f"{r['result']}, first suspect {first}")

print(f"\nProblems: {len(problems)}")
for line in problems:
    print("  " + line)
sys.exit(1 if problems else 0)
