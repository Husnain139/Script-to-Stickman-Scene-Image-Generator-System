"""plan.yaml: your content (spec §5.1), and the checks across the whole plan."""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from stickman.plan.jsonx import error_path
from stickman.settings import Aspect

MASCOT = "mascot"
MAX_CHARACTERS = 3
MAX_SETTING = 2
TIME_TOLERANCE = 1e-6

Shot = Literal["wide", "medium", "close-up"]
TimeOfDay = Literal["day", "night", "unspecified"]
VisualType = Literal["literal", "metaphor"]
EnergyMark = Literal["motion", "surprise", "wind", "emphasis"]
Part = Literal["1 of 2", "2 of 2"]
SplitStatus = Literal["none", "split", "no_valid_cut"]

_CAST_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_SCENE_ID = re.compile(r"^\d{3}$")


def is_cast_id(value: str) -> bool:
    return value != MASCOT and bool(_CAST_ID.match(value))


class PlanValidationError(ValueError):
    """plan.yaml breaks the schema or the whole-plan rules. `errors` has one message per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MascotEntry(_Model):
    """The mascot's cast entry. Its description comes from config/mascot.yaml."""

    id: Literal["mascot"]


class CastMember(_Model):
    id: str
    name: str = Field(min_length=1)
    figures: int = Field(ge=1)
    description: str = Field(min_length=1)
    library_ref: str | None = None

    @field_validator("id")
    @classmethod
    def _cast_id(cls, value: str) -> str:
        if not is_cast_id(value):
            raise ValueError("cast ids are snake_case and are not 'mascot'")
        return value


class Correction(_Model):
    scene: str
    from_: str = Field(alias="from", min_length=1)
    to: str
    reason: str = ""


class MergeCheck(_Model):
    """A line where the fragment rules and the LLM disagree about merging it with the next (§4.4)."""

    line: int = Field(ge=1)
    rules: Literal["merge", "separate"]
    llm: Literal["merge", "separate"]


class SplitInfo(_Model):
    status: SplitStatus
    cut_after_word: int | None = None
    candidates: list[int] = Field(default_factory=list)


class CharacterRef(_Model):
    ref: str
    action: str = Field(min_length=1)
    emotion: str = Field(min_length=1)


class UnitDesign(_Model):
    """A unit's visual fields: what its picture shows (stage 3, spec §6.4)."""

    visual_idea: str = Field(min_length=1)
    visual_type: VisualType
    shot: Shot
    time_of_day: TimeOfDay
    characters: list[CharacterRef] = Field(max_length=MAX_CHARACTERS)
    mood: str | None = None
    setting: list[str] = Field(default_factory=list, max_length=MAX_SETTING)
    props: list[str] = Field(default_factory=list)
    composition: str = Field(min_length=1)
    energy_marks: list[EnergyMark] = Field(default_factory=list)
    softened: bool = False
    softened_reason: str | None = None


VISUAL_FIELDS: tuple[str, ...] = tuple(UnitDesign.model_fields)


class PlanUnit(UnitDesign):
    id: str
    part: Part | None = None
    start: float = Field(ge=0)
    end: float
    source_text: str = Field(min_length=1)
    corrected_text: str = Field(min_length=1)
    seed: int | None = None
    image_prompt: str = ""
    prompt_locked: bool = False


class PlanScene(_Model):
    id: str
    lines: list[int] = Field(min_length=1)
    start: float = Field(ge=0)
    end: float
    source_text: str = Field(min_length=1)
    corrected_text: str = Field(min_length=1)
    split: SplitInfo
    units: list[PlanUnit] = Field(min_length=1, max_length=2)


class Plan(_Model):
    schema_version: Literal[1] = 1
    project: str = Field(min_length=1)
    aspect: Aspect
    style_version: int = Field(ge=1)
    image_model: str = Field(min_length=1)
    duration_end: float = Field(gt=0)
    pace_wps: float = Field(gt=0)
    cast: list[MascotEntry | CastMember]
    corrections: list[Correction] = Field(default_factory=list)
    merge_check: list[MergeCheck] = Field(default_factory=list)
    scenes: list[PlanScene] = Field(min_length=1)

    def units(self) -> list[PlanUnit]:
        return [unit for scene in self.scenes for unit in scene.units]

    def scene_of(self, unit_id: str) -> PlanScene:
        for scene in self.scenes:
            if any(unit.id == unit_id for unit in scene.units):
                return scene
        raise KeyError(unit_id)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TIME_TOLERANCE


def _check_cast(plan: Plan, library_ids: Collection[str] | None) -> list[str]:
    errors = []
    ids = [member.id for member in plan.cast]
    if ids.count(MASCOT) != 1:
        errors.append("cast: must contain the mascot entry (- id: mascot) exactly once")
    duplicates = sorted({i for i in ids if ids.count(i) > 1} - {MASCOT})
    if duplicates:
        errors.append(f"cast: duplicate ids {duplicates}")
    if library_ids is not None:
        for index, member in enumerate(plan.cast):
            if isinstance(member, CastMember) and member.library_ref is not None:
                if member.library_ref not in library_ids:
                    errors.append(f"cast[{index}].library_ref: {member.library_ref!r} is not in the library")
    return errors


def _check_units(scene: PlanScene, where: str, known: set[str]) -> list[str]:
    errors = []
    if scene.split.status == "split":
        if scene.split.cut_after_word is None:
            errors.append(f"{where}.split.cut_after_word: required when status is split")
        expected = [(f"{scene.id}a", "1 of 2"), (f"{scene.id}b", "2 of 2")]
    else:
        expected = [(scene.id, None)]
    got = [(unit.id, unit.part) for unit in scene.units]
    if got != expected:
        errors.append(f"{where}.units: expected (id, part) {expected} for split status {scene.split.status!r}, got {got}")
    t = scene.start
    for index, unit in enumerate(scene.units):
        at = f"{where}.units[{index}]"
        if not _close(unit.start, t):
            errors.append(f"{at}.start: {unit.start} must equal {t} (no gaps or overlaps)")
        if unit.end <= unit.start:
            errors.append(f"{at}.end: {unit.end} must be after start {unit.start}")
        for c_index, character in enumerate(unit.characters):
            if character.ref not in known:
                errors.append(f"{at}.characters[{c_index}].ref: {character.ref!r} is not mascot or a cast id")
        t = unit.end
    if not _close(scene.units[-1].end, scene.end):
        errors.append(f"{where}.units[{len(scene.units) - 1}].end: {scene.units[-1].end} must equal the scene end {scene.end}")
    return errors


def check_plan(plan: Plan, *, library_ids: Collection[str] | None = None) -> list[str]:
    """The rules across the whole plan (spec §5.1) that the schema alone can't express."""
    errors = _check_cast(plan, library_ids)
    known = {member.id for member in plan.cast} | {MASCOT}
    scene_ids = [scene.id for scene in plan.scenes]
    duplicates = sorted({i for i in scene_ids if scene_ids.count(i) > 1})
    if duplicates:
        errors.append(f"scenes: duplicate ids {duplicates}")
    next_line = 1
    previous_end = 0.0
    for index, scene in enumerate(plan.scenes):
        where = f"scenes[{index}]"
        if not _SCENE_ID.match(scene.id):
            errors.append(f"{where}.id: {scene.id!r} must be three digits, like '006'")
        expected_lines = list(range(next_line, next_line + len(scene.lines)))
        if scene.lines != expected_lines:
            errors.append(f"{where}.lines: expected {expected_lines} (consecutive, continuing from the previous scene), got {scene.lines}")
        next_line = scene.lines[-1] + 1
        if not _close(scene.start, previous_end):
            errors.append(f"{where}.start: {scene.start} must equal the previous end {previous_end}")
        if scene.end <= scene.start:
            errors.append(f"{where}.end: {scene.end} must be after start {scene.start}")
        errors.extend(_check_units(scene, where, known))
        previous_end = scene.end
    if not _close(previous_end, plan.duration_end):
        errors.append(f"duration_end: {plan.duration_end} must equal the last scene's end {previous_end}")
    scenes = {scene.id: scene for scene in plan.scenes}
    for index, correction in enumerate(plan.corrections):
        scene = scenes.get(correction.scene)
        if scene is None:
            errors.append(f"corrections[{index}].scene: no scene {correction.scene!r}")
        elif correction.from_ not in scene.source_text:
            errors.append(f"corrections[{index}].from: {correction.from_!r} does not occur in scene {scene.id}'s source_text")
    return errors


def parse_plan(data: Any, *, library_ids: Collection[str] | None = None) -> Plan:
    try:
        plan = Plan.model_validate(data)
    except ValidationError as exc:
        raise PlanValidationError([f"{error_path(err['loc'])}: {err['msg']}" for err in exc.errors()]) from None
    errors = check_plan(plan, library_ids=library_ids)
    if errors:
        raise PlanValidationError(errors)
    return plan
