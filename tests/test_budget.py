from datetime import datetime, timedelta, timezone

import pytest

from stickman.budget import Budget, BudgetExceeded
from stickman.ledger import Ledger, LedgerEntry
from stickman.settings import BudgetSettings

PK = timezone(timedelta(hours=5))


def test_a_reservation_counts_until_it_is_released():
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=0.1)
    token = budget.reserve(0.2)
    assert budget.in_flight == pytest.approx(0.2)
    budget.release(token)
    assert budget.in_flight == 0


def test_in_flight_calls_count_toward_the_limit():
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=0.5)
    budget.reserve(0.3)
    with pytest.raises(BudgetExceeded, match="over the weekly budget"):
        budget.reserve(0.3)


def test_reaching_the_limit_exactly_is_allowed():
    Budget(BudgetSettings(weekly_usd=1.0), spent=0.7).reserve(0.3)


def test_spend_added_after_a_call_counts():
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=0.0)
    budget.add_spent(0.9)
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.2)


def test_the_warning_comes_once_at_the_warn_ratio():
    warnings = []
    budget = Budget(BudgetSettings(weekly_usd=1.0, warn_ratio=0.8), spent=0.75, warn=warnings.append)
    budget.reserve(0.01)
    assert warnings == []
    budget.reserve(0.04)
    budget.reserve(0.01)
    assert len(warnings) == 1
    assert "80%" in warnings[0]


def test_force_goes_past_the_limit_with_one_warning():
    warnings = []
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=1.0, force=True, warn=warnings.append)
    budget.reserve(0.1)
    budget.reserve(0.1)
    assert len(warnings) == 1
    assert "--force" in warnings[0]


def test_the_weeks_spend_comes_from_the_ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for day, usd in ((20, 5.0), (22, 1.25)):
        ledger.append(LedgerEntry(ts=datetime(2026, 9, day, 12, 0, tzinfo=PK), project="p", kind="image",
                                  model="m", est_usd=usd, billing="billed"))
    budget = Budget.from_ledger(ledger, BudgetSettings(), now=datetime(2026, 9, 25, 9, 0, tzinfo=PK))
    assert budget.spent == pytest.approx(1.25)
