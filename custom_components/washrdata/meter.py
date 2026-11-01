"""Energy of a cycle from the plug's energy meter instead of integrating the power.

A plug that reports its power seldom makes the integrated energy coarse; its
energy meter counts every watt-hour. Detection keeps using the power (a meter
that steps in 10 Wh would delay every start), but once a cycle has finished
its energy becomes the meter difference over the cycle, and its cost and the
total energy follow.
"""

from __future__ import annotations

from collections import deque

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfEnergy
from homeassistant.core import State

from .engine import CycleDetector, CycleRecord

_TO_WH = {UnitOfEnergy.WATT_HOUR: 1.0, UnitOfEnergy.KILO_WATT_HOUR: 1000.0, UnitOfEnergy.MEGA_WATT_HOUR: 1e6}
# Readings kept, enough for the longest cycle at one reading every few seconds.
KEEP_READINGS = 20_000
# A cycle whose meter energy differs more than this factor from the integrated
# one keeps the integrated energy: probably the wrong meter or a gap.
MAX_RATIO = 3.0
# Below this much integrated energy a meter that did not move is plausible.
MIN_INTEGRATED_WH = 10.0


def parse_energy(state: State | None) -> float | None:
    """Watt-hours from an energy sensor state, None when unavailable or invalid."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, UnitOfEnergy.KILO_WATT_HOUR)
    return value * _TO_WH.get(unit, 1000.0)


class EnergyMeter:
    """Readings of a meter as a monotonic count; a meter reset continues the count."""

    def __init__(self) -> None:
        self._readings: deque[tuple[float, float]] = deque(maxlen=KEEP_READINGS)
        self._offset = 0.0
        self._last_raw: float | None = None

    def add(self, ts: float, wh: float | None) -> None:
        if wh is None:
            return
        if self._last_raw is not None and wh < self._last_raw:
            self._offset += self._last_raw
        self._last_raw = wh
        self._readings.append((ts, wh + self._offset))

    def _at(self, ts: float) -> float | None:
        """The count at `ts`: the last reading at or before it."""
        value = None
        for t, wh in self._readings:
            if t > ts:
                break
            value = wh
        return value

    def energy(self, start: float, end: float) -> float | None:
        """Meter energy between two times, None when the readings do not cover them."""
        if not self._readings or self._readings[0][0] > start:
            return None
        first, last = self._at(start), self._at(end)
        if first is None or last is None:
            return None
        return last - first


def apply_meter(cycle: CycleRecord, detector: CycleDetector, meter: EnergyMeter, now: float) -> bool:
    """Replace a finished cycle's integrated energy with the meter's, if plausible.

    Readings up to `now` count: a meter reports with some delay, and the cycle
    finishes only after its end delay, when the last readings are in.
    """
    metered = meter.energy(cycle.start, now)
    integrated = cycle.energy_wh
    if metered is None or metered < 0:
        return False
    if integrated >= MIN_INTEGRATED_WH and not (1 / MAX_RATIO <= metered / integrated <= MAX_RATIO):
        return False
    if cycle.cost is not None and integrated > 0:
        cycle.cost *= metered / integrated
    detector.total_energy_wh += metered - integrated
    cycle.energy_wh = metered
    return True
