"""The comparison's runs (spec §14.4): each is the source plan rendered with one model, size and reference
setting into runs/<run id>/, by the normal Renderer. A run paused by the daily limit continues next time,
like `stickman resume`. There are no QC retries: the report measures how often the first image passes."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from stickman.compare.setup import RUNS_DIR, CompareRun, CompareSetup
from stickman.library import find_references
from stickman.meter import Meter
from stickman.plan.models import Plan
from stickman.plan.refresh import refresh_prompts, with_prompts
from stickman.render.jobs import JobBuilder, RenderContext, RenderJob
from stickman.render.recovery import recover
from stickman.render.renderer import Renderer, RenderClient, RunControl, RunResult
from stickman.render.state import StateStore, needs_work
from stickman.runlog import RunLog
from stickman.settings import Aspect, Settings


def run_settings(settings: Settings, run: CompareRun, aspect: Aspect) -> Settings:
    image = settings.image.model_copy(
        update={"sizes": {**settings.image.sizes, aspect: (run.width, run.height)}, "use_references": run.references}
    )
    retry = settings.retry.model_copy(update={"qc_max": 0})
    return settings.model_copy(update={"image": image, "retry": retry})


@dataclass
class PreparedRun:
    run: CompareRun
    folder: Path
    store: StateStore
    builder: JobBuilder
    jobs: list[RenderJob]  # the picked units that still need work
    notes: list[str]  # what recover() put right
    settings: Settings


def prepare_run(compare_folder: Path, setup: CompareSetup, plan: Plan, run: CompareRun, base: RenderContext) -> PreparedRun:
    """The run's plan is the frozen plan on the run's model, with the tool-built prompts rebuilt in memory
    for the run's reference images (spec §7.4 [M5])."""
    settings = run_settings(base.settings, run, plan.aspect)
    ctx = dataclasses.replace(base, settings=settings)
    references = find_references(
        ctx.workspace, use_references=run.references, style_version=plan.style_version, mascot=ctx.mascot,
        cast=plan.cast, library=ctx.library,
    )
    refresh = refresh_prompts(plan, style=ctx.style, mascot=ctx.mascot, references=references)
    run_plan = with_prompts(plan, refresh.rebuilt).model_copy(update={"image_model": run.model})
    builder = JobBuilder(ctx, run_plan)
    folder = compare_folder / RUNS_DIR / run.id
    folder.mkdir(parents=True, exist_ok=True)
    store = StateStore.load(folder)
    picked = {pick.unit for pick in setup.picks}
    expected = {unit_id: want for unit_id, want in builder.expected().items() if unit_id in picked}
    notes = recover(store, expected)
    units = [unit for unit in run_plan.units() if unit.id in picked and needs_work(store.unit(unit.id))]
    return PreparedRun(run, folder, store, builder, builder.jobs(units), notes, settings)


async def render_runs(
    client: RenderClient,
    prepared: Sequence[PreparedRun],
    meter: Meter,
    *,
    secrets: Sequence[str],
    control: RunControl,
    sleep: Callable[[float], Awaitable[None]],
    on_done: Callable[[str], None] | None = None,
) -> RunResult:
    """The runs one after another, sharing the meter (budget) and the stop flag: a daily limit in one
    stops the rest. Each run logs to its own folder."""
    started = time.perf_counter()
    for run in prepared:
        if control.reason is not None:
            break
        if not run.jobs:
            continue
        renderer = Renderer(
            client, run.store, meter, run.builder, qc=run.settings.qc, vision_model=run.settings.llm.vision_model,
            retry=run.settings.retry, concurrency=run.settings.render.concurrency,
            log=RunLog.for_project(run.folder, secrets=secrets), rewriter=None, sleep=sleep, on_done=on_done,
            control=control,
        )
        await renderer.run(run.jobs)
    return RunResult(control.reason, control.detail, time.perf_counter() - started)
