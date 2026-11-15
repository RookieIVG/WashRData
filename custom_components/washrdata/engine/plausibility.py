"""A labelled wash that does not fit the other runs of its program.

Runs of one washing machine program draw about the same energy and heat
about as long; the inflow temperature moves both by a quarter at most. A
run far outside that is most likely labelled wrongly (found on real runs:
two "Super Speed 40" heated 6 and 8 minutes against 16-24 for the other
nine, and drew 0.28 and 0.35 against 0.60-0.92 kWh; they were Super Speed
30). A wrong label spoils the references and the remaining time, so the
panel points such runs out, with the program they fit better.

Dryers are left out: sensor drying makes energy and run time depend on the
load.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

from .model import CycleRecord
from .phases import Phase, label, segments, windows

# A run fits when its energy and heating time are within this factor of the
# median of the program's other runs.
MAX_FACTOR = 1.6
# The program needs this many other runs to compare with, so that one odd run
# among them cannot move the median.
MIN_OTHERS = 3
# A program is suggested instead when it has at least this many runs.
MIN_BETTER = 2


@dataclass(frozen=True)
class Suspicion:
    program: str
    better: str | None


def heating_s(cycle: CycleRecord) -> float:
    return sum(
        s.end_s - s.start_s
        for s in segments(label(windows(cycle.trace, cycle.duration())))
        if s.phase == Phase.HEATING
    )


def _features(cycle: CycleRecord, heating: dict[str, float]) -> tuple[float, float]:
    return cycle.energy_wh, heating[cycle.id]


def _distance(f: tuple[float, float], others: list[tuple[float, float]]) -> float | None:
    """Sum of |ln ratio| against the medians; None when a feature is far off."""
    total = 0.0
    for value, ref in ((f[0], median(o[0] for o in others)), (f[1], median(o[1] for o in others))):
        if ref <= 0 and value <= 0:
            continue
        if ref <= 0 or value <= 0:
            return None
        ratio = abs(math.log(value / ref))
        if ratio > math.log(MAX_FACTOR):
            return None
        total += ratio
    return total


def suspicious(cycles: list[CycleRecord]) -> dict[str, Suspicion]:
    """Labelled, complete washes that do not fit their program, by cycle id."""
    done = [c for c in cycles if c.program and not c.interrupted and c.end is not None]
    heating = {c.id: heating_s(c) for c in done}
    by_program: dict[str, list[CycleRecord]] = {}
    for c in done:
        assert c.program is not None
        by_program.setdefault(c.program, []).append(c)
    out: dict[str, Suspicion] = {}
    for c in done:
        assert c.program is not None
        others = [_features(o, heating) for o in by_program[c.program] if o is not c]
        if len(others) < MIN_OTHERS:
            continue
        f = _features(c, heating)
        if _distance(f, others) is not None:
            continue
        fits = []
        for program, runs in by_program.items():
            if program == c.program or len(runs) < MIN_BETTER:
                continue
            if (d := _distance(f, [_features(o, heating) for o in runs])) is not None:
                fits.append((d, program))
        out[c.id] = Suspicion(c.program, min(fits)[1] if fits else None)
    return out
