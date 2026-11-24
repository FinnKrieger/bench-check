# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""Stress test: generate many circuits, plan them, and check every step and every mistake.

    .venv/bin/python tests/fuzz_circuits.py            # 300 random circuits plus edge cases
    .venv/bin/python tests/fuzz_circuits.py -n 1000 --seed 7

For each circuit:
  1. plan it (test points + breadboard layout) and check the layout is the same circuit,
  2. build it correctly and check every step passes,
  3. plant every simulated mistake at every step and check the outcome:
       caught    the step fails and the true mistake is among the "read the same" group
       listed    the step fails and the true mistake is listed, but behind a better match
       missed    the step fails and the true mistake is not listed
       unsure    the readings were unstable (a floating test point)
       invisible the step passes, and the planner had flagged the mistake as undetectable
       BUG       the step passes although the planner said the mistake is detectable,
                 or anything raises
  4. for each invisible mistake, keep it on the board one more step and see if it is caught.
"""

import argparse
import random
import sys
import traceback
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "laptop"))
from benchcheck.adc import as_read, pull_shifts  # noqa: E402
from benchcheck.checker import RELIABLE_MARGIN, Checker  # noqa: E402
from benchcheck.circuit import GROUND, Circuit, Part, solve_z  # noqa: E402
from benchcheck.faults import KIT_VALUES, apply_fault, candidate_faults  # noqa: E402
from benchcheck.layout import ESP_PINS, LayoutError, MAX_SPAN, MIN_SPAN, make_layout  # noqa: E402
from benchcheck.testpoints import ADC1_PINS, build_table, choose_test_points, part_status, undetectable  # noqa: E402

TOL = 0.15
NOISE = 0.01


# ---------- circuit generators ----------
def kit():
    return random.choice(KIT_VALUES)


def power():
    return [Part("V1", "vcc", GROUND, 3.3, "power")]


def ladder(n):
    parts, prev = power(), "vcc"
    for i in range(n):
        node = f"n{i}"
        parts += [Part(f"R{2 * i + 1}", prev, node, kit()), Part(f"R{2 * i + 2}", node, GROUND, kit())]
        prev = node
    return parts


def chain(n):
    parts, prev = power(), "vcc"
    for i in range(n):
        node = GROUND if i == n - 1 else f"n{i}"
        parts.append(Part(f"R{i + 1}", prev, node, kit()))
        prev = node
    return parts


def bridge():
    return power() + [Part("R1", "vcc", "a", kit()), Part("R2", "vcc", "b", kit()),
                      Part("R3", "a", GROUND, kit()), Part("R4", "b", GROUND, kit()),
                      Part("R5", "a", "b", kit())]


def parallel():
    v = kit()
    return power() + [Part("R1", "vcc", "a", kit()), Part("R2", "a", GROUND, v), Part("R3", "a", GROUND, v)]


def random_net(n_nodes, n_parts):
    nodes = ["vcc", GROUND] + [f"n{i}" for i in range(n_nodes)]
    parts = power()
    for i in range(n_parts):
        a, b = random.sample(nodes, 2)
        if {a, b} == {"vcc", GROUND} and random.random() < 0.7:
            continue
        parts.append(Part(f"R{i + 1}", a, b, kit()))
    return parts


def shuffle_build(parts):
    rest = parts[1:]
    random.shuffle(rest)
    return parts[:1] + rest


EDGE_CASES = {
    "single resistor to gnd": power() + [Part("R1", "vcc", GROUND, 1e3)],
    "single resistor to a node": power() + [Part("R1", "vcc", "a", 1e3)],
    "resistor across the rails": power() + [Part("R1", "vcc", "a", 1e3), Part("R2", "a", GROUND, 1e3),
                                            Part("R3", "vcc", GROUND, 10e3)],
    "ground part placed first": power() + [Part("R1", "a", GROUND, 2.2e3), Part("R2", "vcc", "a", 1e3)],
    "equal divider": power() + [Part("R1", "vcc", "a", 1e3), Part("R2", "a", GROUND, 1e3)],
    "extreme ratio": power() + [Part("R1", "vcc", "a", 100e3), Part("R2", "a", GROUND, 100)],
    "parallel identical": parallel(),
    "bridge balanced": power() + [Part("R1", "vcc", "a", 1e3), Part("R2", "vcc", "b", 1e3),
                                  Part("R3", "a", GROUND, 1e3), Part("R4", "b", GROUND, 1e3),
                                  Part("R5", "a", "b", 4.7e3)],
    "dangling branch": power() + [Part("R1", "vcc", "a", 1e3), Part("R2", "a", GROUND, 2.2e3),
                                  Part("R3", "a", "x", 1e3)],
    "seven nodes (more than ADC pins)": ladder(7),
    "five parts on one node": power() + [Part("R1", "vcc", "a", 1e3)] +
                              [Part(f"R{i}", "a", GROUND, 10e3) for i in range(2, 6)],
}


# ---------- checks ----------
def layout_matches(circuit, layout, pins):
    """The breadboard layout must be the same circuit: each node is exactly one net."""
    joined = {}
    for j in layout.get("jumpers", []):
        cols = [h.split(":")[1] for h in j["holes"]]
        joined[f"col{cols[1]}"] = f"col{cols[0]}"

    def net(hole):
        k, col = hole.split(":")
        n = "rail+" if k == "+" else "rail-" if k == "-" else f"col{col}"
        return joined.get(n, n)
    node_net = {}
    for p in circuit.parts:
        for node, hole in zip((p.a, p.b), layout["parts"][p.name]):
            n = net(hole)
            if node_net.setdefault(node, n) != n:
                return f"node {node} is split over {node_net[node]} and {n}"
    nets = {}
    for node, n in node_net.items():
        if nets.setdefault(n, node) != node:
            return f"nodes {nets[n]} and {node} share {n}"
    holes = [h for legs in layout["parts"].values() for h in legs if not h.startswith("esp")]
    holes += list(layout["probes"].values()) + [h for j in layout.get("jumpers", []) for h in j["holes"]]
    dup = [h for h, c in Counter(holes).items() if c > 1 and not (h in ("+:1", "-:1"))]
    if dup:
        return f"two things in one hole: {dup}"
    for pin, hole in layout["probes"].items():
        if node_net.get(pins[pin], net(hole)) != net(hole):
            return f"probe GPIO {pin} is not on node {pins[pin]}"
    return None


def layout_tidy(circuit, layout):
    """The layout must look like a tidy build: parts in a lane never overlap or lie over a hole in use,
    nothing sits on a power-wire or socket row, and resistors between nodes are neither cramped nor huge."""
    letters = "abcde"
    unit = set(layout["node_columns"].values()) | {r for rs in layout.get("extra_columns", {}).values() for r in rs}
    top = layout["esp"]["top"]
    sock = layout.get("socket")
    reserved = {top, top + ESP_PINS - 1}
    own = set()                                   # the socket's own holes
    if sock:
        reserved |= {sock["column"], sock["column"] + 1}
        own = {sock["reference"][1], sock["probe"], sock["test"][0]}
    if unit & reserved:
        return f"node rows {sorted(unit & reserved)} are on reserved rows {sorted(reserved)}"
    main_of = {r: layout["node_columns"][n] for n, rs in layout.get("extra_columns", {}).items() for r in rs}
    cramped_ok = {r for r, m in main_of.items() if abs(r - m) == 1}      # a node's second row right beside it
    items = []                                    # (name, first hole, second hole, is a resistor)
    for p in circuit.parts:
        a, b = layout["parts"][p.name]
        if p.kind == "R" and a[0] in letters and b[0] in letters:
            items.append((p.name, a, b, True))
    for i, j in enumerate(layout.get("jumpers", [])):
        items.append((f"jumper {j['node']}", j["holes"][0], j["holes"][1], False))
    used = {h for legs in layout["parts"].values() for h in legs if h[0] in letters}
    used |= {h for h in layout["probes"].values() if h[0] in letters}
    used |= {h for j in layout.get("jumpers", []) for h in j["holes"]}
    used |= own
    for h in used - own:
        if int(h.split(":")[1]) in reserved:
            return f"hole {h} is on a reserved row"
    lanes = {}
    for name, a, b, is_res in items:
        (la, ra), (lb, rb) = a.split(":"), b.split(":")
        if la != lb:
            return f"{name} is not in one lane ({a}, {b})"
        lo, hi = sorted((int(ra), int(rb)))
        for name2, lo2, hi2 in lanes.get(la, []):
            if lo <= hi2 and lo2 <= hi:
                return f"{name} and {name2} overlap in lane {la}"
        lanes.setdefault(la, []).append((name, lo, hi))
        for r in range(lo + 1, hi):
            if f"{la}:{r}" in used:
                return f"{name} passes over hole {la}:{r}, which is in use"
        if is_res and len(layout["node_columns"]) <= 10 and not ({lo, hi} & cramped_ok):
            if not MIN_SPAN <= hi - lo <= MAX_SPAN:
                return f"{name} spans {hi - lo} rows (wanted {MIN_SPAN}-{MAX_SPAN})"
    return None


def window(volts, nodes, n=10):
    """What the laptop gets from the ESP32-S3: noisy, clipped at the ADC ceiling, and None where the
    pull-up / pull-down test found the pin floating."""
    return [{k: (None if volts[k] is None else as_read(volts[k] + random.gauss(0, NOISE))) for k in nodes}
            for _ in range(n)]


def measure(parts, circuit, nodes):
    """(window, pulls) as the laptop gets them from a board built as `parts`."""
    volts, ohms = solve_z(parts, circuit.nodes)
    pulls = {n: None if volts[n] is None else tuple(s + random.gauss(0, 0.003) for s in pull_shifts(volts[n], ohms[n]))
             for n in nodes}
    return window(volts, nodes), pulls


def run_circuit(name, parts, stats, problems, verbose):
    circuit = Circuit(parts)
    table = build_table(circuit)
    nodes = choose_test_points(circuit, table, 2 * TOL)
    pins = dict(zip(ADC1_PINS, nodes))
    try:
        layout = make_layout(circuit, pins)
        err = layout_matches(circuit, layout, layout["pins"])  # the layout picks pins for short wires
        err = err or layout_tidy(circuit, layout)
        if err:
            problems.append(f"{name}: layout wrong: {err}")
    except LayoutError as e:
        stats["layout refused"] += 1
        if verbose:
            print(f"  {name}: layout refused: {e}")
    if not nodes:
        problems.append(f"{name}: no test points chosen")
        return
    checker = Checker(table, pins, TOL, part_status(table, nodes, RELIABLE_MARGIN * TOL))
    hidden = {(s, f.label) for s, f in undetectable(table, nodes, RELIABLE_MARGIN * TOL)}

    for step in range(1, circuit.num_steps + 1):
        r = checker.check(step, *measure(circuit.step_parts(step), circuit, nodes)[:1], True,
                          measure(circuit.step_parts(step), circuit, nodes)[1])
        if r["result"] != "pass":
            # a correct board can still read unstable when a checked node is floating
            problems.append(f"{name} step {step}: correct board gave {r['result']}: {r['message']}")
        stats["correct steps"] += 1

        for fault in candidate_faults(circuit.step_parts(step)):
            w, pulls = measure(apply_fault(circuit.step_parts(step), fault), circuit, nodes)
            r = checker.check(step, w, True, pulls)
            labels = [f["label"] for f in r["likely_faults"]]
            if r["result"] == "fail":
                outcome = ("caught" if fault.label in (r.get("same_reading") or labels[:1])
                           else "listed" if fault.label in labels else "missed")
            elif r["result"] == "unsure":
                outcome = "unsure"
            elif (step, fault.label) in hidden:
                outcome = "invisible"
                if step < circuit.num_steps:          # does the next step catch it?
                    nxt = circuit.step_parts(step + 1)
                    w2, pulls2 = measure(apply_fault(nxt, fault), circuit, nodes)
                    r2 = checker.check(step + 1, w2, True, pulls2)
                    l2 = r2.get("same_reading") or [f["label"] for f in r2["likely_faults"]][:1]
                    stats["invisible, caught next step" if r2["result"] == "fail" and fault.label in l2
                          else "invisible, still hidden next step" if r2["result"] == "pass"
                          else "invisible, next step unsure" if r2["result"] == "unsure"
                          else "invisible, next step fails but blames other"] += 1
            else:
                outcome = "BUG"
                problems.append(f"{name} step {step}: '{fault.label}' passed but was not flagged invisible "
                                f"({r['message']})")
            stats[outcome] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    random.seed(args.seed)

    cases = list(EDGE_CASES.items())
    for i in range(args.n):
        kind = random.choice(["ladder", "chain", "bridge", "parallel", "random"])
        parts = {"ladder": lambda: ladder(random.randint(1, 4)), "chain": lambda: chain(random.randint(2, 5)),
                 "bridge": bridge, "parallel": parallel,
                 "random": lambda: random_net(random.randint(1, 5), random.randint(2, 8))}[kind]()
        if random.random() < 0.5:
            parts = shuffle_build(parts)
        cases.append((f"#{i} {kind}", parts))

    stats, problems = Counter(), []
    for name, parts in cases:
        try:
            run_circuit(name, parts, stats, problems, args.verbose)
        except Exception:
            problems.append(f"{name}: crashed\n" + traceback.format_exc())
        stats["circuits"] += 1

    print(f"Circuits: {stats.pop('circuits')}   correct steps checked: {stats.pop('correct steps')}")
    total = sum(v for k, v in stats.items() if k in ("caught", "listed", "missed", "unsure", "invisible", "BUG"))
    for k in ("caught", "listed", "missed", "unsure", "invisible", "BUG"):
        print(f"  {k:<10} {stats[k]:>6}  ({100 * stats[k] / max(1, total):.1f}%)")
    for k, v in sorted(stats.items()):
        if k.startswith("invisible,") or k == "layout refused":
            print(f"  {k}: {v}")
    print(f"\nProblems: {len(problems)}")
    shown = Counter()
    for p in problems:
        key = p.split(":")[1][:40] if ":" in p else p
        shown[key] += 1
        if shown[key] <= 3:
            print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
