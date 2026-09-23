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
