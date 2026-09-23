"""Waiting out rate limits and temporary errors (spec §9.5)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from stickman.cf.errors import CFError, ErrorCategory
from stickman.settings import RetrySettings

T = TypeVar("T")
MAX_WAIT_S = 60.0


def backoff_seconds(attempt: int, *, jitter: float) -> float:
    """1, 2, 4 … s for attempts 1, 2, 3 …, times a jitter factor, capped at 60 s."""
    return min(MAX_WAIT_S, 2.0 ** (attempt - 1) * jitter)


async def with_retries(
    call: Callable[[], Awaitable[T]],
    retry: RetrySettings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
) -> T:
    """Run `call`, retrying rate limits up to retry.rate_limit_max times and temporary
    errors up to retry.transient_max times. Every other error is raised at once."""
    rate_limited = 0
    transient = 0
    while True:
        try:
            return await call()
        except CFError as exc:
            if exc.category is ErrorCategory.RATE_LIMITED and rate_limited < retry.rate_limit_max:
                rate_limited += 1
                if exc.retry_after is not None:
                    wait = min(exc.retry_after, MAX_WAIT_S)
                else:
                    wait = backoff_seconds(rate_limited, jitter=0.5 + rand())
            elif exc.category is ErrorCategory.TRANSIENT and transient < retry.transient_max:
                transient += 1
                wait = backoff_seconds(transient, jitter=0.5 + rand())
            else:
                raise
        await sleep(wait)
