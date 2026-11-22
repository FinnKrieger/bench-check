"""Decide pass / fail / unsure for one build step, and find the most likely mistake."""

import math

from .adc import Pulls, pull_tolerance
from .circuit import format_ohms

STABLE_SPREAD = 0.10  # volts; max - min over the window above this means "unstable"
MAX_MATCH_DISTANCE = 0.30  # volts; a fault further than this is not a believable match
# A mistake only counts as reliably visible if it moves a test point this many tolerances away;
# one just past the tolerance can pass or fail depending on noise.
RELIABLE_MARGIN = 1.25
FLOAT_MISMATCH = 1.0  # volts; how badly "connected" vs "unconnected" misses when matching a mistake
MAX_SUSPECTS = 3
# Two matches closer than this (2 mV, below the ADC's noise) are the same reading for practical
# purposes; only then does "the part placed this step" decide which one is listed first.
TIE_WINDOW = 0.002


def _hole(h):
    """A layout hole id ("c:4", "-:7") as the picture labels it ("4c", "- rail, row 7")."""
    k, r = h.split(":")
    return f"{k.replace('-', '−')} rail, row {r}" if k in "+-" else f"{r}{k}"


def compose_message(result):
    """The one-line text: what is off, then the first suspect still open."""
    problem, text = result["problem"], result["problem"]["headline"]
    found = next((s for s in result["suspects"] if s.get("found")), None)
    if found:
        return text
    open_ = [s for s in result["suspects"] if not s.get("ruled_out")]
    if open_:
        text += f". Most likely: {open_[0]['label']}"
    elif result["suspects"]:
        text += ". The suspects so far are ruled out"
    elif problem["kind"] in ("voltage", "floating", "extra", "resistance"):
        text += ". No single simulated mistake matches"
    if problem["kind"] == "resistance":
        text += ". This test is rough and often a false alarm: if the board matches the picture, press Skip step"
    if result.get("more_same"):
        text += f" ({result['more_same']} other mistakes would read the same)"
    return text


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class Checker:
    def __init__(self, table, pins, tolerance, status=None):
        self.table = table
        # status[step][part]: "checked", "connected" (value not confirmed) or "unchecked"
        # (placement not confirmed), from testpoints.part_status
        self.status = status or {}
        self.unverified = {s: {p for p, st in d.items() if st != "checked"} for s, d in self.status.items()}
        # parts whose value the resistor test socket confirmed (filled in by the laptop node)
        self.value_confirmed = set()
        self.pins = pins  # {"32": "a", ...}
        self.node_pin = {n: p for p, n in pins.items()}
        self.tolerance = tolerance
        self.part_holes = {}  # {part: (hole, hole)} from the layout, set by the laptop node

    def check(self, step, window, float_aware=False, pulls=None, pull_model=None):
        """window: recent readings, each {node: volts}, with None for a pin the ESP32 found floating.

        float_aware: the readings come with the ESP32's pull-up / pull-down test, so "floating" is
        known for certain. Then every test point is checked, including the ones that should still be
        unconnected at this step. Without it (older firmware), a floating pin can only show up as an
        unstable reading and unconnected nodes are skipped.

        pulls: {node: (rise with the pull-up, fall with the pull-down)} in volts, measured. They show
        how hard each node is driven, which catches a wrong value even where no current flows yet.
        pull_model: how this ESP32's pulls behave (adc.Pulls), as calibrated with the test socket.
        """
        row = self.table[step]
        pulls = pulls or {}
        model = Pulls.of(pull_model)

        def shifts(volts, ohms, n):
            return model.shifts(volts[n], ohms[n])

        def drive_ok(n, want):
            """Do node n's measured pull shifts match the expected ones?"""
            if n not in pulls or pulls[n] is None:
                return True
            return all(abs(got - exp) <= pull_tolerance(exp) for got, exp in zip(pulls[n], want))

        expected = self.table[step]["correct"]
        nodes = list(self.pins.values())
        if not window:
            return self._result(step, "unsure", "No readings from the ESP32 yet", {}, [],
                                {"kind": "no_data", "nodes": [], "headline": "No readings from the ESP32 yet"})

        floating, mean, spread = {}, {}, {}
        for n in nodes:
            vals = [r[n] for r in window if r[n] is not None]
            floating[n] = float_aware and len(vals) < len(window) / 2
            mean[n] = sum(vals) / len(vals) if vals else None
            spread[n] = max(vals) - min(vals) if vals else 0.0
        checked = [n for n in nodes if float_aware or expected[n] is not None]

        def matches(n, want):
            """Does node n read like `want` (a voltage, or None for unconnected)?"""
            if want is None:
                return floating[n]
            return not floating[n] and mean[n] is not None and abs(mean[n] - want) <= self.tolerance

        points = {}
        for n in nodes:
            exp = expected[n]
            ok = matches(n, exp) if n in checked else None
            drive = None
            if ok and exp is not None and "ohms" in row:
                drive = drive_ok(n, shifts(row["volts"], row["ohms"], n))
                ok = ok and drive
            points[n] = {"pin": self.node_pin[n],
                         "expected": None if exp is None else round(exp, 3),
                         "measured": None if floating[n] or mean[n] is None else round(mean[n], 3),
                         "floating": bool(floating[n]),
                         "drive_ok": drive,   # False: right voltage, but driven too weakly or too strongly
                         "ok": ok}

        # connected in some readings and floating in others: a contact that comes and goes
        flicker = [n for n in checked
                   if float_aware and 2 <= sum(r[n] is None for r in window) <= len(window) - 2]
        if flicker:
            return self._result(step, "unsure", f"The contact at {_join(flicker)} comes and goes: a loose leg or wire",
                                points, [], {"kind": "flicker", "nodes": flicker, "headline":
                                             f"The contact at {_join([n.upper() for n in flicker])} comes and goes"})
        unstable = [n for n in checked if expected[n] is not None and not floating[n] and spread[n] > STABLE_SPREAD]
        if unstable:
            names = ", ".join(unstable)
            return self._result(step, "unsure",
                                f"Unstable reading at {names}: a loose wire" +
                                ("" if float_aware else ", or nothing connected there"), points, [],
                                {"kind": "unstable", "nodes": unstable, "headline":
                                 f"{'Node' if len(unstable) == 1 else 'Nodes'} {_join([n.upper() for n in unstable])} "
                                 f"{'reads' if len(unstable) == 1 else 'read'} unsteadily"})

        if all(points[n]["ok"] for n in checked):
            return self._result(step, "pass", self._pass_message(step), points, [])

        ranked = []
        for fault, sig in self.table[step]["faults"]:
            if fault.kind == "value" and fault.part in self.value_confirmed:
                continue  # the socket measured this resistor: it is not the wrong value
            terms = []
            for n in checked:
                if float_aware and (sig[n] is None) != floating[n]:
                    terms.append(FLOAT_MISMATCH ** 2)          # connected vs unconnected: a clear miss
                elif sig[n] is None:
                    terms.append(0.0 if float_aware else self.tolerance ** 2)
                else:
                    terms.append((mean[n] - sig[n]) ** 2)
                    if pulls.get(n) is not None and not floating[n] and "drive" in row:
                        fv, fo = row["drive"][fault.label]
                        terms += [(got - exp) ** 2 for got, exp in zip(pulls[n], shifts(fv, fo, n))]
            ranked.append((math.sqrt(sum(terms) / len(terms)), fault))
        # Distance decides. Only mistakes whose distances are within TIE_WINDOW of each other read the
        # same, and among those the order worth checking is: the part placed in this step, then parts
        # no earlier step could verify.
        new_part = self.table[step]["new_part"]
        unverified = set(self.unverified.get(step - 1, ()))
        rank = lambda f: 0 if f.part == new_part else 1 if f.part in unverified else 2
        ranked = self._order(ranked, rank)
        best = ranked[0][0] if ranked else float("inf")
        # Mistakes within half a tolerance of the best match cannot be told apart reliably (the
        # fuzz test counts a planted mistake as found when it is in this group).
        tied = [r for r in ranked if r[0] <= best + self.tolerance / 2]
        # Show one mistake per part before a second one of the same part, so a short list still
        # names every part that could be at fault.
        rounds, seen = [], {}
        for r in tied:
            k = seen[r[1].part] = seen.get(r[1].part, -1) + 1
            rounds.append((k, len(rounds), r))
        tied = [r for _, _, r in sorted(rounds, key=lambda x: (x[0], x[1]))]
        rest = [r for r in ranked if r[0] > best + self.tolerance / 2]

        wrong = [n for n in checked if not points[n]["ok"]]
        weak = [n for n in wrong if points[n]["drive_ok"] is False]
        # Every voltage is right and only the pull test disagrees: say exactly that, with the closest
        # matches first. The pull test is rougher than the voltage check, so this is "unsure", not "fail".
        if weak and all(points[n]["ok"] or n in weak for n in checked):
            for n in weak:
                behind = model.ohms_behind(mean[n], pulls[n][0])
                points[n]["behind_ohms"] = behind and round(behind)
                points[n]["behind_expected"] = round(row["ohms"][n])
            close = [(d, f) for d, f in ranked[:MAX_SUSPECTS] if d <= MAX_MATCH_DISTANCE]
            problem = {"kind": "resistance", "nodes": weak, "headline": self._drive_headline(weak, points)}
            result = self._result(step, "unsure", "", points, [], problem, [self._suspect(f) for _, f in close])
            result["likely_faults"] = [self._likely(d, f) for d, f in close]
            result["message"] = compose_message(result)
            return result

        loose = [n for n in wrong if floating[n]]
        extra = [n for n in wrong if expected[n] is None and not floating[n]]
        volts = [n for n in wrong if n not in loose and n not in extra and n not in weak]
        kind, nodes = next((k, ns) for k, ns in (("voltage", volts), ("floating", loose), ("extra", extra)) if ns)
        problem = {"kind": kind, "nodes": nodes, "headline": self._headline(kind, nodes, points, len(wrong))}

        if step == 1 and len(extra) >= 2:
            # Nothing is placed yet, so several connected nodes mean the last build is still on the board.
            problem["headline"] = f"Nodes {_join([n.upper() for n in extra])} are connected, but should be empty"
            result = self._result(step, "fail", "", points, [], problem, [])
            result["message"] = ("Parts from an earlier build seem to be on the board. Clear the board, or use "
                                 "Skip step to jump to the step the board is at")
            result["same_reading"] = []
            return result

        shown = [(d, f) for d, f in (tied + rest)[:MAX_SUSPECTS] if d <= MAX_MATCH_DISTANCE]
        suspects = [self._suspect(f) for _, f in shown]
        result = self._result(step, "fail", "", points, [self._likely(d, f) for d, f in shown], problem, suspects)
        if shown:
            # more mistakes that read the same as the last one shown
            last = shown[-1][0]
            others = [r for r in (tied + rest)[len(shown):] if r[0] <= min(last + TIE_WINDOW, MAX_MATCH_DISTANCE)]
            if others:
                result["more_same"] = len(others)
        result["message"] = compose_message(result)
        result["same_reading"] = [f.label for d, f in tied if d <= MAX_MATCH_DISTANCE]
        return result

    @staticmethod
    def _order(ranked, rank):
        """Closest first. Matches within TIE_WINDOW of the run's first one are put in `rank` order."""
        out, run = [], []
        key = lambda r: (rank(r[1]), r[0])
        for r in sorted(ranked, key=lambda r: r[0]):
            if run and r[0] - run[0][0] > TIE_WINDOW:
                out += sorted(run, key=key)
                run = []
            run.append(r)
        return out + sorted(run, key=key)

    @staticmethod
    def _likely(d, fault):
        return {"label": fault.label, "part": fault.part, "distance": round(d, 3)}

    def _suspect(self, fault):
        """One mistake to try, with a one-line instruction for testing it."""
        part = fault.part
        if fault.kind == "value":
            action = f"Read {part}'s colour bands, or pull it and put it in the test socket"
        elif fault.kind == "open":
            action = ("Push the ESP32's 3.3 V wire firmly into the + rail" if part.startswith("V")
                      else f"Press both legs of {part} firmly into the board")
        elif fault.kind == "ground":
            action = "Push the ESP32's GND wire firmly into the − rail"
        elif fault.kind == "short":
            action = f"Check that {part}'s two legs are in different rows"
        else:  # moved
            holes = self.part_holes.get(part)
            action = (f"Check {part}'s legs against the picture: one should be in {_hole(holes[0])}, "
                      f"the other in {_hole(holes[1])}" if holes else f"Check {part}'s legs against the picture")
        s = {"label": fault.label, "part": part, "kind": fault.kind, "action": action}
        if fault.kind == "value":
            s["wrong_ohms"] = fault.new_value  # the laptop compares a socket reading with this
        return s

    @staticmethod
    def _headline(kind, nodes, points, n_wrong):
        def one(n):
            p, name = points[n], f"node {n.upper()}"
            got = "" if p["measured"] is None else f"{abs(round(p['measured'], 2)):.2f} V"
            if kind == "voltage":
                return f"{name} reads {got}, expected {p['expected']:.2f} V"
            if kind == "floating":
                return f"{name} is not connected, expected {p['expected']:.2f} V"
            return f"{name} reads {got}, but nothing should be connected there yet"
        text = "; ".join(one(n) for n in nodes[:2])
        if n_wrong > min(len(nodes), 2):
            text += f"; {n_wrong - min(len(nodes), 2)} more node{'s are' if n_wrong - min(len(nodes), 2) > 1 else ' is'} off too"
        return text[0].upper() + text[1:]

    @staticmethod
    def _drive_headline(weak, points):
        def one(n):
            got, exp = points[n]["behind_ohms"], points[n]["behind_expected"]
            if got is None:
                return f"node {n.upper()} does not act like it has {format_ohms(exp)} behind it"
            return (f"node {n.upper()} acts like it has about {format_ohms(float(f'{got:.2g}'))} behind it, "
                    f"where {format_ohms(float(f'{exp:.2g}'))} is expected")
        return "Every voltage is right, but " + "; ".join(one(n) for n in weak)

    def _pass_message(self, step):
        """A pass only says what the readings can actually confirm."""
        st = self.status.get(step, {})
        last = step == len(self.table)
        new = self.table[step]["new_part"]
        unchecked = sorted(p for p, s in st.items() if s == "unchecked")
        connected = sorted(p for p, s in st.items()
                           if (s == "connected" and p not in self.value_confirmed)
                           or (s == "value" and p not in self.value_confirmed))
        socket_only = sorted(p for p, s in st.items() if s == "connected" and p in self.value_confirmed)
        notes = []
        if new in unchecked and st.get(new) and new.startswith("V"):
            notes.append("The GND wire cannot be checked until a part connects to the − rail")
            unchecked.remove(new)
        elif new in unchecked:
            notes.append(f"Nothing the ESP32 measures can see {new} yet, so this step could not check it "
                         f"(not even where it is plugged in)" +
                         ("" if last else ". It gets checked once the parts around it are in"))
            unchecked.remove(new)
        unchecked = [p for p in unchecked if not p.startswith("V")]
        if unchecked:
            notes.append(f"{_join(unchecked)}: still not confirmed to be in place")
        if socket_only:
            notes.append(f"{_join(socket_only)}: value checked in the test socket, but a short (both legs in one "
                         f"row) would " + ("not show with these test points" if last else "only show at a later step"))
        if connected:
            v = dict(self.table[step].get("volts", self.table[step]["correct"]), gnd=0.0)
            def carries_current(p):
                a, b = self.table[step]["ends"][p]
                return v.get(a) is not None and v.get(b) is not None and abs(v[a] - v[b]) > 1e-6
            idle = [p for p in connected if not carries_current(p)]
            busy = [p for p in connected if carries_current(p)]
            if idle:
                one = len(idle) == 1
                notes.append(f"{_join(idle)} {'is' if one else 'are'} in place, but no current flows through "
                             f"{'it' if one else 'them'} yet, so {'its value' if one else 'their values'} "
                             + ("cannot be ruled out with these test points" if last
                                else ("gets" if one else "get") + " checked at a later step"))
            if busy:
                notes.append(f"Some mistakes on {_join(busy)} read the same as the correct circuit on these "
                             f"test points, so they cannot be ruled out" + ("" if last else " yet"))
        head = "Step is wired correctly" if not st.get(new) or st[new] in ("checked", "value") \
            or new in connected or new in socket_only \
            else "No problem found"
        return ". ".join([head] + notes)

    @staticmethod
    def _result(step, result, message, points, likely, problem=None, suspects=()):
        led = {"pass": "green", "fail": "red", "unsure": "yellow"}[result]
        return {"step": step, "result": result, "led": led, "message": message,
                "likely_faults": likely, "points": points, "problem": problem, "suspects": list(suspects)}
