"""Making and checking one bootstrap step's candidates, several at once (spec §8.1).

Each candidate is an image on bootstrap.model, saved with its record inside, then checked by QC like a
unit's image. There are no retries: the candidates are alternatives, and you choose one. A candidate the
checker couldn't reach is checked again on the next run, with no new image (spec §9.4 [M4]).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from stickman.bootstrap.prompts import anchor_expected, anchor_prompt, mascot_expected, mascot_prompt
from stickman.bootstrap.store import BootstrapStore, Candidate, Step, made_with
from stickman.budget import BudgetExceeded
from stickman.cf.errors import CFError
from stickman.config_files import MascotConfig, StyleConfig
from stickman.fsutil import safe_write
from stickman.ledger import Kind
from stickman.library import anchor_ref_path
from stickman.meter import local_now
from stickman.pricing import PricingConfig, image_cost_usd
from stickman.qc.vision import ExpectedPicture
from stickman.render.calls import STOPS, Calls, RunStopped, StopReason, error_text
from stickman.render.images import ImageDecodeError, decode_image, encode_png
from stickman.render.jobs import random_seed
from stickman.render.references import RefImage, ReferenceFiles
from stickman.render.renderer import RunResult
from stickman.settings import Settings

BOOTSTRAP_PROJECT = "bootstrap"  # the ledger's project for bootstrap calls
LEDGER_KIND: dict[Step, Kind] = {"anchor": "anchor", "mascot": "sheet"}


@dataclass(frozen=True)
class CandidateJob:
    step: Step
    n: int
    prompt: str
    model: str
    width: int
    height: int
    seed: int
    steps: int | None
    references: tuple[RefImage, ...]
    estimate_usd: float

    @property
    def label(self) -> str:
        """How the ledger and the run log name it."""
        return f"{self.step}-c{self.n}"


@dataclass(frozen=True)
class StepPlan:
    """What every candidate of one step shares."""

    step: Step
    prompt: str
    model: str
    size: tuple[int, int]
    steps: int | None
    references: tuple[RefImage, ...]
    estimate_usd: float
    expected: ExpectedPicture

    def current(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        """The candidates that count: every anchor candidate, and the mascot-sheet candidates made with the
        approved anchor as it is now. The others are listed, never checked again, and can't be approved."""
        if not self.references:
            return list(candidates)
        return [c for c in candidates if made_with(c, self.references[0].label)]

    def jobs(self, store: BootstrapStore, count: int, *, seeds: Callable[[], int] = random_seed) -> list[CandidateJob]:
        first = store.next_number(self.step)
        return [
            CandidateJob(self.step, first + i, self.prompt, self.model, self.size[0], self.size[1], seeds(),
                         self.steps, self.references, self.estimate_usd)
            for i in range(count)
        ]


def step_plan(
    step: Step,
    *,
    workspace: Path,
    settings: Settings,
    style: StyleConfig,
    mascot: MascotConfig,
    pricing: PricingConfig,
    files: ReferenceFiles,
) -> StepPlan:
    """The anchor: no reference images, at image.anchor_size. The mascot sheet: the approved anchor's
    reference copy in slot 0, at image.sheet_size (spec §8.1)."""
    model = settings.bootstrap.model
    price = pricing.image(model)
    steps = settings.image.steps if price.supports_steps else None
    if step == "anchor":
        scene = settings.bootstrap.anchor_scene
        prompt, size, references, expected = anchor_prompt(style, scene), settings.image.anchor_size, (), anchor_expected(scene)
    else:
        anchor = files.load(anchor_ref_path(workspace, style.style_version))
        prompt, size, references, expected = mascot_prompt(style, mascot), settings.image.sheet_size, (anchor,), mascot_expected(mascot)
    estimate = image_cost_usd(price, size, [ref.size for ref in references], steps=steps or 1)
    return StepPlan(step, prompt, model, size, steps, references, estimate, expected)


class CandidateMaker:
    def __init__(
        self,
        calls: Calls,
        store: BootstrapStore,
        *,
        concurrency: int,
        now: Callable[[], datetime] = local_now,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self._calls = calls
        self._store = store
        self._concurrency = concurrency
        self._now = now
        self._on_done = on_done
        self.errors: dict[str, str] = {}

    async def run(self, plan: StepPlan, jobs: Sequence[CandidateJob]) -> RunResult:
        """The current candidates with no QC result are checked first (no new image), then the new ones
        are made and checked, at most `concurrency` at once. Once the run is stopping (daily limit,
        budget, a rejected token, the circuit breaker) nothing new starts and running work finishes."""
        started = time.perf_counter()
        control = self._calls.control
        semaphore = asyncio.Semaphore(self._concurrency)

        async def guarded(work: Callable[[], Awaitable[None]]) -> None:
            async with semaphore:
                if control.reason is not None:
                    return
                try:
                    await work()
                except RunStopped:
                    return
                except BudgetExceeded as exc:
                    control.stop(StopReason.BUDGET, str(exc))
                    return
                except CFError as exc:
                    if exc.category not in STOPS:
                        raise
                    control.stop(STOPS[exc.category], self._calls.log.mask(exc.message))
                    return
                if self._on_done is not None:
                    self._on_done()

        unchecked = [c for c in plan.current(self._store.step(plan.step).candidates) if c.qc is None]
        work: list[Callable[[], Awaitable[None]]] = [
            *(lambda c=c: self._check_saved(plan, c) for c in unchecked),
            *(lambda job=job: self._make(plan, job) for job in jobs),
        ]
        await asyncio.gather(*(guarded(item) for item in work))
        return RunResult(control.reason, control.detail, time.perf_counter() - started)

    async def _make(self, plan: StepPlan, job: CandidateJob) -> None:
        try:
            metered = await self._calls.image(job, unit=job.label, kind=LEDGER_KIND[plan.step])
            image = decode_image(metered.result.image_bytes)
        except CFError as exc:
            if exc.category in STOPS:
                raise
            self.errors[job.label] = error_text(exc, self._calls.log)
            return
        except ImageDecodeError as exc:
            self.errors[job.label] = f"bad_image: {exc}"
            return
        path = self._store.candidate_path(plan.step, job.n)
        candidate = Candidate(
            n=job.n, file=self._store.relative(path), seed=job.seed, model=job.model, width=image.width,
            height=image.height, prompt_sent=job.prompt, refs=[ref.label for ref in job.references],
            est_cost_usd=metered.usd, latency_s=round(metered.latency_s, 2), created=self._now(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_write(path, encode_png(image, candidate.model_dump(mode="json")))  # the file first, then bootstrap.json
        self._store.add(plan.step, candidate)
        await self._check(plan, candidate, image)

    async def _check_saved(self, plan: StepPlan, candidate: Candidate) -> None:
        label = f"{plan.step}-c{candidate.n}"
        try:
            image = decode_image((self._store.workspace / candidate.file).read_bytes())
        except (OSError, ImageDecodeError) as exc:
            self.errors[label] = f"bad_image: can't read {candidate.file}: {exc}"
            return
        await self._check(plan, candidate, image)

    async def _check(self, plan: StepPlan, candidate: Candidate, image: Image.Image) -> None:
        label = f"{plan.step}-c{candidate.n}"
        try:
            qc = await self._calls.check(image, expected=plan.expected, reference=None, unit=label)
        except CFError as exc:
            if exc.category in STOPS:
                raise
            # The checker couldn't be reached: the candidate stays unchecked, and the next run checks it.
            self.errors[label] = error_text(exc, self._calls.log) + " (checked again next run)"
            return
        self._store.set_qc(plan.step, candidate.n, qc)
