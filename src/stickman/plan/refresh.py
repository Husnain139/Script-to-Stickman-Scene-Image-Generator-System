"""Rebuilding tool-built image prompts for the reference images that exist now (spec §7.4 [M5]).

A plan made before bootstrap has prompts with no "Reference images:" paragraph. Once the style anchor
and sheets exist, the images go out with reference images, and the prompt must say what each one is.
Only prompts the tool built are rebuilt: a prompt that, with that paragraph removed, is the builder's
output with no references. A hand-edited prompt is left as it is (M7 locks it), and a locked prompt is
never touched. Because the tool writes the rebuilt text, M7's lock detection never takes it for a hand edit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from stickman.config_files import MascotConfig, StyleConfig
from stickman.plan.cast import cast_infos
from stickman.plan.models import Plan
from stickman.prompt.builder import REFERENCE_INTRO, ReferenceAvailability, build_prompt

# The builder writes the paragraph as one line after a blank line: the intro, then one sentence per slot.
_REFERENCE_PARAGRAPH = re.compile(r"\n\n" + re.escape(REFERENCE_INTRO) + r"[^\n]*")


def without_references(prompt: str) -> str:
    return _REFERENCE_PARAGRAPH.sub("", prompt)


@dataclass(frozen=True)
class PromptRefresh:
    rebuilt: dict[str, str]  # unit id -> its new prompt, in plan order
    hand_edited: list[str]  # unlocked units whose prompt isn't the builder's, sent with references it doesn't describe


def refresh_prompts(
    plan: Plan, *, style: StyleConfig, mascot: MascotConfig, references: ReferenceAvailability
) -> PromptRefresh:
    table = cast_infos(plan.cast, mascot)
    rebuilt: dict[str, str] = {}
    hand_edited: list[str] = []
    for unit in plan.units():
        if unit.prompt_locked or not unit.image_prompt.strip():
            continue  # an empty prompt is `stickman replan`'s job (JobError says so)
        slots = references.for_unit(unit.characters)
        fresh = build_prompt(unit, style=style, cast=table, references=slots)
        if unit.image_prompt == fresh:
            continue
        if without_references(unit.image_prompt) == build_prompt(unit, style=style, cast=table, references=None):
            rebuilt[unit.id] = fresh
        elif slots is not None:
            hand_edited.append(unit.id)
    return PromptRefresh(rebuilt, hand_edited)


def with_prompts(plan: Plan, prompts: Mapping[str, str]) -> Plan:
    return with_unit_fields(plan, {unit_id: {"image_prompt": prompt} for unit_id, prompt in prompts.items()})


def with_unit_fields(plan: Plan, fields: Mapping[str, Mapping[str, object]]) -> Plan:
    """The plan in memory with these units' fields changed (unit id -> field -> value)."""
    if not fields:
        return plan
    scenes = [
        scene.model_copy(update={"units": [
            unit.model_copy(update=dict(fields[unit.id])) if unit.id in fields else unit
            for unit in scene.units
        ]})
        for scene in plan.scenes
    ]
    return plan.model_copy(update={"scenes": scenes})
