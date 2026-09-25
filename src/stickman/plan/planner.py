"""Planning a script end to end (spec §6): ingest, analyse, split, cut, describe and prompts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from stickman.config_files import MascotConfig, StyleConfig, VisualRules, load_mascot, load_style, load_visual_rules
from stickman.ingest.fragments import fragment_hints
from stickman.ingest.parse import parse_script
from stickman.ingest.timing import build_timeline, length_warning
from stickman.library import LibraryCharacter, find_references, load_library
from stickman.plan.analyse import analyse, group_texts
from stickman.plan.cast import CastInfo, cast_infos
from stickman.plan.corrections import CorrectedScene, correct_scenes, merge_check
from stickman.plan.cut import cut_candidates
from stickman.plan.describe import DescribedUnit, PreviousUnit, UnitContext, describe_batch, describe_units
from stickman.plan.llm import StageRunner
from stickman.plan.models import (
    MASCOT,
    VISUAL_FIELDS,
    CastMember,
    MascotEntry,
    Plan,
    PlanScene,
    PlanUnit,
    PlanValidationError,
    SplitInfo,
    check_plan,
)
from stickman.prompt.builder import ReferenceAvailability, build_prompt
from stickman.settings import Aspect, Settings
from stickman.split.engine import SplitDecision, decide_split
from stickman.split.scenes import Scene, build_scenes, merge_short_scenes
from stickman.split.units import Unit, build_units

# replan never changes corrected_text: code set it at planning time, or you edited it (spec §6.5)
REPLAN_KEYS: tuple[str, ...] = (*VISUAL_FIELDS, "image_prompt", "prompt_locked")


@dataclass(frozen=True)
class PlanningContext:
    """Everything planning reads besides the script: settings, config files and the library."""

    workspace: Path
    settings: Settings
    style: StyleConfig
    mascot: MascotConfig
    rules: VisualRules
    library: tuple[LibraryCharacter, ...]

    @property
    def library_ids(self) -> set[str]:
        return {entry.id for entry in self.library}


def load_planning_context(workspace: Path, settings: Settings) -> PlanningContext:
    return PlanningContext(
        workspace=workspace,
        settings=settings,
        style=load_style(workspace),
        mascot=load_mascot(workspace),
        rules=load_visual_rules(workspace),
        library=tuple(load_library(workspace)),
    )


@dataclass(frozen=True)
class PlanOutcome:
    plan: Plan
    warnings: list[str]


def _t(seconds: float) -> float:
    """plan.yaml stores times with millisecond precision."""
    return round(seconds, 3)


def _references(ctx: PlanningContext, cast: Sequence[MascotEntry | CastMember]) -> ReferenceAvailability:
    return find_references(
        ctx.workspace,
        use_references=ctx.settings.image.use_references,
        style_version=ctx.style.style_version,
        mascot=ctx.mascot,
        cast=cast,
        library=ctx.library,
    )


def _with_prompt(
    unit: PlanUnit, ctx: PlanningContext, table: Mapping[str, CastInfo], references: ReferenceAvailability
) -> PlanUnit:
    """The unit with its image prompt built from its fields (spec §7.4)."""
    prompt = build_prompt(unit, style=ctx.style, cast=table, references=references.for_unit(unit.characters))
    return unit.model_copy(update={"image_prompt": prompt})


def _unit_texts(
    units: Sequence[Unit], decisions: Mapping[int, SplitDecision], corrected: Mapping[int, CorrectedScene]
) -> dict[str, str | None]:
    """Each unit's corrected text, set by code (spec §4.6).

    An unsplit unit gets its scene's corrected text, and a split part its own words with its
    own corrections. None marks a part whose text the describe stage gives instead, because a
    correction straddles the cut.
    """
    texts: dict[str, str | None] = {}
    for unit in units:
        scene = corrected[unit.scene_number]
        cut = decisions[unit.scene_number].cut_after_word
        if unit.part is None or cut is None:
            texts[unit.id] = scene.corrected_text
            continue
        parts = scene.part_texts(cut)
        texts[unit.id] = None if parts is None else parts[0 if unit.part == "1 of 2" else 1]
    return texts


def _plan_unit(unit: Unit, design: DescribedUnit, text: str | None) -> PlanUnit:
    return PlanUnit.model_validate(
        {
            **design.model_dump(include=set(VISUAL_FIELDS)),
            "id": unit.id,
            "part": unit.part,
            "start": _t(unit.start),
            "end": _t(unit.end),
            "source_text": unit.source_text,
            "corrected_text": design.corrected_text if text is None else text,
        }
    )


def _plan_scene(scene: Scene, decision: SplitDecision, corrected: str, units: list[PlanUnit]) -> PlanScene:
    return PlanScene(
        id=f"{scene.number:03d}",
        lines=[line.number for line in scene.lines],
        start=_t(scene.start),
        end=_t(scene.end),
        source_text=scene.source_text,
        corrected_text=corrected,
        split=SplitInfo(status=decision.status, cut_after_word=decision.cut_after_word, candidates=list(decision.candidates)),
        units=units,
    )


async def plan_script(
    runner: StageRunner,
    ctx: PlanningContext,
    *,
    script_text: str,
    project: str,
    aspect: Aspect,
    duration: float | None,
) -> PlanOutcome:
    s = ctx.settings
    timeline = build_timeline(parse_script(script_text), s.timing, duration=duration)
    warning = length_warning(timeline, s.input.max_minutes)
    hints = fragment_hints(timeline.lines)
    analysed = await analyse(runner, timeline.lines, hints=hints, library=ctx.library, max_lines=s.merge.max_lines)

    scenes = merge_short_scenes(
        build_scenes(timeline.lines, analysed.groups, max_lines=s.merge.max_lines),
        min_scene_seconds=s.merge.min_scene_seconds,
        max_lines=s.merge.max_lines,
    )
    corrected, corrections = correct_scenes(
        scenes, analysed.groups, group_texts(timeline.lines, analysed.groups), analysed.corrections
    )
    candidates = await cut_candidates(runner, scenes, s.split)
    decisions = {scene.number: decide_split(scene, candidates.get(scene.number, []), s.split) for scene in scenes}
    units = build_units(scenes, decisions)
    texts = _unit_texts(units, decisions, corrected)

    cast: list[MascotEntry | CastMember] = [MascotEntry(id=MASCOT)]
    cast += [CastMember.model_validate(member.model_dump()) for member in analysed.cast]
    table = cast_infos(cast, ctx.mascot)
    contexts = [
        UnitContext(
            u.id, u.start, u.end, u.source_text, corrected[u.scene_number].corrected_text, u.part,
            text_from_llm=texts[u.id] is None,
        )
        for u in units
    ]
    designs = await describe_units(runner, contexts, cast=table, rules=ctx.rules.rules, batch_size=s.llm.batch_size)

    references = _references(ctx, cast)
    by_scene: dict[int, list[PlanUnit]] = {}
    for unit in units:
        plan_unit = _plan_unit(unit, designs[unit.id], texts[unit.id])
        by_scene.setdefault(unit.scene_number, []).append(_with_prompt(plan_unit, ctx, table, references))

    plan = Plan(
        project=project,
        aspect=aspect,
        style_version=ctx.style.style_version,
        image_model=s.image.model,
        duration_end=_t(timeline.end),
        pace_wps=round(timeline.pace_wps, 4),
        cast=cast,
        corrections=corrections,
        merge_check=merge_check(timeline.lines, hints, analysed.groups),
        scenes=[
            _plan_scene(sc, decisions[sc.number], corrected[sc.number].corrected_text, by_scene[sc.number])
            for sc in scenes
        ],
    )
    errors = check_plan(plan, library_ids=ctx.library_ids)
    if errors:  # a bug: every stage was already checked
        raise PlanValidationError(errors)
    return PlanOutcome(plan, [warning] if warning else [])


async def replan_unit(
    runner: StageRunner, ctx: PlanningContext, plan: Plan, unit_id: str, *, hint: str | None
) -> PlanUnit:
    """Rerun stage 3 for one unit (spec §6.5). Timing and corrected_text are unchanged; the prompt
    is rebuilt and unlocked."""
    units = plan.units()
    index = [unit.id for unit in units].index(unit_id)
    unit = units[index]
    scene = plan.scene_of(unit_id)
    table = cast_infos(plan.cast, ctx.mascot)
    context = UnitContext(unit.id, unit.start, unit.end, unit.source_text, scene.corrected_text, unit.part)
    previous = [PreviousUnit(u.id, u.visual_idea, u.shot) for u in units[max(0, index - 2) : index]]
    following = [(u.id, u.source_text) for u in units[index + 1 : index + 3]]
    [design] = await describe_batch(
        runner, [context], previous=previous, following=following, cast=table,
        rules=ctx.rules.rules, hint=hint, use_cache=False,
    )
    fresh = PlanUnit.model_validate(
        {**unit.model_dump(), **design.model_dump(include=set(VISUAL_FIELDS)), "prompt_locked": False}
    )
    return _with_prompt(fresh, ctx, table, _references(ctx, plan.cast))
