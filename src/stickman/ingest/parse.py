"""Parse narration scripts into RawLines (spec §4.1)."""

from __future__ import annotations

import re

from stickman.ingest.models import RawLine

_TIMESTAMPED = re.compile(
    r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\s*[:\-–]?\s+(.+)$"
)


class ScriptParseError(ValueError):
    def __init__(self, message: str, line_number: int | None = None) -> None:
        self.line_number = line_number
        super().__init__(f"line {line_number}: {message}" if line_number else message)


def _fraction(digits: str | None) -> float:
    return int(digits.ljust(3, "0")) / 1000 if digits else 0.0


def parse_timestamped(text: str) -> list[RawLine]:
    lines: list[RawLine] = []
    for source_line, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        match = _TIMESTAMPED.match(raw)
        if match is None:
            raise ScriptParseError(f"not a timestamped line: {raw.strip()[:40]!r}", source_line)
        hours, minutes, seconds, fraction, body = match.groups()
        minutes_i, seconds_i = int(minutes), int(seconds)
        if seconds_i >= 60 or (hours is not None and minutes_i >= 60):
            raise ScriptParseError("minutes and seconds must be below 60", source_line)
        start = int(hours or 0) * 3600 + minutes_i * 60 + seconds_i + _fraction(fraction)
        lines.append(
            RawLine(number=len(lines) + 1, start=start, text=body.strip(), source_line=source_line)
        )
    return lines


_SRT_TIMES = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _hms(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + _fraction(millis)


def parse_srt(text: str) -> list[RawLine]:
    rows = text.splitlines()
    cues: list[RawLine] = []
    i = 0
    while i < len(rows):
        if not rows[i].strip():
            i += 1
            continue
        block_start = i + 1  # 1-based line number of the block's first line
        block: list[str] = []
        while i < len(rows) and rows[i].strip():
            block.append(rows[i])
            i += 1
        offset = 1 if block[0].strip().isdigit() else 0
        time_line = block_start + offset
        match = _SRT_TIMES.search(block[offset]) if offset < len(block) else None
        if match is None:
            raise ScriptParseError(
                "expected an SRT time line 'HH:MM:SS,mmm --> HH:MM:SS,mmm'", time_line
            )
        body = " ".join(row.strip() for row in block[offset + 1 :] if row.strip())
        if not body:
            raise ScriptParseError("SRT cue has no text", time_line)
        cues.append(
            RawLine(
                number=len(cues) + 1,
                start=_hms(*match.group(1, 2, 3, 4)),
                text=body,
                srt_end=_hms(*match.group(5, 6, 7, 8)),
                source_line=time_line,
            )
        )
    return cues


def _check_increasing(lines: list[RawLine]) -> None:
    for previous, current in zip(lines, lines[1:]):
        if current.start <= previous.start:
            raise ScriptParseError(
                f"timestamp {current.start:.3f}s is not after the previous line ({previous.start:.3f}s)",
                current.source_line,
            )


def parse_script(text: str) -> list[RawLine]:
    text = text.lstrip("﻿")
    lines = parse_srt(text) if "-->" in text else parse_timestamped(text)
    if not lines:
        raise ScriptParseError("the script contains no lines")
    _check_increasing(lines)
    return lines
