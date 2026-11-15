"""The learned end delay ignores a pause seen only once (tests/fixtures/dryer_door_pause.json).

A real dryer paused for ten minutes once, mid-cycle (door opened); learned
as the longest pause, it raised the end delay of every later cycle from 3 to
13 minutes. A pause now has to be seen twice, unless the cycle was ended too
early and resumed, which counts twice at once.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine import PRESETS, CycleDetector, CycleRecord, DeviceType
from engine.learning import LearnedStats

from .sim import HOUR, MIN, play

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "dryer_door_pause.json").read_text())
DOOR = CycleRecord.from_dict(FIXTURE["cycle"])
DRYER = PRESETS[DeviceType.DRYER]
WASHER = PRESETS[DeviceType.WASHER]


def test_a_pause_seen_once_does_not_raise_the_end_delay() -> None:
    assert DOOR.longest_pause > 9 * MIN and DOOR.resumed == 0
    stats = LearnedStats()
    for pause in FIXTURE["other_longest_pauses"]:
        stats.longest_pauses.append(pause)
    stats.learn(DOOR)
    assert stats.end_delay(DRYER) == DRYER.end_delay_s


def test_a_pause_seen_twice_does() -> None:
    stats = LearnedStats()
    stats.learn(DOOR)
    stats.learn(DOOR)
    assert stats.end_delay(DRYER) > 10 * MIN


def test_a_resumed_cycle_counts_at_once() -> None:
    """A soak longer than the end delay ends the cycle early; it resumes, and
    the next run of that program is not ended early again."""
    det = CycleDetector(WASHER)
    soak = [(20 * MIN, 300.0), (10 * MIN, 1.0), (20 * MIN, 300.0), (2 * HOUR, 1.0)]
    run = play(det, soak)
    assert run.events[-1].cycle.resumed == 1
    assert det.end_delay > 10 * MIN
    again = play(det, soak, start_ts=run.end_ts)
    assert again.events[-1].cycle.resumed == 0


def test_forget_last_takes_back_both_entries() -> None:
    stats = LearnedStats()
    stats.longest_pauses.append(60.0)
    resumed = CycleRecord(start=0, end=3600, pauses=[600.0], resumed=1)
    stats.learn(resumed)
    assert list(stats.longest_pauses) == [60.0, 600.0, 600.0]
    stats.forget_last()
    assert list(stats.longest_pauses) == [60.0]
