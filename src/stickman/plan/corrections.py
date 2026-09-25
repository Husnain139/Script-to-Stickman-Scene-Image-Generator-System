"""Corrections applied at scene level, and the "Check merges" list (spec §4.4, §6.2)."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stickman.ingest.models import TimedLine
from stickman.plan.models import Correction, MergeCheck
from stickman.split.scenes import Scene

if TYPE_CHECKING:  # analyse.py uses this module's locator, so this import is for annotations only
    from stickman.plan.analyse import LineCorrection

_WORD_CHAR = re.compile(r"\w")


def find_whole_words(text: str, phrase: str) -> list[tuple[int, int]]:
    """Every (start, end) where `phrase` occurs in `text` as whole words, overlapping ones included.

    A word edge is required only where `phrase` itself starts or ends with a word character, so a
    phrase may begin or end with punctuation (", 2" or "Ju/'hoansi,").
    """
    before = r"(?<!\w)" if _WORD_CHAR.match(phrase[:1]) else ""
    after = r"(?!\w)" if _WORD_CHAR.match(phrase[-1:]) else ""
    pattern = re.compile(f"(?=({before}{re.escape(phrase)}{after}))")
    return [match.span(1) for match in pattern.finditer(text)]


def line_groups(groups: Sequence[Sequence[int]]) -> dict[int, int]:
    """Line number -> 1-based position of the group holding it."""
    return {number: position for position, group in enumerate(groups, 1) for number in group}


@dataclass(frozen=True)
class Placement:
    """Where one correction goes: its group (1-based position) and its span in the group's text."""

    index: int  # position in the analyse stage's corrections list
    group: int
    start: int
    end: int
    correction: LineCorrection


def place_corrections(
    groups: Sequence[Sequence[int]], texts: Sequence[str], corrections: Sequence[LineCorrection]
) -> tuple[list[Placement], list[str]]:
    """Locate each correction in the text of the group holding its line (spec §6.2).

    Its `from` must occur there exactly once as whole words, and must not overlap another
    correction in the same group. The errors are the analyse stage check's; applying
    corrections uses the same placements, so a correction lands exactly where it was checked.
    """
    group_of = line_groups(groups)
    placements: list[Placement] = []
    errors: list[str] = []
    for index, correction in enumerate(corrections):
        where = f"corrections[{index}]"
        group = group_of.get(correction.line)
        if group is None:
            errors.append(f"{where}.line: {correction.line} is not a line number")
            continue
        text = texts[group - 1]
        spans = find_whole_words(text, correction.from_)
        if not spans:
            errors.append(
                f'{where}.from: "{correction.from_}" does not occur as whole words in the text of the group '
                f'holding line {correction.line}: "{text}"'
            )
            continue
        if len(spans) > 1:
            errors.append(
                f'{where}.from: "{correction.from_}" must appear exactly once as whole words in its group, but '
                f'appears {len(spans)} times in the text of the group holding line {correction.line}: "{text}"; '
                "quote enough surrounding words to make it unique"
            )
            continue
        [(start, end)] = spans
        clash = next((p for p in placements if p.group == group and p.start < end and start < p.end), None)
        if clash is not None:
            errors.append(
                f'{where}.from: "{correction.from_}" overlaps corrections[{clash.index}].from '
                f'("{clash.correction.from_}") in the same group; give one correction for the whole text'
            )
            continue
        placements.append(Placement(index, group, start, end, correction))
    return placements, errors


Edit = tuple[int, int, str]  # (start, end, new text): a span of the source text and its replacement


def _replace(text: str, edits: Sequence[Edit]) -> str:
    """`text` with each span replaced by its new text. The spans don't overlap."""
    pieces: list[str] = []
    at = 0
    for start, end, new in sorted(edits):
        pieces += [text[at:start], new]
        at = end
    pieces.append(text[at:])
    return "".join(pieces)


def _normalise(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class CorrectedScene:
    """A scene's source text and its corrections, placed in that text (spec §4.6, §6.2)."""

    source_text: str
    edits: tuple[Edit, ...]

    @property
    def corrected_text(self) -> str:
        return _replace(self.source_text, self.edits)

    def part_texts(self, cut_after_word: int) -> tuple[str, str] | None:
        """The corrected text of each part when the scene is cut after word `cut_after_word`.

        Each part is its own words with only its own corrections applied, so the two parts
        joined with a space are the scene's corrected text. None when a correction straddles
        the cut: code can't tell which words of its replacement belong to which part.
        """
        words = [match.span() for match in re.finditer(r"\S+", self.source_text)]
        a_end, b_start = words[cut_after_word - 1][1], words[cut_after_word][0]
        in_a = [edit for edit in self.edits if edit[1] <= a_end]
        in_b = [(start - b_start, end - b_start, new) for start, end, new in self.edits if start >= b_start]
        if len(in_a) + len(in_b) != len(self.edits):
            return None
        return (
            _normalise(_replace(self.source_text[:a_end], in_a)),
            _normalise(_replace(self.source_text[b_start:], in_b)),
        )


def correct_scenes(
    scenes: Sequence[Scene],
    groups: Sequence[Sequence[int]],
    texts: Sequence[str],
    corrections: Sequence[LineCorrection],
) -> tuple[dict[int, CorrectedScene], list[Correction]]:
    """Each scene's text with its corrections placed, and its corrections keyed by scene ID.

    A correction names the line where its text starts; it belongs to the group holding that
    line. A scene's text is its groups' texts joined with one space, so a correction still
    lands in the right group after short-scene merging has combined groups (§4.4).
    """
    placements, errors = place_corrections(groups, texts, corrections)
    if errors:  # a bug: the analyse stage check rejects these
        raise ValueError("corrections failed their check: " + "; ".join(errors))
    by_group: dict[int, list[Placement]] = {}
    for placement in placements:
        by_group.setdefault(placement.group, []).append(placement)
    corrected: dict[int, CorrectedScene] = {}
    stored: list[Correction] = []
    for scene in scenes:
        if not scene.groups:
            raise ValueError(f"scene {scene.number} has no group provenance")
        edits: list[Edit] = []
        offset = 0
        for group in scene.groups:
            for p in by_group.get(group, []):
                edits.append((offset + p.start, offset + p.end, p.correction.to))
                stored.append(
                    Correction(scene=f"{scene.number:03d}", from_=p.correction.from_, to=p.correction.to,
                               reason=p.correction.reason)
                )
            offset += len(texts[group - 1]) + 1  # the space that joins it to the next group
        source = " ".join(texts[group - 1] for group in scene.groups)
        corrected[scene.number] = CorrectedScene(source, tuple(edits))
    return corrected, stored


def merge_check(
    lines: Sequence[TimedLine], hints: Collection[int], groups: Sequence[Sequence[int]]
) -> list[MergeCheck]:
    """Lines where the fragment rules and the LLM's grouping disagree (informational only)."""
    merged_with_next = {number for group in groups for number in group[:-1]}
    checks = []
    for line in lines[:-1]:
        rules = "merge" if line.number in hints else "separate"
        llm = "merge" if line.number in merged_with_next else "separate"
        if rules != llm:
            checks.append(MergeCheck(line=line.number, rules=rules, llm=llm))
    return checks
