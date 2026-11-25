# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""Turn a circuit into a simple breadboard layout: which hole every leg and wire goes in.

Breadboard model (same as the simulator page): 60 numbered rows, each a strip of five connected
holes a-e, a + rail and a - rail down the left side. Holes are written "+:6" (+ rail at row 6),
"-:6" (- rail), or "b:6" (row 6, hole b; "6b" on the board). (The code says "column" for a numbered
strip of five holes; on the upright board it is a row.)

The layout aims to look like a tidy build by hand, with room around every part:
  - every node except the supply and ground gets its own row, spread down the whole board: 6 rows
    apart when every resistor joins neighbouring nodes, 4 apart when some resistor skips a node (so
    that one spans 8 rows). Resistors are never shorter than 4 holes unless the board runs out of room;
  - nodes are ordered so that few resistors skip a node and the spans stay short;
  - a resistor between two nodes lies straight along one hole letter (a "lane") between their rows.
    A resistor that passes over another node's row uses up that row's hole in the lane; lanes are
    picked so parts do not touch, and an empty lane is left between neighbouring bodies when possible;
  - a resistor to the supply or ground goes from the free hole nearest the rail a few rows down the rail,
    so it is long enough to look right;
  - a node that needs a second row gets room for it: the rows below move down one, so nothing from
    the second row to the next node is too short;
  - a resistor that would be longer than 9 rows gets a "helper" row: a spare row 4 rows from the far
    node, joined to its own node by one jumper wire. A node with more legs than holes gets a second
    row beside it, joined the same way. Nothing else uses jumpers;
  - the ESP32's probe wire takes hole e of its node's row;
  - the test socket goes below everything, or above it when there is no room.
Anything the user moves by hand ("place") wins; if the roomy layout fails, tighter ones are tried.
"""

import math
from itertools import permutations

from .circuit import GROUND, format_ohms

LAYOUT_VERSION = 2      # placements saved by another version are not reused: they pinned every node in place
COLUMNS = 60            # rows on the board ("column" in the code = a numbered strip of five holes)
SOCKET_ROW = 40         # where the resistor test socket was in the first layout; now a fallback

# Where the ESP32-S3-DevKitC-1 sits: on the right half, its left pin row in column j, 3V3 at row 1
# (antenna up, USB down). Every pin then owns the f-i holes of its row, and the other pin row hangs
# off the right edge of the board, clear of the rails.
ESP_COLUMN = "j"
ESP_LEFT_HEADER = ["3V3", "3V3", "RST", "4", "5", "6", "7", "15", "16", "17", "18", "8", "3", "46",
                   "9", "10", "11", "12", "13", "14", "5V", "GND"]
ESP_PINS = len(ESP_LEFT_HEADER)  # 22 rows from 3V3 to GND


def esp_rows(top):
    """Row of each pin when the 3V3 pin is at row `top` (first match for the doubled 3V3)."""
    return {name: top + i for i, name in reversed(list(enumerate(ESP_LEFT_HEADER)))}
REFERENCE_OHMS = 10000  # the socket's known resistor; with 10k every kit value (100 to 100k) reads 0.03-3.0 V,
                        # below the S3 ADC's 3.1 V ceiling, so a 100k is not mistaken for an empty socket
FIRST_GAP = 5      # first node row = ESP32 top row + this: keeps rows for the 3V3 wire and the + rail probe
NODE_SPACING = 6   # rows between nodes when every resistor joins neighbours
TIGHT_SPACING = 4  # rows between nodes when a resistor skips one (it then spans 8 rows)
MIN_SPAN = 4       # a resistor between two rows is never shorter than this, if the board has room
MAX_SPAN = 9       # ... and a longer one gets a helper row (8 = skipping one node, +1 for a second row)
RAIL_PART_MIN = 3.5  # a resistor to a rail is at least this long, in hole pitches
SOCKET_GAP = 8     # rows between the lowest circuit row and the test socket
ROWS = "abcde"
MAX_EXACT_ORDER = 7  # try every node order up to this many nodes
MAX_NODE_ROWS = 3    # a node's main row, plus up to two more joined to it by jumpers

# What each lane costs for a resistor between neighbouring rows, or one that passes over rows; a and e
# are kept for rail resistors and the probe wire.
LANE_NEAR = {"c": 0, "b": 1, "d": 2, "a": 30, "e": 40}
LANE_FAR = {"d": 0, "c": 1, "b": 2, "a": 30, "e": 40}
BESIDE_COST = 10   # a lane right beside a body that shares rows with this one
OVER_WIRE_COST = 5  # per reserved row (a power wire) a part lies across
RAIL_HOLE_COST = 25  # using a hole that a resistor to a rail on that row will want
SEARCH_BUDGET = 2500  # lane choices tried per fill of the holes
TOTAL_BUDGET = 7000   # ... and for all the tries of one layout
BAD_SPAN_COST = 200  # per row too short or too long for a resistor


class LayoutError(ValueError):
    pass


class _NoRoom(Exception):
    """Internal: nothing fits in these nodes' rows (most likely culprit first)."""
    def __init__(self, nodes, why):
        super().__init__(why)
        self.nodes, self.why = nodes, why


# Horizontal positions (in hole pitches) of the columns the wires run between, as on the board.
WX = {"+": 0.0, "-": 1.0, "a": 3.2, "b": 4.2, "c": 5.2, "d": 6.2, "e": 7.2, "f": 9.6, "g": 10.6, "h": 11.6, "i": 12.6}
START_HOLES = "fghi"  # a wire may leave a pin's row from any of these (j holds the pin itself)


def _cross(s1, s2):
    """Do two straight wires cross (sharing an end does not count)?"""
    (p1, p2), (p3, p4) = s1, s2
    if {p1, p2} & {p3, p4}:
        return False
    def side(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 1e-9) - (v < -1e-9)
    d1, d2, d3, d4 = side(p3, p4, p1), side(p3, p4, p2), side(p1, p2, p3), side(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def hole_name(hole, node_of_col=None):
    kind, col = hole.split(":")
    if kind == "+":
        return f"the + rail (row {col})"
    if kind == "-":
        return f"the − rail (row {col})"
    node = (node_of_col or {}).get(int(col)) if kind in ROWS else None  # f-j holes belong to the ESP32's pins
    return f"{col}{kind}" + (f" (node {node.upper()})" if node else "")


def _order_nodes(order, links):
    """Node order with the fewest resistors skipping a node, then the shortest spans.
    links: [(node, node), ...]. Cost, most important first: resistors spanning more than 2 places,
    resistors spanning exactly 2, total span; ties keep first-appearance order."""
    if len(order) <= 1:
        return order

    def cost(seq):
        pos = {n: i for i, n in enumerate(seq)}
        gaps = [abs(pos[a] - pos[b]) for a, b in links]
        return (sum(g > 2 for g in gaps), sum(g == 2 for g in gaps), sum(gaps))

    if len(order) <= MAX_EXACT_ORDER:
        return min(permutations(order), key=lambda seq: (cost(seq), [order.index(n) for n in seq]))
    seq = list(order)  # greedy fallback: keep first-appearance order
    return seq


def _start_row(top, length, gnd_row):
    """First node row for a block of nodes `length` rows long. Normally top + FIRST_GAP, as long as the
    block fits between the ESP32's 3V3 and GND rows. Otherwise centred on the ADC pins, clamped to the
    board and kept out of the 3V3 wire's rows."""
    first = top + FIRST_GAP
    if first + length <= gnd_row - 2:
        return first
    start = max(1, min(COLUMNS - length, top + 9 - length // 2))
    if start + length >= top - 1 and start <= top + FIRST_GAP - 1:
        if first + length <= COLUMNS:
            start = first
        elif top - 2 - length >= 1:
            start = top - 2 - length
        else:
            start = first
    return start


def _node_rows(auto, fixed, reserved, top, gnd_row, spacing, whole_board):
    """Rows for the automatic nodes. whole_board: spread over the board, shrinking the spacing only if
    they do not fit; else the old way, between the ESP32's 3V3 and GND rows."""
    n = len(auto)
    first = top + FIRST_GAP

    def lay(step, start):
        # Slide the whole block down a row or two if a node would land on a reserved row, so the spacing
        # stays even (a skip-one resistor stays 8 rows long); bump single rows only if nothing works.
        for shift in range(step):
            rows = [start + shift + i * step for i in range(n)]
            if all(r not in reserved and all(abs(r - f) >= 2 for f in fixed.values()) for r in rows):
                return dict(zip(auto, rows))
        rows, taken, row = {}, list(fixed.values()), start
        for node in auto:
            while row in reserved or any(abs(row - r) < 2 for r in taken):
                row += 1
            rows[node] = row
            taken.append(row)
            row += step
        return rows

    if not auto:
        return {}
    if not whole_board:
        step = spacing
        while first + (n - 1) * step > gnd_row - 2 and step > 2:
            step -= 1
        return lay(step, first)
    for step in range(spacing, 1, -1):
        rows = lay(step, _start_row(top, (n - 1) * step, gnd_row))
        if max(rows.values()) <= COLUMNS:
            return rows
    return lay(2, first)  # does not fit at all; the caller reports the rows that are off the board


def _socket_row(unit, gnd_row, reserved):
    """Rows for the test socket (it takes this row and the next): below everything, else above the
    circuit (the nearest pair that is 3 rows clear), else anywhere free. None if there is no room at all."""
    def free(s):
        return s >= 1 and s + 1 <= COLUMNS and all(x not in reserved and x not in unit for x in (s, s + 1))
    lowest = max(max(unit, default=0), gnd_row)
    s = min(lowest + SOCKET_GAP, COLUMNS - 2)
    while s - lowest >= 4:
        if free(s):
            return s
        s -= 1
    for s in range(min(unit, default=COLUMNS) - 4, 0, -1):     # above the circuit, as near as is 3 rows clear
        if free(s) and all(abs(x - r) >= 3 for x in (s, s + 1) for r in unit):
            return s
    pairs = [s for s in range(1, COLUMNS) if free(s)]
    return max(pairs, key=lambda s: min((abs(x - r) for x in (s, s + 1) for r in unit), default=0), default=None)


def _helper_row(o_row, n_row, occupied, reserved):
    """A row MIN_SPAN rows from o_row, toward n_row (a little further if that is taken): (crowded, how
    much further, row), or None."""
    sign = 1 if n_row > o_row else -1
    best = None
    for extra in range(MAX_SPAN - MIN_SPAN + 1):
        h = o_row + sign * (MIN_SPAN + extra)
        if not 1 <= h <= COLUMNS or h in occupied or h in reserved or sign * (h - n_row) >= 0:
            continue
        key = (any(abs(h - r) < 2 for r in occupied), extra, h)
        best = key if best is None or key < best else best
    return best


def _add_helpers(resistors, rows_of, reserved, need):
    """Give one end of each resistor that would be longer than MAX_SPAN a helper row nearer the other
    end, so the resistor itself comes out short. Adds the row to rows_of (the node's own row first) and
    returns {(node, row): {the two nodes of that resistor}}: only that resistor uses the helper row."""
    helper_of = {}

    def fits(a, b):
        mine = {a, b}
        ok = lambda n, r: r == rows_of[n][0] or helper_of.get((n, r)) == mine
        return any(MIN_SPAN <= abs(x - y) <= MAX_SPAN for x in rows_of[a] for y in rows_of[b] if ok(a, x) and ok(b, y))

    longs = sorted(((abs(rows_of[a][0] - rows_of[b][0]), a, b) for a, b in resistors), reverse=True)
    for span, a, b in longs:
        if span <= MAX_SPAN or fits(a, b):
            continue
        occupied = {r for rs in rows_of.values() for r in rs}
        options = []
        for n, o in ((a, b), (b, a)):
            if len(rows_of[n]) < MAX_NODE_ROWS:
                h = _helper_row(rows_of[o][0], rows_of[n][0], occupied, reserved)
                if h:
                    options.append((h[0], h[1], need[n], n, h[2]))
        if options:
            _, _, _, n, row = min(options)
            rows_of[n].append(row)
            helper_of[(n, row)] = {a, b}
    return helper_of


def _extra_rows(n, rows_of, reserved):
    """Free rows right beside node n's own row for a second strip, best first."""
    main = rows_of[n][0]
    occupied = {r for rs in rows_of.values() for r in rs}
    cands = [r for r in (main + 1, main - 1) if 1 <= r <= COLUMNS and r not in occupied and r not in reserved]
    if len(rows_of[n]) >= MAX_NODE_ROWS:
        return []
    return sorted(cands, key=lambda r: (-min((abs(r - o) for o in occupied if o != main), default=99), r < main))


class _Board:
    """Which holes are taken, and which lanes (hole letters) hold a part between two rows."""

    def __init__(self, unit, reserved, keep=None, strict=True):
        self.strict = strict                      # False: only keep the holes apart (the last resort)
        self.keep = keep or {}                    # row -> holes nearest the rail kept for resistors to a rail
        self.used, self.blocked = set(), set()   # blocked: a hole a part lies over
        self.spans = {lane: [] for lane in ROWS}  # lane -> [(first row, last row)]
        self.unit = unit                          # rows that belong to a node
        self.reserved = reserved
        self.segs = []                            # straight parts, to keep rail resistors from crossing them

    def free(self, hole):
        return hole not in self.used and hole not in self.blocked

    def snapshot(self):
        return (set(self.used), set(self.blocked), {k: list(v) for k, v in self.spans.items()}, list(self.segs))

    def restore(self, snap):
        self.used, self.blocked, self.spans, self.segs = set(snap[0]), set(snap[1]), {k: list(v) for k, v in snap[2].items()}, list(snap[3])

    def take(self, hole):
        if not self.free(hole):
            raise LayoutError(f"hole {hole} used twice")
        self.used.add(hole)
        return hole

    def rail_hole(self, rail, near):
        for d in range(COLUMNS):
            for c in (near + d, near - d):
                if 1 <= c <= COLUMNS and f"{rail}:{c}" not in self.used:
                    return self.take(f"{rail}:{c}")
        raise LayoutError(f"the {rail} rail is full")

    def lane_cost(self, lane, lo, hi):
        """Cost of a part in `lane` from row lo to row hi (lower is better), None if it cannot go there:
        it would overlap another part in the lane or lie over a hole that is in use."""
        if not self.strict:
            return 0 if self.free(f"{lane}:{lo}") and self.free(f"{lane}:{hi}") else None
        if any(not (hi < a or b < lo) for a, b in self.spans[lane]):
            return None
        if any(not self.free(f"{lane}:{r}") for r in range(lo, hi + 1)):
            return None
        passes = any(lo < r < hi for r in self.unit)
        cost = (LANE_FAR if passes else LANE_NEAR)[lane]
        i = ROWS.index(lane)
        for j in (i - 1, i + 1):
            if 0 <= j < len(ROWS):
                cost += BESIDE_COST * sum(max(a, lo) < min(b, hi) for a, b in self.spans[ROWS[j]])
        cost += RAIL_HOLE_COST * sum(i < self.keep.get(r, 0) for r in range(lo, hi + 1))
        return cost + OVER_WIRE_COST * sum(lo < r < hi for r in self.reserved)

    def lay(self, lane, lo, hi):
        self.take(f"{lane}:{lo}")
        self.take(f"{lane}:{hi}")
        if not self.strict:
            return
        self.spans[lane].append((lo, hi))
        self.blocked |= {f"{lane}:{r}" for r in self.unit if lo < r < hi}
        self.segs.append(((WX[lane], lo), (WX[lane], hi)))

    def free_count(self, rows):
        return sum(self.free(f"{lane}:{r}") for r in rows for lane in ROWS)

    def rail_end(self, rail, leg):
        """The rail hole for a resistor whose other leg is `leg`: a few rows down the rail, so the part
        is at least RAIL_PART_MIN pitches long, in a free hole that is not on a power-wire row and does
        not make the part cross another."""
        letter, row = leg.split(":")
        row = int(row)
        dx = WX[letter] - WX[rail]
        d = 0
        while math.hypot(dx, d) < RAIL_PART_MIN:
            d += 1
        if d == 0 and any(not self.free(f"{lane}:{row}") for lane in ROWS[:ROWS.index(letter)]):
            d = 1                                  # do not lie over the holes between it and the rail
        cands = [(k, r) for k in range(COLUMNS) for r in (row + d + k, row - d - k)
                 if 1 <= r <= COLUMNS and f"{rail}:{r}" not in self.used]
        if not cands:
            raise LayoutError(f"the {rail} rail is full")
        near = [c for c in cands if c[0] <= 8] or cands

        def key(c):
            seg = ((WX[letter], row), (WX[rail], c[1]))
            return (c[1] in self.reserved, sum(_cross(seg, s) for s in self.segs), c[0])
        k, r = min(near, key=key)
        self.segs.append(((WX[letter], row), (WX[rail], r)))
        return self.take(f"{rail}:{r}")


def _assign_pins(measured, rails, node_col, with_socket, S, top, esp_row, gnd_row):
    """Which ESP32 pin measures what: try every way to hand out the six measuring pins and keep the one
    whose wires cross the fewest other wires, then never run the + rail wire through a node's row, then
    the shortest total length. Returns ({pin: node}, socket pin or None, {pin: start hole})."""
    from .adc import ADC1_PINS
    targets = [("rail", n) for n in measured if n in rails] + [("node", n) for n in measured if n not in rails]
    if with_socket:
        targets.append(("socket", None))
    fixed_segs = [((WX["f"], top), (WX["+"], top)), ((WX["f"], gnd_row), (WX["-"], gnd_row))]

    def end_of(pin, kind, n):
        if kind == "rail":
            return (WX[rails[n]], esp_row[pin])            # straight across the pin's own row
        return (WX["e"], node_col[n] if kind == "node" else S)

    def evaluate(ps, starts):
        segs = list(fixed_segs)
        length = 0.0
        for pin, (kind, n), st in zip(ps, targets, starts):
            a, b = (WX[st], esp_row[pin]), end_of(pin, kind, n)
            segs.append((a, b))
            length += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        crossings = sum(_cross(s1, s2) for i, s1 in enumerate(segs) for s2 in segs[i + 1:])
        through_nodes = sum(esp_row[pin] in node_col.values() for pin, (k, _) in zip(ps, targets) if k == "rail")
        return (crossings, through_nodes, round(length, 3))

    # Pins first, every wire leaving from hole f; then, for the best few pin choices, let each wire
    # move its start along its row (f-i) if that removes a crossing.
    ranked = sorted(permutations(ADC1_PINS, len(targets)),
                    key=lambda ps: evaluate(ps, ["f"] * len(targets)))[:12]
    best_pins, best_starts, best_cost = None, None, None
    for ps in ranked:
        starts = ["f"] * len(targets)
        cost = evaluate(ps, starts)
        improved = True
        while improved:                                     # move one wire's start at a time
            improved = False
            for i in range(len(targets)):
                for st in START_HOLES:
                    trial = starts[:i] + [st] + starts[i + 1:]
                    c = evaluate(ps, trial)
                    if c[:2] < cost[:2] or (c[:2] == cost[:2] and st == "f" and starts[i] != "f"):
                        starts, cost, improved = trial, c, True
        if best_cost is None or cost < best_cost:
            best_pins, best_starts, best_cost = ps, starts, cost
    start_of, pin_of, socket_pin = {}, {}, None
    for pin, (kind, n), st in zip(best_pins or (), targets, best_starts or ()):
        start_of[pin] = st
        if kind == "socket":
            socket_pin = pin
        else:
            pin_of[n] = pin
    return {pin_of[n]: n for n in measured}, socket_pin, start_of


def _check_rows(node_col, reserved):
    for n, r in node_col.items():
        if not 1 <= r <= COLUMNS:
            raise LayoutError(f"node {n.upper()} would be at row {r}, off the board")
        if r in reserved:
            raise LayoutError(f"row {r} is where {reserved[r]} goes; pick another row for node {n.upper()}")
    rows_sorted = sorted(node_col.items(), key=lambda x: x[1])
    for (n1, r1), (n2, r2) in zip(rows_sorted, rows_sorted[1:]):
        if r2 - r1 < 2:
            raise LayoutError(f"nodes {n1.upper()} and {n2.upper()} are only {r2 - r1} row apart; "
                              f"leave at least one empty row between nodes")


def make_layout(circuit, pins, socket_pin=None, place=None):
    """place (optional) moves things by hand: {"esp_row": row of the ESP32's 3V3 pin,
    "nodes": {node: row}, "socket_row": row}. Anything not given is placed automatically.
    A move that cannot work raises LayoutError with the reason."""
    place = place or {}
    sources = [p for p in circuit.parts if p.kind == "V"]
    if len(sources) != 1 or sources[0].b != GROUND:
        raise LayoutError("the layout needs exactly one source, from the supply node to gnd (the ESP32 3.3 V)")
    supply = sources[0].a
    rails = {supply: "+", GROUND: "-"}

    first_seen = []
    for p in circuit.parts:
        for n in (p.a, p.b):
            if n not in rails and n not in first_seen:
                first_seen.append(n)
    links = [(p.a, p.b) for p in circuit.parts if p.kind == "R" and p.a not in rails and p.b not in rails]
    order = list(_order_nodes(first_seen, links))
    top = int(place.get("esp_row", 1))
    if not 1 <= top <= COLUMNS - ESP_PINS + 1:
        raise LayoutError(f"the ESP32 needs {ESP_PINS} rows: its 3V3 pin can go from row 1 to row {COLUMNS - ESP_PINS + 1}")
    ESP_ROW = esp_rows(top)
    ESP_GND_ROW = top + ESP_PINS - 1
    reserved = {top: "the ESP32's 3V3 wire", ESP_GND_ROW: "the ESP32's GND wire"}
    hand_socket = "socket_row" in place
    if hand_socket:
        S = int(place["socket_row"])
        if socket_pin and not 1 <= S <= COLUMNS - 1:
            raise LayoutError(f"the test socket needs rows {S} and {S + 1}, off the board")
        if socket_pin and (S in reserved or S + 1 in reserved):
            raise LayoutError(f"row {S if S in reserved else S + 1} is where {reserved.get(S) or reserved.get(S + 1)} goes; "
                              f"pick another row for the test socket")
        if socket_pin:
            reserved[S] = "the test socket"
            reserved[S + 1] = "the test socket"

    # Hand-placed rows win; the rest are spread down the board, 6 rows apart when every resistor joins
    # neighbours, else 4 (a resistor skipping a node then spans 8). If the roomy layout cannot be filled,
    # try 4 apart, then the old squeeze between the ESP32's 3V3 and GND rows.
    fixed = {n: int(r) for n, r in (place.get("nodes") or {}).items() if n in order}
    _check_rows(fixed, reserved)
    auto = [n for n in order if n not in fixed]
    pos = {n: i for i, n in enumerate(order)}
    spacing = NODE_SPACING if all(abs(pos[a] - pos[b]) <= 1 for a, b in links) else TIGHT_SPACING
    attempts = ([(True, spacing, True)] + ([(True, TIGHT_SPACING, True)] if spacing > TIGHT_SPACING else [])
                + [(False, NODE_SPACING, True), (False, NODE_SPACING, False)])

    measured = list(pins.values())
    need = {n: 0 for n in order}  # legs touching each node, plus its probe wire
    for p in circuit.parts:
        for n in (p.a, p.b):
            if n in need:
                need[n] += 1
    for n in measured:
        if n in need:
            need[n] += 1
    resistors = links

    def fill(node_col, rows_of, helper_of, pin_memo, strict, total):
        res = dict(reserved)
        unit = {r for rs in rows_of.values() for r in rs}
        if hand_socket:
            S = int(place["socket_row"])
        else:                                   # below the circuit, which is placed first
            S = _socket_row(unit, ESP_GND_ROW, reserved)
            if S is None:
                if socket_pin:
                    raise LayoutError("no two free rows are left for the test socket")
                S = SOCKET_ROW
        if socket_pin:
            res[S] = res[S + 1] = "the test socket"
        if S not in pin_memo:
            pin_memo[S] = _assign_pins(measured, rails, node_col, bool(socket_pin), S, top, ESP_ROW, ESP_GND_ROW)
        fpins, spin, start_of = pin_memo[S]
        keep = {r: sum(1 for p in circuit.parts if p.kind == "R" and (p.a in rails) != (p.b in rails)
                       and n in (p.a, p.b)) for n in order for r in rows_of[n][:1]}
        board = _Board(unit, res, keep, strict)

        # Power: short wires from the ESP32's 3V3 and GND pins straight across empty rows to the rails.
        power = {"3v3": board.take(f"+:{top}"), "gnd": board.take(f"-:{ESP_GND_ROW}")}
        wires = [{"kind": "3v3", "from": f"f:{ESP_ROW['3V3']}", "to": power["3v3"]},
                 {"kind": "gnd", "from": f"f:{ESP_GND_ROW}", "to": power["gnd"]}]

        # Resistor test socket: + rail -> reference resistor -> row S (watched by its pin); the resistor
        # under test goes from row S to the - rail.
        socket = None
        if spin:
            socket = {"pin": spin, "column": S, "reference_ohms": REFERENCE_OHMS,
                      "reference": [board.take(f"+:{S}"), board.take(f"a:{S}")], "probe": board.take(f"e:{S}"),
                      "test": [board.take(f"c:{S}"), board.take(f"-:{S + 1}")]}
            wires.append({"kind": "probe", "pin": spin, "from": f"{start_of.get(spin, 'f')}:{ESP_ROW[spin]}",
                          "to": socket["probe"]})

        # Measuring wires: into hole e of each node's own row, or across an empty row into a rail.
        # Taken before the parts so they always get these holes.
        probes = {}
        for pin, node in fpins.items():
            probes[pin] = board.rail_hole(rails[node], ESP_ROW[pin]) if node in rails else board.take(f"e:{node_col[node]}")
            wires.append({"kind": "probe", "pin": pin, "from": f"{start_of.get(pin, 'f')}:{ESP_ROW[pin]}", "to": probes[pin]})

        # Jumpers (a node's second row) and resistors between two nodes: each is an interval of rows in one
        # lane. Shortest first; each takes the cheapest lane where it overlaps nothing and lies over no
        # hole that is in use. If a part finds no lane, the ones before it move (a small search).
        items = [(1, 0, i, "J", [(rows_of[n][0], r)], n, None) for i, (n, r) in
                 enumerate((n, r) for n in order for r in rows_of[n][1:])]
        for i, p in enumerate(circuit.parts):
            if p.kind == "R" and p.a not in rails and p.b not in rails:
                mine = {p.a, p.b}
                ok = lambda n, r: helper_of.get((n, r), mine) == mine      # a helper row serves one resistor only
                pairs = [(x, y) for x in rows_of[p.a] for y in rows_of[p.b] if ok(p.a, x) and ok(p.b, y)]
                items.append((min(abs(x - y) for x, y in pairs), 1, i, "R", pairs, None, p))
        items.sort(key=lambda it: it[:3])
        rail_legs = {n: 0 for n in order}           # holes the resistors to a rail will still need
        for p in circuit.parts:
            if p.kind == "R" and (p.a in rails) != (p.b in rails):
                rail_legs[p.b if p.a in rails else p.a] += 1
        later = [{n: 0 for n in order} for _ in range(len(items) + 1)]   # holes the parts from item i on still need
        for i in range(len(items) - 1, -1, -1):
            later[i] = dict(later[i + 1])
            for n in ([items[i][5]] if items[i][3] == "J" else [items[i][6].a, items[i][6].b]):
                later[i][n] += 1

        def candidates(it):
            _, _, _, kind, pairs, node, p = it
            found = []
            for x, y in pairs:
                lo, hi = min(x, y), max(x, y)
                for lane in ROWS:
                    cost = board.lane_cost(lane, lo, hi)
                    if cost is None:
                        continue
                    if not strict:                   # old rules: short ones first, hole b, c, d, a, e
                        found.append((10 * (hi - lo) + "bcdae".index(lane), ROWS.index(lane), lane, x, y, lo, hi))
                        continue
                    if kind == "R":
                        cost += (BAD_SPAN_COST * max(0, MIN_SPAN - (hi - lo), (hi - lo) - MAX_SPAN) + 0.01 * (hi - lo)
                                 + 2 * ((x != rows_of[p.a][0]) + (y != rows_of[p.b][0])))
                    found.append((cost, ROWS.index(lane), lane, x, y, lo, hi))
            return sorted(found)

        chosen, best, worst, budget = [], [None, None], [-1, []], [min(SEARCH_BUDGET, total[0]) if strict else 10 ** 6]

        def search(i, spent):
            """Try every way to place items[i:], cheapest lanes first; keep the cheapest complete one.
            worst: the deepest part that got stuck, and its nodes."""
            if best[1] is not None and (spent >= best[1] or not strict):
                return
            for n in order:                          # every node keeps holes for what is still to come
                if board.free_count(rows_of[n]) < later[i][n] + rail_legs[n]:
                    if i > worst[0]:
                        worst[:] = [i, [n]]
                    return
            if i == len(items):
                best[0], best[1] = list(chosen), spent
                return
            it = items[i]
            options = candidates(it)
            if not options and i >= worst[0]:
                nodes = [it[5]] if it[3] == "J" else sorted((it[6].a, it[6].b), key=lambda n: board.free_count(rows_of[n]))
                worst[:] = [i, nodes]
            for cost, _, lane, x, y, lo, hi in options:
                if budget[0] <= 0:
                    return
                budget[0] -= 1
                snap = board.snapshot()
                board.lay(lane, lo, hi)
                chosen.append((it, lane, x, y))
                search(i + 1, spent + cost)
                chosen.pop()
                board.restore(snap)

        start_budget = budget[0]
        search(0, 0)
        if strict:
            total[0] -= start_budget - budget[0]
        if best[0] is None:
            nodes = worst[1] or order[:1]
            raise _NoRoom(nodes, f"no lane is free for every part at node {nodes[0]}")
        jumpers, parts = [], {}
        for it, lane, x, y in best[0]:
            board.lay(lane, min(x, y), max(x, y))
            if it[3] == "J":
                jumpers.append({"node": it[5], "holes": [f"{lane}:{x}", f"{lane}:{y}"]})
            else:
                parts[it[6].name] = [f"{lane}:{x}", f"{lane}:{y}"]

        # Resistors to a rail: the free hole nearest the rail, then a few rows down the rail.
        by_node = {}
        for p in circuit.parts:
            if p.kind == "R" and (p.a in rails) != (p.b in rails):
                by_node.setdefault(p.b if p.a in rails else p.a, []).append(p)
        for n in sorted(by_node, key=lambda n: node_col[n]):
            for p in by_node[n]:
                leg = next((f"{lane}:{r}" for r in rows_of[n] for lane in ROWS if board.free(f"{lane}:{r}")), None)
                if leg is None:
                    raise _NoRoom([n], f"more things connect to node {n} than its rows have holes")
                board.take(leg)
                rail_leg = board.rail_end(rails[p.b if p.a == n else p.a], leg)
                parts[p.name] = [leg if m == n else rail_leg for m in (p.a, p.b)]

        for p in circuit.parts:
            if p.kind == "V":
                parts[p.name] = [power["3v3"], power["gnd"]]
            elif p.a in rails and p.b in rails:    # from the + rail a few rows down to the - rail, so it looks like a part
                start = max(unit, default=top + FIRST_GAP) + 2
                span = 4                           # rows between its legs: long enough to read as a resistor
                ok = lambda c: (c + span <= COLUMNS and c not in res and c not in unit
                                and board.free(f"+:{c}") and board.free(f"-:{c + span}"))
                col = next((c for c in [*range(start, COLUMNS + 1), *range(1, start)] if ok(c)), None)
                if col is None:
                    raise LayoutError("no free row for a part across the rails")
                ends = {"+": f"+:{col}", "-": f"-:{col + span}"}
                parts[p.name] = [board.take(ends[rails[n]]) for n in (p.a, p.b)]

        return best[1], {"supply": supply, "node_columns": node_col, "power": power, "probes": probes,
                "parts": {p.name: parts[p.name] for p in circuit.parts},
                "socket": socket, "pins": fpins, "wires": wires,
                "esp": {"column": ESP_COLUMN, "top": top, "rows": ESP_ROW},
                "place": {"esp_row": top, "nodes": dict(node_col), "socket_row": S},
                "extra_columns": {n: rs[1:] for n, rs in rows_of.items() if len(rs) > 1},
                "jumpers": jumpers}

    def rows_for(node_col, flips, strict):
        """Each node's rows: its own, a helper for a very long resistor, and a second row beside it if it has
        more connections than holes. flips picks the other side for the second rows (bit k for the k-th)."""
        rows_of = {n: [node_col[n]] for n in order}
        helper_of = _add_helpers(resistors, rows_of, reserved, need) if strict else {}
        demand = dict(need)                        # holes a node needs: its legs, plus one for each part passing over it
        for a, b in resistors:
            lo, hi = sorted((node_col[a], node_col[b]))
            for n in order:
                demand[n] += strict and lo < node_col[n] < hi
        k = 0
        for n in order:                            # a strip has 5 holes; each extra row costs one in two strips
            while demand[n] > 5 * len(rows_of[n]) - 2 * (len(rows_of[n]) - 1):
                cands = _extra_rows(n, rows_of, reserved)
                if not cands:
                    raise LayoutError(f"node {n} needs another row for its {need[n]} connections, but its neighbours are taken")
                rows_of[n].append(cands[(flips >> k) & 1 if len(cands) > 1 else 0])
                k += 1
        return rows_of, helper_of, k

    def settle(rows_of, helper_of, node_col, pin_memo, strict, total):
        """Fill the holes; when a node has no room, give it a second row and try again."""
        for _ in range(4 * len(order) + 4):
            try:
                return fill(node_col, rows_of, helper_of, pin_memo, strict, total)
            except _NoRoom as e:
                for n in e.nodes:
                    cands = _extra_rows(n, rows_of, reserved)
                    if cands:
                        rows_of[n].append(cands[0])
                        break
                else:
                    raise LayoutError(e.why)
        raise LayoutError("no layout found for every leg")

    def build(node_col, strict, total):
        """The cheapest layout for these node rows; where a second row could go either side, try both
        when the first choice leaves a resistor too short or too long."""
        pin_memo, best, error = {}, None, None
        rows_of, helper_of, k = rows_for(node_col, 0, strict)
        for flips in range(1 << min(k, 3)):
            if flips:
                rows_of, helper_of, _ = rows_for(node_col, flips, strict)
            try:
                cost, layout = settle(rows_of, helper_of, node_col, pin_memo, strict, total)
            except LayoutError as e:
                error = error or e
                continue
            if best is None or cost < best[0]:
                best = (cost, layout)
            if best[0] < BAD_SPAN_COST or not strict:
                break
        if best is None:
            raise error
        return best[1]

    def with_gaps(node_col):
        """A node that needs a second row gets room for it: the automatic rows below move down one,
        so a resistor from the second row to the next node is not a row too short."""
        crowded = sorted(node_col[n] for n in order if rows_for(node_col, 0, True)[0][n][1:2])
        moved = {n: r + sum(c < r for c in crowded) if n in auto else r for n, r in node_col.items()}
        return moved if crowded and moved != node_col else None

    def spans_ok(layout):
        rows = lambda name: [int(h.split(":")[1]) for h in layout["parts"][name]]
        return all(MIN_SPAN <= abs(rows(p.name)[0] - rows(p.name)[1]) <= MAX_SPAN for p in circuit.parts
                   if p.kind == "R" and p.a not in rails and p.b not in rails)

    error, tried, total = None, set(), [TOTAL_BUDGET]
    for whole_board, step, strict in attempts:
        node_col = {**fixed, **_node_rows(auto, fixed, reserved, top, ESP_GND_ROW, step, whole_board)}
        if (tuple(node_col.items()), strict) in tried:
            continue
        tried.add((tuple(node_col.items()), strict))
        try:
            _check_rows(node_col, reserved)
            first = build(node_col, strict, total)
        except LayoutError as e:
            error = e
            continue
        if strict and whole_board and not spans_ok(first):
            try:                                   # a resistor came out too short or long: open a gap
                gapped = with_gaps(node_col)
                if gapped:
                    _check_rows(gapped, reserved)
                    second = build(gapped, strict, [TOTAL_BUDGET])
                    if spans_ok(second):
                        return second
            except LayoutError:
                pass
        return first
    raise error


def placement_text(part, legs, probes, node_columns=None, layout=None):
    node_of_col = {c: n for n, c in (node_columns or {}).items()}
    for n, cols in ((layout or {}).get("extra_columns") or {}).items():
        node_of_col.update({c: n for c in cols})
    if part.kind == "V":
        lay = layout or {}
        def short(h):
            return hole_name(h, node_of_col).replace("the ", "")
        top = lay.get("esp", {}).get("top", 1)
        text = (f"Put the ESP32 on the right half: its left pin row in column j, 3V3 at row {top} (USB at "
                f"the bottom). Then the wires")
        names = {"3v3": "3V3", "gnd": "GND"}
        text += ": " + "; ".join(
            f"{names.get(w['kind']) or 'GPIO ' + w['pin']} {short(w['from'])} to {short(w['to'])}"
            for w in lay.get("wires", []))
        sock = lay.get("socket")
        if sock:
            text += (f". Test socket: the {format_ohms(sock['reference_ohms'])} reference resistor from "
                     f"{short(sock['reference'][0])} to {short(sock['reference'][1])}")
        jumpers = lay.get("jumpers") or []
        if jumpers:
            text += ". Jumper wires: " + ", ".join(
                f"{short(j['holes'][0])} to {short(j['holes'][1])} (joins node {j['node'].upper()}'s two rows)"
                for j in jumpers)
        return text
    return (f"Place {part.name} ({format_ohms(part.value)}): one leg in {hole_name(legs[0], node_of_col)}, "
            f"the other in {hole_name(legs[1], node_of_col)}")
