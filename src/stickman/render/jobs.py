"""What each unit's image request is made of (spec §7.4, §9.2, §10.4)."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from stickman.config_files import MascotConfig, load_mascot
from stickman.library import LibraryCharacter, find_references, load_library
from stickman.plan.cast import cast_infos
from stickman.plan.models import Plan, PlanUnit
from stickman.pricing import PricingConfig, image_cost_usd, load_pricing
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
    """What rendering reads besides the plan: settings, the mascot, the library and the prices."""

    workspace: Path
    settings: Settings
    mascot: MascotConfig
    library: tuple[LibraryCharacter, ...]
    pricing: PricingConfig

    @classmethod
    def load(cls, workspace: Path, settings: Settings) -> RenderContext:
        return cls(workspace, settings, load_mascot(workspace), tuple(load_library(workspace)), load_pricing(workspace))

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
        self._descriptions = {ref: info.description for ref, info in cast_infos(plan.cast, ctx.mascot).items()}
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

    def jobs(self, units: Sequence[PlanUnit]) -> list[RenderJob]:
        empty = [unit.id for unit in units if not unit.image_prompt.strip()]
        if empty:
            raise JobError(f"no image_prompt for {', '.join(empty)}: run `stickman replan <unit>` for each")
        return [self._job(unit) for unit in units]

    def _job(self, unit: PlanUnit) -> RenderJob:
        refs = self.references(unit)
        steps = self._ctx.settings.image.steps if self._price.supports_steps else None
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
        )
