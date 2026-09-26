"""The review page's jobs (spec §12.1): regenerate a unit, replan one with a hint, make more bootstrap
candidates. They run in the server process with the renderer, planner, budget, ledger and lock the CLI uses,
one at a time (ProjectAccess), and report through `job` events: started, progress, then finished, paused or
failed with a message. Messages are masked."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from stickman.bootstrap.approve import mascot_version_mismatch
from stickman.bootstrap.generate import BOOTSTRAP_PROJECT, CandidateMaker, step_plan
from stickman.bootstrap.store import BootstrapStore, Step, bootstrap_folder, pending_step
from stickman.budget import Budget
from stickman.cf.errors import CFError
from stickman.config_files import load_mascot, load_style
from stickman.ledger import LEDGER_FILE, Ledger
from stickman.library import find_references
from stickman.meter import Meter, unit_scope
from stickman.plan.llm import PlanningError, StageRunner
from stickman.plan.models import PlanValidationError
from stickman.plan.planner import REPLAN_KEYS, PlanningContext, load_planning_context, replan_unit
from stickman.plan.refresh import refresh_plan_file
from stickman.plan.store import PlanChangedError, load_plan, update_unit, write_plan
from stickman.pricing import load_pricing
from stickman.render.calls import Calls
from stickman.render.jobs import JobBuilder, JobError, RenderContext
from stickman.render.recovery import recover
from stickman.render.references import ReferenceFiles
from stickman.render.renderer import GuardedChat, Renderer, RunControl
from stickman.render.rewrite import PlanRewriter
from stickman.render.summary import PAUSE_NAMES
from stickman.review.access import Busy, JobInfo, ProjectAccess
from stickman.review.events import EventHub
from stickman.runlog import RunLog, mask
from stickman.settings import ConfigError, Settings

Outcome = tuple[str, str]  # (message, state): finished | paused | failed


class Jobs:
    def __init__(
        self,
        workspace: Path,
        project_dir: Path,
        settings: Settings,
        access: ProjectAccess,
        hub: EventHub,
        *,
        client_factory: Callable[[], Any],
        secrets: Sequence[str] = (),
        on_plan_written: Callable[[str], None] = lambda written_hash: None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._workspace = workspace
        self._project = project_dir
        self._plan_path = project_dir / "plan.yaml"
        self._settings = settings
        self._access = access
        self._hub = hub
        self._client_factory = client_factory
        self._secrets = tuple(secrets)
        self._on_plan_written = on_plan_written
        self._sleep = sleep
        self._tasks: set[asyncio.Task[None]] = set()

    # --- starting jobs: the claim is made now (Busy -> HTTP 409), the work runs in the background ---

    def regenerate(self, unit_id: str) -> JobInfo:
        info = self._access.claim("regenerate", unit_id)
        self._spawn(info, self._regenerate(unit_id))
        return info

    def replan(self, unit_id: str, hint: str) -> JobInfo:
        info = self._access.claim("replan", unit_id)
        self._spawn(info, self._replan(unit_id, hint))
        return info

    def more_candidates(self, step: Step) -> JobInfo:
        info = self._access.claim("candidates", step, project=False)
        self._spawn(info, self._more_candidates(step))
        return info

    async def wait(self) -> None:
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _spawn(self, info: JobInfo, work: Coroutine[Any, Any, Outcome]) -> None:
        async def run() -> None:
            self._publish(info, "started")
            try:
                message, state = await work
            except Exception as exc:  # an unexpected error must not leave the job slot claimed
                message, state = f"{type(exc).__name__}: {exc}", "failed"
            finally:
                self._access.release()
            info.message = mask(message, self._secrets)
            self._publish(info, state)
            self._hub.publish("state", {})

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _publish(self, info: JobInfo, state: str) -> None:
        self._hub.publish("job", {**info.as_dict(), "state": state})

    def _meter(self, pricing: Any, *, budget: bool, project: str) -> Meter:
        ledger = Ledger(self._workspace / LEDGER_FILE)
        spend = None
        if budget:
            spend = Budget.from_ledger(
                ledger, self._settings.budget, now=datetime.now().astimezone(),
                warn=lambda message: self._hub.publish("budget", {"message": message}),
            )
        return Meter(project=project, ledger=ledger, budget=spend, pricing=pricing)

    # --- the jobs ---

    async def _regenerate(self, unit_id: str) -> Outcome:
        """A new chain for the unit (spec §12.2 [M6]); its old current version is kept to compare with."""
        store = self._access.store
        assert store is not None
        settings = self._settings
        try:
            ctx = RenderContext.load(self._workspace, settings)
            loaded = load_plan(self._plan_path, library_ids=ctx.library_ids)
            client = self._client_factory()  # before any change: no credentials means nothing happens
        except (ConfigError, PlanValidationError, OSError) as exc:
            return f"Can't regenerate {unit_id}: {exc}", "failed"
        if unit_id not in {unit.id for unit in loaded.plan.units()}:
            return f"No unit {unit_id} in this plan.", "failed"
        references = find_references(
            self._workspace, use_references=settings.image.use_references, style_version=loaded.plan.style_version,
            mascot=ctx.mascot, cast=loaded.plan.cast, library=ctx.library,
        )
        try:
            plan, _, written = refresh_plan_file(self._plan_path, loaded, style=ctx.style, mascot=ctx.mascot, references=references)
        except PlanChangedError:
            return "plan.yaml changed on disk while the prompts were being rebuilt; try again.", "failed"
        if written is not None:
            self._on_plan_written(written)
        try:
            builder = JobBuilder(ctx, plan)
            expected = builder.expected()
            job = builder.job(next(unit for unit in plan.units() if unit.id == unit_id))
        except (ConfigError, JobError) as exc:
            return f"Can't regenerate {unit_id}: {exc}", "failed"
        recover(store, expected)
        store.begin_regeneration(unit_id)
        meter = self._meter(ctx.pricing, budget=True, project=self._project.name)
        log = RunLog.for_project(self._project, secrets=self._secrets)
        async with client:
            control = RunControl(settings.retry.circuit_breaker)
            runner = StageRunner(
                GuardedChat(client, control), settings.llm, settings.retry, cache_dir=self._project / ".cache" / "llm",
                log=log, meter=meter, sleep=self._sleep,
            )
            planning = PlanningContext(ctx.workspace, ctx.settings, ctx.style, ctx.mascot, ctx.rules, ctx.library)
            renderer = Renderer(
                client, store, meter, builder, qc=settings.qc, vision_model=settings.llm.vision_model,
                retry=settings.retry, concurrency=1, log=log, rewriter=PlanRewriter(runner, planning, self._plan_path),
                sleep=self._sleep, control=control,
            )
            result = await renderer.run([job], fresh={unit_id})
        if result.stop is not None:
            detail = f": {result.detail}" if result.detail else ""
            return f"Stopped ({PAUSE_NAMES[result.stop]}){detail}. Finished images are kept.", "paused"
        unit = store.unit(unit_id)
        return f"{unit_id} is {unit.status.replace('_', ' ')}" + (f": {unit.error}" if unit.error else "."), "finished"

    async def _replan(self, unit_id: str, hint: str) -> Outcome:
        """Stage 3 again for one unit with the hint (spec §6.5), written to plan.yaml hash-checked. Planning is
        ledgered but never stopped by the budget (spec §9.7 [M3])."""
        settings = self._settings
        try:
            planning = load_planning_context(self._workspace, settings)
            loaded = load_plan(self._plan_path, library_ids=planning.library_ids)
            pricing = load_pricing(self._workspace)
            client = self._client_factory()
        except (ConfigError, PlanValidationError, OSError) as exc:
            return f"Can't replan {unit_id}: {exc}", "failed"
        if unit_id not in {unit.id for unit in loaded.plan.units()}:
            return f"No unit {unit_id} in this plan.", "failed"
        meter = self._meter(pricing, budget=False, project=self._project.name)
        log = RunLog.for_project(self._project, secrets=self._secrets)
        try:
            async with client:
                runner = StageRunner(
                    client, settings.llm, settings.retry, cache_dir=self._project / ".cache" / "llm", log=log,
                    meter=meter, sleep=self._sleep,
                )
                with unit_scope(unit_id):
                    updated = await replan_unit(runner, planning, loaded.plan, unit_id, hint=hint.strip() or None)
        except PlanningError as exc:
            return f"Replanning {unit_id} failed: {exc}", "failed"
        except CFError as exc:
            return f"Replanning {unit_id} failed: {exc.category}: {exc.message}", "failed"
        fields = {key: value for key, value in updated.model_dump(mode="json").items() if key in REPLAN_KEYS}
        update_unit(loaded.doc, unit_id, fields)
        try:
            written = write_plan(self._plan_path, loaded.doc, expected_hash=loaded.hash)
        except PlanChangedError:
            return "plan.yaml changed on disk while the LLM was working; nothing was written.", "failed"
        self._on_plan_written(written)
        return f"Replanned {unit_id}: {updated.visual_idea}", "finished"

    async def _more_candidates(self, step: Step) -> Outcome:
        """Two more candidates for bootstrap's current step (spec §8.1), under bootstrap's lock."""
        settings = self._settings
        try:
            style, mascot, pricing = load_style(self._workspace), load_mascot(self._workspace), load_pricing(self._workspace)
            client = self._client_factory()
        except ConfigError as exc:
            return f"Can't make candidates: {exc}", "failed"
        pending = pending_step(self._workspace, mascot, style.style_version)
        if pending != step:
            where = "complete" if pending is None else f"on the {pending} step"
            return f"Bootstrap is {where}, so no {step} candidates are made.", "failed"
        if step == "mascot" and (mismatch := mascot_version_mismatch(mascot, style.style_version)) is not None:
            return mismatch, "failed"
        folder = bootstrap_folder(self._workspace, style.style_version)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            self._access.hold(folder)
        except Busy as exc:
            return f"{exc}.", "failed"
        try:
            store = BootstrapStore.load(self._workspace, style.style_version)
            store.recover()
            plan = step_plan(step, workspace=self._workspace, settings=settings, style=style, mascot=mascot,
                             pricing=pricing, files=ReferenceFiles(self._workspace, ref_max_side=settings.image.ref_max_side))
            jobs = plan.jobs(store, 2)
            meter = self._meter(pricing, budget=True, project=BOOTSTRAP_PROJECT)
            log = RunLog.for_project(folder, secrets=self._secrets)
            info = self._access.job
            async with client:
                calls = Calls(client, meter, log, RunControl(settings.retry.circuit_breaker), retry=settings.retry,
                              qc=settings.qc, vision_model=settings.llm.vision_model, sleep=self._sleep)
                maker = CandidateMaker(calls, store, concurrency=settings.render.concurrency,
                                       on_done=lambda: self._publish(info, "progress") if info else None)
                result = await maker.run(plan, jobs)
        finally:
            self._access.drop(folder)
        if result.stop is not None:
            return f"Stopped ({PAUSE_NAMES[result.stop]}). Finished candidates are kept.", "paused"
        errors = "; ".join(f"{label}: {error}" for label, error in maker.errors.items())
        made = len(jobs) - sum(1 for job in jobs if job.label in maker.errors)
        return f"Made {made} {step} candidate(s)." + (f" {errors}" if errors else ""), "finished"
