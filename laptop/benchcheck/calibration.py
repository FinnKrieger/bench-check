# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""Raw ESP32 ADC counts -> volts.

calibration.json holds measured points [[raw, volts], ...] (ESP32 reading vs multimeter).
Between points the curve is linear, which handles the ESP32 ADC's bend near 0 V and 3.3 V.
"""

import json
from pathlib import Path

import numpy as np

DEFAULT_POINTS = [[0, 0.0], [4095, 3.1]]  # ideal S3 ADC at 12 dB until the real test (task 0.5) is done


class Calibration:
    def __init__(self, points=DEFAULT_POINTS, custom=False):
        # custom: points measured by us. Without them the laptop prefers the millivolts the
        # ESP32-S3 computes from its factory calibration.
        self.custom = custom
        pts = sorted(points)
        self.raw = np.array([p[0] for p in pts], dtype=float)
        self.volts = np.array([p[1] for p in pts], dtype=float)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text())["points"], custom=True)

    def to_volts(self, raw):
        return float(np.interp(raw, self.raw, self.volts))
