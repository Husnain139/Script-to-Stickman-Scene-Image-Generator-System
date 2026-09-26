"""A run's image and vision calls (spec §9.4, §9.5, §9.7): budget-checked and ledgered by the meter,
logged, retried, and counted by the circuit breaker. The renderer, bootstrap and compare all make their
calls through a Calls object, so they all stop, pause and record the same way."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any, Protocol

from PIL import Image

from stickman.cf.client import ImageResult, LLMResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.cf.retry import with_retries
from stickman.ledger import Kind
from stickman.meter import Meter, Metered, billing_of
from stickman.plan.llm import finish_reason
from stickman.qc.decide import QCResult, decide
from stickman.qc.pixel import pixel_check
from stickman.qc.vision import (
    IMAGE_TOKENS,
    VISION_ATTEMPTS,
    VISION_MAX_TOKENS,
    ExpectedPicture,
    VisionReport,
    parse_vision,
    retry_messages,
    vision_messages,
    vision_png,
    vision_prompt,
)
from stickman.render.references import RefImage
from stickman.runlog import RunLog, shorten
from stickman.settings import QCSettings, RetrySettings

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
    """The run is stopping, so this request isn't started."""


class RunControl:
    """Whether the run is stopping, and why. The first reason is the one reported.

    It is also the circuit breaker (spec §9.5): temporary errors in a row are counted per kind of
    call (`image`, `vision`, `llm`), a success resets only its own kind's count, and any count reaching
    `circuit_breaker` stops the run. So an outage of one model trips it while the others still answer."""

    def __init__(self, circuit_breaker: int) -> None:
        self.reason: StopReason | None = None
        self.detail = ""
        self._limit = circuit_breaker
        self._in_a_row: dict[str, int] = {}

    def stop(self, reason: StopReason, detail: str = "") -> None:
        if self.reason is None:
            self.reason, self.detail = reason, detail

    def check(self) -> None:
        if self.reason is not None:
            raise RunStopped(str(self.reason))

    def transient(self, kind: str) -> None:
        """One attempt of a `kind` call ended in a temporary error."""
        count = self._in_a_row.get(kind, 0) + 1
        self._in_a_row[kind] = count
        if count >= self._limit:
            self.stop(StopReason.CIRCUIT_BREAKER, f"{count} temporary errors in a row ({kind} calls)")

    def success(self, kind: str) -> None:
        self._in_a_row[kind] = 0


# Errors that stop the whole run, from any call (spec §9.5).
STOPS: dict[ErrorCategory, StopReason] = {
    ErrorCategory.DAILY_LIMIT: StopReason.DAILY_LIMIT,
    ErrorCategory.AUTH: StopReason.AUTH,
}
# A vision call that ends in one of these (after its retries) never reached the checker (spec §9.4 [M4]).
UNREACHABLE = (ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMITED)


class ImageSpec(Protocol):
    """What an image request is made of. RenderJob (a unit) and CandidateJob (bootstrap) have it."""

    @property
    def model(self) -> str: ...
    @property
    def prompt(self) -> str: ...
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def seed(self) -> int: ...
    @property
    def steps(self) -> int | None: ...
    @property
    def references(self) -> tuple[RefImage, ...]: ...
    @property
    def estimate_usd(self) -> float: ...


def error_text(exc: CFError, log: RunLog) -> str:
    """How an API error is kept and shown. Masked before the cut, which could split a secret."""
    return f"{exc.category}: {shorten(log.mask(exc.message), ERROR_CHARS)}"


class Calls:
    """One run's image and vision calls. `control` is the run's stop flag and circuit breaker; pass
    the one a GuardedChat uses too, so a rewrite's errors reach the same breaker."""

    def __init__(
        self,
        client: RenderClient,
        meter: Meter,
        log: RunLog,
        control: RunControl,
        *,
        retry: RetrySettings,
        qc: QCSettings,
        vision_model: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._meter = meter
        self.log = log
        self.control = control
        self._retry = retry
        self._qc = qc
        self._vision_model = vision_model
        self._sleep = sleep

    async def image(self, spec: ImageSpec, *, unit: str | None, kind: Kind = "image") -> Metered[ImageResult]:
        """One image, with rate limits and temporary errors retried (spec §9.5). The ledger records it
        under `kind` (image, anchor or sheet); the breaker counts it as an `image` call either way."""
        return await with_retries(lambda: self._image_once(spec, unit, kind), self._retry, sleep=self._sleep)

    async def _image_once(self, spec: ImageSpec, unit: str | None, kind: Kind) -> Metered[ImageResult]:
        fields = {
            "seed": spec.seed, "width": spec.width, "height": spec.height, "steps": spec.steps,
            "refs": [ref.label for ref in spec.references], "prompt": shorten(spec.prompt),
        }
        metered = await self._call(
            unit, kind, "image",
            lambda: self._client.generate_image(
                spec.model, prompt=spec.prompt, width=spec.width, height=spec.height, seed=spec.seed,
                steps=spec.steps, input_images=[ref.data for ref in spec.references],
            ),
            model=spec.model, estimate=spec.estimate_usd, fields=fields,
        )
        self.log.write(
            kind=kind, unit=unit, model=spec.model, **fields, latency_s=round(metered.latency_s, 2),
            ok=True, billing="billed", usd=metered.usd, neurons=metered.result.neurons,
            request_id=metered.result.request_id,
        )
        return metered

    async def check(
        self, image: Image.Image, *, expected: ExpectedPicture, reference: RefImage | None, unit: str | None
    ) -> QCResult:
        """Pixel checks, then the vision check only if they pass (spec §11.2)."""
        pixel = await asyncio.to_thread(pixel_check, image, self._qc)
        common = {
            "expected_figures": expected.figures,
            "min_figures": expected.fewest,
            "reference": reference is not None,
            "min_idea_score": self._qc.min_idea_score,
        }
        if pixel.reason is not None or not self._qc.vision:
            return decide(pixel, **common)
        report, error = await self._vision(image, expected, reference, unit)
        return decide(pixel, report, vision_error=error, **common)

    async def _vision(
        self, image: Image.Image, expected: ExpectedPicture, reference: RefImage | None, unit: str | None
    ) -> tuple[VisionReport | None, str | None]:
        """Up to VISION_ATTEMPTS answers; the second sees the first one's errors (spec §9.4). The daily
        limit and a rejected token stop the run. A checker that couldn't be reached (temporary errors or
        rate limits past their retries) raises its CFError, so the image stays unchecked. A checker that
        answered unusably (an invalid reply twice, bad_request, refused) is the result's vision_error."""
        prompt = vision_prompt(expected, reference=reference is not None)
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
                        unit, "vision", "vision",
                        lambda: self._client.chat(model, sent, temperature=0.0, max_tokens=VISION_MAX_TOKENS),
                        model=model, estimate=estimate, fields={**fields, "attempt": attempt},
                        cost_of=lambda reply: self._meter.llm_cost(model, reply.input_tokens, reply.output_tokens),
                    ),
                    self._retry,
                    sleep=self._sleep,
                )
            except CFError as exc:
                if exc.category in STOPS or exc.category in UNREACHABLE:
                    raise
                return None, error_text(exc, self.log)
            reply: LLMResult = metered.result
            stop = finish_reason(reply)
            report, errors = parse_vision(reply.text, finish_reason=stop)
            self.log.write(
                kind="vision", unit=unit, model=model, **fields, attempt=attempt,
                latency_s=round(metered.latency_s, 2), ok=report is not None, errors=errors, finish_reason=stop,
                input_tokens=reply.input_tokens, output_tokens=reply.output_tokens, billing="billed",
                usd=metered.usd, neurons=reply.neurons, request_id=reply.request_id,
            )
            if report is not None:
                return report, None
            messages = retry_messages(messages, reply.text, errors)
        return None, "invalid reply: " + "; ".join(errors[:3])

    async def _call(
        self,
        unit: str | None,
        kind: Kind,
        breaker: str,
        request: Callable[[], Awaitable[Any]],
        *,
        model: str,
        estimate: float,
        fields: dict[str, Any],
        cost_of: Callable[[Any], float | None] | None = None,
    ) -> Metered[Any]:
        """One call, metered and logged when it fails. `breaker` is the kind the circuit breaker counts
        its temporary errors under (spec §9.5 [M4]): image, vision or llm."""
        self.control.check()
        started = time.perf_counter()
        try:
            metered = await self._meter.run(
                request, kind=kind, model=model, estimate_usd=estimate, unit=unit, cost_of=cost_of
            )
        except CFError as exc:
            self.log.write(
                kind=kind, unit=unit, model=model, **fields,
                latency_s=round(time.perf_counter() - started, 2), ok=False, error=str(exc.category),
                status=exc.status, message=shorten(self.log.mask(exc.message)),  # masked before the cut
                billing=billing_of(exc), usd=estimate,
            )
            if exc.category is ErrorCategory.TRANSIENT:
                self.control.transient(breaker)
            raise
        self.control.success(breaker)
        return metered
