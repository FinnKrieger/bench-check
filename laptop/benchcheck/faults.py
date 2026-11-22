"""Likely wiring mistakes, and the circuit you get when one of them happens."""

from dataclasses import dataclass, replace

from .circuit import GROUND, format_ohms

SHORT_OHMS = 0.01  # a resistor whose legs sit in the same row
# Every resistor in the lab kit (and in the simulator's parts bin): grabbing the wrong one from
# the kit is the usual way a value goes wrong, so each of these is simulated.
KIT_VALUES = [100, 470, 1e3, 2.2e3, 4.7e3, 10e3, 22e3, 47e3, 100e3]


@dataclass(frozen=True)
class Fault:
    kind: str  # open | short | value | moved | ground
    part: str
    label: str
    new_value: float = 0.0
    leg: str = ""  # "a" or "b" for a moved leg
    to_node: str = ""


def candidate_faults(parts, all_nodes=()):
    """Every single mistake we simulate for the parts currently on the board.

    all_nodes: every node of the finished circuit. A leg can end up in the row of a node that is
    not in use yet, so moved legs are simulated for those rows too.
    """
    nodes = sorted({n for p in parts for n in (p.a, p.b)} | set(all_nodes))
    faults = []
    for p in parts:
        if p.kind == "V":
            faults.append(Fault("open", p.name, "The ESP32's 3.3 V wire is not in the + rail"))
            faults.append(Fault("ground", p.name, "The ESP32's GND wire is not in the − rail"))
            continue
        faults.append(Fault("open", p.name, f"{p.name} is missing or a leg is not making contact"))
        faults.append(Fault("short", p.name, f"{p.name} is shorted (both legs in the same row)",
                            new_value=SHORT_OHMS))
        for wrong in KIT_VALUES:
            if wrong != p.value:
                faults.append(Fault("value", p.name,
                                    f"{p.name} is {format_ohms(wrong)} instead of {format_ohms(p.value)}",
                                    new_value=wrong))
        for leg, here, other in (("a", p.a, p.b), ("b", p.b, p.a)):
            for n in nodes:
                if n not in (here, other):
                    where = "GND" if n == GROUND else f"node {n}"
                    faults.append(Fault("moved", p.name,
                                        f"{p.name}: the leg meant for {here} is in {where}",
                                        leg=leg, to_node=n))
    return faults


RAIL = "gnd-rail-not-connected"


def apply_fault(parts, fault):
    if fault.kind == "ground":
        # The − rail is no longer the ESP32's ground: every part meant for ground floats with it.
        return [p if p.kind == "V" else replace(p, a=RAIL if p.a == GROUND else p.a, b=RAIL if p.b == GROUND else p.b)
                for p in parts]
    out = []
    for p in parts:
        if p.name != fault.part:
            out.append(p)
        elif fault.kind == "open":
            continue
        elif fault.kind in ("short", "value"):
            out.append(replace(p, value=fault.new_value))
        elif fault.kind == "moved":
            out.append(replace(p, **{fault.leg: fault.to_node}))
    return out
