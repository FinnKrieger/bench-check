# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""What the ESP32-S3's ADC can see.

The bench node is an ESP32-S3-DevKitC-1. Only ADC1 works while Wi-Fi is on; on the S3 that is
GPIO 1-10. GPIO 3 is a strapping pin and GPIO 1-2 are on the pin row that hangs off the board, so the
bench uses GPIO 4-10 (all on the ESP32's left pin row).

At 12 dB attenuation (the widest range) the S3 measures about 0-3.1 V: anything above reads as
3.1 V. Expected voltages are clipped the same way, so a 3.3 V node is expected to read 3.1 V.
"""

ADC1_PINS = ["4", "5", "6", "7", "8", "9", "10"]
ADC_MAX_VOLTS = 3.1


def as_read(v):
    """The voltage the ADC reports for a true node voltage v (None = floating)."""
    return None if v is None else min(v, ADC_MAX_VOLTS)


# ---------- sensing how hard a node is driven ----------
# The ESP32 reads every pin again with its internal pull-up, then pull-down, switched on. Through a
# pull resistor Rp a node with Thevenin voltage V and resistance R reads
#     pull-down:  V * Rp / (Rp + R)          pull-up:  V + (3.3 - V) * R / (R + Rp)
# so the shift from the plain reading tells R: zero for a node on a rail, large for a node behind a
# big resistor, rail to rail for a floating pin. That exposes wrong values where no current flows.
SUPPLY_VOLTS = 3.3
PULL_OHMS = 45000.0   # typical; the laptop measures the real value with the test socket
PULL_TOL = 0.04       # volts: how far a pull shift may be off before it counts
PULL_REL = 0.25       # plus this fraction of the expected shift (the pulls are not ideal resistors)


def pull_readings(v, r, pull_up=PULL_OHMS, pull_down=PULL_OHMS):
    """(up, down): what the ADC reports with each pull on, for true voltage v and resistance r."""
    if v is None or r is None:
        return ADC_MAX_VOLTS, 0.0
    return as_read(v + (SUPPLY_VOLTS - v) * r / (r + pull_up)), as_read(v * pull_down / (pull_down + r))


def pull_shifts(v, r, pull_up=PULL_OHMS, pull_down=PULL_OHMS):
    """(rise with the pull-up, fall with the pull-down) relative to the plain reading."""
    up, down = pull_readings(v, r, pull_up, pull_down)
    plain = as_read(v)
    return up - plain, plain - down


def pull_tolerance(expected_shift):
    return PULL_TOL + PULL_REL * abs(expected_shift)


# ---------- the pulls as they really are ----------
# Measured on a real S3, the pull-up is not a resistor: it pushes about 70 uA at any pin voltage up
# to 1.5 V, where a 45k resistor would push 67 uA at 0.3 V and only 40 uA at 1.5 V. So a node behind
# 20k rose a third more than the resistor formula says. A pull is therefore described by a curve:
# the current it pushes (pull-up) or sinks (pull-down) at each pin voltage, measured once per ESP32
# with known resistors in the test socket. A plain resistor is the two-point curve below.
class Pulls:
    def __init__(self, up, down, calibrated=False, when=None):
        """up, down: [(pin volts, amps), ...]. The pull-up pushes nothing at 3.3 V and the pull-down
        sinks nothing at 0 V, so those two points are always part of the curves."""
        self.up = sorted({(float(v), float(i)) for v, i in up if v < SUPPLY_VOLTS} | {(SUPPLY_VOLTS, 0.0)})
        self.down = sorted({(float(v), float(i)) for v, i in down if v > 0} | {(0.0, 0.0)})
        self.calibrated, self.when = calibrated, when
        self.ohms = None   # set for a plain resistor: then the formulas above answer directly

    @classmethod
    def ohmic(cls, pull_up=PULL_OHMS, pull_down=PULL_OHMS, known=(False, False)):
        """known: which pull's resistance has been measured (the laptop's one-number estimate)."""
        pulls = cls([(0.0, SUPPLY_VOLTS / pull_up)], [(SUPPLY_VOLTS, SUPPLY_VOLTS / pull_down)])
        pulls.ohms, pulls.known = (pull_up, pull_down), list(known)
        return pulls

    @classmethod
    def of(cls, x):
        """Accept a Pulls, or the older (pull-up ohms, pull-down ohms) pair."""
        return x if isinstance(x, cls) else cls.ohmic(*x) if x else cls.ohmic()

    @staticmethod
    def current(curve, v):
        """Straight lines between the measured points; flat beyond the first and last one."""
        if v <= curve[0][0]:
            return curve[0][1]
        for (v0, i0), (v1, i1) in zip(curve, curve[1:]):
            if v <= v1:
                return i0 + (i1 - i0) * (v - v0) / (v1 - v0)
        return curve[-1][1]

    def _settle(self, curve, sign, v, r, lo, hi):
        """The pin voltage where the node (v behind r) and the pull's current balance, by halving."""
        f = lambda x: v + sign * self.current(curve, x) * r - x
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
        return (lo + hi) / 2

    def readings(self, v, r):
        """(up, down): what the ADC reports with each pull on, for true voltage v and resistance r."""
        if v is None or r is None:
            return ADC_MAX_VOLTS, 0.0
        if self.ohms:
            return pull_readings(v, r, *self.ohms)
        return (as_read(self._settle(self.up, +1, v, r, v, SUPPLY_VOLTS)),
                as_read(self._settle(self.down, -1, v, r, 0.0, v)))

    def shifts(self, v, r):
        """(rise with the pull-up, fall with the pull-down) relative to the plain reading."""
        up, down = self.readings(v, r)
        return up - as_read(v), as_read(v) - down

    def ohms_behind(self, plain, rise):
        """The resistance a node must have behind it to rise this far with the pull-up on."""
        i = self.current(self.up, plain + rise)
        return rise / i if i > 0 and rise > 0 and plain + rise < ADC_MAX_VOLTS else None

    def to_json(self):
        if self.ohms:
            return {"ohms": list(self.ohms), "known": self.known}
        return {"up": self.up, "down": self.down, "calibrated": self.calibrated, "when": self.when}

    @classmethod
    def from_json(cls, d):
        if "ohms" in d:   # the file from before the curves: one resistance per pull
            return cls.ohmic(*d["ohms"], known=d.get("known", (False, False)))
        return cls(d["up"], d["down"], d.get("calibrated", False), d.get("when"))


CAL_SAMPLES = 8          # readings per resistor during a calibration
CAL_MIN_AMPS, CAL_MAX_AMPS = 3e-6, 400e-6   # a pull current outside this is a bad contact, not a pull


def pull_curves(samples, reference_ohms):
    """Turn socket readings into the two curves.

    samples: {ohms of the known resistor, or None for the empty socket: (plain, up, down) in volts}.
    With resistor x in the socket the pin sees the plain voltage behind x parallel to the reference
    (behind the reference alone when empty), so the pull's current is the shift over that resistance.
    Returns (up points, down points); readings at the ADC ceiling are left out.
    """
    up, down = [], []
    for x, (plain, u, d) in samples.items():
        vth = SUPPLY_VOLTS if x is None else plain
        rth = reference_ohms if x is None else reference_ohms * x / (reference_ohms + x)
        if x is not None and u < ADC_MAX_VOLTS - 0.1 and CAL_MIN_AMPS < (u - vth) / rth < CAL_MAX_AMPS:
            up.append((u, (u - vth) / rth))
        if 0.02 < d < ADC_MAX_VOLTS - 0.1 and CAL_MIN_AMPS < (vth - d) / rth < CAL_MAX_AMPS:
            down.append((d, (vth - d) / rth))
    return sorted(up), sorted(down)
