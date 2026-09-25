"""The vision check (spec §9.4, §11.2): what is asked, what is sent, and reading the reply."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stickman.plan.jsonx import NoJSONError, error_path, extract_json, inline_schema
from stickman.plan.llm import CUT_OFF_ERROR, RETRY_MESSAGE

VISION_MAX_SIDE = 1024  # px on the long side: small text stays readable
VISION_MAX_TOKENS = 2048  # qwen also writes a reasoning field; only used tokens are billed
VISION_ATTEMPTS = 2  # validation attempts; the second shows the model its errors
IMAGE_TOKENS = 800  # per image, for the pre-call estimate (M0: two 768 px images + a question = 828 prompt tokens)
TYPICAL_TOKENS = (1400, 500)  # input and output tokens of a typical check, for the free-plan message (Task 11 measures it)


class VisionReport(BaseModel):
    """The vision model's answer (spec §11.2 schema)."""

    model_config = ConfigDict(extra="forbid")

    has_text: bool
    text_seen: str | None = None
    style_ok: bool
    anatomy_ok: bool
    watermark_like: bool
    character_count: int = Field(ge=0)
    matches_visual_idea: int = Field(ge=1, le=5)
    mascot_matches_sheet: bool | None = None
    notes: str | None = None


@dataclass(frozen=True)
class ExpectedPicture:
    """What the unit's picture should show, for the prompt."""

    visual_idea: str
    figures: int  # the stick figures expected in total
    cast: str  # "Everyman: 1, Caveman group: 3", or "none"


_PROMPT = """You are a strict quality checker for black-and-white stickman illustrations. Look at the image{reference} and return ONLY JSON matching the schema. Expected: {visual_idea}.
Expected figures: {figures} ({cast}).
- has_text: true if ANY letters, numbers, words, symbols like "Zzz", or pseudo-text squiggles appear.
- text_seen: the text you see, or "".
- style_ok: false if there is colour, grey shading, gradients, a filled background, or a realistic/3D look.
- anatomy_ok: false if any figure has extra/missing heads or limbs, or merged bodies.
- watermark_like: true if there is any faint overlaid pattern, logo or watermark-like mark.
- character_count: number of stick figures you see.
- matches_visual_idea: 1–5, how clearly the image shows the expected idea.
- mascot_matches_sheet: (only when a reference is given) true if the main character has the same
  HEAD and HAIR as the reference: round head, exactly three short hair strokes curling right.
  Judge head and hair only. Ignore clothing: the necktie and shoes may be hidden (blanket,
  close-up) or absent, and that is NOT a mismatch. Otherwise null.
- notes: one short sentence on the most important problem, or "".

SCHEMA:
{schema}"""


def vision_prompt(expected: ExpectedPicture, *, reference: bool) -> str:
    return _PROMPT.format(
        reference=" and the reference character (the second image)" if reference else "",
        visual_idea=expected.visual_idea.strip().rstrip("."),
        figures=expected.figures,
        cast=expected.cast,
        schema=json.dumps(inline_schema(VisionReport), ensure_ascii=False),
    )


def vision_png(image: Image.Image) -> bytes:
    """The image as sent: RGB (colour must stay visible), at most VISION_MAX_SIDE px on its long side."""
    copy = image.convert("RGB")
    copy.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _image_part(png: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}


def vision_messages(prompt: str, image_png: bytes, reference_png: bytes | None = None) -> list[dict[str, Any]]:
    """One user message: the prompt, the image, then the reference (spec §9.4: 2 images per call)."""
    content = [{"type": "text", "text": prompt}, _image_part(image_png)]
    if reference_png is not None:
        content.append(_image_part(reference_png))
    return [{"role": "user", "content": content}]


def retry_messages(messages: Sequence[dict[str, Any]], reply_text: str, errors: Sequence[str]) -> list[dict[str, Any]]:
    feedback = RETRY_MESSAGE.format(errors="\n".join(f"- {error}" for error in errors))
    return [*messages, {"role": "assistant", "content": reply_text}, {"role": "user", "content": feedback}]


def parse_vision(text: str, *, finish_reason: str | None) -> tuple[VisionReport | None, list[str]]:
    """The report, or the errors to show the model on its second attempt. The JSON is taken
    leniently, since qwen adds a blank line or a ```json fence (spec §9.4)."""
    try:
        data = extract_json(text)
    except NoJSONError as exc:
        return None, [CUT_OFF_ERROR] if finish_reason == "length" else [str(exc)]
    try:
        return VisionReport.model_validate(data), []
    except ValidationError as exc:
        return None, [f"{error_path(err['loc'])}: {err['msg']}" for err in exc.errors()]
