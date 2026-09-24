"""Running one LLM planning stage: cache, checks, retry and fallback (spec §6.1)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from stickman.cf.client import LLMResult
from stickman.cf.errors import CFError
from stickman.cf.retry import with_retries
from stickman.fsutil import safe_write
from stickman.plan.jsonx import NoJSONError, error_path, extract_json, inline_schema
from stickman.runlog import RunLog, shorten
from stickman.settings import LLMSettings, RetrySettings

M = TypeVar("M", bound=BaseModel)

STAGE_MAX_TOKENS = 16384  # gpt-oss spends part of this on reasoning; only used tokens are billed
RETRY_MESSAGE = "Your previous JSON had these errors:\n{errors}\nReturn corrected JSON only."
PREVIOUS_REPLY_CHARS = 6000

CUT_OFF_ERROR = (
    "the reply was cut off at the token limit before the JSON was complete; "
    "answer with the JSON directly and keep any reasoning short"
)


def finish_reason(reply: LLMResult) -> str | None:
    """Why the model stopped ("stop", "length", …), when the response says so."""
    try:
        return reply.raw["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


class ChatClient(Protocol):
    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = ...,
        max_tokens: int = ...,
        response_format: dict[str, Any] | None = ...,
    ) -> LLMResult: ...


class PlanningError(Exception):
    """A stage failed its checks twice on each model, so planning stops (spec §6.1)."""

    def __init__(self, stage: str, errors: list[str]) -> None:
        self.stage = stage
        self.errors = errors
        super().__init__(f"the {stage} stage failed on both models: {'; '.join(errors[:5])}")


@dataclass(frozen=True)
class StageRequest(Generic[M]):
    stage: str
    system: str
    user: str
    schema: type[M]
    check: Callable[[M], list[str]] | None = None


def cache_key(model: str, stage: str, prompt: str, schema: dict[str, Any]) -> str:
    material = "\n".join((model, stage, prompt, json.dumps(schema, sort_keys=True)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class StageRunner:
    def __init__(
        self,
        client: ChatClient,
        llm: LLMSettings,
        retry: RetrySettings,
        *,
        cache_dir: Path | None,
        log: RunLog,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._llm = llm
        self._retry = retry
        self._cache_dir = cache_dir
        self._log = log
        self._sleep = sleep

    async def run(self, request: StageRequest[M], *, use_cache: bool = True) -> M:
        schema = inline_schema(request.schema)
        system = f"{request.system}\n\nSCHEMA:\n{json.dumps(schema, ensure_ascii=False)}"
        # Keyed on the planner model even when the fallback answered, so a rerun finds it.
        key = cache_key(self._llm.planner_model, request.stage, f"{system}\n\n{request.user}", schema)
        if use_cache:
            cached = self._read_cache(key)
            if cached is not None:
                result, _ = self._validate(request, cached)
                if result is not None:
                    return result
        base = [{"role": "system", "content": system}, {"role": "user", "content": request.user}]
        json_mode = {"type": "json_schema", "json_schema": {"name": request.stage, "schema": schema}}
        errors: list[str] = []
        for model, response_format in (
            (self._llm.planner_model, None),
            (self._llm.fallback_model, json_mode),
        ):
            messages = base
            for attempt in (1, 2):
                reply, latency = await self._call(request, model, messages, response_format, attempt)
                stop = finish_reason(reply)
                result, errors = self._parse(request, reply.text)
                if result is None and stop == "length":
                    errors = [CUT_OFF_ERROR]
                self._log.write(
                    kind="llm",
                    stage=request.stage,
                    model=model,
                    attempt=attempt,
                    ok=result is not None,
                    errors=errors,
                    finish_reason=stop,
                    reply_chars=len(reply.text),
                    latency_s=round(latency, 2),
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    neurons=reply.neurons,
                    prompt=shorten(request.user),
                )
                if result is not None:
                    self._write_cache(key, result)
                    return result
                feedback = RETRY_MESSAGE.format(errors="\n".join(f"- {error}" for error in errors))
                messages = [
                    *base,
                    {"role": "assistant", "content": reply.text[:PREVIOUS_REPLY_CHARS]},
                    {"role": "user", "content": feedback},
                ]
        raise PlanningError(request.stage, errors)

    async def _call(
        self,
        request: StageRequest[Any],
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None,
        attempt: int,
    ) -> tuple[LLMResult, float]:
        started = time.perf_counter()
        try:
            reply = await with_retries(
                lambda: self._client.chat(
                    model,
                    messages,
                    temperature=self._llm.temperature,
                    max_tokens=STAGE_MAX_TOKENS,
                    response_format=response_format,
                ),
                self._retry,
                sleep=self._sleep,
            )
        except CFError as exc:
            self._log.write(
                kind="llm",
                stage=request.stage,
                model=model,
                attempt=attempt,
                ok=False,
                error=str(exc.category),
                status=exc.status,
                message=shorten(exc.message),
                prompt=shorten(request.user),
                latency_s=round(time.perf_counter() - started, 2),
            )
            raise
        return reply, time.perf_counter() - started

    def _parse(self, request: StageRequest[M], text: str) -> tuple[M | None, list[str]]:
        try:
            data = extract_json(text)
        except NoJSONError as exc:
            return None, [str(exc)]
        return self._validate(request, data)

    def _validate(self, request: StageRequest[M], data: Any) -> tuple[M | None, list[str]]:
        try:
            result = request.schema.model_validate(data)
        except ValidationError as exc:
            return None, [f"{error_path(err['loc'])}: {err['msg']}" for err in exc.errors()]
        errors = request.check(result) if request.check else []
        return (None, errors) if errors else (result, [])

    def _read_cache(self, key: str) -> Any | None:
        if self._cache_dir is None:
            return None
        path = self._cache_dir / f"{key}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))["data"]
        except (ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, key: str, result: BaseModel) -> None:
        if self._cache_dir is None:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"data": result.model_dump(mode="json", by_alias=True)}, ensure_ascii=False)
        safe_write(self._cache_dir / f"{key}.json", payload.encode("utf-8"))
