"""Stage 1, analyse: line groups, speech-to-text corrections and cast (spec §6.2)."""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence

from pydantic import BaseModel, ConfigDict, Field

from stickman.ingest.models import TimedLine
from stickman.library import LibraryCharacter
from stickman.plan.llm import StageRequest, StageRunner
from stickman.plan.models import MASCOT, is_cast_id
from stickman.plan.prompts import analyse_system
from stickman.split.scenes import GroupingError, build_scenes


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GroupCorrection(_Reply):
    group: int
    from_: str = Field(alias="from", min_length=1)
    to: str
    reason: str = ""


class ProposedCastMember(_Reply):
    id: str
    name: str = Field(min_length=1)
    figures: int = Field(ge=1)
    description: str = Field(min_length=1)
    library_ref: str | None = None


class AnalyseResult(_Reply):
    groups: list[list[int]]
    corrections: list[GroupCorrection] = Field(default_factory=list)
    cast: list[ProposedCastMember] = Field(default_factory=list)


def format_clock(seconds: float) -> str:
    """M:SS, or H:MM:SS from one hour on (whole seconds, rounded down)."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def analyse_user_message(
    lines: Sequence[TimedLine], hints: Sequence[int], library: Sequence[LibraryCharacter]
) -> str:
    rows = ["LINES"]
    rows += [
        f"[{line.number}] {format_clock(line.start)} ({line.duration:.1f} s, {line.words} words) {line.text}"
        for line in lines
    ]
    rows.append(f"HINTS: {json.dumps(list(hints))}")
    entries = [
        {"id": e.id, "name": e.name, "figures": e.figures, "description": e.description, "tags": e.tags}
        for e in library
    ]
    rows.append(f"LIBRARY: {json.dumps(entries, ensure_ascii=False)}")
    return "\n".join(rows)


def group_texts(lines: Sequence[TimedLine], groups: Sequence[Sequence[int]]) -> list[str]:
    """Each group's text: its lines joined with one space (what corrections refer to)."""
    by_number = {line.number: line.text for line in lines}
    return [" ".join(by_number[n] for n in group if n in by_number) for group in groups]


def check_analyse(
    result: AnalyseResult,
    lines: Sequence[TimedLine],
    *,
    max_lines: int,
    library_ids: Collection[str],
) -> list[str]:
    try:
        build_scenes(lines, result.groups, max_lines=max_lines)
    except GroupingError as exc:
        return [f"groups: {exc}"]  # corrections refer to groups, so check them once groups are valid
    errors = []
    texts = group_texts(lines, result.groups)
    for index, correction in enumerate(result.corrections):
        if not 1 <= correction.group <= len(texts):
            errors.append(f"corrections[{index}].group: {correction.group} is not a group number (1..{len(texts)})")
        elif correction.from_ not in texts[correction.group - 1]:
            errors.append(
                f'corrections[{index}].from: "{correction.from_}" does not occur in group '
                f'{correction.group}: "{texts[correction.group - 1]}"'
            )
    ids = [member.id for member in result.cast]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"cast: duplicate ids {duplicates}")
    for index, member in enumerate(result.cast):
        if not is_cast_id(member.id):
            errors.append(f"cast[{index}].id: {member.id!r} must be snake_case and must not be {MASCOT!r}")
        if member.library_ref is not None and member.library_ref not in library_ids:
            errors.append(f"cast[{index}].library_ref: {member.library_ref!r} is not in the LIBRARY")
    return errors


async def analyse(
    runner: StageRunner,
    lines: Sequence[TimedLine],
    *,
    hints: Sequence[int],
    library: Sequence[LibraryCharacter],
    max_lines: int,
) -> AnalyseResult:
    library_ids = {entry.id for entry in library}
    request = StageRequest(
        stage="analyse",
        system=analyse_system(max_lines),
        user=analyse_user_message(lines, hints, library),
        schema=AnalyseResult,
        check=lambda result: check_analyse(result, lines, max_lines=max_lines, library_ids=library_ids),
    )
    return await runner.run(request)
