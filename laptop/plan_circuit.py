"""Solve a circuit file, choose the test points, and print the plan. Writes nothing.

    python plan_circuit.py ../circuits/demo.cir
"""

import argparse

from benchcheck.circuit import Circuit
from benchcheck.planner import plan
from benchcheck.testpoints import build_table

DEFAULT_TOLERANCE = 0.15  # volts; replace with the number from the ADC test (task 0.6)


def fmt(v):
    return "  float" if v is None else f"{v:6.3f}V"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("circuit")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = ap.parse_args()

    circuit = Circuit.from_file(args.circuit)
    table = build_table(circuit)

    print(f"Circuit: {args.circuit}  ({circuit.num_steps} steps, nodes: {', '.join(circuit.nodes)})\n")
    print("Expected voltages per step")
    print("  step  " + "".join(f"{n:>9}" for n in circuit.nodes) + "   instruction")
    for step in range(1, circuit.num_steps + 1):
        v = table[step]["correct"]
        print(f"  {step:>4}  " + "".join(f"{fmt(v[n]):>9}" for n in circuit.nodes)
              + f"   {circuit.parts[step - 1].instruction}")

    p = plan(circuit, args.tolerance)
    pins, missed, never = p["pins"], p["hidden"], p["never"]

    print(f"\nSimulated {p['faults_simulated']} mistakes across all steps.")
    print("Test points (the ESP32 pin that measures each node):")
    for pin, node in pins.items():
        print(f"  node {node:<6} -> GPIO {pin}")
    if p["socket_pin"]:
        print(f"  test socket -> GPIO {p['socket_pin']}")
    print(f"\nMistakes the test points cannot see at their own step: {len(missed)}")
    print(f"  caught at a later step: {len(missed) - len(never)}")
    print(f"  never visible (cannot be checked with these test points): {len(never)}")
    for step, fault in never:
        print(f"    step {step}: {fault.label}")

    if p["layout_error"]:
        raise SystemExit(f"\nCannot lay this circuit out on the breadboard: {p['layout_error']}")
    print("\nBreadboard layout (build in this order):")
    for step, text in enumerate(p["placements"], 1):
        print(f"  {step}. {text}")


if __name__ == "__main__":
    main()
