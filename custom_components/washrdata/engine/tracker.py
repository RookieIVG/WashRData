"""Detector, program library, cycle history and matcher of one appliance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bursts import cut_turns
from .detector import CycleDetector
from .evaluation import select_references
from .matching import MatchResult, ProgramMatcher, _Snapshot
from .model import CycleRecord, DetectorConfig, Event, EventKind, LabelSource, State
from .split import split_cycle

KEEP_RECENT = 50
KEEP_LABELLED_PER_PROGRAM = 10
# Recognised programs with at least this confidence hold off the end while
# they still have this much to go.
HOLD_CONFIDENCE = 0.5
HOLD_MIN_REMAINING_S = 600.0

CURRENT = "current"
LAST = "last"


@dataclass
class ProgramAccuracy:
    cycles: int = 0
    hits: int = 0

    @property
    def rate(self) -> float | None:
        return self.hits / self.cycles if self.cycles else None


@dataclass(frozen=True)
class Accuracy:
    total: ProgramAccuracy
    programs: dict[str, ProgramAccuracy]


class Tracker:
    """Everything WashRData knows about one appliance, without Home Assistant.

    Programs are names. A program's references are its most recent labelled
    cycles (manual labels first). Labelling a finished cycle, by hand or
    automatically after a confident match, is how programs are learnt.
    """

    def __init__(
        self,
        config: DetectorConfig,
        detector: CycleDetector | None = None,
        programs: list[str] | None = None,
        history: list[CycleRecord] | None = None,
    ) -> None:
        self.detector = detector or CycleDetector(config)
        self.programs: list[str] = list(programs or [])
        self.history: list[CycleRecord] = list(history or [])
        self.match: MatchResult | None = None
        self._matcher: ProgramMatcher | None = None
        self._matcher_cycle: str | None = None
        self._checkpoint: _Snapshot | None = None

    @property
    def config(self) -> DetectorConfig:
        return self.detector.config

    @property
    def cycle(self) -> CycleRecord | None:
        return self.detector.cycle

    @property
    def last_cycle(self) -> CycleRecord | None:
        return self.history[-1] if self.history else None

    @property
    def state(self) -> State:
        return self.detector.state

    # -- samples ---------------------------------------------------------

    def update(self, ts: float, power: float | None) -> list[Event]:
        return self._after(ts, self.detector.update(ts, power))

    def tick(self, ts: float) -> list[Event]:
        return self._after(ts, self.detector.tick(ts))

    def _after(self, ts: float, events: list[Event]) -> list[Event]:
        out: list[Event] = []
        for event in events:
            if event.kind == EventKind.FINISHED and event.cycle is not None:
                self._on_finished(event.cycle)
            elif event.kind == EventKind.RESUMED and event.cycle is not None:
                self.history = [c for c in self.history if c.start != event.cycle.start]
            out.append(event)

        cycle = self.detector.cycle
        if cycle is None:
            self.match = None
            self.detector.hold_end = False
            return out

        matcher = self._matcher_for(cycle)
        if self.detector.state == State.PAUSED:
            if self._checkpoint is None:
                self._checkpoint = matcher.snapshot()
        else:
            self._checkpoint = None
        matcher.update(cycle, ts)
        forced = cycle.program if cycle.program_source == LabelSource.MANUAL else None
        previous = self.match.program if self.match else None
        self.match = matcher.result(cycle.duration(ts), forced, self.config.show_confidence)
        if self.match and self.match.program and self.match.program != previous:
            out.append(Event(EventKind.PROGRAM_DETECTED, ts, cycle))
        self.detector.hold_end = bool(
            self.match
            and self.match.program
            and self.match.confidence >= HOLD_CONFIDENCE
            and (self.match.remaining_s or 0.0) >= HOLD_MIN_REMAINING_S
        )
        return out

    def _matcher_for(self, cycle: CycleRecord) -> ProgramMatcher:
        if self._matcher is None or self._matcher_cycle != cycle.id:
            self._matcher = ProgramMatcher(self.references())
            self._matcher_cycle = cycle.id
            self._checkpoint = None
        return self._matcher

    def _on_finished(self, cycle: CycleRecord) -> None:
        # Recognition runs for labelled cycles too, so its accuracy can be measured.
        if self._matcher is not None and self._matcher_cycle == cycle.id:
            cycle.known_programs = sorted(self._matcher.programs)
            final = self._matcher.final(self._checkpoint)
            if final is not None:
                program, conf = final
                cycle.recognised = program
                cycle.match_confidence = round(conf, 3)
                if (
                    cycle.program is None
                    and conf >= self.config.auto_label_confidence
                    and not cycle.interrupted
                ):
                    cycle.program = program
                    cycle.program_source = LabelSource.AUTO
            elif cycle.known_programs:
                cycle.match_confidence = 0.0
        self.history.append(cycle)
        self._trim()
        self._invalidate()
        self.match = None

    def _invalidate(self) -> None:
        self._matcher = None
        self._matcher_cycle = None
        self._checkpoint = None

    def _trim(self) -> None:
        keep = {c.id for c in self.history[-KEEP_RECENT:]}
        for program in self.programs:
            labelled = [c for c in self.history if c.program == program]
            keep.update(c.id for c in labelled[-KEEP_LABELLED_PER_PROGRAM:])
        self.history = [c for c in self.history if c.id in keep]

    # -- programs --------------------------------------------------------

    def references(self) -> dict[str, list[CycleRecord]]:
        return select_references(self.history, self.programs)

    def find_cycle(self, target: str) -> CycleRecord | None:
        if target == CURRENT:
            return self.detector.cycle
        if target == LAST:
            return self.last_cycle
        return next((c for c in self.history if c.id == target), None)

    def label(self, target: str, program: str | None) -> CycleRecord:
        """Label a cycle by hand; `None` removes the label.

        `target` is "current", "last" or a cycle id. Unknown programs are created.
        """
        cycle = self.find_cycle(target)
        if cycle is None:
            raise KeyError(target)
        if program is not None:
            self.create_program(program)
        cycle.program = program
        cycle.program_source = LabelSource.MANUAL if program else None
        if cycle is not self.detector.cycle:
            self._invalidate()
        return cycle

    def split(self, cycle_id: str, at_s: float) -> tuple[CycleRecord, CycleRecord]:
        """Split a stored cycle at `at_s` seconds after its start; both parts are unlabelled.

        The gap between the parts no longer counts as a pause, so the learned
        end delay forgets it.
        """
        cycle = next((c for c in self.history if c.id == cycle_id), None)
        if cycle is None:
            raise KeyError(cycle_id)
        first, second = split_cycle(cycle, at_s, self.config)
        i = self.history.index(cycle)
        self.history[i : i + 1] = [first, second]
        detector = self.detector
        if not cycle.interrupted:
            parts = [p.longest_pause for p in (first, second) if not p.interrupted]
            detector.learned.replace(cycle.longest_pause, parts)
        if detector.last_cycle is not None and detector.last_cycle.id == cycle.id:
            detector.last_cycle = second
        detector.cycle_count += 1
        self._invalidate()
        return first, second

    def trim_turns(self) -> int:
        """End stored cycles where trailing anti-crease drum turns began.

        For cycles recorded before the detector recognised those turns; it
        does nothing to a cycle that does not end in them, so it can run on
        every start.
        """
        if not self.config.anti_crease:
            return 0
        trimmed = sum(cut_turns(c, self.config, c.duration()) for c in self.history)
        if trimmed:
            self._invalidate()
        return trimmed

    def create_program(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("empty program name")
        if name not in self.programs:
            self.programs.append(name)

    def rename_program(self, old: str, new: str) -> None:
        new = new.strip()
        if old not in self.programs:
            raise KeyError(old)
        if not new:
            raise ValueError("empty program name")
        if new in self.programs and new != old:
            raise ValueError(f"program {new!r} exists")
        self.programs[self.programs.index(old)] = new
        for c in self._all_cycles():
            if c.program == old:
                c.program = new
            if c.recognised == old:
                c.recognised = new
            c.known_programs = [new if p == old else p for p in c.known_programs]
        self._invalidate()

    def delete_program(self, name: str) -> None:
        if name not in self.programs:
            raise KeyError(name)
        self.programs.remove(name)
        for c in self._all_cycles():
            if c.program == name:
                c.program = None
                c.program_source = None
            if c.recognised == name:
                c.recognised = None
            if name in c.known_programs:
                c.known_programs.remove(name)
        self._invalidate()

    # -- accuracy --------------------------------------------------------

    def accuracy(self) -> Accuracy:
        """How often recognition named the program the cycle ended up with.

        Only finished, complete, labelled cycles whose program already had
        references count. A hit is a confident recognition (the threshold of
        the automatic label) of exactly that program, so every correction of
        an automatic label, and every manual label recognition missed, is a miss.
        """
        total = ProgramAccuracy()
        per_program: dict[str, ProgramAccuracy] = {}
        for c in self.history:
            if c.interrupted or not c.program or c.program not in c.known_programs:
                continue
            hit = (
                c.recognised == c.program and (c.match_confidence or 0.0) >= self.config.auto_label_confidence
            )
            for stats in (total, per_program.setdefault(c.program, ProgramAccuracy())):
                stats.cycles += 1
                stats.hits += hit
        return Accuracy(total, per_program)

    def _all_cycles(self) -> list[CycleRecord]:
        return [*self.history, *([self.detector.cycle] if self.detector.cycle else [])]

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector.to_dict(),
            "programs": list(self.programs),
            "cycles": [c.to_dict() for c in self.history],
        }

    @classmethod
    def from_dict(cls, config: DetectorConfig, data: dict[str, Any]) -> Tracker:
        return cls(
            config,
            detector=CycleDetector.from_dict(config, data.get("detector", {})),
            programs=data.get("programs", []),
            history=[CycleRecord.from_dict(c) for c in data.get("cycles", [])],
        )
