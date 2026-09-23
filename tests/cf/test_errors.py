import pytest

from stickman.cf.errors import CFError, ErrorCategory, classify

DAILY = "You have used up your daily free allocation of 10,000 neurons, please upgrade"
# Recorded in M0 (tests/fixtures/cf/style_dev_clock_3am.json etc.): the probe's
# fixture recorder truncates long error messages at 60 chars, which lands
# mid-word, just before "allocation" finishes.
DAILY_TRUNCATED = "AiError: AiError: you have used up your daily free allocatio...<205 chars>"


@pytest.mark.parametrize(
    "status, body, plan, expected",
    [
        (401, "", "paid", ErrorCategory.AUTH),
        (403, "forbidden", "paid", ErrorCategory.AUTH),
        (429, "Too many requests", "paid", ErrorCategory.RATE_LIMITED),
        (429, DAILY, "free", ErrorCategory.DAILY_LIMIT),
        (429, DAILY, "paid", ErrorCategory.RATE_LIMITED),
        (400, DAILY, "free", ErrorCategory.DAILY_LIMIT),
        (429, DAILY_TRUNCATED, "free", ErrorCategory.DAILY_LIMIT),
        (429, DAILY_TRUNCATED, "paid", ErrorCategory.RATE_LIMITED),
        (500, "internal error", "paid", ErrorCategory.TRANSIENT),
        (503, "", "paid", ErrorCategory.TRANSIENT),
        (400, "width must be at most 1920", "paid", ErrorCategory.BAD_REQUEST),
        (400, "Input was flagged as NSFW", "paid", ErrorCategory.REFUSED),
        (422, "Blocked by content policy", "paid", ErrorCategory.REFUSED),
        (408, "request timeout", "paid", ErrorCategory.TRANSIENT),
    ],
)
def test_classify(status, body, plan, expected):
    assert classify(status, body, plan=plan) is expected


def test_cf_error_carries_details():
    err = CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True)
    assert err.category is ErrorCategory.TRANSIENT
    assert err.possibly_billed is True
    assert err.status is None
    assert "transient" in str(err)
