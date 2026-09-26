"""Generating units' images several at once. Each image is checked by QC and, when it fails, retried
with the fix for its reason (spec §9.5, §10.3, §11)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PIL import Image

from stickman.budget import BudgetExceeded
from stickman.cf.client import ImageResult, LLMResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.fsutil import safe_write
from stickman.meter import Meter, Metered, local_now, unit_scope
from stickman.plan.llm import ChatClient
from stickman.plan.models import PlanUnit
from stickman.qc.decide import QCResult
from stickman.render.calls import (  # noqa: F401  (re-exported: the CLI and tests import them from here)
    ERROR_CHARS,
    STOPS,
    UNREACHABLE,
    Calls,
    RenderClient,
    RunControl,
    RunStopped,
    StopReason,
    error_text,
)
from stickman.render.chain import Check, Finish, Render, chain_of, finish_step, next_step
from stickman.render.images import HISTORY_DIR, ImageDecodeError, decode_image, encode_png, history_name
from stickman.render.jobs import JobBuilder, JobError, RenderJob
from stickman.render.rewrite import Rewriter, RewriteFailed, softened_by_qc
from stickman.render.state import StateStore, UnitStatus, Version, write_current_copy
from stickman.runlog import RunLog, shorten
from stickman.settings import ConfigError, QCSettings, RetrySettings


class GuardedChat:
    """The chat client of a run's plan rewrites (soften, redesign), which go through StageRunner, not
    Calls._call: no call starts once the run is stopping, and each attempt's temporary error counts
    toward the circuit breaker as kind `llm` (spec §9.5)."""

    def __init__(self, client: ChatClient, control: RunControl, *, kind: str = "llm") -> None:
        self._client = client
        self._control = control
        self._kind = kind

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResult:
        self._control.check()
        try:
            reply = await self._client.chat(
                model, messages, temperature=temperature, max_tokens=max_tokens, response_format=response_format
            )
        except CFError as exc:
            if exc.category is ErrorCategory.TRANSIENT:
                self._control.transient(self._kind)
            raise
        self._control.success(self._kind)
        return reply


@dataclass(frozen=True)
class RunResult:
    stop: StopReason | None
    detail: str
    elapsed_s: float


class Renderer:
    def __init__(
        self,
        client: RenderClient,
        store: StateStore,
        meter: Meter,
        builder: JobBuilder,
        *,
        qc: QCSettings,
        vision_model: str,
        retry: RetrySettings,
        concurrency: int,
        log: RunLog,
        rewriter: Rewriter | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = local_now,
        on_done: Callable[[str], None] | None = None,
        control: RunControl | None = None,
    ) -> None:
        """`control`: pass the one the rewriter's GuardedChat uses, so its errors reach the same breaker."""
        self._client = client
        self._store = store
        self._meter = meter
        self._builder = builder
        self._qc = qc
        self._vision_model = vision_model
        self._retry = retry
        self._concurrency = concurrency
        self._log = log
        self._rewriter = rewriter
        self._sleep = sleep
        self._now = now
        self._on_done = on_done
        self.control = control if control is not None else RunControl(retry.circuit_breaker)
        self.softened: list[str] = []  # units softened after a safety filter in this run
        self._fresh: frozenset[str] = frozenset()  # units that start a new chain in this run
        self._calls = Calls(client, meter, log, self.control, retry=retry, qc=qc, vision_model=vision_model, sleep=sleep)

    async def run(self, jobs: Sequence[RenderJob], *, fresh: Collection[str] = ()) -> RunResult:
        """Each job's unit, at most `concurrency` at once. A unit holds its slot for its whole chain, so
        vision calls share the image requests' limit (spec §10.3). Once the run is stopping, no new
        request starts and the requests already out finish (spec §9.5). Units in `fresh` (regenerated from
        the review page) start a new chain instead of continuing one; their earlier versions stay as a
        record (spec §12.2 [M6])."""
        self._fresh = frozenset(fresh)
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(job: RenderJob) -> None:
            async with semaphore:
                if self.control.reason is not None:
                    return
                await self._unit(job)
                if self._on_done is not None:
                    self._on_done(job.unit_id)

        await asyncio.gather(*(one(job) for job in jobs))
        return RunResult(self.control.reason, self.control.detail, time.perf_counter() - started)

    async def _unit(self, job: RenderJob) -> None:
        """`generating` while the unit's chain runs, then its final status (spec §10.3). Each step is
        saved as it happens, so a run stopped or killed here continues the same chain next time."""
        unit_id = job.unit_id
        self._store.set_status(unit_id, "generating")
        # A regenerated unit starts a new chain; its earlier versions stay as a record (spec §12.2 [M6]).
        chain = [] if unit_id in self._fresh else chain_of(self._store.unit(unit_id), job.fingerprint)
        images: dict[int, Image.Image] = {}  # images made in this run, by version, so QC needn't read them back
        refused = False
        error: str | None = None
        try:
            while True:
                step = next_step(
                    chain, qc_max=self._retry.qc_max, locked=job.unit.prompt_locked,
                    softened=softened_by_qc(job.unit), refused=refused, fingerprint=job.fingerprint,
                )
                refused = False
                if isinstance(step, Finish):
                    self._finish(job, step.status, step.current, error)
                    return
                if isinstance(step, Check):
                    image = images.get(step.v)
                    if image is None:  # made by an earlier run
                        try:
                            image = self._read(chain[-1])
                        except (OSError, ImageDecodeError) as exc:
                            # The file can't be read, so no version counts as current: the next run
                            # makes a new image instead of failing the same check again. An OSError
                            # elsewhere in the check (below) fails only the unit, keeping its version.
                            self._finish(job, "failed", None, f"bad_image: can't read {chain[-1].file}: {exc}")
                            return
                    try:
                        qc = await self._check(job, image)
                    except CFError as exc:
                        if exc.category in STOPS:
                            raise
                        # The checker couldn't be reached: the image stays unchecked (qc null), and
                        # the next run checks it again without a new image (spec §9.4 [M4]).
                        self._store.set_status(unit_id, "failed", error=self._error_text(exc))
                        return
                    except OSError as exc:
                        # A check-time OSError (a run-log or ledger write, say) fails only this unit,
                        # keeping its current version. The image stays unchecked (qc null), so the
                        # next run checks it again with no new image, same as the outage above.
                        self._store.set_status(
                            unit_id, "failed", error=f"check failed: {shorten(self._log.mask(str(exc)), ERROR_CHARS)}"
                        )
                        return
                    self._store.set_qc(unit_id, step.v, qc)
                    continue
                if step.rewrite is not None:
                    try:
                        job = await self._rewrite(job, step)
                    except RewriteFailed as exc:
                        # Nothing was written, so plan.yaml still holds the design the chain shows.
                        failure = f"rewrite failed: {shorten(self._log.mask(str(exc)), ERROR_CHARS)}"
                        done = finish_step(chain, job.fingerprint)
                        self._finish(job, done.status, done.current, f"{error}; {failure}" if error else failure)
                        return
                attempt = self._builder.retry(job, step.reasons) if step.reasons else job
                try:
                    version, image = await self._generate(attempt, step)
                except CFError as exc:
                    if exc.category in STOPS:
                        raise
                    error = self._error_text(exc)
                    if exc.category is ErrorCategory.REFUSED:  # safety_filtered with no image (spec §9.5)
                        refused = True
                        continue
                    self._failed(job, chain, error)
                    return
                except ImageDecodeError as exc:
                    self._failed(job, chain, f"bad_image: {exc}")
                    return
                chain.append(version)
                images[version.v] = image
        except RunStopped:
            self._store.set_status(unit_id, "planned")
        except BudgetExceeded as exc:
            self.control.stop(StopReason.BUDGET, str(exc))
            self._store.set_status(unit_id, "planned")
        except CFError as exc:
            if exc.category not in STOPS:
                raise
            self.control.stop(STOPS[exc.category], self._log.mask(exc.message))
            self._store.set_status(unit_id, "planned")

    def _failed(self, job: RenderJob, chain: list[Version], error: str) -> None:
        """An API error (after its retries) or unreadable bytes. With no image the unit is failed.
        With an image of the unit's latest design, its best one goes to review. With images of an older
        design only (a rewrite was written, its image never came), it is failed with no current version,
        so the next run renders the new design afresh."""
        if not chain:
            self._store.set_status(job.unit_id, "failed", error=error)
            return
        done = finish_step(chain, job.fingerprint)
        if done.current is None:
            self._finish(job, "failed", None, error)
        else:
            self._finish(job, done.status, done.current, error)

    def _error_text(self, exc: CFError) -> str:
        return error_text(exc, self._log)

    async def _rewrite(self, job: RenderJob, step: Render) -> RenderJob:
        """Soften or redesign the unit with the LLM (spec §7.5); the new job has its new fields. None
        starts once the run is stopping. The new job is built before plan.yaml is written, so a unit
        whose new request can't be made is never written."""
        self.control.check()
        if self._rewriter is None:
            raise RewriteFailed("no plan rewriter in this run")
        built: list[RenderJob] = []

        def check(unit: PlanUnit) -> None:
            try:
                built.append(self._builder.job(unit))
            except (JobError, ConfigError) as exc:
                raise RewriteFailed(str(exc)) from exc

        rewrite = self._rewriter.soften if step.rewrite == "soften" else self._rewriter.redesign
        try:
            with unit_scope(job.unit_id):
                unit = await rewrite(job.unit_id, step.notes, check)
        except CFError as exc:
            if exc.category in STOPS:
                raise
            raise RewriteFailed(self._error_text(exc)) from exc
        if step.rewrite == "soften":
            self.softened.append(job.unit_id)
        if not built or built[-1].unit != unit:  # a rewriter that didn't check the unit it wrote
            check(unit)
        return built[-1]

    async def _generate(self, job: RenderJob, step: Render) -> tuple[Version, Image.Image]:
        metered = await self._calls.image(job, unit=job.unit_id)
        reason = step.reason if step.retry_of is not None else None
        return self._save(job, metered, retry_of=step.retry_of, reason=reason)

    def _save(
        self, job: RenderJob, metered: Metered[ImageResult], *, retry_of: int | None, reason: str | None
    ) -> tuple[Version, Image.Image]:
        """The history file first, then state.json (the unit stays `generating` until its chain
        finishes). A kill between the two is put right by recover() (spec §5.2 [M3])."""
        image = decode_image(metered.result.image_bytes)
        v = self._store.next_version(job.unit_id)
        version = Version(
            v=v,
            file=f"{HISTORY_DIR}/{history_name(job.unit_id, v)}",
            seed=job.seed,
            model=job.model,
            width=image.width,
            height=image.height,
            fingerprint=job.fingerprint,
            refs=[ref.label for ref in job.references],
            prompt_sent=job.prompt,
            retry_of=retry_of,
            retry_reason=reason,
            est_cost_usd=metered.usd,
            latency_s=round(metered.latency_s, 2),
            created=self._now(),
        )
        project = self._store.project_dir
        (project / HISTORY_DIR).mkdir(parents=True, exist_ok=True)
        safe_write(project / version.file, encode_png(image, version.model_dump(mode="json")))
        self._store.add_version(job.unit_id, version, status="generating")
        return version, image

    def _read(self, version: Version) -> Image.Image:
        return decode_image((self._store.project_dir / version.file).read_bytes())

    async def _check(self, job: RenderJob, image: Image.Image) -> QCResult:
        return await self._calls.check(image, expected=job.expected, reference=job.vision_reference, unit=job.unit_id)

    def _finish(self, job: RenderJob, status: UnitStatus, current: int | None, error: str | None) -> None:
        """The final status, then the current copy images/<stem>.png. A kill between the two is put
        right by recover() (spec §5.2 [M3]). `current` None: no version shows the unit's latest design,
        so it has no current version and no current copy (its versions stay in _history). A chain that
        isn't a regeneration from the review page ends any side-by-side pair an earlier one left."""
        if job.unit_id not in self._fresh:
            self._store.unit(job.unit_id).compare_with = None  # saved by finish()
        self._store.finish(job.unit_id, status, current=current, clear_current=current is None,
                           error=error if status in ("needs_review", "failed") else None)
        unit = self._store.unit(job.unit_id)
        version = unit.version(unit.current_version) if unit.current_version is not None else None
        write_current_copy(self._store.project_dir, job.stem, version.file if version is not None else None)
