"""Energy as a second feature of program recognition, on real runs.

tests/fixtures/washer_super_speed.json: three Super Speed 30 and three Super
Speed 40 of one washing machine. Their curves differ mainly in heating time,
which the minute-by-minute comparison barely sees; their energy differs
clearly (0.39-0.46 against 0.60-0.76 kWh).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import engine.matching as matching
from engine import CycleRecord
from engine.evaluation import evaluate
from engine.matching import ENERGY_TOLERANCE, energy_penalty

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "washer_super_speed.json").read_text())
CYCLES = [CycleRecord.from_dict(c) for c in FIXTURE["cycles"]]
PROGRAMS = ["Super Speed 30", "Super Speed 40"]


def test_small_differences_cost_nothing() -> None:
    assert energy_penalty(600, 650) == 0.0
    assert energy_penalty(400, 700) > 0.0
    assert energy_penalty(400, 700) == pytest.approx(energy_penalty(700, 400))
    assert energy_penalty(0, 0) == 0.0
    assert ENERGY_TOLERANCE > 0


def test_energy_makes_recognition_surer(monkeypatch: pytest.MonkeyPatch) -> None:
    with_energy = evaluate(CYCLES, PROGRAMS)
    monkeypatch.setattr(matching, "ENERGY_WEIGHT", 0.0)
    curve_only = evaluate(CYCLES, PROGRAMS)

    for e in with_energy.cycles:
        assert e.final == e.program
    assert (
        sum(e.final_confidence for e in with_energy.cycles)
        > sum(e.final_confidence for e in curve_only.cycles) + 1
    )
    row = next(r for r in with_energy.thresholds if r.threshold == 0.6)
    before = next(r for r in curve_only.thresholds if r.threshold == 0.6)
    assert (row.auto_correct, row.auto_wrong) == (3, 0)
    assert before.auto_correct == 1
