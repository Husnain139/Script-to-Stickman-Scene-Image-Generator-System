import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from stickman.cf.client import LLMResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.plan.llm import CUT_OFF_ERROR, FALLBACK_MAX_TOKENS, STAGE_MAX_TOKENS, PlanningError, StageRequest, StageRunner
from stickman.pricing import load_pricing
from stickman.runlog import RunLog
from stickman.settings import LLMSettings, RetrySettings

LLM = LLMSettings()
GOOD = '{"animal": "dog", "legs": 4}'


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    animal: str
    legs: int


def four_legs(answer):
    return [] if answer.legs == 4 else [f"legs: expected 4, got {answer.legs}"]


def request(check=None):
    return StageRequest(stage="demo", system="SYSTEM", user="USER", schema=Answer, check=check)


def run(runner, req, **kwargs):
    return asyncio.run(runner.run(req, **kwargs))


def test_valid_first_reply_is_returned(fake_chat, stage_runner):
    chat = fake_chat([GOOD])
    assert run(stage_runner(chat), request()) == Answer(animal="dog", legs=4)
    [call] = chat.calls
    assert call["model"] == LLM.planner_model
    assert call["response_format"] is None
    assert call["max_tokens"] == STAGE_MAX_TOKENS
    assert call["temperature"] == LLM.temperature
    system, user = call["messages"]
    assert system["role"] == "system"
    assert system["content"].startswith("SYSTEM\n\nSCHEMA:\n")
    assert '"legs"' in system["content"]
    assert user == {"role": "user", "content": "USER"}


def test_invalid_reply_is_retried_with_the_errors(fake_chat, stage_runner):
    chat = fake_chat(['{"animal": "dog"}', GOOD])
    assert run(stage_runner(chat), request()).legs == 4
    retry = chat.calls[1]
    assert retry["model"] == LLM.planner_model
    assert [m["role"] for m in retry["messages"]] == ["system", "user", "assistant", "user"]
    assert retry["messages"][2]["content"] == '{"animal": "dog"}'
    feedback = retry["messages"][3]["content"]
    assert feedback.startswith("Your previous JSON had these errors:")
    assert "- legs: Field required" in feedback
    assert feedback.endswith("Return corrected JSON only.")


def test_a_retry_shows_the_model_its_whole_previous_reply(fake_chat, stage_runner):
    long_reply = '{"animal": "dog", "note": "' + "x" * 20000 + '"}'  # live describe replies ran to 7,390 chars
    chat = fake_chat([long_reply, GOOD])
    assert run(stage_runner(chat), request()).legs == 4
    assert chat.calls[1]["messages"][2]["content"] == long_reply


def test_stage_check_failures_count_as_invalid(fake_chat, stage_runner):
    chat = fake_chat(['{"animal": "spider", "legs": 8}', GOOD])
    assert run(stage_runner(chat), request(four_legs)).legs == 4
    assert "legs: expected 4, got 8" in chat.calls[1]["messages"][3]["content"]


def test_two_failures_switch_to_the_fallback_in_json_mode(fake_chat, stage_runner):
    chat = fake_chat(["not json", "still not json", GOOD])
    assert run(stage_runner(chat), request()).animal == "dog"
    fallback = chat.calls[2]
    assert fallback["model"] == LLM.fallback_model
    assert fallback["response_format"]["type"] == "json_schema"
    assert fallback["response_format"]["json_schema"]["name"] == "demo"
    assert "legs" in fallback["response_format"]["json_schema"]["schema"]["properties"]
    assert len(fallback["messages"]) == 2  # a fresh request, not the planner's retry


def test_four_failures_stop_planning(fake_chat, stage_runner):
    chat = fake_chat(["nope"] * 4)
    with pytest.raises(PlanningError) as info:
        run(stage_runner(chat), request())
    assert info.value.stage == "demo"
    assert info.value.errors == ["the reply contains no JSON object"]
    assert [c["model"] for c in chat.calls] == [LLM.planner_model] * 2 + [LLM.fallback_model] * 2
    assert chat.calls[3]["response_format"] is not None


def test_a_cached_result_needs_no_call(fake_chat, stage_runner, tmp_path):
    run(stage_runner(fake_chat([GOOD]), cache_dir=tmp_path), request())
    again = fake_chat([])
    assert run(stage_runner(again, cache_dir=tmp_path), request()).legs == 4
    assert again.calls == []


def test_use_cache_false_always_calls(fake_chat, stage_runner, tmp_path):
    run(stage_runner(fake_chat([GOOD]), cache_dir=tmp_path), request())
    again = fake_chat(['{"animal": "cat", "legs": 4}'])
    assert run(stage_runner(again, cache_dir=tmp_path), request(), use_cache=False).animal == "cat"


def test_a_cached_result_that_now_fails_its_check_is_requested_again(fake_chat, stage_runner, tmp_path):
    run(stage_runner(fake_chat(['{"animal": "spider", "legs": 8}']), cache_dir=tmp_path), request())
    again = fake_chat([GOOD])
    assert run(stage_runner(again, cache_dir=tmp_path), request(four_legs)).legs == 4
    assert len(again.calls) == 1


def test_daily_limit_propagates_without_retry(fake_chat, stage_runner):
    chat = fake_chat([CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation")])
    with pytest.raises(CFError) as info:
        run(stage_runner(chat), request())
    assert info.value.category is ErrorCategory.DAILY_LIMIT
    assert len(chat.calls) == 1


def test_rate_limits_are_waited_out(fake_chat, stage_runner):
    chat = fake_chat([CFError(ErrorCategory.RATE_LIMITED, "slow down"), GOOD])
    assert run(stage_runner(chat), request()).legs == 4
    assert len(chat.calls) == 2


def test_every_attempt_is_logged(fake_chat, stage_runner, tmp_path):
    log_path = tmp_path / "run.jsonl"
    run(stage_runner(fake_chat(["nope", GOOD]), log_path=log_path), request())
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["stage"], e["attempt"], e["ok"]) for e in entries] == [("demo", 1, False), ("demo", 2, True)]
    assert entries[0]["errors"] == ["the reply contains no JSON object"]
    assert entries[1]["neurons"] == 1.5
    assert all(e["kind"] == "llm" and e["model"] == LLM.planner_model for e in entries)


def test_a_cloudflare_error_is_logged_with_its_prompt(fake_chat, stage_runner, tmp_path):
    log_path = tmp_path / "run.jsonl"
    chat = fake_chat([CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation")])
    with pytest.raises(CFError):
        run(stage_runner(chat, log_path=log_path), request())
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["ok"] is False
    assert entries[0]["error"] == "daily_limit"
    assert entries[0]["prompt"] == "USER"


def cut_off(text):
    return LLMResult(text=text, input_tokens=10, output_tokens=5, raw={"choices": [{"finish_reason": "length"}]}, neurons=1.5)


def test_a_cut_off_reply_is_retried_with_a_clear_error(fake_chat, stage_runner, tmp_path):
    log_path = tmp_path / "run.jsonl"
    chat = fake_chat([cut_off('{"animal": "dog", "le'), GOOD])
    assert run(stage_runner(chat, log_path=log_path), request()).legs == 4
    assert f"- {CUT_OFF_ERROR}" in chat.calls[1]["messages"][3]["content"]
    first, second = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert (first["finish_reason"], first["reply_chars"], first["errors"]) == ("length", 21, [CUT_OFF_ERROR])
    assert (second["finish_reason"], second["reply_chars"]) == (None, len(GOOD))


def test_a_complete_answer_is_kept_even_if_the_reply_was_cut_off_after_it(fake_chat, stage_runner):
    chat = fake_chat([cut_off(GOOD + " and then some")])
    assert run(stage_runner(chat), request()).legs == 4


def test_a_complete_but_wrong_answer_keeps_its_real_errors_when_cut_off(fake_chat, stage_runner):
    chat = fake_chat([cut_off('{"animal": "spider", "legs": 8}'), GOOD])
    assert run(stage_runner(chat), request(four_legs)).legs == 4
    feedback = chat.calls[1]["messages"][3]["content"]
    assert "legs: expected 4, got 8" in feedback
    assert CUT_OFF_ERROR not in feedback


def test_every_api_call_is_logged_including_retried_ones(fake_chat, stage_runner, tmp_path):
    log_path = tmp_path / "run.jsonl"
    chat = fake_chat([
        CFError(ErrorCategory.RATE_LIMITED, "slow down", status=429),
        CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True),
        GOOD,
    ])
    run(stage_runner(chat, log_path=log_path), request())
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["attempt"], e["call"], e["ok"], e.get("error"), e["billing"]) for e in entries] == [
        (1, 1, False, "rate_limited", "not_billed"),
        (1, 2, False, "transient", "possibly_billed"),
        (1, 3, True, None, "billed"),
    ]
    assert len({e["cache_key"] for e in entries}) == 1
    assert all(e["max_tokens"] == STAGE_MAX_TOKENS and e["json_mode"] is False for e in entries)


def test_the_fallback_model_has_its_own_token_limit(fake_chat, stage_runner):
    chat = fake_chat(["nope", "nope", GOOD])
    run(stage_runner(chat), request())
    assert [c["max_tokens"] for c in chat.calls] == [STAGE_MAX_TOKENS, STAGE_MAX_TOKENS, FALLBACK_MAX_TOKENS]
    assert chat.calls[2]["model"] == LLM.fallback_model


def test_planning_calls_go_in_the_ledger(fake_chat, tmp_path):
    async def no_sleep(seconds):
        return None

    ledger = Ledger(tmp_path / "ledger.jsonl")
    meter = Meter(project="2026-09-25_demo", ledger=ledger, pricing=load_pricing(tmp_path))
    runner = StageRunner(fake_chat([GOOD]), LLMSettings(), RetrySettings(), cache_dir=None, log=RunLog(None),
                         sleep=no_sleep, meter=meter)
    run(runner, request())
    [entry] = list(ledger.entries())
    assert (entry.kind, entry.model, entry.billing, entry.neurons, entry.project) == (
        "llm", LLM.planner_model, "billed", 1.5, "2026-09-25_demo")
    assert entry.est_usd == pytest.approx(1.5 * 0.011 / 1000)
