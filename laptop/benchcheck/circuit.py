# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""Read a circuit file and solve it for node voltages (nodal analysis).

Only resistors (R...) and DC voltage sources (V...) are supported, so the circuit is linear and
one matrix solve gives every node voltage.
"""

import re
from dataclasses import dataclass

import numpy as np

GROUND = "gnd"


@dataclass(frozen=True)
class Part:
    name: str
    a: str
    b: str
    value: float
    instruction: str = ""

    @property
    def kind(self):
        return self.name[0].upper()


def parse_value(text):
    """'2.2k' -> 2200.0, '1M' -> 1e6, '3.3' -> 3.3"""
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(k|K|M|meg)?", text.strip())
    if not m:
        raise ValueError(f"cannot read value '{text}'")
    scale = {None: 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "meg": 1e6}[m.group(2)]
    return float(m.group(1)) * scale


def format_ohms(value):
    if value >= 1e6:
        return f"{value / 1e6:g}M"
    if value >= 1e3:
        return f"{value / 1e3:g}k"
    return f"{value:g}"


class Circuit:
    def __init__(self, parts):
        self.parts = list(parts)
        names = [p.name for p in self.parts]
        if len(names) != len(set(names)):
            raise ValueError("part names must be unique")
        nodes = {n for p in self.parts for n in (p.a, p.b)} - {GROUND}
        self.nodes = sorted(nodes)

    @classmethod
    def from_file(cls, path):
        with open(path) as f:
            return cls.from_text(f.read(), str(path))

    @classmethod
    def from_text(cls, text, source="circuit"):
        parts = []
        for line_no, raw in enumerate(text.splitlines(), 1):
            line, _, comment = raw.partition("#")
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 4 or fields[0][0].upper() not in "RV":
                raise ValueError(f"{source}:{line_no}: expected '<R|V name> <node> <node> <value>'")
            name, a, b, value = fields
            part = Part(name, a.lower(), b.lower(), parse_value(value), comment.strip())
            where = f"{source}:{line_no}: {name}"
            if part.kind == "R" and part.value <= 0:
                raise ValueError(f"{where} must have a value above zero")
            if part.kind == "R" and part.a == part.b:
                raise ValueError(f"{where} has both ends on the same node")
            if part.kind == "V" and (parts or part.value != 3.3):
                raise ValueError(f"{where}: the supply must be the first part, at 3.3 V (the ESP32's 3.3 V pin)")
            parts.append(part)
        if not parts:
            raise ValueError(f"{source}: no parts")
        return cls(parts)

    @property
    def num_steps(self):
        return len(self.parts)

    def step_parts(self, step):
        """Parts on the board after build step `step` (1-based)."""
        return self.parts[:step]


def _grounded_nodes(parts):
    """Nodes with a path to ground. Every other node is floating: its voltage is undefined."""
    neighbours = {}
    for p in parts:
        neighbours.setdefault(p.a, set()).add(p.b)
        neighbours.setdefault(p.b, set()).add(p.a)
    if GROUND not in neighbours:
        return set()
    seen, todo = {GROUND}, [GROUND]
    while todo:
        for n in neighbours[todo.pop()]:
            if n not in seen:
                seen.add(n)
                todo.append(n)
    return seen


def _solve(parts, all_nodes, want_impedance):
    grounded = _grounded_nodes(parts)
    live = [p for p in parts if p.a in grounded and p.b in grounded and p.a != p.b]
    nodes = sorted(grounded - {GROUND})
    index = {n: i for i, n in enumerate(nodes)}
    sources = [p for p in live if p.kind == "V"]
    size = len(nodes) + len(sources)
    volts = {n: None for n in all_nodes}
    ohms = {n: None for n in all_nodes}
    if size == 0:
        return volts, ohms

    A = np.zeros((size, size))
    z = np.zeros(size)
    for p in live:
        if p.kind == "R":
            g = 1.0 / p.value
            for x, y in ((p.a, p.b), (p.b, p.a)):
                if x in index:
                    A[index[x], index[x]] += g
                    if y in index:
                        A[index[x], index[y]] -= g
    for k, p in enumerate(sources):
        row = len(nodes) + k
        if p.a in index:
            A[index[p.a], row] += 1
            A[row, index[p.a]] += 1
        if p.b in index:
            A[index[p.b], row] -= 1
            A[row, index[p.b]] -= 1
        z[row] = p.value

    if want_impedance:
        # Injecting 1 A into node n raises its voltage by inv(A)[n, n]: its Thevenin resistance.
        try:
            inv = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            inv = np.linalg.pinv(A)
        x = inv @ z
    else:
        try:
            x = np.linalg.solve(A, z)
        except np.linalg.LinAlgError:
            x = np.linalg.lstsq(A, z, rcond=None)[0]

    for n in nodes:
        if n in volts:
            volts[n] = float(x[index[n]])
            if want_impedance:
                ohms[n] = max(0.0, float(inv[index[n], index[n]]))
    return volts, ohms


def solve(parts, all_nodes):
    """Voltage at every node in `all_nodes`, or None where the node is floating or absent.

    Modified nodal analysis: one KCL equation per node, plus one equation per voltage source
    (V(a) - V(b) = value), with the source currents as extra unknowns.
    """
    return _solve(parts, all_nodes, False)[0]


def solve_z(parts, all_nodes):
    """(voltages, Thevenin resistances): how hard the circuit drives each node, in ohms.

    A node tied to the supply or ground has 0 ohms; one behind a 10k resistor has about 10k.
    Floating nodes are None in both. The ESP32 senses this with its pull-up and pull-down.
    """
    return _solve(parts, all_nodes, True)
