"""What a washing machine is doing right now, from its power alone.

A wash looks the same on every machine of this kind (found on a real
washing machine, tests/fixtures/washer_super_speed.json): water comes in
(a few watts), the heater runs (about 2 kW), the drum turns back and forth
(80-150 W, restless), then spins in short ramps up to a few hundred watts
between rinses, and ends with a longer final spin. The drain pump shows as a
flat ~30 W only when the drum stands still, so it is not a phase of its own.

Windows of 20 seconds are labelled from their mean power and their context:
the heater is unmistakable; a spin is a steady rise to well above the
drum's level; drum turning before the first spin is washing, after it
rinsing. The phases also give anchors for the remaining time: the end of
heating, the first spin and the final spin happen at the same distance
from the end on every run of a program.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from .drying import cooling_start
from .matching import STEP_S, resample
from .model import CycleRecord

WINDOW_S = 20.0
HEATING_W = 1000.0
STILL_W = 20.0
# A spin: at least this many windows in a row above this multiple of the drum level.
SPIN_WINDOWS = 3
SPIN_LEVEL = 1.4
# ... after a dip below this share of the drum level within this many windows (pumping out),
DRAIN_LEVEL = 0.85
DRAIN_WINDOWS = 8
# ... unless it lasts this long (the final spin).
LONG_SPIN_WINDOWS = 10
# Washing lasts at least this many windows after the heating before any spin.
MIN_WASH_WINDOWS = 30
# Drum level when none has been seen yet.
DEFAULT_DRUM_W = 100.0


class Phase(StrEnum):
    FILLING = "filling"
    HEATING = "heating"
    WASHING = "washing"
    RINSING = "rinsing"
    SPINNING = "spinning"
    # Dryers with sensor drying.
    DRYING = "drying"
    COOLING = "cooling"


@dataclass(frozen=True)
class Segment:
    phase: Phase
    start_s: float
    end_s: float


def windows(trace: Sequence[tuple[float, float]], duration_s: float) -> list[float]:
    """Mean power per 20 s window."""
    return resample(trace, duration_s, WINDOW_S)


def label(means: Sequence[float]) -> list[Phase]:
    """A phase per window. Works on a growing curve: earlier labels never
    depend on later windows except that a spin is found once it has risen."""
    raw: list[str] = []
    for m in means:
        if m >= HEATING_W:
            raw.append("H")
        elif m < STILL_W:
            raw.append(".")
        else:
            raw.append("D")
    drum = [m for m, r in zip(means, raw, strict=True) if r == "D"]
    level = median(drum) if drum else DEFAULT_DRUM_W
    # A spin holds the drum well above its washing level for a minute or more;
    # it climbs in steps and wobbles, so no steady rise is required. Spins come
    # only after a good while of washing past the heating. A long one is the
    # final spin; a short one follows pumping out, which shows as a dip with
    # the pump still running (a wash that pauses drops to almost nothing).
    spin = [False] * len(means)
    high = [SPIN_LEVEL * level < m < HEATING_W for m in means]
    heat_end = max((i for i, r in enumerate(raw) if r == "H"), default=-1)
    i = 0
    while i < len(means):
        if not high[i]:
            i += 1
            continue
        k = i
        while k < len(means) and high[k]:
            k += 1
        before = means[max(0, i - DRAIN_WINDOWS) : i]
        drained = (
            any(STILL_W <= m < DRAIN_LEVEL * level for m in before) and min(before, default=0) >= STILL_W
        )
        washed = i - heat_end > MIN_WASH_WINDOWS
        if washed and k - i >= SPIN_WINDOWS and (drained or k - i >= LONG_SPIN_WINDOWS):
            for j in range(i, k):
                spin[j] = True
        i = k

    phases: list[Phase] = []
    heated = spun = False
    for r, s in zip(raw, spin, strict=True):
        if r == "H":
            heated = True
            phases.append(Phase.HEATING)
        elif s:
            spun = True
            phases.append(Phase.SPINNING)
        elif r == "D" and spun:
            phases.append(Phase.RINSING)
        elif r == "D" and (heated or Phase.WASHING in phases[-3:]):
            phases.append(Phase.WASHING)
        elif spun:
            phases.append(Phase.RINSING)
        elif heated:
            phases.append(Phase.WASHING)
        else:
            phases.append(Phase.FILLING)
    return phases


def segments(phases: Sequence[Phase]) -> list[Segment]:
    out: list[Segment] = []
    for i, p in enumerate(phases):
        if out and out[-1].phase == p:
            out[-1] = Segment(p, out[-1].start_s, (i + 1) * WINDOW_S)
        else:
            out.append(Segment(p, i * WINDOW_S, (i + 1) * WINDOW_S))
    return out


@dataclass(frozen=True)
class Anchors:
    """Offsets in seconds from the start; None when not (yet) seen."""

    heating_end: float | None
    first_spin: float | None
    spins: int
    last_spin: float | None


def anchors(phases: Sequence[Phase]) -> Anchors:
    segs = segments(phases)
    heat = [s for s in segs if s.phase == Phase.HEATING]
    spins = [s for s in segs if s.phase == Phase.SPINNING]
    # Heating that resumes after the drum has turned a bit is the same heating.
    heating_end = (
        heat[-1].end_s
        if heat and not any(s.phase == Phase.SPINNING for s in segs[: segs.index(heat[-1])])
        else None
    )
    return Anchors(
        heating_end=heating_end,
        first_spin=spins[0].start_s if spins else None,
        spins=len(spins),
        last_spin=spins[-1].start_s if spins else None,
    )


def heating_to_end(cycle: CycleRecord) -> float | None:
    """Seconds from the end of heating to the end of a finished wash."""
    end = anchors(label(windows(cycle.trace, cycle.duration()))).heating_end
    return None if end is None else cycle.duration() - end


def remaining_after_heating(
    phases: Sequence[Phase], elapsed_s: float, times: Sequence[float]
) -> float | None:
    """Remaining seconds once heating is over, from the references' time from heating to end.

    On real runs this is off by about 3 minutes against about 6 for the
    curve alignment: what comes after the heating barely varies within a
    program, while the heating itself depends on the inflow temperature.
    """
    if not phases or phases[-1] == Phase.HEATING or not times:
        return None
    end = anchors(phases).heating_end
    if end is None:
        return None
    return max(0.0, median(times) - (elapsed_s - end))


def current_phase(cycle: CycleRecord, elapsed_s: float, sensor_drying: bool) -> Phase | None:
    """The phase of a running cycle: washer phases, or drying / cooling for a dryer."""
    segs = cycle_segments(cycle, sensor_drying, cycle.start + elapsed_s)
    return segs[-1].phase if segs else None


def cycle_segments(cycle: CycleRecord, sensor_drying: bool, now: float | None = None) -> list[Segment]:
    """Phase segments of a cycle; a dryer has drying and then cooling (engine/drying.py)."""
    duration = cycle.duration(now)
    if not sensor_drying:
        return segments(label(windows(cycle.trace, duration)))
    watts = resample(cycle.trace, duration)
    if not watts:
        return []
    start = cooling_start(watts)
    if start is None:
        return [Segment(Phase.DRYING, 0.0, duration)]
    return [Segment(Phase.DRYING, 0.0, start * STEP_S), Segment(Phase.COOLING, start * STEP_S, duration)]
