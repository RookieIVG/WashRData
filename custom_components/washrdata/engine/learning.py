"""Thresholds learned from the appliance's own completed cycles."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .model import CycleRecord, DetectorConfig

HISTORY = 20
# Safety margin on top of the longest quiet spell seen inside a cycle.
END_DELAY_FACTOR = 1.25
END_DELAY_MARGIN_S = 60.0


@dataclass
class LearnedStats:
    """Longest in-cycle pause of recent cycles, used to size the end delay.

    A program that soaks for 12 minutes teaches the detector not to end a
    cycle after 5 quiet minutes. The end delay follows the second longest
    entry, so a pause must be seen twice: a door opened once for ten minutes
    mid-cycle must not delay every later end (found on a real dryer, whose
    end delay grew from 3 to 13 minutes that way). A cycle that was ended too
    early and then resumed is certain evidence and counts twice at once, so
    that mistake is still not repeated.
    """

    longest_pauses: deque[float] = field(default_factory=lambda: deque(maxlen=HISTORY))
    # Entries the last learn added (two for a resumed cycle), for forget_last.
    _last_count: int = field(default=1, repr=False)

    def learn(self, cycle: CycleRecord) -> bool:
        """Record a finished cycle. Returns whether it was used."""
        if cycle.interrupted:
            return False
        self._last_count = 2 if cycle.resumed else 1
        for _ in range(self._last_count):
            self.longest_pauses.append(cycle.longest_pause)
        return True

    def forget_last(self) -> None:
        """Undo the last `learn`, for a cycle that turned out not to be over."""
        for _ in range(min(self._last_count, len(self.longest_pauses))):
            self.longest_pauses.pop()
        self._last_count = 1

    def replace(self, old: float, new: list[float]) -> bool:
        """Swap one cycle's entries (two for a resumed cycle) for others, e.g. after splitting it."""
        entries = list(self.longest_pauses)
        matches = [i for i, value in enumerate(entries) if abs(value - old) < 1e-6]
        if not matches:
            return False
        for i in reversed(matches):
            del entries[i]
        entries[matches[0] : matches[0]] = new
        self.longest_pauses = deque(entries[-HISTORY:], maxlen=HISTORY)
        return True

    def end_delay(self, config: DetectorConfig) -> float:
        pauses = sorted(self.longest_pauses, reverse=True)
        learned = (pauses[1] if len(pauses) > 1 else 0.0) * END_DELAY_FACTOR
        if learned > 0:
            learned += END_DELAY_MARGIN_S
        return min(config.max_end_delay_s, max(config.end_delay_s, learned))

    def to_dict(self) -> dict[str, Any]:
        return {"longest_pauses": list(self.longest_pauses)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearnedStats:
        stats = cls()
        stats.longest_pauses.extend(float(v) for v in data.get("longest_pauses", []))
        return stats
