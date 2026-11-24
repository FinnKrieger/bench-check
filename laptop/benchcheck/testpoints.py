# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""Simulate every mistake at every step, then choose the nodes worth measuring."""

from itertools import combinations

from .circuit import solve_z
from .faults import apply_fault, candidate_faults

from .adc import ADC1_PINS, as_read, pull_shifts, pull_tolerance  # noqa: F401  (ADC1_PINS is re-exported)


def build_table(circuit):
    """table[step] = {"correct": voltages, "faults": [(Fault, voltages), ...]}"""
    table = {}
    for step in range(1, circuit.num_steps + 1):
        parts = circuit.step_parts(step)
        true_volts, ohms = solve_z(parts, circuit.nodes)
        faults, drive = [], {}
        for f in candidate_faults(parts, circuit.nodes):
            fv, fo = solve_z(apply_fault(parts, f), circuit.nodes)
            faults.append((f, _read(fv)))
            drive[f.label] = (fv, fo)
        table[step] = {
            "new_part": parts[-1].name,
            "ends": {p.name: (p.a, p.b) for p in parts},
            "volts": true_volts,                                 # the physics
            "ohms": ohms,                                        # how hard each node is driven
            "correct": _read(true_volts),                        # what the ADC will report
            "faults": faults,
            "drive": drive,                                      # per mistake: (true volts, ohms)
        }
    return table


def _read(volts):
    return {n: as_read(v) for n, v in volts.items()}


def _differs(v1, v2, margin):
    return v1 is not None and v2 is not None and abs(v1 - v2) > margin


def _drive_differs(v1, r1, v2, r2, factor=1.25):
    """Do the pull-up / pull-down shifts tell two (voltage, resistance) cases apart, with margin?"""
    if v1 is None or v2 is None:
        return False  # floating in either case is already covered by _separates
    return any(abs(a - b) > factor * pull_tolerance(a) for a, b in zip(pull_shifts(v1, r1), pull_shifts(v2, r2)))


def _separates(v1, v2, margin):
    """A node tells two outcomes apart if its voltages differ, or if it floats in only one of them
    (a floating pin reads as unstable, which the check reports)."""
    return _differs(v1, v2, margin) or ((v1 is None) != (v2 is None))


def choose_test_points(circuit, table, margin, max_points=len(ADC1_PINS)):
    """Greedy choice: keep adding the node that separates the most pairs of outcomes.

    Telling a mistake apart from a correct step (detection) counts 10 times more than telling
    two mistakes apart (location).
    """
    pairs = []  # (weight, voltages1, voltages2)
    for row in table.values():
        sigs = [v for _, v in row["faults"]]
        pairs += [(10, row["correct"], s) for s in sigs]
        pairs += [(1, s1, s2) for s1, s2 in combinations(sigs, 2)]

    chosen = []
    covered = [False] * len(pairs)
    while len(chosen) < max_points:
        best, best_gain = None, 0
        for node in circuit.nodes:
            if node in chosen:
                continue
            gain = sum(w for i, (w, s1, s2) in enumerate(pairs)
                       if not covered[i] and _separates(s1[node], s2[node], margin))
            if gain > best_gain:
                best, best_gain = node, gain
        if best is None:
            break
        chosen.append(best)
        for i, (_, s1, s2) in enumerate(pairs):
            if _separates(s1[best], s2[best], margin):
                covered[i] = True
    return chosen


def undetectable(table, nodes, margin):
    """Mistakes that read the same as a correct step on the chosen nodes."""
    missed = []
    for step, row in table.items():
        for fault, sig in row["faults"]:
            # visible if a voltage moves, a node is connected in one case and floating in the other,
            # or the pull-up / pull-down test shifts a node by a clearly different amount
            if any(_separates(row["correct"][n], sig[n], margin) for n in nodes):
                continue
            fv, fo = row["drive"][fault.label]
            if not any(_drive_differs(row["volts"][n], row["ohms"][n], fv[n], fo[n]) for n in nodes):
                missed.append((step, fault))
    return missed


CONNECTION_FAULTS = ("open", "ground")  # is it there and making contact at all
VALUE_FAULTS = ("value",)               # the wrong resistor from the kit (the test socket rules these out)


def part_status(table, nodes, margin):
    """status[step][part] for every part on the board after a pass at that step.

    "checked"    every simulated mistake of the part has shown up at some step so far
    "value"      in place and everything confirmed except its value (a socket check settles it)
    "connected"  in place, but a short or a leg in a row that reads the same would still pass
    "unchecked"  even a missing part would still pass: nothing measured can see it yet
    A group of mistakes counts as confirmed at the first step where none of them is hidden.
    """
    hidden = {}
    for step, fault in undetectable(table, nodes, margin):
        group = "conn" if fault.kind in CONNECTION_FAULTS else "value" if fault.kind in VALUE_FAULTS else "other"
        hidden.setdefault(step, set()).add((fault.part, group))
    status, placed, ok = {}, [], {"conn": set(), "value": set(), "other": set()}
    for step in sorted(table):
        placed.append(table[step]["new_part"])
        for p in placed:
            for g in ok:
                if (p, g) not in hidden.get(step, ()):
                    ok[g].add(p)
        status[step] = {p: ("unchecked" if p not in ok["conn"] else
                            "connected" if p not in ok["other"] else
                            "value" if p not in ok["value"] else "checked") for p in placed}
    return status
