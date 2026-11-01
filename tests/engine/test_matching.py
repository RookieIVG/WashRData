"""Program recognition on synthetic programs with run-to-run variation."""

from __future__ import annotations

import json

import pytest

from engine import CURRENT, LAST, PRESETS, DeviceType, EventKind, LabelSource, LearnedStats, Tracker
from engine.matching import resample

from .sim import HOUR, MIN, duration_of, play, vary

WASHER = PRESETS[DeviceType.WASHER]

COTTON = "Baumwolle 60"
QUICK = "Kurz 30"
ECO = "Eco mit Einweichen"

PROGRAMS = {
    # fill, heat, wash, drain (held active by hysteresis), spin
    COTTON: [(2 * MIN, 15.0), (20 * MIN, 2000.0), (40 * MIN, 250.0), (3 * MIN, 6.0), (8 * MIN, 450.0)],
    QUICK: [(2 * MIN, 15.0), (25 * MIN, 250.0), (5 * MIN, 450.0)],
    # 12 min soak at standby power: longer than the 5 min end delay
    ECO: [(2 * MIN, 15.0), (10 * MIN, 1500.0), (12 * MIN, 1.0), (30 * MIN, 250.0), (8 * MIN, 450.0)],
}
IDLE = [(5 * MIN, 1.5)]
TAIL = [(2 * HOUR, 1.5)]


class Bench:
    """Runs programs through a tracker, one after another."""

    def __init__(self, tracker: Tracker | None = None) -> None:
        self.tracker = tracker or Tracker(WASHER)
        self.ts = 0.0

    def run(self, program: str, seed: int, *, label: bool = False):
        run = play(self.tracker, IDLE + vary(PROGRAMS[program], seed) + TAIL, start_ts=self.ts)
        self.ts = run.end_ts
        if label:
            self.tracker.label(LAST, program)
        return run

    def start(self, program: str, seed: int, minutes: float):
        """Play the first `minutes` of a program; returns the rest and the truth."""
        curve = vary(PROGRAMS[program], seed)
        head, t = [], 0.0
        while curve and t < minutes * MIN:
            head.append(curve.pop(0))
            t += head[-1][0]
        run = play(self.tracker, IDLE + head, start_ts=self.ts)
        self.ts = run.end_ts
        return curve, duration_of(curve)

    def finish(self, rest):
        run = play(self.tracker, rest + TAIL, start_ts=self.ts)
        self.ts = run.end_ts
        return run


def trained(*programs: str, runs: int = 2) -> Bench:
    bench = Bench()
    for seed in range(runs):
        for program in programs:
            bench.run(program, seed=100 * seed + len(program), label=True)
    return bench


def test_resample_is_mean_per_window() -> None:
    assert resample([(0.0, 100.0), (90.0, 0.0)], 180.0) == [100.0, 50.0, 0.0]
    assert resample([(0.0, 10.0), (30.0, 20.0), (45.0, 40.0)], 60.0) == [pytest.approx(20.0)]
    assert resample([], 600.0) == []


def test_labelled_runs_become_references() -> None:
    bench = trained(COTTON, QUICK)
    refs = bench.tracker.references()
    assert set(refs) == {COTTON, QUICK}
    assert all(len(v) == 2 for v in refs.values())
    assert bench.tracker.programs == [COTTON, QUICK]


@pytest.mark.parametrize("seed", [7, 8, 9])
def test_recognises_program_and_remaining_time(seed: int) -> None:
    bench = trained(COTTON, QUICK)
    rest, truth_remaining = bench.start(COTTON, seed, minutes=30)

    match = bench.tracker.match
    assert match is not None
    assert match.program == COTTON
    assert match.confidence >= 0.5
    assert match.remaining_s == pytest.approx(truth_remaining, abs=8 * MIN)
    assert 0.3 < match.progress < 0.6

    run = bench.finish(rest)
    assert EventKind.FINISHED in run.kinds()
    cycle = bench.tracker.last_cycle
    assert cycle.program == COTTON
    assert cycle.program_source == LabelSource.AUTO
    assert cycle.match_confidence >= 0.6


def test_quick_program_is_auto_labelled() -> None:
    bench = trained(COTTON, QUICK)
    bench.run(QUICK, seed=42)
    cycle = bench.tracker.last_cycle
    assert cycle.program == QUICK
    assert cycle.program_source == LabelSource.AUTO


def test_program_detected_event_once() -> None:
    bench = trained(COTTON, QUICK)
    run = bench.run(COTTON, seed=11)
    detected = run.of(EventKind.PROGRAM_DETECTED)
    assert len(detected) == 1


def test_unknown_program_is_not_labelled() -> None:
    bench = trained(COTTON, QUICK)
    bench.run(ECO, seed=5)
    cycle = bench.tracker.last_cycle
    assert cycle.program is None


def test_nothing_reported_without_references() -> None:
    bench = Bench()
    bench.start(COTTON, 1, minutes=30)
    assert bench.tracker.match is None


def test_recognised_program_holds_off_premature_end() -> None:
    """The 12 min soak of the eco program is longer than the end delay.
    Without references the cycle ends and resumes; with references the
    matcher knows the program is not over and the cycle is not ended."""
    control = Bench()
    run = control.run(ECO, seed=3)
    assert EventKind.RESUMED in run.kinds()

    bench = trained(ECO)
    bench.tracker.detector.learned = LearnedStats()  # isolate the hold from learning
    run = bench.run(ECO, seed=3)
    assert EventKind.RESUMED not in run.kinds()
    assert run.kinds().count(EventKind.FINISHED) == 1
    assert bench.tracker.last_cycle.duration() == pytest.approx(duration_of(vary(PROGRAMS[ECO], 3)))


def test_manual_label_during_run_wins() -> None:
    bench = trained(COTTON, QUICK)
    rest, _ = bench.start(QUICK, 21, minutes=10)
    bench.tracker.label(CURRENT, COTTON)
    play(bench.tracker, rest[:4], start_ts=bench.ts)
    match = bench.tracker.match
    assert match is not None and match.program == COTTON and match.manual
    bench.ts += duration_of(rest[:4])
    bench.finish(rest[4:])
    cycle = bench.tracker.last_cycle
    assert cycle.program == COTTON
    assert cycle.program_source == LabelSource.MANUAL


def test_rename_and_delete_program() -> None:
    bench = trained(COTTON, QUICK)
    bench.tracker.rename_program(COTTON, "Baumwolle")
    assert bench.tracker.programs == ["Baumwolle", QUICK]
    assert set(bench.tracker.references()) == {"Baumwolle", QUICK}
    with pytest.raises(ValueError):
        bench.tracker.rename_program(QUICK, "Baumwolle")

    bench.tracker.delete_program(QUICK)
    assert set(bench.tracker.references()) == {"Baumwolle"}
    assert all(c.program != QUICK for c in bench.tracker.history)


def test_round_trip_keeps_programs_and_recognition() -> None:
    bench = trained(COTTON, QUICK)
    data = json.loads(json.dumps(bench.tracker.to_dict()))
    restored = Bench(Tracker.from_dict(WASHER, data))
    restored.ts = bench.ts
    assert restored.tracker.programs == [COTTON, QUICK]
    restored.run(QUICK, seed=77)
    assert restored.tracker.last_cycle.program == QUICK


def test_labelled_cycles_outlive_recent_history() -> None:
    bench = trained(COTTON)
    for seed in range(55):
        bench.run(QUICK, seed=1000 + seed)
    cotton = [c for c in bench.tracker.history if c.program == COTTON]
    assert len(cotton) == 2


def test_merge_programs_by_renaming() -> None:
    bench = trained(COTTON, QUICK)
    tracker = bench.tracker
    with pytest.raises(ValueError):
        tracker.rename_program(QUICK, COTTON)
    tracker.rename_program(QUICK, COTTON, merge=True)
    assert tracker.programs == [COTTON]
    assert all(c.program == COTTON for c in tracker.history)
    assert all(c.known_programs.count(COTTON) <= 1 for c in tracker.history)
    assert len(tracker.references()[COTTON]) == 4
