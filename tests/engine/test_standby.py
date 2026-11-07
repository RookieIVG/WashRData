"""Standby at the stop power between two washes (tests/fixtures/washer_standby_band.json).

The plug read exactly 4 W, the configured stop power, for 22 minutes between
two washes. Only power below the stop power counted as quiet, so the first
wash never ended, and the split offered no quiet stretch.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from engine import PRESETS, CycleRecord, DeviceType, EventKind
from engine.split import quiet_stretches, split_cycle

from .test_split import replay

FIXTURE = Path(__file__).parent.parent / "fixtures" / "washer_standby_band.json"
MERGED = CycleRecord.from_dict(json.loads(FIXTURE.read_text())["cycle"])
# The reporter's options.
CONFIG = replace(
    PRESETS[DeviceType.WASHER], start_power_w=8.0, stop_power_w=4.0, end_delay_s=300.0, resume_window_s=300.0
)
MIN = 60.0


def test_two_washes_are_two_cycles() -> None:
    _, events = replay(CONFIG, MERGED.trace)
    finished = [e.cycle for e in events if e.kind == EventKind.FINISHED]
    assert [e.kind for e in events].count(EventKind.STARTED) == 2
    assert len(finished) == 2
    assert finished[0].duration() == pytest.approx(74 * MIN, abs=2 * MIN)
    assert finished[1].start - finished[0].end > 15 * MIN
    assert finished[1].duration() > 90 * MIN


def test_the_standby_stretch_is_offered_as_split_point() -> None:
    gap = quiet_stretches(MERGED, CONFIG)[0]
    assert 72 * MIN < gap[0] < 76 * MIN
    assert gap[1] - gap[0] > 18 * MIN
    first, second = split_cycle(MERGED, (gap[0] + gap[1]) / 2, CONFIG)
    assert first.duration() == pytest.approx(gap[0])
    assert second.start == pytest.approx(MERGED.start + gap[1])
    assert first.energy_wh + second.energy_wh == pytest.approx(MERGED.energy_wh)
