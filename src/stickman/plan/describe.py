"""Stage 3, describe: what each unit's picture shows (spec §6.4)."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from stickman.plan.cast import CastInfo
from stickman.plan.llm import StageRequest, StageRunner
from stickman.plan.models import UnitDesign
from stickman.plan.prompts import DESCRIBE_SYSTEM, MASCOT_RULE

# Whole words only, so "texts", "context" and "labelled" pass (spec §6.4).
TEXT_WORDS = re.compile(r"\b(?:text|label|caption|writing|zzz)\b|\bsign that says\b", re.IGNORECASE)
CONTEXT_UNITS = 2  # PREVIOUS and NEXT each show this many units


@dataclass(frozen=True)
class UnitContext:
    id: str
    start: float
    end: float
    source_text: str
    scene_corrected_text: str
    part: str | None = None
    # A split part whose corrected text code can't derive, because a correction straddles the
    # cut (spec §4.6): its text comes from this stage, so the check holds it to its share of
    # the scene text. Every other unit's corrected text is set by code and the answer's is ignored.
    text_from_llm: bool = False


@dataclass(frozen=True)
class PreviousUnit:
    id: str
    visual_idea: str
    shot: str


class DescribedUnit(UnitDesign):
    id: str
    corrected_text: str = Field(min_length=1)


class DescribeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[DescribedUnit]


def text_word_errors(design: UnitDesign, where: str) -> list[str]:
    """Only the fields that go into the image prompt; never source_text or corrected_text."""
    fields = [
        ("visual_idea", design.visual_idea),
        *((f"characters[{i}].action", c.action) for i, c in enumerate(design.characters)),
        *((f"setting[{i}]", value) for i, value in enumerate(design.setting)),
        *((f"props[{i}]", value) for i, value in enumerate(design.props)),
        ("composition", design.composition),
    ]
    errors = []
    for name, value in fields:
        match = TEXT_WORDS.search(value)
        if match:
            errors.append(f'{where}.{name}: asks for text ("{match.group(0)}"); follow the VISUAL RULES instead')
    return errors


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _part_text_errors(text: str, context: UnitContext, where: str) -> list[str]:
    """Part 1 of 2 must be the start of the scene's corrected text and part 2 of 2 its end,
    each at a word edge and shorter than the whole (whitespace normalised)."""
    part, scene = _normalise(text), _normalise(context.scene_corrected_text)
    if part == scene:
        return [f"{where}.corrected_text: return only this part's words, not the whole scene"]
    if context.part == "1 of 2":
        if scene.startswith(part + " "):
            return []
        return [f"{where}.corrected_text: return only this part's words: the start of the scene's corrected text, up to the cut"]
    if scene.endswith(" " + part):
        return []
    return [f"{where}.corrected_text: return only this part's words: the end of the scene's corrected text, from the cut"]


def check_describe(result: DescribeResult, batch: Sequence[UnitContext], cast_ids: Collection[str]) -> list[str]:
    expected = [context.id for context in batch]
    got = [unit.id for unit in result.units]
    errors = []
    if sorted(got) != sorted(expected):
        errors.append(f"units: ids must be exactly {expected}, got {got}")
    contexts = {context.id: context for context in batch}
    for index, unit in enumerate(result.units):
        where = f"units[{index}]"
        for c_index, character in enumerate(unit.characters):
            if character.ref not in cast_ids:
                errors.append(f'{where}.characters[{c_index}].ref: {character.ref!r} is not "mascot" or a CAST id')
        errors += text_word_errors(unit, where)
        context = contexts.get(unit.id)
        if context is not None and context.text_from_llm:
            errors += _part_text_errors(unit.corrected_text, context, where)
    return errors


def describe_user_message(
    batch: Sequence[UnitContext],
    *,
    previous: Sequence[PreviousUnit],
    following: Sequence[tuple[str, str]],
    cast: Mapping[str, CastInfo],
    rules: Sequence[str],
    hint: str | None,
) -> str:
    def dumps(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)

    rows = [
        "CAST: " + dumps([{"id": c.id, "name": c.name, "figures": c.figures, "description": c.description} for c in cast.values()]),
        MASCOT_RULE,
        "VISUAL RULES:",
        *(f"- {rule}" for rule in rules),
        "PREVIOUS: " + dumps([{"id": p.id, "visual_idea": p.visual_idea, "shot": p.shot} for p in previous]),
        "NEXT: " + dumps([{"id": unit_id, "text": text} for unit_id, text in following]),
        "UNITS: " + dumps([
            {
                "id": c.id, "start": round(c.start, 3), "end": round(c.end, 3), "part": c.part,
                "source_text": c.source_text, "scene_corrected_text": c.scene_corrected_text,
            }
            for c in batch
        ]),
    ]
    if hint:
        rows.append(f"HINT: {hint}")
    return "\n".join(rows)


async def describe_batch(
    runner: StageRunner,
    batch: Sequence[UnitContext],
    *,
    previous: Sequence[PreviousUnit],
    following: Sequence[tuple[str, str]],
    cast: Mapping[str, CastInfo],
    rules: Sequence[str],
    hint: str | None = None,
    use_cache: bool = True,
) -> list[DescribedUnit]:
    cast_ids = set(cast)
    request = StageRequest(
        stage="describe",
        system=DESCRIBE_SYSTEM,
        user=describe_user_message(batch, previous=previous, following=following, cast=cast, rules=rules, hint=hint),
        schema=DescribeResult,
        check=lambda result: check_describe(result, batch, cast_ids),
    )
    result = await runner.run(request, use_cache=use_cache)
    by_id = {unit.id: unit for unit in result.units}
    return [by_id[context.id] for context in batch]


async def describe_units(
    runner: StageRunner,
    contexts: Sequence[UnitContext],
    *,
    cast: Mapping[str, CastInfo],
    rules: Sequence[str],
    batch_size: int,
) -> dict[str, DescribedUnit]:
    designs: dict[str, DescribedUnit] = {}
    done: list[PreviousUnit] = []
    for start in range(0, len(contexts), batch_size):
        stop = start + batch_size
        following = [(c.id, c.source_text) for c in contexts[stop : stop + CONTEXT_UNITS]]
        results = await describe_batch(
            runner, contexts[start:stop], previous=done[-CONTEXT_UNITS:], following=following, cast=cast, rules=rules
        )
        for unit in results:
            designs[unit.id] = unit
            done.append(PreviousUnit(unit.id, unit.visual_idea, unit.shot))
    return designs
