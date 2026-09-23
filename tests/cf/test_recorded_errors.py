import json
from pathlib import Path

from stickman.cf.errors import ErrorCategory, classify

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cf"


def load(name):
    record = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return record["status"], json.dumps(record["body"])


def test_recorded_bad_token_is_auth():
    status, body = load("errors_bad_token.json")
    assert classify(status, body, plan="paid") is ErrorCategory.AUTH


def test_recorded_oversized_width_is_bad_request():
    status, body = load("errors_width_too_big.json")
    assert classify(status, body, plan="paid") is ErrorCategory.BAD_REQUEST


def test_recorded_daily_limit_is_daily_limit_on_free():
    status, body = load("style_dev_clock_3am.json")
    assert classify(status, body, plan="free") is ErrorCategory.DAILY_LIMIT


def test_recorded_daily_limit_is_rate_limited_on_paid():
    status, body = load("style_dev_clock_3am.json")
    assert classify(status, body, plan="paid") is ErrorCategory.RATE_LIMITED


def test_recorded_dev_timeout_is_transient():
    status, body = load("image_dev_1920x1080.json")
    assert classify(status, body, plan="paid") is ErrorCategory.TRANSIENT


def test_recorded_unknown_model_is_bad_request():
    status, body = load("errors_unknown_model.json")
    assert classify(status, body, plan="paid") is ErrorCategory.BAD_REQUEST


def test_recorded_too_many_refs_is_bad_request():
    status, body = load("refs_five_refs_512.json")
    assert classify(status, body, plan="paid") is ErrorCategory.BAD_REQUEST
