"""Cycle detection state machine.

Feed it samples with `update(ts, watts)`; call `tick(ts)` periodically so
timeouts fire even when the plug stops reporting an unchanged value.
`watts=None` means the reading is unavailable.

States:
    IDLE         nothing happening
    STARTING     activity seen, not yet enough energy to call it a cycle
    RUNNING      cycle in progress, power above the stop threshold
    PAUSED       cycle in progress, quiet (soak, pause, or the end not yet confirmed)
    FINISHED     cycle ended; sustained activity soon after resumes it
    ANTI_CREASE  short drum turns after the end, not counted as a new cycle
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .bursts import MIN_GAP_S, cut_turns
from .learning import LearnedStats
from .model import ACTIVE_STATES, CycleRecord, DetectorConfig, Event, EventKind, State

# Anti-crease bursts are separated by short quiet spells; a burst is over
# after this much quiet, so consecutive bursts are not merged into a run.
# After the end, a drum turn ends once the drum stood still this long.
BURST_GAP_S = MIN_GAP_S
TRACE_MAX_POINTS = 5000
TRACE_MAX_INTERVAL_S = 300.0


@dataclass
class _Activity:
    """Activity not yet confirmed as a cycle: a start candidate or a post-cycle burst."""

    start: float
    energy_wh: float = 0.0
    cost: float = 0.0
    peak_power_w: float = 0.0
    trace: list[tuple[float, float]] = field(default_factory=list)


class CycleDetector:
    def __init__(self, config: DetectorConfig, learned: LearnedStats | None = None) -> None:
        self.config = config
        self.learned = learned or LearnedStats()
        self.state = State.IDLE
        self.cycle: CycleRecord | None = None
        self.last_cycle: CycleRecord | None = None
        self.total_energy_wh = 0.0
        self.cycle_count = 0
        self._candidate: _Activity | None = None
        self._active = False
        self._power: float | None = None
        self._last_ts: float | None = None
        self._low_since: float | None = None
        self._unknown_since: float | None = None
        self._finished_ts: float | None = None
        self._last_burst_ts: float | None = None
        self._last_learned = False
        # Set by the program matcher: the recognised program is not over yet,
        # so quiet time up to max_end_delay_s does not end the cycle.
        self.hold_end = False
        # Energy price per kWh, set by the adapter; None while unknown.
        self.price: float | None = None

    @property
    def end_delay(self) -> float:
        return self.learned.end_delay(self.config)

    @property
    def power(self) -> float | None:
        return self._power

    @property
    def last_ts(self) -> float | None:
        return self._last_ts

    def update(self, ts: float, power: float | None) -> list[Event]:
        if self._last_ts is not None and ts < self._last_ts:
            return []
        if power is not None:
            power = None if math.isnan(power) else max(0.0, power)
        self._integrate(ts)
        self._last_ts = ts
        self._power = power
        self._set_activity(ts, power)
        events: list[Event] = []
        self._evaluate(ts, events)
        self._record_trace(ts, power)
        return events

    def tick(self, ts: float) -> list[Event]:
        return self.update(ts, self._power)

    # -- sample handling -------------------------------------------------

    def _integrate(self, ts: float) -> None:
        if self._last_ts is None or self._power is None:
            return
        dt = ts - self._last_ts
        if dt <= 0 or dt > self.config.max_sample_gap_s:
            return
        wh = self._power * dt / 3600.0
        self.total_energy_wh += wh
        cost = wh / 1000 * self.price if self.price is not None else None
        if self.cycle is not None:
            self.cycle.energy_wh += wh
            if cost is not None:
                self.cycle.cost = (self.cycle.cost or 0.0) + cost
        elif self._candidate is not None:
            self._candidate.energy_wh += wh
            self._candidate.cost += cost or 0.0

    def _set_activity(self, ts: float, power: float | None) -> None:
        if power is None:
            if self._unknown_since is None:
                self._unknown_since = ts
            return
        self._unknown_since = None
        was_active = self._active
        if power >= self.config.start_power_w:
            self._active = True
        elif power < self.config.stop_power_w:
            self._active = False
        if was_active and not self._active:
            self._low_since = ts

    def _record_trace(self, ts: float, power: float | None) -> None:
        target = self.cycle or self._candidate
        if target is None or power is None:
            return
        target.peak_power_w = max(target.peak_power_w, power)
        offset = ts - target.start
        trace = target.trace
        if trace:
            last_offset, last_power = trace[-1]
            changed = abs(power - last_power) >= max(1.0, 0.05 * last_power)
            if not changed and offset - last_offset < TRACE_MAX_INTERVAL_S:
                return
        trace.append((offset, power))
        if len(trace) > TRACE_MAX_POINTS:
            target.trace = trace[::2]

    # -- state machine ---------------------------------------------------

    def _quiet_for(self, ts: float) -> float:
        if self._active or self._low_since is None:
            return 0.0
        return ts - self._low_since

    def _rising(self) -> bool:
        """A new activity needs a reading at start power. A load that is only
        held active by the hysteresis band cannot start one again after it
        was dropped, which would make the state flap."""
        return self._power is not None and self._power >= self.config.start_power_w

    def _evaluate(self, ts: float, events: list[Event]) -> None:
        cfg = self.config
        if self._unknown_since is not None:
            unknown_for = ts - self._unknown_since
            if unknown_for >= cfg.unavailable_timeout_s:
                if self.state == State.STARTING:
                    self._drop_candidate()
                elif self.state in ACTIVE_STATES:
                    self._finish(self._unknown_since, events, interrupted=True)
            self._expire(ts, events)
            return

        if self.state == State.IDLE:
            if self._rising():
                self._candidate = _Activity(start=ts)
                self.state = State.STARTING
        elif self.state == State.STARTING:
            self._advance_candidate(ts, events)
        elif self.state == State.RUNNING:
            if not self._active:
                self.state = State.PAUSED
                if cfg.anti_crease:
                    self._end_at_turns(ts, events)
        elif self.state == State.PAUSED:
            assert self.cycle is not None
            low_since = self._low_since if self._low_since is not None else ts
            if self._active:
                self.cycle.pauses.append(ts - low_since)
                self.state = State.RUNNING
            elif ts - low_since >= self.end_delay and not (
                self.hold_end and ts - low_since < self.config.max_end_delay_s
            ):
                self._finish(low_since, events)
        else:  # FINISHED, ANTI_CREASE
            if self._candidate is None and self._rising():
                self._candidate = _Activity(start=ts)
            if self._candidate is not None:
                self._advance_candidate(ts, events)
            if self._candidate is None:
                self._expire(ts, events)

    def _advance_candidate(self, ts: float, events: list[Event]) -> None:
        cfg = self.config
        c = self._candidate
        assert c is not None
        after_cycle = self.state in (State.FINISHED, State.ANTI_CREASE)
        bursts = after_cycle and cfg.anti_crease

        if self._active:
            sustained = c.energy_wh >= cfg.start_energy_wh
            if bursts:
                sustained = sustained and ts - c.start >= cfg.burst_max_s
            if sustained:
                self._confirm(c, events)
            elif ts - c.start >= cfg.start_timeout_s:
                if after_cycle:
                    # Weak load after the end: not a cycle, let the state time out.
                    self._candidate = None
                else:
                    self._drop_candidate()
            return

        gap = BURST_GAP_S if bursts else cfg.start_gap_s
        if self._quiet_for(ts) < gap:
            if not after_cycle and ts - c.start >= cfg.start_timeout_s:
                self._drop_candidate()
            return

        self._candidate = None
        if bursts:
            if self.state == State.FINISHED:
                self.state = State.ANTI_CREASE
                events.append(Event(EventKind.ANTI_CREASE_STARTED, ts, self.last_cycle))
            self._last_burst_ts = self._low_since
            if self.last_cycle is not None:
                self.last_cycle.anti_crease_bursts += 1
        elif not after_cycle:
            self.state = State.IDLE

    def _drop_candidate(self) -> None:
        self._candidate = None
        self.state = State.IDLE

    def _confirm(self, c: _Activity, events: list[Event]) -> None:
        cfg = self.config
        previous = self.last_cycle
        resumable = (
            self.state in (State.FINISHED, State.ANTI_CREASE)
            and previous is not None
            and previous.end is not None
            and c.start - previous.end <= cfg.resume_window_s
        )
        if self.state == State.ANTI_CREASE:
            events.append(Event(EventKind.ANTI_CREASE_ENDED, c.start, previous))

        if resumable:
            assert previous is not None and previous.end is not None
            cycle = previous
            shift = c.start - cycle.start
            cycle.pauses.append(c.start - cycle.end)
            cycle.end = None
            cycle.resumed += 1
            cycle.interrupted = False
            cycle.energy_wh += c.energy_wh
            if c.cost:
                cycle.cost = (cycle.cost or 0.0) + c.cost
            cycle.peak_power_w = max(cycle.peak_power_w, c.peak_power_w)
            cycle.trace.extend((o + shift, p) for o, p in c.trace)
            if self._last_learned:
                self.learned.forget_last()
            self.cycle_count -= 1
            self.last_cycle = None
            kind = EventKind.RESUMED
        else:
            cycle = CycleRecord(
                start=c.start,
                energy_wh=c.energy_wh,
                peak_power_w=c.peak_power_w,
                trace=list(c.trace),
                cost=c.cost if c.cost or self.price is not None else None,
            )
            kind = EventKind.STARTED

        self.cycle = cycle
        self._candidate = None
        self._finished_ts = None
        self._last_burst_ts = None
        self.state = State.RUNNING
        events.append(Event(kind, c.start, cycle))

    def _end_at_turns(self, ts: float, events: list[Event]) -> None:
        """A cycle that has gone over into anti-crease drum turns ended where they began."""
        cycle = self.cycle
        assert cycle is not None
        if not cut_turns(cycle, self.config, ts - cycle.start):
            return
        assert cycle.end is not None
        self._finish(cycle.end, events)
        self.state = State.ANTI_CREASE
        self._last_burst_ts = ts
        events.append(Event(EventKind.ANTI_CREASE_STARTED, ts, cycle))

    def _finish(self, end: float, events: list[Event], interrupted: bool = False) -> None:
        cycle = self.cycle
        assert cycle is not None
        cycle.end = end
        cycle.interrupted = interrupted or cycle.duration() < self.config.min_cycle_s
        cutoff = end - cycle.start
        cycle.trace = [p for p in cycle.trace if p[0] <= cutoff]
        self._last_learned = self.learned.learn(cycle)
        self.cycle_count += 1
        self.last_cycle = cycle
        self.cycle = None
        self._finished_ts = end
        self.state = State.FINISHED
        events.append(Event(EventKind.FINISHED, end, cycle))

    def _expire(self, ts: float, events: list[Event]) -> None:
        cfg = self.config
        if self.state == State.FINISHED:
            if self._finished_ts is not None and ts - self._finished_ts >= cfg.finished_timeout_s:
                self.state = State.IDLE
        elif self.state == State.ANTI_CREASE:
            idle = self._last_burst_ts is not None and ts - self._last_burst_ts >= cfg.anti_crease_idle_s
            too_long = self._finished_ts is not None and ts - self._finished_ts >= cfg.anti_crease_max_s
            if idle or too_long:
                self.state = State.IDLE
                events.append(Event(EventKind.ANTI_CREASE_ENDED, ts, self.last_cycle))

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        c = self._candidate
        return {
            "state": self.state.value,
            "cycle": self.cycle.to_dict() if self.cycle else None,
            "last_cycle": self.last_cycle.to_dict() if self.last_cycle else None,
            "candidate": (
                {
                    "start": c.start,
                    "energy_wh": c.energy_wh,
                    "cost": c.cost,
                    "peak_power_w": c.peak_power_w,
                    "trace": [list(p) for p in c.trace],
                }
                if c
                else None
            ),
            "total_energy_wh": self.total_energy_wh,
            "cycle_count": self.cycle_count,
            "active": self._active,
            "power": self._power,
            "last_ts": self._last_ts,
            "low_since": self._low_since,
            "unknown_since": self._unknown_since,
            "finished_ts": self._finished_ts,
            "last_burst_ts": self._last_burst_ts,
            "last_learned": self._last_learned,
            "learned": self.learned.to_dict(),
        }

    @classmethod
    def from_dict(cls, config: DetectorConfig, data: dict[str, Any]) -> CycleDetector:
        det = cls(config, LearnedStats.from_dict(data.get("learned", {})))
        det.state = State(data.get("state", State.IDLE))
        det.cycle = CycleRecord.from_dict(data["cycle"]) if data.get("cycle") else None
        det.last_cycle = CycleRecord.from_dict(data["last_cycle"]) if data.get("last_cycle") else None
        if cand := data.get("candidate"):
            det._candidate = _Activity(
                start=cand["start"],
                energy_wh=cand["energy_wh"],
                cost=cand.get("cost", 0.0),
                peak_power_w=cand.get("peak_power_w", 0.0),
                trace=[tuple(p) for p in cand.get("trace", [])],
            )
        det.total_energy_wh = data.get("total_energy_wh", 0.0)
        det.cycle_count = data.get("cycle_count", 0)
        det._active = data.get("active", False)
        det._power = data.get("power")
        det._last_ts = data.get("last_ts")
        det._low_since = data.get("low_since")
        det._unknown_since = data.get("unknown_since")
        det._finished_ts = data.get("finished_ts")
        det._last_burst_ts = data.get("last_burst_ts")
        det._last_learned = data.get("last_learned", False)
        # A state that needs data it does not have falls back safely.
        if det.state in ACTIVE_STATES and det.cycle is None:
            det.state = State.IDLE
        if det.state == State.STARTING and det._candidate is None:
            det.state = State.IDLE
        return det
