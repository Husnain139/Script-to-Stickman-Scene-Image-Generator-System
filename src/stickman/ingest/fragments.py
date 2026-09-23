"""Rule-based fragment hints for the analyse stage (spec §4.4)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from stickman.ingest.models import TimedLine

_TERMINAL = (".", "!", "?", "…", '"', "“", "”", "'", ")")
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.", "vs.", "etc.", "e.g.", "i.e.", "u.s."}
_INITIAL = re.compile(r"^[A-Z]\.$")


def is_likely_fragment(text: str, next_text: str | None) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if not stripped.endswith(_TERMINAL):
        return True
    last_token = stripped.split()[-1]
    if _INITIAL.match(last_token) or last_token.lower() in _ABBREVIATIONS:
        return True
    return bool(next_text and next_text.lstrip()[:1].islower())


def fragment_hints(lines: Sequence[TimedLine]) -> list[int]:
    hints = []
    for index, line in enumerate(lines):
        next_text = lines[index + 1].text if index + 1 < len(lines) else None
        if is_likely_fragment(line.text, next_text):
            hints.append(line.number)
    return hints
