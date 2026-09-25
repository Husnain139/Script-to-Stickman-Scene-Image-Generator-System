"""Shared test helpers. Tests never touch the network: FakeChat plays the LLM."""

from __future__ import annotations

import functools
import io
import json

import pytest
from PIL import Image

from stickman.cf.client import LLMResult
from stickman.plan.llm import StageRunner
from stickman.runlog import RunLog
from stickman.settings import LLMSettings, RetrySettings


@pytest.fixture(autouse=True)
def _plain_console(monkeypatch):
    """Rich colours CLI output when FORCE_COLOR is set in the user's shell; assertions compare plain text."""
    for name in ("FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
        monkeypatch.delenv(name, raising=False)


@functools.lru_cache
def jpeg_bytes(width=64, height=36):
    """A small white JPEG, like the base64 JPEG Klein returns (M0)."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def jpeg():
    return jpeg_bytes()


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
        if isinstance(reply, LLMResult):
            return reply
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


SAMPLE_GROUPS = [[n] for n in range(1, 13)] + [[13, 14]] + [[n] for n in range(15, 30)]
SAMPLE_CORRECTIONS = [
    {"line": 1, "from": "90 at night", "to": "9 at night", "reason": "impossible clock time"},
    {"line": 6, "from": "Zhuansi", "to": "Ju/'hoansi", "reason": "misheard name"},
    {"line": 13, "from": "Roger E. Kirch", "to": "Roger Ekirch", "reason": "name split by speech-to-text"},
    {"line": 15, "from": "2 sleep", "to": "second sleep", "reason": "misheard term"},
    {"line": 24, "from": "Thomas Ware", "to": "Thomas Wehr", "reason": "misheard name"},
]
SAMPLE_CAST = [
    {"id": "caveman_group", "name": "Caveman group", "figures": 3,
     "description": "a group of three cavemen stickmen in fur loincloths", "library_ref": None},
    {"id": "historian", "name": "Historian", "figures": 1,
     "description": "a stickman historian with round glasses and a book", "library_ref": None},
]
SAMPLE_CANDIDATES = {"005": [6, 15], "006": [7], "008": [10], "013": [13], "015": [11],
                     "023": [10], "024": [19], "026": [12], "028": [6, 9]}


def _sample_cut(user):
    scenes = []
    for block in user.split("\n\n"):
        rows = block.splitlines()
        scene_id = rows[0][len("id: "):]
        n = len(rows[2].split()) - 1  # "words: 1:a 2:b ..."
        candidates = SAMPLE_CANDIDATES.get(scene_id, [])
        if not all(1 <= k <= n - 1 for k in candidates):
            candidates = [n // 2]
        scenes.append({"id": scene_id, "candidates": candidates})
    return {"scenes": scenes}


def _sample_part_text(unit):
    """What a good model returns: the scene's corrected text, or for a part its own words corrected."""
    if unit["part"] is None:
        return unit["scene_corrected_text"]
    text = unit["source_text"]
    for fix in SAMPLE_CORRECTIONS:
        text = text.replace(fix["from"], fix["to"])
    return text


def _sample_design(unit):
    return {
        "id": unit["id"], "corrected_text": _sample_part_text(unit), "visual_idea": f"Idea for {unit['id']}",
        "visual_type": "literal", "shot": "wide", "time_of_day": "night",
        "characters": [{"ref": "caveman_group", "action": "sitting by the fire", "emotion": "calm"}],
        "mood": None, "setting": ["flat ground line"], "props": ["small campfire"],
        "composition": "figures centred, big white sky", "energy_marks": [],
        "softened": False, "softened_reason": None,
    }


def _sample_reply(model, messages):
    """Plays the planner LLM for tests/fixtures/scripts/first-sleep.txt."""
    system, user = messages[0]["content"], messages[1]["content"]
    if system.startswith("You plan illustrations"):
        return json.dumps({"groups": SAMPLE_GROUPS, "corrections": SAMPLE_CORRECTIONS, "cast": SAMPLE_CAST})
    if system.startswith("Each scene below"):
        return json.dumps(_sample_cut(user))
    if system.startswith("You design one illustration"):
        line = next(row for row in user.splitlines() if row.startswith("UNITS: "))
        return json.dumps({"units": [_sample_design(unit) for unit in json.loads(line[len("UNITS: "):])]})
    raise AssertionError(f"unexpected system prompt: {system[:40]!r}")


@pytest.fixture
def sample_reply():
    return _sample_reply


@pytest.fixture
def sample_chat():
    return FakeChat(_sample_reply)
