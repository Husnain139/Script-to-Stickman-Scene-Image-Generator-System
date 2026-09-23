"""Scenes: script lines merged into one picture's worth of narration (spec §4.4)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stickman.ingest.models import TimedLine


class GroupingError(ValueError):
    """The line groups don't form a valid partition of the script."""


@dataclass(frozen=True)
class Scene:
    number: int
    lines: tuple[TimedLine, ...]

    @property
    def start(self) -> float:
        return self.lines[0].start

    @property
    def end(self) -> float:
        return self.lines[-1].end

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def words(self) -> int:
        return sum(line.words for line in self.lines)

    @property
    def source_text(self) -> str:
        return " ".join(line.text for line in self.lines)


def build_scenes(
    lines: Sequence[TimedLine], groups: Sequence[Sequence[int]], *, max_lines: int
) -> list[Scene]:
    for group in groups:
        if not group:
            raise GroupingError("groups must not be empty")
        if len(group) > max_lines:
            raise GroupingError(f"group {list(group)} has more than {max_lines} lines")
    expected = [line.number for line in lines]
    listed = [number for group in groups for number in group]
    if listed != expected:
        raise GroupingError("groups must list every line exactly once, in order")
    by_number = {line.number: line for line in lines}
    return [
        Scene(index, tuple(by_number[n] for n in group)) for index, group in enumerate(groups, 1)
    ]


def merge_short_scenes(
    scenes: Sequence[Scene], *, min_scene_seconds: float, max_lines: int
) -> list[Scene]:
    if min_scene_seconds <= 0:
        return list(scenes)
    merged: list[list[TimedLine]] = []
    for scene in scenes:
        lines = list(scene.lines)
        if (
            merged
            and scene.duration < min_scene_seconds
            and len(merged[-1]) + len(lines) <= max_lines
        ):
            merged[-1].extend(lines)
        else:
            merged.append(lines)
    if len(merged) >= 2:
        first = merged[0]
        if first[-1].end - first[0].start < min_scene_seconds and len(first) + len(merged[1]) <= max_lines:
            merged[1] = first + merged[1]
            merged.pop(0)
    return [Scene(index, tuple(lines)) for index, lines in enumerate(merged, 1)]
