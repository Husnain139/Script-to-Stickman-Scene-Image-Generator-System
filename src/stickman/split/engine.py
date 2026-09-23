"""Split engine: decides long-scene splits with plain code (spec §4.5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from stickman.settings import SplitSettings
from stickman.split.scenes import Scene

SplitStatus = Literal["none", "split", "no_valid_cut"]
_EPSILON = 1e-9


@dataclass(frozen=True)
class SplitDecision:
    status: SplitStatus
    candidates: tuple[int, ...] = ()
    cut_after_word: int | None = None
    cut_time: float | None = None


def needs_split(scene: Scene, s: SplitSettings) -> bool:
    return scene.duration >= s.split_seconds or scene.words >= s.split_words


def cut_time(scene: Scene, k: int) -> float:
    """Time of a cut after word k, interpolated inside the line that holds word k."""
    if not 1 <= k <= scene.words - 1:
        raise ValueError(f"cut after word {k} is outside 1..{scene.words - 1}")
    words_before = 0
    for line in scene.lines:
        if k <= words_before + line.words:
            position = k - words_before
            return line.start + (position / line.words) * (line.end - line.start)
        words_before += line.words
    raise AssertionError("unreachable: k is within the scene's word count")


def decide_split(scene: Scene, candidates: Sequence[int], s: SplitSettings) -> SplitDecision:
    listed = tuple(candidates)
    if not needs_split(scene, s):
        return SplitDecision("none")
    for k in listed:
        if not 1 <= k <= scene.words - 1:
            continue
        t = cut_time(scene, k)
        if t - scene.start >= s.min_part_seconds - _EPSILON and scene.end - t >= s.min_part_seconds - _EPSILON:
            return SplitDecision("split", listed, k, t)
    return SplitDecision("no_valid_cut", listed)
