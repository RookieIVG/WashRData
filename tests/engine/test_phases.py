"""Washing phases and the remaining time after heating, on real runs.

tests/fixtures/washer_super_speed.json (three Super Speed 30, three Super
Speed 40) and tests/fixtures/washer_pflegeleicht.json (two Pflegeleicht 30,
two Pflegeleicht 60), all of one machine. On this machine a run fills, heats at about 2 kW,
washes, spins briefly between rinses and ends with a long final spin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import engine.evaluation as evaluation_module
from engine import CycleRecord
from engine.evaluation import evaluate
from engine.phases import WINDOW_S, Phase, anchors, cycle_segments, heating_to_end, label, windows

from .test_energy import CYCLES as SUPER_SPEED
from .test_energy import PROGRAMS

MIN = 60.0
_PL = json.loads((Path(__file__).parent.parent / "fixtures" / "washer_pflegeleicht.json").read_text())
PFLEGELEICHT = [CycleRecord.from_dict(c) for c in _PL["cycles"]]
RUNS = [*SUPER_SPEED, *PFLEGELEICHT]
ALL_PROGRAMS = [*PROGRAMS, "Pflegeleicht 30", "Pflegeleicht 60"]


@pytest.mark.parametrize("cycle", RUNS, ids=lambda c: c.program)
def test_phases_of_a_real_run(cycle) -> None:
    phases = label(windows(cycle.trace, cycle.duration()))
    segs = cycle_segments(cycle, sensor_drying=False)
    heating = [s for s in segs if s.phase == Phase.HEATING]
    spins = [s for s in segs if s.phase == Phase.SPINNING]
    assert heating and heating[0].start_s < 10 * MIN
    assert sum(s.end_s - s.start_s for s in heating) >= 5 * MIN
    # The final spin is long and ends the run.
    final = spins[-1]
    assert final.end_s - final.start_s >= 2 * MIN
    assert cycle.duration() - final.end_s < 3 * MIN
    # No spin before a good while of washing after the heating.
    assert spins[0].start_s - heating[-1].end_s >= 10 * MIN
    # Washing comes before rinsing, never after.
    first_rinse = phases.index(Phase.RINSING) if Phase.RINSING in phases else len(phases)
    assert Phase.WASHING not in phases[first_rinse:]
    assert anchors(phases).heating_end is not None


def test_labels_do_not_change_once_given() -> None:
    """Live, the phase of a window is fixed once it is shown (except a spin being recognised)."""
    cycle = SUPER_SPEED[0]
    full = label(windows(cycle.trace, cycle.duration()))
    for minutes in (10, 30, 50):
        part = label(windows(cycle.trace, minutes * MIN))
        for live, final in zip(part[:-12], full, strict=False):
            assert live == final or Phase.SPINNING in (live, final)


def test_heating_to_end_is_steadier_than_the_run_time() -> None:
    """The heating and the washing before the first spin vary; the rest barely does."""
    for program in ALL_PROGRAMS:
        runs = [c for c in RUNS if c.program == program]
        times = [heating_to_end(c) for c in runs]
        assert all(t is not None for t in times)
        assert max(times) - min(times) < 10 * MIN


def test_remaining_time_after_heating_beats_the_alignment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over both kinds of program: much better for Pflegeleicht, whose long wash
    the alignment misjudges, and about as good for Super Speed."""
    anchored = evaluate(RUNS, ALL_PROGRAMS)
    monkeypatch.setattr(evaluation_module, "remaining_after_heating", lambda *_: None)
    aligned = evaluate(RUNS, ALL_PROGRAMS)
    for progress in (0.5, 0.75):
        a = next(r for r in anchored.remaining if r.progress == progress)
        b = next(r for r in aligned.remaining if r.progress == progress)
        assert a.mean_abs_error_s < b.mean_abs_error_s
        assert a.mean_abs_error_s < 5 * MIN


def test_window_size() -> None:
    assert WINDOW_S == 20.0
