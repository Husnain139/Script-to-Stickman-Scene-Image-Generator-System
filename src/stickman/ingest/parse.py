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


def _check_increasing(lines: list[RawLine]) -> None:
    for previous, current in zip(lines, lines[1:]):
        if current.start <= previous.start:
            raise ScriptParseError(
                f"timestamp {current.start:.3f}s is not after the previous line ({previous.start:.3f}s)",
                current.source_line,
            )


def parse_script(text: str) -> list[RawLine]:
    text = text.lstrip("﻿")
    lines = parse_timestamped(text)
    if not lines:
        raise ScriptParseError("the script contains no lines")
    _check_increasing(lines)
    return lines
