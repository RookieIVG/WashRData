"""Recognition is recorded for every cycle, so its accuracy can be measured."""

from __future__ import annotations

from engine import AUTO_LABEL_CONFIDENCE, CURRENT, LAST, LabelSource

from .sim import play
from .test_matching import COTTON, ECO, QUICK, trained


def test_first_runs_of_a_program_are_not_rated() -> None:
    bench = trained(COTTON, QUICK)
    first = bench.tracker.history[0]
    assert first.known_programs == []
    assert first.recognised is None and first.match_confidence is None
    # The second runs had references of both programs and were recognised.
    assert bench.tracker.accuracy().total.cycles == 2


def test_confident_auto_labels_are_hits() -> None:
    bench = trained(COTTON, QUICK, runs=1)
    bench.run(COTTON, seed=7)
    bench.run(QUICK, seed=8)
    cycle = bench.tracker.last_cycle
    assert cycle.program == cycle.recognised == QUICK
    assert cycle.known_programs == [COTTON, QUICK]

    accuracy = bench.tracker.accuracy()
    assert (accuracy.total.cycles, accuracy.total.hits) == (2, 2)
    assert accuracy.programs[COTTON].rate == 1.0


def test_correcting_an_auto_label_is_a_miss() -> None:
    bench = trained(COTTON, QUICK, runs=1)
    bench.run(QUICK, seed=8)
    bench.tracker.label(LAST, COTTON)
    cycle = bench.tracker.last_cycle
    assert cycle.recognised == QUICK and cycle.program_source == LabelSource.MANUAL

    accuracy = bench.tracker.accuracy()
    assert accuracy.programs[COTTON].cycles == 1
    assert accuracy.programs[COTTON].hits == 0


def test_manual_label_during_run_keeps_what_recognition_said() -> None:
    bench = trained(COTTON, QUICK)
    rest, _ = bench.start(QUICK, 21, minutes=15)
    bench.tracker.label(CURRENT, COTTON)
    play(bench.tracker, rest[:1], start_ts=bench.ts)
    match = bench.tracker.match
    assert match.program == COTTON and match.confidence == 1.0
    # The live recognition still sees the quick program.
    assert match.recognised == QUICK
    assert match.recognition_confidence < 1.0

    bench.ts += rest[0][0]
    bench.finish(rest[1:])
    cycle = bench.tracker.last_cycle
    assert cycle.program == COTTON and cycle.recognised == QUICK
    assert cycle.match_confidence >= AUTO_LABEL_CONFIDENCE


def test_unknown_program_rated_once_it_has_references() -> None:
    bench = trained(COTTON, QUICK, runs=1)
    bench.run(ECO, seed=5)
    eco = bench.tracker.last_cycle
    bench.tracker.label(LAST, ECO)
    # ECO had no references yet: not recognisable, not counted.
    assert ECO not in eco.known_programs
    assert ECO not in bench.tracker.accuracy().programs


def test_rename_and_delete_keep_recognition_consistent() -> None:
    bench = trained(COTTON, QUICK, runs=1)
    bench.run(QUICK, seed=8)
    bench.tracker.rename_program(QUICK, "Kurz")
    cycle = bench.tracker.last_cycle
    assert cycle.recognised == "Kurz" and "Kurz" in cycle.known_programs
    assert bench.tracker.accuracy().programs["Kurz"].hits == 1

    bench.tracker.delete_program("Kurz")
    assert cycle.recognised is None and "Kurz" not in cycle.known_programs


def test_right_but_unsure_is_counted_apart() -> None:
    bench = trained(COTTON, QUICK, runs=1)
    bench.run(QUICK, seed=8)
    cycle = bench.tracker.last_cycle
    cycle.match_confidence = 0.3  # named right, below the labelling threshold
    accuracy = bench.tracker.accuracy().programs[QUICK]
    assert (accuracy.cycles, accuracy.hits, accuracy.unsure) == (1, 0, 1)
    cycle.recognised = COTTON  # named wrong: neither hit nor unsure
    accuracy = bench.tracker.accuracy().programs[QUICK]
    assert (accuracy.hits, accuracy.unsure) == (0, 0)
