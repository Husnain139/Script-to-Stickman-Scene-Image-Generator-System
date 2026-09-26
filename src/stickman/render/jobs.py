"""What each unit's image request is made of (spec §7.4, §9.2, §10.4)."""

from __future__ import annotations

import dataclasses
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from stickman.config_files import MascotConfig, StyleConfig, VisualRules, load_mascot, load_style, load_visual_rules
from stickman.library import LibraryCharacter, find_references, load_library
from stickman.plan.cast import cast_infos
from stickman.plan.models import MASCOT, Plan, PlanUnit
from stickman.pricing import PricingConfig, image_cost_usd, load_pricing
from stickman.qc.decide import QCReason
from stickman.qc.fixes import FixContext, retry_prompt
from stickman.qc.vision import ExpectedPicture
from stickman.render.fingerprint import fingerprint
from stickman.render.images import image_stem
from stickman.render.recovery import ExpectedUnit
from stickman.render.references import RefImage, ReferenceFiles, reference_paths
from stickman.settings import Settings


class JobError(ValueError):
    """Some units can't be sent as they are (CLI exit code 1)."""


def random_seed() -> int:
    """From 0 to 2**31 − 1, valid whether the API reads it as signed or unsigned 32-bit (spec §9.2).
    It is kept with the version as a record, not as a way to recreate the image."""
    return secrets.randbelow(2**31)


@dataclass(frozen=True)
class RenderContext:
    """What rendering reads besides the plan: settings, the mascot, the library, the prices, the style
    (its strict clause goes into retries) and the visual rules (their text-free wording)."""

    workspace: Path
    settings: Settings
    mascot: MascotConfig
    library: tuple[LibraryCharacter, ...]
    pricing: PricingConfig
    style: StyleConfig
    rules: VisualRules

    @classmethod
    def load(cls, workspace: Path, settings: Settings) -> RenderContext:
        return cls(
            workspace, settings, load_mascot(workspace), tuple(load_library(workspace)), load_pricing(workspace),
            load_style(workspace), load_visual_rules(workspace),
        )

    @property
    def library_ids(self) -> set[str]:
        return {entry.id for entry in self.library}


@dataclass(frozen=True)
class RenderJob:
    unit_id: str
    stem: str  # images/<stem>.png is the unit's current image
    prompt: str
    model: str
    width: int
    height: int
    seed: int
    steps: int | None  # only for models that accept it (FLUX.2 dev)
    references: tuple[RefImage, ...]  # slot order
    fingerprint: str
    estimate_usd: float
    unit: PlanUnit  # the plan unit it was made from
    expected: ExpectedPicture  # what QC expects to see (spec §11.2)
    vision_reference: RefImage | None  # the mascot's sheet for the vision check, in mascot units once it exists
    fixes: FixContext  # what a retry's prompt fixes need (spec §7.5)


class JobBuilder:
    """RenderJobs and fingerprints for one plan. The plan's `image_prompt` is sent as it stands;
    the references are the ones that exist now (spec §7.4)."""

    def __init__(self, ctx: RenderContext, plan: Plan, *, seeds: Callable[[], int] = random_seed) -> None:
        self._ctx = ctx
        self._plan = plan
        self._seeds = seeds
        self._files = ReferenceFiles(ctx.workspace, ref_max_side=ctx.settings.image.ref_max_side)
        self._availability = find_references(
            ctx.workspace,
            use_references=ctx.settings.image.use_references,
            style_version=plan.style_version,
            mascot=ctx.mascot,
            cast=plan.cast,
            library=ctx.library,
        )
        self._cast = cast_infos(plan.cast, ctx.mascot)
        self._descriptions = {ref: info.description for ref, info in self._cast.items()}
        self._mascot_ref = self._mascot_reference()
        self._price = ctx.pricing.image(plan.image_model)
        width, height = ctx.settings.image.sizes[plan.aspect]
        self.size = (width, height)

    def references(self, unit: PlanUnit) -> tuple[RefImage, ...]:
        paths = reference_paths(
            self._ctx.workspace,
            self._availability.for_unit(unit.characters),
            style_version=self._plan.style_version,
            mascot=self._ctx.mascot,
            cast=self._plan.cast,
            library=self._ctx.library,
        )
        return tuple(self._files.load(path) for path in paths)

    def fingerprint(self, unit: PlanUnit) -> str:
        return fingerprint(
            unit,
            cast_descriptions=self._descriptions,
            model=self._plan.image_model,
            aspect=self._plan.aspect,
            size=self.size,
            style_version=self._plan.style_version,
            reference_hashes=[ref.sha256 for ref in self.references(unit)],
        )

    def expected(self) -> dict[str, ExpectedUnit]:
        return {unit.id: ExpectedUnit(image_stem(unit.id, unit.start), self.fingerprint(unit)) for unit in self._plan.units()}

    def _mascot_reference(self) -> Path | None:
        """The mascot's reference copy once bootstrap approved it (spec §8.3): for the vision check's
        head-and-hair comparison, whether or not reference images go to the image model."""
        mascot = self._ctx.mascot
        path = self._ctx.workspace / mascot.ref
        if mascot.seed is None or mascot.style_version != self._plan.style_version or not path.is_file():
            return None
        return path

    def expected_picture(self, unit: PlanUnit) -> ExpectedPicture:
        """Each distinct character once, showing at least one figure and at most its cast entry's figures:
        a group may appear as one member (changed after the M4 live check)."""
        refs = dict.fromkeys(character.ref for character in unit.characters)
        infos = [self._cast[ref] for ref in refs]
        shown = [(info.name, "1" if info.figures == 1 else f"1-{info.figures}") for info in infos]
        cast = ", ".join(f"{name}: {figures}" for name, figures in shown) or "none"
        return ExpectedPicture(unit.visual_idea, sum(info.figures for info in infos), cast, len(infos))

    def jobs(self, units: Sequence[PlanUnit]) -> list[RenderJob]:
        empty = [unit.id for unit in units if not unit.image_prompt.strip()]
        if empty:
            raise JobError(f"no image_prompt for {', '.join(empty)}: run `stickman replan <unit>` for each")
        return [self.job(unit) for unit in units]

    def job(self, unit: PlanUnit) -> RenderJob:
        if not unit.image_prompt.strip():
            raise JobError(f"no image_prompt for {unit.id}: run `stickman replan {unit.id}`")
        slots = self._availability.for_unit(unit.characters)
        refs = self.references(unit)
        steps = self._ctx.settings.image.steps if self._price.supports_steps else None
        expected = self.expected_picture(unit)
        in_unit = any(character.ref == MASCOT for character in unit.characters)
        return RenderJob(
            unit_id=unit.id,
            stem=image_stem(unit.id, unit.start),
            prompt=unit.image_prompt,
            model=self._plan.image_model,
            width=self.size[0],
            height=self.size[1],
            seed=unit.seed if unit.seed is not None else self._seeds(),
            steps=steps,
            references=refs,
            fingerprint=self.fingerprint(unit),
            estimate_usd=image_cost_usd(self._price, self.size, [ref.size for ref in refs], steps=steps or 1),
            unit=unit,
            expected=expected,
            vision_reference=self._files.load(self._mascot_ref) if in_unit and self._mascot_ref is not None else None,
            fixes=FixContext(
                strict_clause=self._ctx.style.strict_clause,
                props=tuple(unit.props),
                text_free=self._ctx.rules.text_free,
                expected=expected,
                identity=self._ctx.mascot.identity,
                mascot_in_image_1=slots is not None and slots[:1] == [MASCOT],
                locked=unit.prompt_locked,
            ),
        )

    def retry(self, job: RenderJob, reasons: Sequence[QCReason]) -> RenderJob:
        """The same request with the prompt fixes for `reasons` and a new seed (spec §7.5)."""
        return dataclasses.replace(job, prompt=retry_prompt(job.unit.image_prompt, reasons, job.fixes), seed=self._seeds())
