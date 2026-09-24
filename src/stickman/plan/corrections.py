"""Corrections applied at scene level, and the "Check merges" list (spec §4.4, §6.2)."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from stickman.ingest.models import TimedLine
from stickman.plan.analyse import GroupCorrection
from stickman.plan.models import Correction, MergeCheck
from stickman.split.scenes import Scene


def apply_corrections(text: str, pairs: Sequence[tuple[str, str]]) -> str:
    """Replace the first occurrence of each `from`, in the order given."""
    for old, new in pairs:
        text = text.replace(old, new, 1)
    return text


def correct_scenes(
    scenes: Sequence[Scene], texts: Sequence[str], corrections: Sequence[GroupCorrection]
) -> tuple[dict[int, str], list[Correction]]:
    """Each scene's corrected text, and its corrections keyed by scene ID.

    Corrections are applied per group, then the groups are joined. So a correction still
    lands in the right place after short-scene merging has combined groups (§4.4).
    """
    by_group: dict[int, list[GroupCorrection]] = {}
    for correction in corrections:
        by_group.setdefault(correction.group, []).append(correction)
    corrected: dict[int, str] = {}
    stored: list[Correction] = []
    for scene in scenes:
        if not scene.groups:
            raise ValueError(f"scene {scene.number} has no group provenance")
        parts = []
        for group in scene.groups:
            fixes = by_group.get(group, [])
            parts.append(apply_corrections(texts[group - 1], [(f.from_, f.to) for f in fixes]))
            stored += [
                Correction(scene=f"{scene.number:03d}", from_=f.from_, to=f.to, reason=f.reason) for f in fixes
            ]
        corrected[scene.number] = " ".join(parts)
    return corrected, stored


def merge_check(
    lines: Sequence[TimedLine], hints: Collection[int], groups: Sequence[Sequence[int]]
) -> list[MergeCheck]:
    """Lines where the fragment rules and the LLM's grouping disagree (informational only)."""
    # If the last line is in hints, it can't merge anyway, so ignore the hint
    if lines and lines[-1].number in hints:
        return []
    merged_with_next = {number for group in groups for number in group[:-1]}
    checks = []
    for line in lines[:-1]:
        rules = "merge" if line.number in hints else "separate"
        llm = "merge" if line.number in merged_with_next else "separate"
        if rules != llm:
            checks.append(MergeCheck(line=line.number, rules=rules, llm=llm))
    return checks
