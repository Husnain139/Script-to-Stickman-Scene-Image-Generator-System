"""Script line models."""

from __future__ import annotations

from dataclasses import dataclass

from stickman.ingest.words import count_words


@dataclass(frozen=True)
class RawLine:
    """One parsed script entry before end times are known."""

    number: int
    start: float
    text: str
    srt_end: float | None = None
    source_line: int = 0


@dataclass(frozen=True)
class TimedLine:
    """One script line with its start and end time."""

    number: int
    start: float
    end: float
    text: str

    @property
    def words(self) -> int:
        return count_words(self.text)

    @property
    def duration(self) -> float:
        return self.end - self.start
