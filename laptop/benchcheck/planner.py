# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""Plan a circuit in one go: test points, breadboard layout, and what can and cannot be checked.

Used by the laptop node at start-up, when the designer hands over a new circuit, and for the
designer's live preview while you draw.
"""

from .checker import RELIABLE_MARGIN
from .circuit import solve
from .faults import apply_fault
from .layout import LayoutError, make_layout, placement_text
from .testpoints import ADC1_PINS, build_table, choose_test_points, part_status, undetectable

DEFAULT_TOLERANCE = 0.15  # volts; replace with the number from the ADC test (task 0.6)


def plan(circuit, tolerance=DEFAULT_TOLERANCE, socket=True, place=None, keep_wires=False):
    """Everything the laptop needs to run a build, plus a report for the designer.

    layout is None (and layout_error says why) when the circuit does not fit the breadboard.
    keep_wires (only without the socket): plan as if the socket were there and then leave it out,
    so the measuring wires stay exactly where they are with the socket. Used when the socket is
    switched off in the middle of a build.
    """
    table = build_table(circuit)
    # With the resistor test socket, one ADC1 pin stays free for it; without, all of them can measure.
    reserve = socket or keep_wires
    nodes = choose_test_points(circuit, table, 2 * tolerance, max_points=len(ADC1_PINS) - (1 if reserve else 0))
    pins = dict(zip(ADC1_PINS, nodes))
    socket_pin = next((p for p in ADC1_PINS if p not in pins), None) if reserve else None
    margin = RELIABLE_MARGIN * tolerance
    hidden = undetectable(table, nodes, margin)

    # A mistake hidden at its own step usually shows up once later parts make current flow.
    hidden_at = {(step, fault.label) for step, fault in hidden}
    never = [(step, fault) for step, fault in hidden
             if all((later, fault.label) in hidden_at for later in range(step + 1, circuit.num_steps + 1))]

    try:
        layout = make_layout(circuit, pins, socket_pin, place)
        pins, socket_pin = layout["pins"], (layout["socket"] or {}).get("pin")  # pins chosen for short wires
        if not socket and layout["socket"]:
            layout["wires"] = [w for w in layout["wires"] if w.get("pin") != socket_pin]
            layout["socket"], socket_pin = None, None
        placements = [placement_text(p, layout["parts"][p.name], layout["probes"], layout["node_columns"], layout) for p in circuit.parts]
        layout_error = None
    except LayoutError as e:
        layout, placements, layout_error = None, None, str(e)

    return {"table": table, "pins": pins, "socket_pin": socket_pin, "socket": socket, "tolerance": tolerance,
            "status": part_status(table, nodes, margin),
            "hidden": hidden, "never": never,
            "layout": layout, "placements": placements, "layout_error": layout_error,
            "faults_simulated": sum(len(r["faults"]) for r in table.values())}


def report(circuit, p):
    """The plan as JSON for the designer: final voltages, test points, and the checkability report."""
    final = p["table"][circuit.num_steps]["correct"]
    late = sorted({(s, part) for s, parts in p["status"].items() for part, st in parts.items()
                   if st in ("connected", "value")})
    blind = [{"step": s, "part": circuit.parts[s - 1].name} for s in sorted(p["status"])
             if circuit.parts[s - 1].kind == "R" and p["status"][s][circuit.parts[s - 1].name] == "unchecked"]
    # Resistors with the same voltage on both ends of the finished circuit carry no current. A wrong
    # value there changes nothing the ESP32 can measure, and in a loop even a missing one does not.
    last = p["table"][circuit.num_steps]
    volts = dict(last["volts"], gnd=0.0)
    idle = [part.name for part in circuit.parts if part.kind == "R"
            and (volts.get(part.a) is None or volts.get(part.b) is None or abs(volts[part.a] - volts[part.b]) < 1e-6)]
    busy_nodes = {n for part in circuit.parts if part.name not in idle for n in (part.a, part.b)}
    only_idle = [n for n in circuit.nodes if n not in busy_nodes and n not in ("vcc", "gnd")]
    hint = None
    if only_idle:   # a resistor from the far end of the idle part to the rail further away makes current flow
        node = only_idle[-1]
        hint = {"node": node, "rail": "GND" if (volts.get(node) or 3.3) > 1.65 else "3.3 V"}
    return {
        "no_current": idle, "no_current_hint": hint,
        "ok": p["layout_error"] is None and bool(p["pins"]),
        "error": p["layout_error"] or (None if p["pins"] else "nothing in this circuit can be measured"),
        "steps": circuit.num_steps,
        "nodes": {n: None if v is None else round(v, 3) for n, v in final.items()},
        "pins": p["pins"],
        "faults_simulated": p["faults_simulated"],
        "hidden_at_own_step": len(p["hidden"]),
        "never_visible": [f"step {s}: {f.label}" for s, f in p["never"]],
        "checked_late": [{"step": s, "part": part} for s, part in late],
        "blind_steps": blind,
        "suggested_order": suggest_order(circuit, p["tolerance"], p["socket"]) if p["layout_error"] is None else None,
        "socket": bool(p["socket_pin"]),
    }


# ---------- build order ----------
def _visible(before, after, margin):
    """Does at least one node read differently (or float in only one case)?"""
    for n, v in before.items():
        w = after[n]
        if (v is None) != (w is None) or (v is not None and abs(v - w) > margin):
            return True
    return False


def _score(p):
    """Lower is better: steps that check nothing, then mistakes never seen, then late ones."""
    blind = sum(1 for s, st in p["status"].items()
                if st.get(p["table"][s]["new_part"]) == "unchecked" and not p["table"][s]["new_part"].startswith("V"))
    return (blind, len(p["never"]), len(p["hidden"]))


def suggest_order(circuit, tolerance=DEFAULT_TOLERANCE, socket=True):
    """A build order that lets every step check as much as possible, or None if the given order
    is already as good.

    Greedy: after the power step, repeatedly add the part that is visible the moment it goes in,
    hides the fewest of its own mistakes, and reveals the most mistakes still hidden from earlier
    parts. Every node counts as measurable here; the real test points are chosen afterwards and the
    two orders are compared on the full plan.
    """
    from .circuit import Circuit
    from .faults import candidate_faults

    margin = RELIABLE_MARGIN * tolerance
    power = [p for p in circuit.parts if p.kind == "V"]
    rest = [p for p in circuit.parts if p.kind != "V"]
    order, still_hidden = list(power), []          # still_hidden: faults not seen yet, as (fault)
    while rest:
        best = None
        for i, cand in enumerate(rest):
            board = order + [cand]
            good = solve(board, circuit.nodes)
            new_hidden, blind, revealed = 0, 0, 0
            for f in candidate_faults(board):
                seen = _visible(good, solve(apply_fault(board, f), circuit.nodes), margin)
                if f.part == cand.name:
                    if not seen:
                        new_hidden += 1
                        blind += f.kind == "open"
                elif f.label in still_hidden and seen:
                    revealed += 1
            key = (blind, new_hidden - 2 * revealed, i)
            if best is None or key < best[0]:
                best = (key, cand)
        cand = best[1]
        order.append(cand)
        rest.remove(cand)
        board = list(order)
        good = solve(board, circuit.nodes)
        still_hidden = [f.label for f in candidate_faults(board)
                        if not _visible(good, solve(apply_fault(board, f), circuit.nodes), margin)]

    if [p.name for p in order] == [p.name for p in circuit.parts]:
        return None
    mine, theirs = plan(circuit, tolerance, socket), plan(Circuit(order), tolerance, socket)
    if theirs["layout_error"] or _score(theirs) >= _score(mine):
        return None
    a, b = _score(mine), _score(theirs)
    return {"order": [p.name for p in order],
            "blind_steps": [a[0], b[0]], "never_visible": [a[1], b[1]], "hidden_at_own_step": [a[2], b[2]]}
