"""Anti-crease drum turns at the end of a dryer cycle, recognised from the trace.

Some dryers turn the drum for a few seconds about every half minute once
drying and cooling are over. The quiet spells between the turns are far
shorter than any end delay, so without this the turns count as part of the
cycle and "finished" comes half an hour late (found on a real heat pump
dryer, tests/fixtures/dryer_anti_crease.json).
"""

from __future__ import annotations

from collections.abc import Sequence

from .model import CycleRecord, DetectorConfig
from .split import pauses

# Quiet spells shorter than this do not split an activity (drum reversals).
MIN_GAP_S = 10.0
# An activity up to this long is a drum turn, longer ones are drying or cooling.
TURN_MAX_S = 60.0
# This many turns in a row after the last long activity mean the cycle is over.
MIN_TURNS = 6

Trace = Sequence[tuple[float, float]]


def _runs(trace: Trace, config: DetectorConfig, until: float) -> list[tuple[float, float]]:
    """Active stretches (start, end) with the detector's hysteresis; short quiet spells are bridged."""
    runs: list[tuple[float, float]] = []
    active = False
    for i, (t, w) in enumerate(trace):
        if w >= config.start_power_w:
            active = True
        elif w < config.stop_power_w:
            active = False
        if not active:
            continue
        end = trace[i + 1][0] if i + 1 < len(trace) else until
        if runs and t - runs[-1][1] < MIN_GAP_S:
            runs[-1] = (runs[-1][0], max(runs[-1][1], end))
        else:
            runs.append((t, end))
    return runs


def trailing_turns(trace: Trace, config: DetectorConfig, until: float) -> tuple[int, float]:
    """Drum turns at the end of the trace and where the last long activity ended.

    Returns (turns, end offset of the last long activity); turns is 0 when the
    trace does not end in turns.
    """
    for turns, (start, end) in enumerate(reversed(_runs(trace, config, until))):
        if end - start > TURN_MAX_S:
            return turns, end
    return 0, 0.0


def trace_energy(trace: Trace, lo: float, hi: float) -> float:
    """Energy in Wh of the step-function trace between two offsets."""
    wh = 0.0
    for i, (t, w) in enumerate(trace):
        end = trace[i + 1][0] if i + 1 < len(trace) else hi
        wh += w * max(0.0, min(end, hi) - max(t, lo))
    return wh / 3600


def cut_turns(cycle: CycleRecord, config: DetectorConfig, until: float) -> bool:
    """End `cycle` where its trailing drum turns begin, if it ends in enough of them.

    `until` is the offset the trace runs to (now, for a running cycle). The
    turns become the cycle's anti-crease bursts; their energy, cost and the
    quiet spells between them leave the cycle.
    """
    turns, long_end = trailing_turns(cycle.trace, config, until)
    if turns < MIN_TURNS:
        return False
    turn_wh = trace_energy(cycle.trace, long_end, until)
    if cycle.cost is not None and cycle.energy_wh > 0:
        cycle.cost *= max(0.0, cycle.energy_wh - turn_wh) / cycle.energy_wh
    cycle.energy_wh = max(0.0, cycle.energy_wh - turn_wh)
    cycle.trace = [p for p in cycle.trace if p[0] < long_end]
    cycle.end = cycle.start + long_end
    cycle.peak_power_w = max((w for _, w in cycle.trace), default=0.0)
    cycle.pauses = pauses(cycle, config)
    cycle.anti_crease_bursts += turns
    return True
