"""Generating units' images several at once. Each image is checked by QC and, when it fails, retried
with the fix for its reason (spec §9.5, §10.3, §11)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from PIL import Image

from stickman.budget import BudgetExceeded
from stickman.cf.client import ImageResult, LLMResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.cf.retry import with_retries
from stickman.fsutil import safe_write
from stickman.meter import Meter, Metered, billing_of, local_now
from stickman.plan.llm import finish_reason
from stickman.qc.decide import QCResult, decide
from stickman.qc.pixel import pixel_check
from stickman.qc.vision import (
    IMAGE_TOKENS,
    VISION_ATTEMPTS,
    VISION_MAX_TOKENS,
    VisionReport,
    parse_vision,
    retry_messages,
    vision_messages,
    vision_png,
    vision_prompt,
)
from stickman.render.chain import Check, Finish, Render, chain_of, finish_step, next_step
from stickman.render.images import HISTORY_DIR, ImageDecodeError, decode_image, encode_png, history_name
from stickman.render.jobs import JobBuilder, JobError, RenderJob
from stickman.render.rewrite import Rewriter, RewriteFailed, softened_by_qc
from stickman.render.state import StateStore, Version
from stickman.runlog import RunLog, shorten
from stickman.settings import ConfigError, QCSettings, RetrySettings

ERROR_CHARS = 200  # how much of an API error message a unit keeps in state.json


class RenderClient(Protocol):
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

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = ...,
        max_tokens: int = ...,
        response_format: dict[str, Any] | None = ...,
    ) -> LLMResult: ...


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



# Errors that stop the whole run, from any call of any unit (spec §9.5).
STOPS: dict[ErrorCategory, StopReason] = {
    ErrorCategory.DAILY_LIMIT: StopReason.DAILY_LIMIT,
    ErrorCategory.AUTH: StopReason.AUTH,
}


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
    ) -> None:
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
        self.control = RunControl()
        self._breaker = CircuitBreaker(retry.circuit_breaker)
        self.softened: list[str] = []  # units softened after a safety filter in this run

    async def run(self, jobs: Sequence[RenderJob]) -> RunResult:
        """Each job's unit, at most `concurrency` at once. A unit holds its slot for its whole chain, so
        vision calls share the image requests' limit (spec §10.3). Once the run is stopping, no new
        request starts and the requests already out finish (spec §9.5)."""
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
        chain = chain_of(self._store.unit(unit_id), job.fingerprint)
        images: dict[int, Image.Image] = {}  # images made in this run, by version, so QC needn't read them back
        refused = False
        error: str | None = None
        try:
            while True:
                step = next_step(
                    chain, qc_max=self._retry.qc_max, locked=job.unit.prompt_locked,
                    softened=softened_by_qc(job.unit), refused=refused,
                )
                refused = False
                if isinstance(step, Finish):
                    self._finish(job, step, error)
                    return
                if isinstance(step, Check):
                    try:
                        qc = await self._check(job, chain[-1], images.get(step.v))
                    except (OSError, ImageDecodeError) as exc:
                        self._store.set_status(unit_id, "failed", error=f"bad_image: can't read {chain[-1].file}: {exc}")
                        return
                    self._store.set_qc(unit_id, step.v, qc)
                    continue
                if step.rewrite is not None:
                    try:
                        job = await self._rewrite(job, step)
                    except RewriteFailed as exc:
                        failure = f"rewrite failed: {shorten(self._log.mask(str(exc)), ERROR_CHARS)}"
                        self._finish(job, finish_step(chain), f"{error}; {failure}" if error else failure)
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
        """An API error (after its retries) or unreadable bytes. With no image the unit is failed;
        otherwise its best image so far goes to review."""
        if chain:
            self._finish(job, finish_step(chain), error)
        else:
            self._store.set_status(job.unit_id, "failed", error=error)

    def _error_text(self, exc: CFError) -> str:
        # Masked before the cut, which could split a secret.
        return f"{exc.category}: {shorten(self._log.mask(exc.message), ERROR_CHARS)}"

    async def _rewrite(self, job: RenderJob, step: Render) -> RenderJob:
        """Soften or redesign the unit with the LLM (spec §7.5); the new job has its new fields."""
        if self._rewriter is None:
            raise RewriteFailed("no plan rewriter in this run")
        rewrite = self._rewriter.soften if step.rewrite == "soften" else self._rewriter.redesign
        try:
            unit = await rewrite(job.unit_id, step.notes)
        except CFError as exc:
            if exc.category in STOPS:
                raise
            raise RewriteFailed(self._error_text(exc)) from exc
        if step.rewrite == "soften":
            self.softened.append(job.unit_id)
        try:
            return self._builder.job(unit)
        except (JobError, ConfigError) as exc:
            raise RewriteFailed(str(exc)) from exc

    async def _call(
        self,
        job: RenderJob,
        kind: str,
        request: Callable[[], Awaitable[Any]],
        *,
        model: str,
        estimate: float,
        fields: dict[str, Any],
        cost_of: Callable[[Any], float | None] | None = None,
    ) -> Metered[Any]:
        """One API call: budget-checked and ledgered by the meter, and logged here when it fails. The
        breaker counts temporary errors of every kind of call (spec §9.5)."""
        self.control.check()
        started = time.perf_counter()
        try:
            metered = await self._meter.run(
                request, kind=kind, model=model, estimate_usd=estimate, unit=job.unit_id, cost_of=cost_of
            )
        except CFError as exc:
            self._log.write(
                kind=kind, unit=job.unit_id, model=model, **fields,
                latency_s=round(time.perf_counter() - started, 2), ok=False, error=str(exc.category),
                status=exc.status, message=shorten(self._log.mask(exc.message)),  # masked before the cut
                billing=billing_of(exc), usd=estimate,
            )
            if exc.category is ErrorCategory.TRANSIENT and self._breaker.failure():
                self.control.stop(StopReason.CIRCUIT_BREAKER, f"{self._breaker.count} temporary errors in a row")
            raise
        self._breaker.success()
        return metered

    def _image_fields(self, job: RenderJob) -> dict[str, Any]:
        return {
            "seed": job.seed, "width": job.width, "height": job.height, "steps": job.steps,
            "refs": [ref.label for ref in job.references], "prompt": shorten(job.prompt),
        }

    async def _image(self, job: RenderJob) -> Metered[ImageResult]:
        fields = self._image_fields(job)
        metered = await self._call(
            job,
            "image",
            lambda: self._client.generate_image(
                job.model, prompt=job.prompt, width=job.width, height=job.height, seed=job.seed,
                steps=job.steps, input_images=[ref.data for ref in job.references],
            ),
            model=job.model,
            estimate=job.estimate_usd,
            fields=fields,
        )
        self._log.write(
            kind="image", unit=job.unit_id, model=job.model, **fields, latency_s=round(metered.latency_s, 2),
            ok=True, billing="billed", usd=metered.usd, neurons=metered.result.neurons,
            request_id=metered.result.request_id,
        )
        return metered

    async def _generate(self, job: RenderJob, step: Render) -> tuple[Version, Image.Image]:
        metered = await with_retries(lambda: self._image(job), self._retry, sleep=self._sleep)
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

    async def _check(self, job: RenderJob, version: Version, image: Image.Image | None) -> QCResult:
        """Pixel checks, then the vision check only if they pass (spec §11.2)."""
        if image is None:  # made by an earlier run
            image = decode_image((self._store.project_dir / version.file).read_bytes())
        pixel = await asyncio.to_thread(pixel_check, image, self._qc)
        common = {
            "expected_figures": job.expected.figures,
            "reference": job.vision_reference is not None,
            "min_idea_score": self._qc.min_idea_score,
        }
        if pixel.reason is not None or not self._qc.vision:
            return decide(pixel, **common)
        report, error = await self._vision(job, image)
        return decide(pixel, report, vision_error=error, **common)

    async def _vision(self, job: RenderJob, image: Image.Image) -> tuple[VisionReport | None, str | None]:
        """Up to VISION_ATTEMPTS answers; the second sees the first one's errors (spec §9.4). The daily
        limit and a rejected token stop the run; any other failure is the result's vision_error."""
        reference = job.vision_reference
        prompt = vision_prompt(job.expected, reference=reference is not None)
        messages = vision_messages(prompt, vision_png(image), reference.data if reference is not None else None)
        images = 1 if reference is None else 2
        model = self._vision_model
        estimate = self._meter.llm_estimate(model, len(prompt) + 4 * IMAGE_TOKENS * images, VISION_MAX_TOKENS)
        fields: dict[str, Any] = {"prompt": shorten(prompt), "images": images,
                                  "refs": [reference.label] if reference is not None else []}
        errors: list[str] = []
        for attempt in range(1, VISION_ATTEMPTS + 1):
            sent = messages
            try:
                metered = await with_retries(
                    lambda: self._call(
                        job, "vision",
                        lambda: self._client.chat(model, sent, temperature=0.0, max_tokens=VISION_MAX_TOKENS),
                        model=model, estimate=estimate, fields={**fields, "attempt": attempt},
                        cost_of=lambda reply: self._meter.llm_cost(model, reply.input_tokens, reply.output_tokens),
                    ),
                    self._retry,
                    sleep=self._sleep,
                )
            except CFError as exc:
                if exc.category in STOPS:
                    raise
                return None, self._error_text(exc)
            reply: LLMResult = metered.result
            stop = finish_reason(reply)
            report, errors = parse_vision(reply.text, finish_reason=stop)
            self._log.write(
                kind="vision", unit=job.unit_id, model=model, **fields, attempt=attempt,
                latency_s=round(metered.latency_s, 2), ok=report is not None, errors=errors, finish_reason=stop,
                input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, billing="billed",
                usd=metered.usd, neurons=reply.neurons, request_id=reply.request_id,
            )
            if report is not None:
                return report, None
            messages = retry_messages(messages, reply.text, errors)
        return None, "invalid reply: " + "; ".join(errors[:3])

    def _finish(self, job: RenderJob, step: Finish, error: str | None) -> None:
        """The final status, then the current copy images/<stem>.png. A kill between the two is put
        right by recover() (spec §5.2 [M3])."""
        self._store.finish(job.unit_id, step.status, current=step.current,
                           error=error if step.status == "needs_review" else None)
        unit = self._store.unit(job.unit_id)
        version = unit.version(unit.current_version) if unit.current_version is not None else None
        if version is not None:
            project = self._store.project_dir
            safe_write(project / "images" / f"{job.stem}.png", (project / version.file).read_bytes())
