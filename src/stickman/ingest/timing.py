"""Line end times and narrator pace (spec §4.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stickman.ingest.models import RawLine, TimedLine
from stickman.ingest.parse import ScriptParseError
from stickman.ingest.words import count_words
from stickman.settings import TimingSettings


@dataclass(frozen=True)
class Timeline:
    lines: tuple[TimedLine, ...]
    pace_wps: float
    end: float
    pace_measured: bool


def measure_pace(raw: Sequence[RawLine], timing: TimingSettings) -> tuple[float, bool]:
    if len(raw) < timing.min_pace_lines:
        return timing.fallback_wps, False
    span = raw[-1].start - raw[0].start
    words = sum(count_words(line.text) for line in raw[:-1])
    if span <= 0 or words == 0:
        return timing.fallback_wps, False
    return words / span, True


def build_timeline(
    raw: Sequence[RawLine], timing: TimingSettings, *, duration: float | None = None
) -> Timeline:
    pace, measured = measure_pace(raw, timing)
    last = raw[-1]
    if duration is not None:
        if duration <= last.start:
            raise ScriptParseError(
                f"--duration {duration:.3f}s must be after the last line's start ({last.start:.3f}s)"
            )
        end = duration
    elif last.srt_end is not None and last.srt_end > last.start:
        end = last.srt_end
    else:
        end = last.start + count_words(last.text) / pace
    timed = []
    for index, line in enumerate(raw):
        start = 0.0 if index == 0 else line.start
        stop = raw[index + 1].start if index + 1 < len(raw) else end
        timed.append(TimedLine(line.number, start, stop, line.text))
    return Timeline(lines=tuple(timed), pace_wps=pace, end=end, pace_measured=measured)


def length_warning(timeline: Timeline, max_minutes: float) -> str | None:
    if timeline.end <= max_minutes * 60:
        return None
    minutes, seconds = divmod(round(timeline.end), 60)
    return (
        f"Script runs {minutes}:{seconds:02d}, longer than the {max_minutes:g}-minute design limit. "
        "It will be processed, but it's outside the tested range."
    )
