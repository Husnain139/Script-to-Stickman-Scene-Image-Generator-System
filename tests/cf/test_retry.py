import asyncio

import pytest

from stickman.cf.errors import CFError, ErrorCategory
from stickman.cf.retry import MAX_WAIT_S, backoff_seconds, with_retries
from stickman.settings import RetrySettings

RETRY = RetrySettings(rate_limit_max=2, transient_max=1)


class Trial:
    """A call that raises the given errors in turn, then returns "ok"."""

    def __init__(self, *errors):
        self.queue = list(errors)
        self.calls = 0
        self.waits = []

    async def call(self):
        self.calls += 1
        if self.queue:
            raise self.queue.pop(0)
        return "ok"

    async def sleep(self, seconds):
        self.waits.append(seconds)

    def run(self, retry=RETRY):
        return asyncio.run(with_retries(self.call, retry, sleep=self.sleep, rand=lambda: 0.5))


def err(category, **kwargs):
    return CFError(category, "boom", **kwargs)


def test_success_needs_no_wait():
    trial = Trial()
    assert trial.run() == "ok"
    assert (trial.calls, trial.waits) == (1, [])


def test_rate_limits_back_off_1_then_2_seconds():
    trial = Trial(err(ErrorCategory.RATE_LIMITED), err(ErrorCategory.RATE_LIMITED))
    assert trial.run() == "ok"
    assert (trial.calls, trial.waits) == (3, [1.0, 2.0])


def test_retry_after_is_used_and_capped():
    trial = Trial(err(ErrorCategory.RATE_LIMITED, retry_after=7))
    trial.run()
    assert trial.waits == [7.0]
    capped = Trial(err(ErrorCategory.RATE_LIMITED, retry_after=500))
    capped.run()
    assert capped.waits == [MAX_WAIT_S]


def test_rate_limit_retries_run_out():
    trial = Trial(*[err(ErrorCategory.RATE_LIMITED)] * 3)
    with pytest.raises(CFError) as info:
        trial.run()
    assert info.value.category is ErrorCategory.RATE_LIMITED
    assert trial.calls == 3


def test_transient_errors_retry_up_to_transient_max():
    trial = Trial(err(ErrorCategory.TRANSIENT))
    assert trial.run() == "ok"
    assert trial.waits == [1.0]
    with pytest.raises(CFError):
        Trial(err(ErrorCategory.TRANSIENT), err(ErrorCategory.TRANSIENT)).run()


@pytest.mark.parametrize(
    "category",
    [ErrorCategory.AUTH, ErrorCategory.DAILY_LIMIT, ErrorCategory.BAD_REQUEST, ErrorCategory.REFUSED],
)
def test_other_categories_are_raised_at_once(category):
    error = err(category)
    trial = Trial(error)
    with pytest.raises(CFError) as info:
        trial.run()
    assert info.value is error
    assert (trial.calls, trial.waits) == (1, [])


def test_backoff_doubles_and_is_capped():
    assert backoff_seconds(1, jitter=1.0) == 1.0
    assert backoff_seconds(3, jitter=1.0) == 4.0
    assert backoff_seconds(10, jitter=1.0) == MAX_WAIT_S
