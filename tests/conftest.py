"""Shared test helpers. Tests never touch the network: FakeChat plays the LLM."""

from __future__ import annotations

import asyncio
import functools
import inspect
import io
import json
import re
import types

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from stickman.cf.client import ImageResult, LLMResult
from stickman.config_files import load_mascot, load_style
from stickman.plan.cast import cast_infos
from stickman.plan.llm import StageRunner
from stickman.plan.models import parse_plan
from stickman.prompt.builder import build_prompt
from stickman.runlog import RunLog
from stickman.settings import LLMSettings, RetrySettings


@pytest.fixture(autouse=True)
def _plain_console(monkeypatch):
    """Rich colours CLI output when FORCE_COLOR is set in the user's shell; assertions compare plain text."""
    for name in ("FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
        monkeypatch.delenv(name, raising=False)


DRAWING_SIZE = (960, 544)  # half of 1920x1088
INK = (0, 0, 0)
PAPER = (255, 255, 255)


def _figure(draw, cx, ground, scale=1.0, *, ink=INK, width=3):
    """A stick figure standing on `ground`: round head, dot eyes, three hair strokes, line body."""
    head = 34 * scale
    neck = ground - 200 * scale
    hips = ground - 90 * scale
    draw.ellipse((cx - head, neck - 2 * head, cx + head, neck), outline=ink, width=width)
    for dx in (-12, 12):
        draw.ellipse((cx + dx * scale - 3, neck - head - 6, cx + dx * scale + 3, neck - head), fill=ink)
    for dx in (-8, 0, 8):
        draw.arc((cx + dx * scale, neck - 2 * head - 18, cx + dx * scale + 18, neck - 2 * head + 4), 180, 300,
                 fill=ink, width=width)
    draw.line((cx, neck, cx, hips), fill=ink, width=width)
    draw.line((cx, neck + 30 * scale, cx - 55 * scale, neck + 90 * scale), fill=ink, width=width)
    draw.line((cx, neck + 30 * scale, cx + 55 * scale, neck + 70 * scale), fill=ink, width=width)
    draw.line((cx, hips, cx - 35 * scale, ground - 8), fill=ink, width=width)
    draw.line((cx, hips, cx + 35 * scale, ground - 8), fill=ink, width=width)
    for fx in (cx - 35 * scale, cx + 35 * scale):
        draw.ellipse((fx - 16 * scale, ground - 14, fx + 16 * scale, ground), outline=ink, width=width)


def _scene(background=PAPER, ink=INK, size=DRAWING_SIZE):
    """Two stick figures on a ground line under a small sun: the clean case."""
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    w, h = size
    ground = round(h * 0.85)
    scale = h / DRAWING_SIZE[1]
    draw.line((round(w * 0.05), ground, round(w * 0.95), ground), fill=ink, width=3)
    _figure(draw, round(w * 0.35), ground, scale, ink=ink)
    _figure(draw, round(w * 0.65), ground, scale, ink=ink)
    sun = (round(w * 0.85), round(h * 0.08))
    draw.ellipse((*sun, sun[0] + 50 * scale, sun[1] + 50 * scale), outline=ink, width=3)
    return image, draw


def _shoes_and_tie():
    image, draw = _scene()
    for cx in (336, 624):
        draw.polygon([(cx - 8, 330), (cx + 8, 330), (cx + 12, 380), (cx, 392), (cx - 12, 380)], fill=INK)
        for fx in (cx - 35, cx + 35):
            draw.ellipse((fx - 18, 448, fx + 18, 464), fill=INK)
    return image


def _with_text():
    image, draw = _scene()
    draw.text((80, 40), "THE FIRST SLEEP", fill=INK, font=ImageFont.load_default(size=48))
    return image


def _colour():
    image, draw = _scene()
    draw.ellipse((780, 30, 900, 150), fill=(230, 40, 30))
    return image


def _big_black_blob():
    image, draw = _scene()
    draw.rectangle((40, 40, 300, 260), fill=INK)
    return image


def _blurred():
    """Large dark shapes, blurred until no edge is left: what a safety filter's blur looks like."""
    image = Image.new("RGB", DRAWING_SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    draw.ellipse((200, 100, 460, 360), fill=(40, 40, 40))
    draw.rectangle((560, 150, 820, 470), fill=(40, 40, 40))
    return image.filter(ImageFilter.GaussianBlur(40))


def _almost_empty():
    image = Image.new("RGB", DRAWING_SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    draw.line((100, 460, 300, 460), fill=INK, width=2)
    draw.line((600, 200, 640, 180), fill=INK, width=2)
    return image


def _uniform(colour):
    return lambda: Image.new("RGB", DRAWING_SIZE, colour)


DRAWINGS = types.SimpleNamespace(
    clean=lambda size=DRAWING_SIZE: _scene(size=size)[0],
    shoes_and_tie=_shoes_and_tie,
    with_text=_with_text,
    colour=_colour,
    filled_background=lambda: _scene(background=(120, 120, 120))[0],
    dark_with_detail=lambda: _scene(background=(10, 10, 10), ink=(255, 255, 255))[0],
    big_black_blob=_big_black_blob,
    all_black=_uniform((0, 0, 0)),
    grey=_uniform((128, 128, 128)),
    white=_uniform((255, 255, 255)),
    cream=_uniform((250, 243, 224)),
    blurred=_blurred,
    almost_empty=_almost_empty,
)


@pytest.fixture
def drawings():
    """Fixture drawings for the pixel checks (spec §17), made in code so each case is exact."""
    return DRAWINGS


def to_jpeg(image, quality=90):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


@functools.lru_cache
def jpeg_bytes():
    """A clean stick-figure JPEG, like the base64 JPEG Klein returns (M0). It passes the pixel checks."""
    return to_jpeg(DRAWINGS.clean())


@functools.lru_cache
def filled_jpeg_bytes():
    """A grey-background JPEG: it fails the pixel checks as background_filled."""
    return to_jpeg(DRAWINGS.filled_background())


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


GOOD_REPORT = {"has_text": False, "text_seen": "", "style_ok": True, "anatomy_ok": True, "watermark_like": False,
               "character_count": 1, "matches_visual_idea": 4, "mascot_matches_sheet": None, "notes": ""}


def vision_reply(**changes):
    """A vision report the way qwen writes it: a blank line, then fenced JSON (M0)."""
    return "\n\n```json\n" + json.dumps({**GOOD_REPORT, **changes}) + "\n```"


def expected_figures(messages):
    text = next(part["text"] for part in messages[0]["content"] if part["type"] == "text")
    return int(re.search(r"Expected figures: (?:\d+-)?(\d+)", text).group(1))  # the most, for a range


def passing_vision(model, messages):
    return vision_reply(character_count=expected_figures(messages))


@pytest.fixture
def vision():
    return types.SimpleNamespace(reply=vision_reply, passing=passing_vision, figures=expected_figures)


class FakeImages:
    """Stands in for CloudflareClient.generate_image and chat() (and `async with`).

    `outcomes` is None (every call gets a small JPEG), a list (one outcome per call, in order), or a
    function `(call) -> outcome`. An outcome is image bytes, an exception to raise, or an awaitable
    that gives image bytes (a slow request).

    `chat` is a function `(model, messages) -> reply` for the vision model. A reply is text, an
    LLMResult, an exception to raise, or an awaitable giving one of those. The default passes every
    vision check, with the figure count the prompt expects.
    """

    def __init__(self, outcomes=None, *, neurons=207.59, chat=None):
        self._outcomes = list(outcomes) if isinstance(outcomes, (list, tuple)) else outcomes
        self._neurons = neurons
        self._chat = chat or passing_vision
        self.calls = []
        self.chat_calls = []
        self.active = 0
        self.max_active = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def generate_image(self, model, *, prompt, width, height, seed, steps=None, guidance=None, input_images=()):
        call = {"model": model, "prompt": prompt, "width": width, "height": height, "seed": seed,
                "steps": steps, "input_images": list(input_images)}
        self.calls.append(call)
        number = len(self.calls)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)  # like a network call: other requests can start meanwhile
            outcome = self._next(call)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        finally:
            self.active -= 1
        if isinstance(outcome, BaseException):
            raise outcome
        return ImageResult(image_bytes=outcome, neurons=self._neurons, request_id=f"req-{number}")

    async def chat(self, model, messages, *, temperature=0.4, max_tokens=4096, response_format=None):
        self.chat_calls.append({"model": model, "messages": messages, "temperature": temperature,
                                "max_tokens": max_tokens, "response_format": response_format})
        await asyncio.sleep(0)
        reply = self._chat(model, messages)
        if inspect.isawaitable(reply):
            reply = await reply
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, LLMResult):
            return reply
        return LLMResult(text=reply, input_tokens=1300, output_tokens=400,
                         raw={"choices": [{"finish_reason": "stop"}]}, neurons=110.0,
                         request_id=f"chat-{len(self.chat_calls)}")

    def _next(self, call):
        if self._outcomes is None:
            return jpeg_bytes()
        if callable(self._outcomes):
            return self._outcomes(call)
        if not self._outcomes:
            raise AssertionError("FakeImages: more calls than scripted outcomes")
        return self._outcomes.pop(0)


@pytest.fixture
def fake_images():
    return FakeImages


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


@pytest.fixture
def built_prompts(tmp_path_factory):
    """Plan data whose image prompts the builder made with no reference images, as `stickman new`
    writes them before bootstrap. Uses the packaged default style.yaml and mascot.yaml."""
    empty = tmp_path_factory.mktemp("defaults")
    style, mascot = load_style(empty), load_mascot(empty)

    def build(data):
        plan = parse_plan(data)
        table = cast_infos(plan.cast, mascot)
        units = {unit.id: unit for unit in plan.units()}
        for scene in data["scenes"]:
            for unit in scene["units"]:
                unit["image_prompt"] = build_prompt(units[unit["id"]], style=style, cast=table, references=None)
        return data

    return build


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
