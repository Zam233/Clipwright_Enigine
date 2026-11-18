"""Shared beat/event grid for animation and audio."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BeatGrid:
    bpm: float = 120.0
    offset_sec: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def beat_sec(self) -> float:
        return 60.0 / max(1.0, float(self.bpm))

    def at(self, beat: int) -> float:
        return round(self.offset_sec + beat * self.beat_sec, 4)

    def snap(self, sec: float) -> float:
        if self.beat_sec <= 0:
            return round(float(sec), 4)
        n = round((float(sec) - self.offset_sec) / self.beat_sec)
        return self.at(n)

    def add(self, at_sec: float, event: str, **payload: Any) -> dict[str, Any]:
        item = {"at_sec": self.snap(at_sec), "event": event, **payload}
        self.events.append(item)
        self.events.sort(key=lambda e: e["at_sec"])
        return item

    def as_dict(self) -> dict[str, Any]:
        return {"bpm": self.bpm, "beat_sec": round(self.beat_sec, 4), "events": self.events}


def beats_for_duration(duration_sec: float, bpm: float = 120.0) -> list[dict[str, Any]]:
    grid = BeatGrid(bpm=bpm)
    dur = max(0.5, float(duration_sec))
    grid.add(0.0, "entry")
    grid.add(dur * 0.4, "main")
    grid.add(dur * 0.8, "hold")
    grid.add(dur, "exit")
    return grid.as_dict()["events"]
