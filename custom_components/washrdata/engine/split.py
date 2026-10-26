"""Splitting a stored cycle that holds two runs, e.g. two washes merged by a resume.

The trace is the only record of when the appliance was quiet, so quiet
stretches, pauses and the energy share of each part are derived from it with
the detector's hysteresis.
"""

from __future__ import annotations

from dataclasses import replace

from .model import CycleRecord, DetectorConfig

# Quiet stretches shorter than this are not offered as split points.
MIN_QUIET_S = 120.0
# Each part must keep at least this much of the cycle.
MIN_PART_S = 60.0


def _segments(cycle: CycleRecord) -> list[tuple[float, float, float]]:
    """(start, end, watts) of each trace point, offsets from the cycle start."""
    trace = cycle.trace
    duration = cycle.duration()
    return [
        (t, trace[i + 1][0] if i + 1 < len(trace) else max(duration, t), w) for i, (t, w) in enumerate(trace)
    ]


def _activity(cycle: CycleRecord, config: DetectorConfig) -> list[tuple[float, float, bool]]:
    """(start, end, active) with the detector's hysteresis: active from start
    power, quiet below stop power, unchanged in between."""
    out: list[tuple[float, float, bool]] = []
    active = True
    for a, b, w in _segments(cycle):
        if w >= config.start_power_w:
            active = True
        elif w < config.stop_power_w:
            active = False
        if out and out[-1][2] == active:
            out[-1] = (out[-1][0], b, active)
        else:
            out.append((a, b, active))
    return out


def quiet_stretches(cycle: CycleRecord, config: DetectorConfig) -> list[tuple[float, float]]:
    """Quiet stretches between activity, longest first: the candidate split points."""
    runs = _activity(cycle, config)
    quiet = [(a, b) for i, (a, b, active) in enumerate(runs) if not active and 0 < i < len(runs) - 1]
    return sorted((q for q in quiet if q[1] - q[0] >= MIN_QUIET_S), key=lambda q: q[0] - q[1])


def pauses(cycle: CycleRecord, config: DetectorConfig) -> list[float]:
    runs = _activity(cycle, config)
    return [b - a for i, (a, b, active) in enumerate(runs) if not active and 0 < i < len(runs) - 1]


def _energy(cycle: CycleRecord, lo: float, hi: float) -> float:
    return sum(w * max(0.0, min(b, hi) - max(a, lo)) for a, b, w in _segments(cycle)) / 3600


def split_cycle(cycle: CycleRecord, at_s: float, config: DetectorConfig) -> tuple[CycleRecord, CycleRecord]:
    """Two cycles from one, cut at `at_s` seconds after its start.

    Inside a quiet stretch the first part ends where the stretch begins and the
    second starts where it ends, so the gap belongs to neither. Both parts lose
    their program and recognition: they are new runs to be labelled.
    """
    if cycle.end is None:
        raise ValueError("cycle is still running")
    duration = cycle.duration()
    if not MIN_PART_S <= at_s <= duration - MIN_PART_S:
        raise ValueError("split point outside the cycle")
    first_end, second_start = at_s, at_s
    for a, b, active in _activity(cycle, config):
        if not active and a <= at_s <= b and a > 0 and b < duration:
            first_end, second_start = a, b
    if first_end < MIN_PART_S or duration - second_start < MIN_PART_S:
        raise ValueError("a part would be empty")

    # Energy and cost by each part's share of the trace, so both add up to the original.
    e1, e2 = _energy(cycle, 0, first_end), _energy(cycle, second_start, duration)
    share = e1 / (e1 + e2) if e1 + e2 > 0 else first_end / duration
    head = [p for p in cycle.trace if p[0] < first_end]
    # The second part starts with the power that held at its start.
    held = [p for p in cycle.trace if p[0] <= second_start][-1:]
    tail = [(0.0, held[0][1])] if held and held[0][0] < second_start else []
    tail += [(t - second_start, w) for t, w in cycle.trace if t >= second_start]

    fresh = {
        "program": None,
        "program_source": None,
        "match_confidence": None,
        "recognised": None,
        "known_programs": [],
        "recognition_retro": False,
    }
    first = replace(
        cycle,
        end=cycle.start + first_end,
        energy_wh=cycle.energy_wh * share,
        cost=cycle.cost * share if cycle.cost is not None else None,
        peak_power_w=max((w for _, w in head), default=0.0),
        resumed=0,
        anti_crease_bursts=0,
        trace=head,
        **fresh,
    )
    second = replace(
        cycle,
        start=cycle.start + second_start,
        energy_wh=cycle.energy_wh * (1 - share),
        cost=cycle.cost * (1 - share) if cycle.cost is not None else None,
        peak_power_w=max((w for _, w in tail), default=0.0),
        resumed=max(0, cycle.resumed - 1),
        trace=tail,
        **fresh,
    )
    for part in (first, second):
        part.pauses = pauses(part, config)
        part.interrupted = part.duration() < config.min_cycle_s
    return first, second
