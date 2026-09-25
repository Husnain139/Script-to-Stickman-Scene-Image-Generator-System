"""Generating units' images, several at once (spec §9.5, §10.3)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from stickman.budget import BudgetExceeded
from stickman.cf.client import ImageResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.cf.retry import with_retries
from stickman.fsutil import safe_write
from stickman.meter import Meter, Metered, billing_of, local_now
from stickman.render.images import HISTORY_DIR, ImageDecodeError, decode_image, encode_png, history_name
from stickman.render.jobs import RenderJob
from stickman.render.state import StateStore, Version
from stickman.runlog import RunLog, shorten
from stickman.settings import RetrySettings

ERROR_CHARS = 200  # how much of an API error message a failed unit keeps in state.json


class ImageClient(Protocol):
    async def generate_image(
        self,
        model: str,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        steps: int | None = ...,
        guidance: float | None = ...,
        input_images: Sequence[bytes] = ...,
    ) -> ImageResult: ...


class StopReason(StrEnum):
    DAILY_LIMIT = "daily_limit"
    BUDGET = "budget"
    CIRCUIT_BREAKER = "circuit_breaker"
    AUTH = "auth"


class RunStopped(Exception):
    """The run is stopping, so this unit's request isn't started."""


class RunControl:
    """Whether the run is stopping, and why. The first reason is the one reported."""

    def __init__(self) -> None:
        self.reason: StopReason | None = None
        self.detail = ""

    def stop(self, reason: StopReason, detail: str = "") -> None:
        if self.reason is None:
            self.reason, self.detail = reason, detail

    def check(self) -> None:
        if self.reason is not None:
            raise RunStopped(str(self.reason))


class CircuitBreaker:
    """Counts temporary errors in a row; a success resets the count (spec §9.5)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def failure(self) -> bool:
        """Count one. True when the limit is reached."""
        self.count += 1
        return self.count >= self.limit

    def success(self) -> None:
        self.count = 0


@dataclass(frozen=True)
class RunResult:
    stop: StopReason | None
    detail: str
    elapsed_s: float


class Renderer:
    def __init__(
        self,
        client: ImageClient,
        store: StateStore,
        meter: Meter,
        *,
        retry: RetrySettings,
        concurrency: int,
        log: RunLog,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = local_now,
        on_done: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._meter = meter
        self._retry = retry
        self._concurrency = concurrency
        self._log = log
        self._sleep = sleep
        self._now = now
        self._on_done = on_done
        self.control = RunControl()
        self._breaker = CircuitBreaker(retry.circuit_breaker)

    async def run(self, jobs: Sequence[RenderJob]) -> RunResult:
        """Render each job's unit, at most `concurrency` at once. Once the run is stopping, no new
        request starts and the requests already running finish (spec §9.5)."""
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(job: RenderJob) -> None:
            async with semaphore:
                if self.control.reason is not None:
                    return
                await self._render(job)
                if self._on_done is not None:
                    self._on_done(job.unit_id)

        await asyncio.gather(*(one(job) for job in jobs))
        return RunResult(self.control.reason, self.control.detail, time.perf_counter() - started)

    async def _render(self, job: RenderJob) -> None:
        """spec §10.3: `generating` is saved before the request, the final status after the image."""
        self._store.set_status(job.unit_id, "generating")
        try:
            metered = await with_retries(lambda: self._attempt(job), self._retry, sleep=self._sleep)
        except RunStopped:
            self._store.set_status(job.unit_id, "planned")
            return
        except BudgetExceeded as exc:
            self.control.stop(StopReason.BUDGET, str(exc))
            self._store.set_status(job.unit_id, "planned")
            return
        except CFError as exc:
            self._failed(job, exc)
            return
        try:
            self._save(job, metered)
        except ImageDecodeError as exc:
            self._store.set_status(job.unit_id, "failed", error=f"bad_image: {exc}")

    async def _attempt(self, job: RenderJob) -> Metered[ImageResult]:
        """One API call: budget-checked, ledgered and logged. The breaker counts temporary errors."""
        self.control.check()
        started = time.perf_counter()
        try:
            metered = await self._meter.run(
                lambda: self._client.generate_image(
                    job.model,
                    prompt=job.prompt,
                    width=job.width,
                    height=job.height,
                    seed=job.seed,
                    steps=job.steps,
                    input_images=[ref.data for ref in job.references],
                ),
                kind="image",
                model=job.model,
                estimate_usd=job.estimate_usd,
                unit=job.unit_id,
            )
        except CFError as exc:
            self._log_call(
                job,
                latency_s=time.perf_counter() - started,
                ok=False,
                error=str(exc.category),
                status=exc.status,
                message=shorten(exc.message),
                billing=billing_of(exc),
                usd=job.estimate_usd,
            )
            if exc.category is ErrorCategory.TRANSIENT and self._breaker.failure():
                self.control.stop(StopReason.CIRCUIT_BREAKER, f"{self._breaker.count} temporary errors in a row")
            raise
        self._breaker.success()
        self._log_call(
            job,
            latency_s=metered.latency_s,
            ok=True,
            billing="billed",
            usd=metered.usd,
            neurons=metered.result.neurons,
            request_id=metered.result.request_id,
        )
        return metered

    def _log_call(self, job: RenderJob, *, latency_s: float, **fields: Any) -> None:
        self._log.write(
            kind="image",
            unit=job.unit_id,
            model=job.model,
            seed=job.seed,
            width=job.width,
            height=job.height,
            steps=job.steps,
            refs=[ref.label for ref in job.references],
            prompt=shorten(job.prompt),
            latency_s=round(latency_s, 2),
            **fields,
        )

    def _failed(self, job: RenderJob, exc: CFError) -> None:
        if exc.category is ErrorCategory.DAILY_LIMIT:
            self.control.stop(StopReason.DAILY_LIMIT, exc.message)
            self._store.set_status(job.unit_id, "planned")
        elif exc.category is ErrorCategory.AUTH:
            self.control.stop(StopReason.AUTH, exc.message)
            self._store.set_status(job.unit_id, "planned")
        else:
            # bad_request and refused (M4 turns a refusal into a softened retry, spec §7.5), or a
            # rate limit or temporary error after its retries.
            message = self._log.mask(shorten(exc.message, ERROR_CHARS))
            self._store.set_status(job.unit_id, "failed", error=f"{exc.category}: {message}")

    def _save(self, job: RenderJob, metered: Metered[ImageResult]) -> None:
        """The history file first, then state.json, then the current copy. A kill between any two
        is put right by recover() when the next run starts (spec §5.2 [M3])."""
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
            est_cost_usd=metered.usd,
            latency_s=round(metered.latency_s, 2),
            created=self._now(),
        )
        data = encode_png(image, version.model_dump(mode="json"))
        project = self._store.project_dir
        (project / HISTORY_DIR).mkdir(parents=True, exist_ok=True)
        safe_write(project / version.file, data)
        self._store.add_version(job.unit_id, version, status="generated")  # M4: QC decides
        safe_write(project / "images" / f"{job.stem}.png", data)
