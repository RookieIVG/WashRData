"""Labelled washes that do not fit their program, on real runs.

tests/fixtures/washer_super_speed.json: three Super Speed 30 (8-11 min
heating, 0.39-0.46 kWh) and three Super Speed 40 (16-20 min, 0.60-0.78 kWh).
On the reporting machine two Super Speed 30 had been labelled 40.
"""

from __future__ import annotations

from dataclasses import replace

from engine.plausibility import heating_s, suspicious

from .test_energy import CYCLES

MIN = 60.0


def test_correct_labels_raise_nothing() -> None:
    assert suspicious(CYCLES) == {}


def test_a_wrong_label_is_pointed_out_with_the_better_program() -> None:
    wrong = next(c for c in CYCLES if c.program == "Super Speed 30")
    cycles = [replace(c, program="Super Speed 40") if c is wrong else c for c in CYCLES]
    found = suspicious(cycles)
    assert list(found) == [wrong.id]
    assert found[wrong.id].program == "Super Speed 40"
    assert found[wrong.id].better == "Super Speed 30"


def test_a_program_needs_three_other_runs() -> None:
    two = [c for c in CYCLES if c.program == "Super Speed 40"][:2]
    odd = replace(next(c for c in CYCLES if c.program == "Super Speed 30"), program="Super Speed 40")
    assert suspicious([*two, odd]) == {}


def test_heating_time_of_real_runs() -> None:
    for c in CYCLES:
        heat = heating_s(c)
        if c.program == "Super Speed 30":
            assert 6 * MIN < heat < 12 * MIN
        else:
            assert 14 * MIN < heat < 26 * MIN
