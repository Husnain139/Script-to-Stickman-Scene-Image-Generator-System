"""Shared test helpers. Tests never touch the network: FakeChat plays the LLM."""

from __future__ import annotations

import pytest

from stickman.cf.client import LLMResult
from stickman.plan.llm import StageRunner
from stickman.runlog import RunLog
from stickman.settings import LLMSettings, RetrySettings


class FakeChat:
    """Stands in for CloudflareClient (chat() and `async with`).

    `replies` is a list (one reply per call, in order) or a function
    `(model, messages) -> reply`. A reply is the model's text, or an exception to raise.
    """

    def __init__(self, replies):
        self._replies = replies if callable(replies) else list(replies)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def chat(self, model, messages, *, temperature=0.4, max_tokens=4096, response_format=None):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
            }
        )
        if callable(self._replies):
            reply = self._replies(model, messages)
        elif self._replies:
            reply = self._replies.pop(0)
        else:
            raise AssertionError("FakeChat: more calls than scripted replies")
        if isinstance(reply, BaseException):
            raise reply
        return LLMResult(text=reply, input_tokens=10, output_tokens=5, raw={}, neurons=1.5)


@pytest.fixture
def fake_chat():
    return FakeChat


@pytest.fixture
def stage_runner():
    async def no_sleep(seconds):
        return None

    def make(chat, cache_dir=None, log_path=None):
        return StageRunner(
            chat, LLMSettings(), RetrySettings(), cache_dir=cache_dir, log=RunLog(log_path), sleep=no_sleep
        )

    return make


def _plan_unit(unit_id, part, start, end, text, corrected=None):
    return {
        "id": unit_id,
        "part": part,
        "start": start,
        "end": end,
        "source_text": text,
        "corrected_text": corrected or text,
        "visual_idea": f"Idea {unit_id}",
        "visual_type": "literal",
        "shot": "wide",
        "time_of_day": "day",
        "characters": [{"ref": "mascot", "action": "waving", "emotion": "happy"}],
        "mood": None,
        "setting": ["ground line"],
        "props": [],
        "composition": "centred, lots of white space",
        "energy_marks": [],
        "softened": False,
        "softened_reason": None,
        "seed": None,
        "image_prompt": f"prompt for {unit_id}",
        "prompt_locked": False,
    }


@pytest.fixture
def plan_data():
    """A small valid plan.yaml as plain data: one whole scene and one split scene."""
    fire = "Fire changed everything for the people around it."
    return {
        "schema_version": 1,
        "project": "demo",
        "aspect": "16:9",
        "style_version": 1,
        "image_model": "@cf/black-forest-labs/flux-2-klein-4b",
        "duration_end": 12.0,
        "pace_wps": 2.5,
        "cast": [
            {"id": "mascot"},
            {"id": "caveman_group", "name": "Caveman group", "figures": 3,
             "description": "three cavemen in fur loincloths", "library_ref": None},
        ],
        "corrections": [{"scene": "001", "from": "90 at night", "to": "9 at night", "reason": "clock time"}],
        "merge_check": [],
        "scenes": [
            {
                "id": "001", "lines": [1], "start": 0.0, "end": 4.0,
                "source_text": "It's 90 at night.", "corrected_text": "It's 9 at night.",
                "split": {"status": "none"},
                "units": [_plan_unit("001", None, 0.0, 4.0, "It's 90 at night.", "It's 9 at night.")],
            },
            {
                "id": "002", "lines": [2], "start": 4.0, "end": 12.0,
                "source_text": fire, "corrected_text": fire,
                "split": {"status": "split", "cut_after_word": 3, "candidates": [3]},
                "units": [
                    _plan_unit("002a", "1 of 2", 4.0, 7.0, "Fire changed everything"),
                    _plan_unit("002b", "2 of 2", 7.0, 12.0, "for the people around it."),
                ],
            },
        ],
    }
