import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from stickman.budget import Budget, BudgetExceeded
from stickman.cf.client import ImageResult, LLMResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.ledger import Ledger
from stickman.meter import Meter, billing_of
from stickman.settings import BudgetSettings

PK = timezone(timedelta(hours=5))
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=PK)
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
IMAGE_USD = 207.59 * 0.011 / 1000


def metered(tmp_path, budget=None):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    meter = Meter(project="2026-09-25_demo", ledger=ledger, budget=budget, now=lambda: NOW,
                  clock=itertools.count().__next__)
    return meter, ledger


def returning(result):
    calls = []

    async def call():
        calls.append(1)
        return result

    return call, calls


def raising(exc):
    async def call():
        raise exc

    return call


def run(meter, call, **kwargs):
    kwargs = {"kind": "image", "model": KLEIN_4B, "estimate_usd": 0.0023, **kwargs}
    return asyncio.run(meter.run(call, **kwargs))


def test_a_successful_call_is_recorded_at_its_measured_cost(tmp_path):
    meter, ledger = metered(tmp_path)
    call, _ = returning(ImageResult(b"img", neurons=207.59, request_id="req-1"))
    result = run(meter, call, unit="006a")
    assert result.usd == pytest.approx(IMAGE_USD)
    assert result.latency_s == 1
    [entry] = list(ledger.entries())
    assert (entry.ts, entry.project, entry.unit, entry.kind, entry.model) == (NOW, "2026-09-25_demo", "006a", "image", KLEIN_4B)
    assert (entry.billing, entry.request_id, entry.neurons) == ("billed", "req-1", 207.59)
    assert entry.est_usd == pytest.approx(IMAGE_USD)
    assert meter.run_usd == pytest.approx(IMAGE_USD)


def test_without_a_neuron_header_the_token_cost_then_the_estimate_is_used(tmp_path):
    meter, _ = metered(tmp_path)
    reply = LLMResult(text="{}", input_tokens=1000, output_tokens=500, raw={})
    call, _ = returning(reply)
    assert run(meter, call, kind="llm", model="m", estimate_usd=0.5, cost_of=lambda r: 0.01).usd == 0.01
    assert run(meter, call, kind="llm", model="m", estimate_usd=0.5).usd == 0.5


def test_a_timeout_is_recorded_as_possibly_billed_at_its_estimate(tmp_path):
    meter, ledger = metered(tmp_path)
    with pytest.raises(CFError):
        run(meter, raising(CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True)))
    [entry] = list(ledger.entries())
    assert (entry.billing, entry.est_usd, entry.neurons) == ("possibly_billed", 0.0023, None)
    assert (meter.run_usd, meter.possibly_billed) == (0.0023, 1)


def test_an_error_response_is_recorded_as_not_billed(tmp_path):
    meter, ledger = metered(tmp_path)
    with pytest.raises(CFError):
        run(meter, raising(CFError(ErrorCategory.BAD_REQUEST, "invalid", status=400)))
    assert [e.billing for e in ledger.entries()] == ["not_billed"]
    assert meter.run_usd == 0.0


def test_the_budget_is_checked_before_the_call_and_charged_after(tmp_path):
    budget = Budget(BudgetSettings(weekly_usd=0.004), spent=0.0)
    meter, ledger = metered(tmp_path, budget)
    call, calls = returning(ImageResult(b"img", neurons=207.59))
    run(meter, call)
    assert budget.spent == pytest.approx(IMAGE_USD)
    assert budget.in_flight == 0
    with pytest.raises(BudgetExceeded):
        run(meter, call)  # 0.00228 spent + 0.0023 estimate is over 0.004
    assert len(calls) == 1
    assert len(list(ledger.entries())) == 1


def test_a_failed_call_releases_its_reservation(tmp_path):
    budget = Budget(BudgetSettings(), spent=0.0)
    meter, _ = metered(tmp_path, budget)
    with pytest.raises(CFError):
        run(meter, raising(CFError(ErrorCategory.BAD_REQUEST, "invalid", status=400)))
    assert budget.in_flight == 0


def test_billing_of_errors():
    assert billing_of(CFError(ErrorCategory.TRANSIENT, "t", possibly_billed=True)) == "possibly_billed"
    assert billing_of(CFError(ErrorCategory.RATE_LIMITED, "r", status=429)) == "not_billed"


def test_a_bare_meter_only_measures():
    meter = Meter(project="")
    call, _ = returning(ImageResult(b"img", neurons=10.0))
    assert run(meter, call).usd == pytest.approx(10 * 0.011 / 1000)
    assert meter.llm_estimate("m", 400, 100) == 0.0
    assert meter.llm_cost("m", 1, 1) is None
