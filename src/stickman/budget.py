"""The weekly budget (spec §9.7), checked before every image call."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from stickman.ledger import Ledger, week_start
from stickman.pricing import format_usd
from stickman.settings import BudgetSettings


class BudgetExceeded(Exception):
    """Starting this call would take the week's spend over budget.weekly_usd."""


class Budget:
    """spent (billed + possibly billed this week) + in-flight reservations + this call's estimate."""

    def __init__(
        self,
        settings: BudgetSettings,
        *,
        spent: float,
        force: bool = False,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self.spent = spent
        self._force = force
        self._warn = warn or (lambda message: None)
        self._reserved: dict[int, float] = {}
        self._next_token = 0
        self._warned = False
        self._forced = False

    @classmethod
    def from_ledger(
        cls,
        ledger: Ledger,
        settings: BudgetSettings,
        *,
        now: datetime,
        force: bool = False,
        warn: Callable[[str], None] | None = None,
    ) -> Budget:
        return cls(settings, spent=ledger.spent_since(week_start(now)), force=force, warn=warn)

    @property
    def in_flight(self) -> float:
        return sum(self._reserved.values())

    def reserve(self, estimate: float) -> int:
        """Reserve a call's estimate. Raises BudgetExceeded above the limit, unless forced."""
        limit = self._settings.weekly_usd
        projected = self.spent + self.in_flight + estimate
        if projected > limit:
            if not self._force:
                raise BudgetExceeded(
                    f"{format_usd(projected)} would be over the weekly budget of {format_usd(limit)} "
                    f"({format_usd(self.spent)} spent this week)"
                )
            if not self._forced:
                self._forced = True
                self._warn(f"Over the weekly budget ({format_usd(projected)} of {format_usd(limit)}): continuing because of --force.")
        elif projected >= self._settings.warn_ratio * limit and not self._warned:
            self._warned = True
            self._warn(f"Weekly spend has reached {projected / limit:.0%} of the {format_usd(limit)} budget.")
        self._next_token += 1
        self._reserved[self._next_token] = estimate
        return self._next_token

    def release(self, token: int) -> None:
        self._reserved.pop(token, None)

    def add_spent(self, usd: float) -> None:
        self.spent += usd
