"""The resistor test socket: read a resistor's value before it goes on the board.

    + rail -- reference resistor (REFERENCE_OHMS) --+-- ESP32 pin
                                                    |
                                       resistor under test
                                                    |
    - rail -----------------------------------------+

The pin reads V = Vs * R / (Rref + R), so R = Rref * V / (Vs - V).
"""

import math

from .adc import ADC_MAX_VOLTS
from .circuit import format_ohms
from .faults import KIT_VALUES

SUPPLY = 3.3
EMPTY_VOLTS = ADC_MAX_VOLTS - 0.05  # nothing in the socket: the pin is pulled to 3.3 V and reads at the ceiling
STABLE_SPREAD = 0.05    # volts over the window; more means the resistor is still being pushed in
MAX_RATIO = 1.35        # further than this from every kit value: not a kit resistor (or bad contact)
BAND_COLORS = ["black", "brown", "red", "orange", "yellow", "green", "blue", "violet", "grey", "white"]


def bands(ohms):
    """Colour code of a kit resistor on a 4-band and on a 5-band (blue) one,
    e.g. 4.7k -> "yellow-violet-red, or yellow-violet-black-brown on a 5-band"."""
    def code(n):   # n digit bands, then the multiplier
        exp = int(math.floor(math.log10(ohms))) - (n - 1)
        digits = str(round(ohms / 10 ** exp))
        return "-".join([BAND_COLORS[int(d)] for d in digits] + [BAND_COLORS[exp]])
    return f"{code(2)}, or {code(3)} on a 5-band"


def measure(window, reference_ohms):
    """window: recent socket voltages. Returns {"state": empty | settling | value, ...}."""
    if not window:
        return {"state": "empty"}
    mean = sum(window) / len(window)
    if max(window) - min(window) > STABLE_SPREAD:
        return {"state": "settling", "volts": round(mean, 3)}
    if mean >= EMPTY_VOLTS:
        return {"state": "empty", "volts": round(mean, 3)}
    ohms = reference_ohms * max(mean, 0.0) / (SUPPLY - mean)
    nearest = min(KIT_VALUES, key=lambda v: abs(math.log(max(ohms, 1) / v)))
    ratio = max(ohms, 1) / nearest
    return {"state": "value", "volts": round(mean, 3), "ohms": round(ohms),
            "nearest": nearest, "kit": 1 / MAX_RATIO <= ratio <= MAX_RATIO,
            "label": format_ohms(nearest), "bands": bands(nearest)}


def verdict(m, part, suspect=False):
    """Compare a measurement with a part: the one the build needs now, or (suspect) one the last
    failed check blamed, which is then already on the board. part: Part or None."""
    if m["state"] == "empty":
        msg = (f"Put {part.name} ({format_ohms(part.value)}, {bands(part.value)}) in the test socket to check it"
               if part else "Socket empty")
        return {"result": "empty", "message": msg}
    if m["state"] == "settling":
        return {"result": "settling", "message": "Reading the resistor… hold still"}
    if not m["kit"]:
        return {"result": "wrong", "message": f"Reads about {format_ohms(m['ohms'])}: not a kit value. "
                                              f"Check that both legs touch the socket"}
    if part is None:
        return {"result": "info", "message": f"That's a {m['label']} ({m['bands']})"}
    if suspect:
        lead = f"Checking {part.name} (a suspect): "
        if m["nearest"] == part.value:
            return {"result": "ok", "message": f"{lead}✓ That's a {m['label']}, so {part.name} is fine. "
                                               f"Put it back where it was"}
        return {"result": "wrong", "message": f"{lead}✗ That's a {m['label']} ({m['bands']}), but {part.name} needs "
                                              f"{format_ohms(part.value)} ({bands(part.value)}). Replace it"}
    if m["nearest"] == part.value:
        return {"result": "ok", "message": f"✓ That's a {m['label']}: right for {part.name}. Now place it on the board"}
    return {"result": "wrong", "message": f"✗ That's a {m['label']} ({m['bands']}). {part.name} needs "
                                          f"{format_ohms(part.value)} ({bands(part.value)})"}
