"""Dryer remaining time only in the cooling phase, on real heat pump dryer runs.

Sensor drying makes the run time depend on the load (Mix Dry took 27 to 154
minutes on the reporting dryer), so references cannot tell it while the
dryer heats. The cooling phase at the end lasts about the same every time.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine import PRESETS, DeviceType, LabelSource, Tracker
from engine.drying import cooling_start, cooling_time
from engine.evaluation import evaluate
from engine.matching import resample

from .test_bursts import CYCLES, DRYER

MIN = 60.0


def _stored() -> list:
    """The fixture runs as stored history, anti-crease turns cut, labelled."""
    tracker = Tracker(
        DRYER,
        history=[
            replace(c, start=i * 100_000.0, end=i * 100_000.0 + c.duration()) for i, c in enumerate(CYCLES)
        ],
    )
    tracker.trim_turns()
    for c in tracker.history:
        c.program_source = LabelSource.MANUAL
    return tracker.history


def test_preset() -> None:
    assert PRESETS[DeviceType.DRYER].sensor_drying
    assert not PRESETS[DeviceType.WASHER].sensor_drying


def test_cooling_is_found_at_the_end_only() -> None:
    for cycle in _stored():
        watts = resample(cycle.trace, cycle.duration())
        start = cooling_start(watts)
        assert start is not None
        assert 5 <= len(watts) - start <= 30
        # No earlier minute of the drying looks like the start of cooling.
        for k in range(10, start - 1):
            assert cooling_start(watts[:k]) is None, (cycle.program, k)


def test_remaining_time_appears_when_cooling_starts() -> None:
    history = _stored()
    mix = [c for c in history if c.program == "Mix Dry"]
    target, references = mix[-1], [c for c in history if c is not mix[-1]]
    tracker = Tracker(DRYER, programs=["Mix Dry", "Baumwolle"], history=references)
    start = 1_000_000.0
    phases = []
    for t, w in target.trace:
        tracker.update(start + t, w)
        match = tracker.match
        if match is not None and match.program:
            phases.append((t, match.phase, match.remaining_s))
    drying = [p for p in phases if p[1] == "drying"]
    cooling = [p for p in phases if p[1] == "cooling"]
    assert drying and cooling
    assert all(r is None for _, _, r in drying)
    # Cooling comes last, and its remaining time is within a few minutes of the real end.
    assert max(t for t, _, _ in drying) < min(t for t, _, _ in cooling)
    t, _, remaining = cooling[0]
    assert remaining == pytest.approx(target.duration() - t, abs=8 * MIN)


def test_evaluation_measures_remaining_time_in_cooling_only() -> None:
    history = _stored()
    times = [cooling_time(c) for c in history]
    assert all(5 * MIN <= t <= 30 * MIN for t in times), times
    evaluation = evaluate(history, ["Mix Dry", "Baumwolle"], sensor_drying=True)
    # One row: the remaining time when cooling begins, not at fixed shares of the run.
    (row,) = evaluation.remaining
    assert row.progress is None
    assert row.cycles >= 3
    assert row.mean_abs_error_s < 10 * MIN


def test_dryer_phases_are_drying_then_cooling() -> None:
    from engine.phases import Phase, cycle_segments

    for cycle in _stored():
        segs = cycle_segments(cycle, sensor_drying=True)
        assert [s.phase for s in segs] == [Phase.DRYING, Phase.COOLING]
        assert segs[1].end_s == pytest.approx(cycle.duration())
