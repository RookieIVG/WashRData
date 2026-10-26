"""Anti-crease drum turns at the end of real heat pump dryer runs.

tests/fixtures/dryer_anti_crease.json: after drying and cooling the drum
turns for 5-10 s about every 30 s, with about 4 W in between. Those gaps are
shorter than any end delay, so the turns were counted as part of the cycle
and "finished" came 20 to 30 minutes late.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from engine import PRESETS, CycleDetector, CycleRecord, DeviceType, EventKind, State, Tracker
from engine.bursts import MIN_TURNS, trailing_turns

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "dryer_anti_crease.json").read_text())
CYCLES = [CycleRecord.from_dict(c) for c in FIXTURE["cycles"]]
DRYER = PRESETS[DeviceType.DRYER]
MIN = 60.0
WITH_TURNS = [
    (c, raw["end_of_drying_min"])
    for c, raw in zip(CYCLES, FIXTURE["cycles"], strict=True)
    if raw["end_of_drying_min"]
]


def replay(config, cycle: CycleRecord, start: float = 1_000_000.0):
    """Feed the trace as plug reports, then the plug at standby; a tick every 30 s."""
    detector = CycleDetector(config)
    events = detector.update(start - 600, 1.0)
    points = [*cycle.trace, (cycle.duration(), 1.0), (cycle.duration() + 7200, 1.0)]
    for (t, w), (t_next, _) in zip(points, [*points[1:], (points[-1][0] + 30, 0)], strict=True):
        events += detector.update(start + t, w)
        tick = t + 30
        while tick < t_next:
            events += detector.tick(start + tick)
            tick += 30
    return detector, events


@pytest.mark.parametrize(("cycle", "end_min"), WITH_TURNS)
def test_cycle_ends_where_the_turns_begin(cycle: CycleRecord, end_min: float) -> None:
    detector, events = replay(DRYER, cycle)
    kinds = [e.kind for e in events]
    assert kinds == [
        EventKind.STARTED,
        EventKind.FINISHED,
        EventKind.ANTI_CREASE_STARTED,
        EventKind.ANTI_CREASE_ENDED,
    ]
    finished = next(e.cycle for e in events if e.kind == EventKind.FINISHED)
    assert finished.duration() == pytest.approx(end_min * MIN, abs=1.5 * MIN)
    assert cycle.duration() - finished.duration() > 15 * MIN
    assert finished.anti_crease_bursts >= MIN_TURNS
    assert finished.energy_wh < cycle.energy_wh
    # The quiet spells between the turns are not pauses of the program.
    assert max(finished.pauses) < 5 * MIN
    assert detector.state == State.IDLE


def test_state_is_anti_crease_while_the_drum_turns() -> None:
    cycle, end_min = WITH_TURNS[0]
    head = replace(cycle, trace=[p for p in cycle.trace if p[0] < (end_min + 10) * MIN])
    detector = CycleDetector(DRYER)
    start = 1_000_000.0
    for t, w in head.trace:
        detector.update(start + t, w)
    assert detector.state == State.ANTI_CREASE
    assert detector.last_cycle.duration() == pytest.approx(end_min * MIN, abs=1.5 * MIN)


def test_runs_without_turns_are_unchanged() -> None:
    plain = [c for c, raw in zip(CYCLES, FIXTURE["cycles"], strict=True) if not raw["end_of_drying_min"]]
    assert plain
    for cycle in plain:
        _, events = replay(DRYER, cycle)
        finished = [e.cycle for e in events if e.kind == EventKind.FINISHED]
        assert len(finished) == 1
        # A short anti-crease tail may be cut, the drying itself is not.
        assert cycle.duration() - finished[0].duration() < 10 * MIN


def test_washers_are_not_affected() -> None:
    cycle, _ = WITH_TURNS[0]
    _, events = replay(replace(DRYER, anti_crease=False), cycle)
    finished = next(e.cycle for e in events if e.kind == EventKind.FINISHED)
    assert finished.duration() == pytest.approx(cycle.duration(), abs=MIN)


def test_drying_itself_has_no_trailing_turns() -> None:
    for cycle, end_min in WITH_TURNS:
        drying = [p for p in cycle.trace if p[0] < end_min * MIN]
        turns, _ = trailing_turns(drying, DRYER, end_min * MIN)
        assert turns < MIN_TURNS


def test_stored_cycles_are_trimmed_once() -> None:
    history = [
        replace(c, start=i * 100_000.0, end=i * 100_000.0 + c.duration(), cost=0.1)
        for i, c in enumerate(CYCLES)
    ]
    durations = [c.duration() for c in history]
    tracker = Tracker(DRYER, history=history)
    assert tracker.trim_turns() >= len(WITH_TURNS)
    for (cycle, end_min), before in zip(WITH_TURNS, durations, strict=False):
        trimmed = next(c for c in tracker.history if c.trace[:5] == cycle.trace[:5])
        assert trimmed.duration() == pytest.approx(end_min * MIN, abs=1.5 * MIN)
        assert trimmed.duration() < before
        assert trimmed.cost < 0.1
    assert tracker.trim_turns() == 0
    assert Tracker(replace(DRYER, anti_crease=False), history=list(CYCLES)).trim_turns() == 0
