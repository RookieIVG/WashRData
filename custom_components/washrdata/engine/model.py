"""Data model of the detection engine: states, configuration, cycle records.

This package must never import Home Assistant. It is driven by plain
(timestamp, watts) samples, which keeps it fast to test and reusable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from enum import StrEnum
from typing import Any


class DeviceType(StrEnum):
    WASHER = "washer"
    DRYER = "dryer"


class State(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    ANTI_CREASE = "anti_crease"


class EventKind(StrEnum):
    STARTED = "cycle_started"
    FINISHED = "cycle_finished"
    RESUMED = "cycle_resumed"
    ANTI_CREASE_STARTED = "anti_crease_started"
    ANTI_CREASE_ENDED = "anti_crease_ended"
    PROGRAM_DETECTED = "program_detected"


class LabelSource(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


ACTIVE_STATES = frozenset({State.RUNNING, State.PAUSED})

# Default confidence thresholds of program recognition: a program is shown
# from SHOW_CONFIDENCE, a finished cycle is labelled from AUTO_LABEL_CONFIDENCE.
SHOW_CONFIDENCE = 0.25
AUTO_LABEL_CONFIDENCE = 0.6


@dataclass(frozen=True)
class DetectorConfig:
    """Thresholds of the detector. All times in seconds, powers in watts."""

    # Hysteresis: at or above start_power the appliance is active, below
    # stop_power it is inactive, in between it keeps its previous activity.
    start_power_w: float
    stop_power_w: float
    # Energy an activity must draw before it counts as a cycle (filters blips).
    start_energy_wh: float
    # A starting activity may go quiet this long before it is dropped.
    start_gap_s: float
    # A start not confirmed within this time is dropped.
    start_timeout_s: float
    # Quiet time that ends a cycle. Learning may raise it up to max_end_delay_s.
    end_delay_s: float
    max_end_delay_s: float
    # Finished cycles shorter than this are flagged as interrupted.
    min_cycle_s: float
    # Sustained activity this soon after an end re-opens the same cycle.
    resume_window_s: float
    # The finished state falls back to idle after this time.
    finished_timeout_s: float
    # Anti-crease: short drum turns after the end, not a new cycle.
    anti_crease: bool
    burst_max_s: float
    anti_crease_idle_s: float
    anti_crease_max_s: float
    # Without a valid reading for this long a running cycle is ended.
    unavailable_timeout_s: float
    # Gaps between samples longer than this are not integrated (restarts).
    max_sample_gap_s: float = 900.0
    # Program recognition thresholds, adjustable per appliance.
    show_confidence: float = SHOW_CONFIDENCE
    auto_label_confidence: float = AUTO_LABEL_CONFIDENCE
    # Sensor drying: the run time depends on the load, so the remaining time
    # is only given in the cooling phase at the end (engine/drying.py).
    sensor_drying: bool = False

    def with_overrides(self, overrides: dict[str, Any]) -> DetectorConfig:
        known = {f.name for f in fields(self)}
        return replace(self, **{k: v for k, v in overrides.items() if k in known})


PRESETS: dict[DeviceType, DetectorConfig] = {
    DeviceType.WASHER: DetectorConfig(
        start_power_w=8.0,
        stop_power_w=4.0,
        start_energy_wh=2.0,
        start_gap_s=120.0,
        start_timeout_s=900.0,
        end_delay_s=300.0,
        max_end_delay_s=3600.0,
        min_cycle_s=600.0,
        resume_window_s=900.0,
        finished_timeout_s=1800.0,
        anti_crease=False,
        burst_max_s=300.0,
        anti_crease_idle_s=1800.0,
        anti_crease_max_s=7200.0,
        unavailable_timeout_s=900.0,
    ),
    DeviceType.DRYER: DetectorConfig(
        start_power_w=20.0,
        stop_power_w=8.0,
        start_energy_wh=5.0,
        start_gap_s=120.0,
        start_timeout_s=900.0,
        end_delay_s=180.0,
        max_end_delay_s=1200.0,
        min_cycle_s=900.0,
        resume_window_s=600.0,
        finished_timeout_s=1800.0,
        anti_crease=True,
        sensor_drying=True,
        burst_max_s=240.0,
        anti_crease_idle_s=900.0,
        anti_crease_max_s=7200.0,
        unavailable_timeout_s=900.0,
    ),
}


@dataclass
class CycleRecord:
    """One appliance run. `end` is None while the cycle is open."""

    start: float
    end: float | None = None
    energy_wh: float = 0.0
    peak_power_w: float = 0.0
    pauses: list[float] = field(default_factory=list)
    resumed: int = 0
    interrupted: bool = False
    anti_crease_bursts: int = 0
    program: str | None = None
    program_source: LabelSource | None = None
    # Closed-end recognition at the end of the cycle: the best program and its
    # confidence, kept when the label is changed later. Confidence 0 with no
    # program means references existed but none fitted.
    match_confidence: float | None = None
    recognised: str | None = None
    # Programs that had references while the cycle ran; only these could be recognised.
    known_programs: list[str] = field(default_factory=list)
    # Recognition filled in later by the evaluation (cycles from before 0.2.4).
    recognition_retro: bool = False
    # Energy cost while a price was known, in the currency of the price.
    cost: float | None = None
    # (seconds since start, watts), change-compressed.
    trace: list[tuple[float, float]] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(int(self.start))

    def duration(self, now: float | None = None) -> float:
        end = self.end if self.end is not None else now
        return max(0.0, (end if end is not None else self.start) - self.start)

    @property
    def longest_pause(self) -> float:
        return max(self.pauses, default=0.0)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trace"] = [list(p) for p in self.trace]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CycleRecord:
        data = dict(data)
        data["trace"] = [tuple(p) for p in data.get("trace", [])]
        if data.get("program_source"):
            data["program_source"] = LabelSource(data["program_source"])
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class Event:
    kind: EventKind
    ts: float
    cycle: CycleRecord | None = None
