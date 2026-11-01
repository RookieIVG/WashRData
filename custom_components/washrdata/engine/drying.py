"""Remaining time of a dryer: only once it cools down.

Sensor drying stops when the laundry is dry, so how long a dryer runs
depends on the load, not on the program: on a real heat pump dryer one
program took 27 to 154 minutes, and a remaining time from the references was
off by half an hour. The cooling phase at the end is different: the heat
goes off, the drum keeps turning at a fraction of the drying power, and it
lasts about as long every time. So a dryer shows no remaining time while it
dries, and the references' cooling time once it cools.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from .matching import STEP_S, resample
from .model import CycleRecord

# Cooling: at most this share of the drying power ...
COOLING_SHARE = 0.4
# ... for this many minutes in a row (between heating bursts it dips for up to three).
COOLING_MINUTES = 4
# The drying power is the median of the minutes above this share of the peak.
DRYING_SHARE = 0.5


def cooling_start(watts: Sequence[float]) -> int | None:
    """First minute of the final cooling phase, None while the dryer still heats.

    The phase must last to the end of the curve: a dip followed by heating
    again is a pause, not cooling.
    """
    if not watts:
        return None
    peak = max(watts)
    drying = [w for w in watts if w >= DRYING_SHARE * peak]
    if not drying:
        return None
    limit = COOLING_SHARE * median(drying)
    start = len(watts)
    while start > 0 and watts[start - 1] <= limit:
        start -= 1
    if len(watts) - start < COOLING_MINUTES or start == 0:
        return None
    return start


def cooling_time(cycle: CycleRecord) -> float | None:
    """Seconds from the start of cooling to the end of a finished cycle."""
    watts = resample(cycle.trace, cycle.duration())
    start = cooling_start(watts)
    return None if start is None else cycle.duration() - start * STEP_S


def remaining_while_cooling(
    watts: Sequence[float], elapsed_s: float, cooling_times: Sequence[float]
) -> float | None:
    """Remaining seconds once cooling has begun, from the references' cooling times."""
    start = cooling_start(watts)
    times = list(cooling_times)
    if start is None or not times:
        return None
    return max(0.0, median(times) - (elapsed_s - start * STEP_S))
