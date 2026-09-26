import asyncio
import json
from dataclasses import dataclass

import pytest

from stickman.cf.errors import CFError, ErrorCategory
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.qc.vision import ExpectedPicture
from stickman.render.calls import Calls, RunControl, StopReason
from stickman.runlog import RunLog
from stickman.settings import QCSettings, RetrySettings

KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
VISION = "@cf/qwen/qwen3.8-27b"
TRANSIENT = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)
TWO = ExpectedPicture("two stickmen talking", 2, "stickmen: 2")


@dataclass(frozen=True)
class Spec:
    """An image request that isn't a plan unit, like a bootstrap candidate."""

    model: str = KLEIN_9B
    prompt: str = "two stickmen talking"
    width: int = 1024
    height: int = 768
    seed: int = 7
    steps: int | None = None
    references: tuple = ()
    estimate_usd: float = 0.015


async def no_sleep(seconds):
    return None


def make_calls(tmp_path, client, *, retry=None, qc=None):
    meter = Meter(project="bootstrap", ledger=Ledger(tmp_path / "ledger.jsonl"))
    log = RunLog(tmp_path / "run.jsonl", secrets=("tok-secret",))
    retry = retry or RetrySettings()
    return Calls(client, meter, log, RunControl(retry.circuit_breaker), retry=retry, qc=qc or QCSettings(),
                 vision_model=VISION, sleep=no_sleep)


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_an_image_call_is_ledgered_under_its_kind_and_logged(tmp_path, fake_images):
    calls = make_calls(tmp_path, fake_images())
    metered = asyncio.run(calls.image(Spec(), unit="anchor-c1", kind="anchor"))
    assert metered.result.neurons == 207.59
    [entry] = lines(tmp_path / "ledger.jsonl")
    assert (entry["kind"], entry["unit"], entry["model"], entry["billing"]) == ("anchor", "anchor-c1", KLEIN_9B, "billed")
    [logged] = lines(tmp_path / "run.jsonl")
    assert (logged["kind"], logged["unit"], logged["ok"], logged["seed"], logged["width"]) == ("anchor", "anchor-c1", True, 7, 1024)


def test_temporary_errors_of_an_anchor_call_count_as_image_calls(tmp_path, fake_images):
    calls = make_calls(tmp_path, fake_images(lambda call: TRANSIENT), retry=RetrySettings(transient_max=1, circuit_breaker=2))
    with pytest.raises(CFError):
        asyncio.run(calls.image(Spec(), unit="anchor-c1", kind="anchor"))
    assert calls.control.reason is StopReason.CIRCUIT_BREAKER
    assert "(image calls)" in calls.control.detail


def test_no_call_starts_once_the_run_is_stopping(tmp_path, fake_images):
    client = fake_images()
    calls = make_calls(tmp_path, client)
    calls.control.stop(StopReason.BUDGET, "over")
    with pytest.raises(Exception, match="budget"):
        asyncio.run(calls.image(Spec(), unit="anchor-c1", kind="anchor"))
    assert client.calls == []


def test_a_pixel_failure_skips_the_vision_check(tmp_path, fake_images, drawings):
    client = fake_images()
    qc = asyncio.run(make_calls(tmp_path, client).check(drawings.all_black(), expected=TWO, reference=None, unit="anchor-c1"))
    assert (qc.passed, qc.reason, qc.vision) == (False, "safety_filtered", None)
    assert client.chat_calls == []


def test_a_clean_image_is_checked_by_the_vision_model(tmp_path, fake_images, drawings):
    client = fake_images()
    qc = asyncio.run(make_calls(tmp_path, client).check(drawings.clean(), expected=TWO, reference=None, unit="anchor-c1"))
    assert qc.passed and qc.expected_figures == 2 and qc.reference is False
    [call] = client.chat_calls
    assert "Expected: two stickmen talking.\nExpected figures: 2 (stickmen: 2).\n" in call["messages"][0]["content"][0]["text"]
    assert [entry["kind"] for entry in lines(tmp_path / "ledger.jsonl")] == ["vision"]
    assert [entry["unit"] for entry in lines(tmp_path / "ledger.jsonl")] == ["anchor-c1"]


def test_a_checker_that_cannot_be_reached_raises(tmp_path, fake_images, drawings):
    calls = make_calls(tmp_path, fake_images(chat=lambda model, messages: TRANSIENT), retry=RetrySettings(transient_max=0))
    with pytest.raises(CFError) as caught:
        asyncio.run(calls.check(drawings.clean(), expected=TWO, reference=None, unit="anchor-c1"))
    assert caught.value.category is ErrorCategory.TRANSIENT


def test_a_checker_that_answers_unusably_is_a_vision_error(tmp_path, fake_images, drawings):
    calls = make_calls(tmp_path, fake_images(chat=lambda model, messages: "no json here"))
    qc = asyncio.run(calls.check(drawings.clean(), expected=TWO, reference=None, unit="anchor-c1"))
    assert (qc.passed, qc.reason) == (False, "vision_error")
    assert qc.vision_error.startswith("invalid reply: ")
