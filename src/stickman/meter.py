"""A budget check before, and a ledger entry after, every API call (spec §9.7)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from stickman.budget import Budget
from stickman.cf.errors import CFError
from stickman.ledger import Billing, Kind, Ledger, LedgerEntry
from stickman.pricing import PricingConfig, neurons_usd


class Measured(Protocol):
    """A result that reports its own cost: LLMResult and ImageResult."""

    @property
    def neurons(self) -> float | None: ...

    @property
    def request_id(self) -> str | None: ...


R = TypeVar("R", bound=Measured)


def local_now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def billing_of(exc: CFError) -> Billing:
    """A timeout, or a 2xx answer that can't be read, may have been billed; an error response wasn't
    (spec §5.4, §9.5)."""
    return "possibly_billed" if exc.possibly_billed else "not_billed"


@dataclass(frozen=True)
class Metered(Generic[R]):
    result: R
    usd: float  # what the ledger recorded for the call
    latency_s: float


class Meter:
    """One per command run. Without a ledger or budget it only measures, which tests use."""

    def __init__(
        self,
        *,
        project: str,
        ledger: Ledger | None = None,
        budget: Budget | None = None,
        pricing: PricingConfig | None = None,
        now: Callable[[], datetime] = local_now,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.project = project
        self.pricing = pricing
        self._ledger = ledger
        self._budget = budget
        self._now = now
        self._clock = clock
        self.run_usd = 0.0  # billed and possibly billed, through this meter
        self.possibly_billed = 0

    def llm_estimate(self, model: str, prompt_chars: int, max_tokens: int) -> float:
        return 0.0 if self.pricing is None else self.pricing.llm_estimate(model, prompt_chars, max_tokens)

    def llm_cost(self, model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
        return None if self.pricing is None else self.pricing.llm_cost(model, input_tokens, output_tokens)

    async def run(
        self,
        call: Callable[[], Awaitable[R]],
        *,
        kind: Kind,
        model: str,
        estimate_usd: float,
        unit: str | None = None,
        cost_of: Callable[[R], float | None] | None = None,
    ) -> Metered[R]:
        """Make one API call. With a budget, BudgetExceeded is raised before the call when it says no."""
        token = self._budget.reserve(estimate_usd) if self._budget is not None else None
        started = self._clock()
        try:
            result = await call()
        except CFError as exc:
            self._record(kind, model, unit, estimate_usd, billing_of(exc))
            raise
        except asyncio.CancelledError:
            # Ctrl+C cancels a request in flight, which may have been billed, like a timeout.
            self._record(kind, model, unit, estimate_usd, "possibly_billed")
            raise
        finally:
            if token is not None and self._budget is not None:
                self._budget.release(token)
        latency = self._clock() - started
        usd = self._cost(result, estimate_usd, cost_of)
        self._record(kind, model, unit, usd, "billed", neurons=result.neurons, request_id=result.request_id)
        return Metered(result, usd, latency)

    @staticmethod
    def _cost(result: R, estimate: float, cost_of: Callable[[R], float | None] | None) -> float:
        """The cf-ai-neurons header, else the result's own cost (token counts), else the estimate."""
        if result.neurons is not None:
            return neurons_usd(result.neurons)
        actual = cost_of(result) if cost_of is not None else None
        return estimate if actual is None else actual

    def _record(
        self,
        kind: Kind,
        model: str,
        unit: str | None,
        usd: float,
        billing: Billing,
        *,
        neurons: float | None = None,
        request_id: str | None = None,
    ) -> None:
        if billing != "not_billed":
            self.run_usd += usd
            if self._budget is not None:
                self._budget.add_spent(usd)
        if billing == "possibly_billed":
            self.possibly_billed += 1
        if self._ledger is not None:
            self._ledger.append(
                LedgerEntry(
                    ts=self._now(), project=self.project, unit=unit, kind=kind, model=model,
                    est_usd=usd, billing=billing, request_id=request_id, neurons=neurons,
                )
            )
