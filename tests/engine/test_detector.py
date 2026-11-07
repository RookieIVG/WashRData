"""Behaviour of the cycle detector on synthetic power curves."""

from __future__ import annotations

import json

import pytest

from engine import PRESETS, CycleDetector, DeviceType, EventKind, State

from .sim import HOUR, MIN, bursts, play, segments

WASHER = PRESETS[DeviceType.WASHER]
DRYER = PRESETS[DeviceType.DRYER]

# Idle 5 min, heat 15 min, wash 30 min, short pause, spin 10 min, standby.
WASH = [
    (5 * MIN, 1.5),
    (15 * MIN, 2000.0),
    (30 * MIN, 300.0),
    (2 * MIN, 2.0),
    (10 * MIN, 500.0),
    (HOUR, 1.5),
]
WASH_START = 5 * MIN
WASH_END = 5 * MIN + 57 * MIN
WASH_ENERGY_WH = 2000 * 0.25 + 300 * 0.5 + 2 * 2 / 60 + 500 * 10 / 60

# Heat 60 min, quiet 5 min, then 10 anti-crease turns of 40 s every 3 min.
DRY = segments([(60 * MIN, 2000.0), (5 * MIN, 0.5)], bursts(10, 40, 140, 200.0), [(2 * HOUR, 0.5)])


def washer() -> CycleDetector:
    return CycleDetector(WASHER)


def dryer() -> CycleDetector:
    return CycleDetector(DRYER)


@pytest.mark.parametrize("on_change_only", [False, True])
def test_washer_cycle_start_end_energy(on_change_only: bool) -> None:
    det = washer()
    run = play(det, WASH, on_change_only=on_change_only)

    assert run.kinds() == [EventKind.STARTED, EventKind.FINISHED]
    started, finished = run.events
    assert started.ts == WASH_START
    assert finished.ts == WASH_END
    cycle = finished.cycle
    assert cycle is not None
    assert cycle.duration() == 57 * MIN
    assert cycle.energy_wh == pytest.approx(WASH_ENERGY_WH, rel=0.01)
    assert cycle.peak_power_w == 2000.0
    assert cycle.pauses == [2 * MIN]
    assert not cycle.interrupted
    assert det.cycle_count == 1
    # Finished is held for finished_timeout, then idle.
    assert det.state == State.IDLE


def test_state_sequence_during_washer_cycle() -> None:
    det = washer()
    play(det, WASH[:2])
    assert det.state == State.RUNNING
    play(det, [(2 * MIN, 2.0)], start_ts=20 * MIN)
    assert det.state == State.PAUSED
    play(det, [(MIN, 500.0)], start_ts=22 * MIN)
    assert det.state == State.RUNNING
    play(det, [(10 * MIN, 1.0)], start_ts=23 * MIN)
    assert det.state == State.FINISHED


def test_blip_is_not_a_cycle() -> None:
    det = washer()
    run = play(det, [(5 * MIN, 1.0), (20, 50.0), (30 * MIN, 1.0)])
    assert run.events == []
    assert det.state == State.IDLE
    assert det.cycle_count == 0


def test_standby_between_thresholds_does_not_start() -> None:
    det = washer()
    run = play(det, [(HOUR, 6.0)])
    assert run.events == []
    assert det.state == State.IDLE


def test_hysteresis_keeps_running_between_thresholds() -> None:
    det = washer()
    run = play(det, [(10 * MIN, 400.0), (4 * MIN, 6.0), (10 * MIN, 400.0), (20 * MIN, 1.0)])
    assert run.kinds() == [EventKind.STARTED, EventKind.FINISHED]
    assert run.events[0].cycle is not None
    assert run.events[0].cycle.pauses == []


def test_standby_in_the_band_for_the_end_delay_ends_the_cycle() -> None:
    """Held between stop and start power for the whole end delay is standby:
    the cycle ends where the power fell below the start power."""
    det = washer()
    run = play(det, [(10 * MIN, 400.0), (20 * MIN, 6.0), (10 * MIN, 400.0), (20 * MIN, 1.0)])
    assert run.kinds() == [EventKind.STARTED, EventKind.FINISHED, EventKind.STARTED, EventKind.FINISHED]
    first = run.of(EventKind.FINISHED)[0].cycle
    assert first is not None and first.duration() == pytest.approx(10 * MIN, abs=MIN)


def test_soak_shorter_than_end_delay_keeps_cycle() -> None:
    det = washer()
    run = play(det, [(20 * MIN, 300.0), (4 * MIN, 1.0), (20 * MIN, 300.0), (30 * MIN, 1.0)])
    assert run.kinds() == [EventKind.STARTED, EventKind.FINISHED]


def test_short_cycle_is_flagged_interrupted() -> None:
    det = washer()
    run = play(det, [(3 * MIN, 400.0), (30 * MIN, 1.0)])
    assert run.kinds() == [EventKind.STARTED, EventKind.FINISHED]
    cycle = run.of(EventKind.FINISHED)[0].cycle
    assert cycle is not None and cycle.interrupted


def test_long_soak_resumes_and_is_learned() -> None:
    """A 10 min soak ends the first cycle early; it is resumed, not split,
    and the next run of the same program is not ended during the soak."""
    program = [(20 * MIN, 300.0), (10 * MIN, 1.0), (20 * MIN, 300.0), (HOUR, 1.0)]

    det = washer()
    run = play(det, program)
    assert run.kinds() == [
        EventKind.STARTED,
        EventKind.FINISHED,
        EventKind.RESUMED,
        EventKind.FINISHED,
    ]
    cycle = run.events[-1].cycle
    assert cycle is not None
    assert cycle.resumed == 1
    assert cycle.duration() == 50 * MIN
    assert cycle.longest_pause == 10 * MIN
    assert det.cycle_count == 1
    assert det.end_delay > 10 * MIN

    second = play(det, program, start_ts=run.end_ts)
    assert second.kinds() == [EventKind.STARTED, EventKind.FINISHED]


def test_learned_end_delay_is_capped() -> None:
    det = washer()
    play(det, [(20 * MIN, 300.0), (14 * MIN, 1.0), (20 * MIN, 300.0), (3 * HOUR, 1.0)])
    det.learned.longest_pauses.append(10 * HOUR)
    assert det.end_delay == WASHER.max_end_delay_s


def test_dryer_anti_crease_is_not_a_new_cycle() -> None:
    det = dryer()
    run = play(det, DRY)
    assert run.kinds() == [
        EventKind.STARTED,
        EventKind.FINISHED,
        EventKind.ANTI_CREASE_STARTED,
        EventKind.ANTI_CREASE_ENDED,
    ]
    cycle = run.of(EventKind.FINISHED)[0].cycle
    assert cycle is not None
    assert cycle.duration() == 60 * MIN
    assert cycle.anti_crease_bursts == 10
    assert det.cycle_count == 1
    assert det.state == State.IDLE


def test_dryer_state_during_anti_crease() -> None:
    det = dryer()
    play(det, DRY[:6])
    assert det.state == State.ANTI_CREASE


def test_dryer_new_cycle_after_anti_crease() -> None:
    det = dryer()
    curve = segments(
        [(60 * MIN, 2000.0), (5 * MIN, 0.5)],
        bursts(5, 40, 140, 200.0),
        [(20 * MIN, 0.5), (40 * MIN, 2000.0), (HOUR, 0.5)],
    )
    run = play(det, curve)
    assert run.kinds().count(EventKind.STARTED) == 2
    assert run.kinds().count(EventKind.FINISHED) == 2
    assert EventKind.RESUMED not in run.kinds()
    second = run.of(EventKind.STARTED)[1]
    assert second.ts == 60 * MIN + 5 * MIN + 5 * 180 + 20 * MIN
    assert det.cycle_count == 2


def test_weak_load_after_end_does_not_block_idle() -> None:
    """A load that stays in the hysteresis band after the end never reaches
    the start energy; the finished state must still time out."""
    det = dryer()
    run = play(det, [(60 * MIN, 2000.0), (5 * MIN, 0.5), (MIN, 21.0), (3 * HOUR, 10.0)])
    # The short activity counts as an anti-crease turn once the band reads as standby.
    assert run.kinds().count(EventKind.STARTED) == 1
    assert run.kinds().count(EventKind.FINISHED) == 1
    assert det.state == State.IDLE


def test_unavailable_sensor_ends_cycle_as_interrupted() -> None:
    det = washer()
    run = play(det, [(20 * MIN, 300.0), (30 * MIN, None)])
    assert run.kinds() == [EventKind.STARTED, EventKind.FINISHED]
    finished = run.of(EventKind.FINISHED)[0]
    assert finished.ts == 20 * MIN
    assert finished.cycle is not None and finished.cycle.interrupted


def test_short_unavailability_does_not_end_cycle() -> None:
    det = washer()
    run = play(det, [(20 * MIN, 300.0), (5 * MIN, None), (20 * MIN, 300.0), (HOUR, 1.0)])
    assert run.kinds() == [EventKind.STARTED, EventKind.FINISHED]
    cycle = run.events[-1].cycle
    assert cycle is not None and not cycle.interrupted


def test_gap_after_restart_is_not_integrated() -> None:
    det = washer()
    play(det, [(20 * MIN, 1000.0)])
    energy = det.total_energy_wh
    det.update(20 * MIN + 2 * HOUR, 1000.0)
    assert det.total_energy_wh == pytest.approx(energy)


def test_persistence_round_trip_mid_cycle() -> None:
    reference = washer()
    expected = play(reference, WASH)

    det = washer()
    play(det, WASH[:3])
    saved = json.loads(json.dumps(det.to_dict()))
    restored = CycleDetector.from_dict(WASHER, saved)
    assert restored.state == det.state
    rest = play(restored, WASH[3:], start_ts=50 * MIN)

    assert rest.kinds() == [EventKind.FINISHED]
    assert rest.events[0].ts == expected.events[-1].ts
    assert restored.total_energy_wh == pytest.approx(reference.total_energy_wh)


def test_out_of_order_samples_are_ignored() -> None:
    det = washer()
    det.update(100.0, 300.0)
    assert det.update(50.0, 0.0) == []
    assert det.power == 300.0


def test_trace_is_compressed() -> None:
    det = washer()
    run = play(det, WASH, sample_s=1.0)
    cycle = run.of(EventKind.FINISHED)[0].cycle
    assert cycle is not None
    assert 5 <= len(cycle.trace) < 50
    assert cycle.trace[0] == (0.0, 2000.0)
