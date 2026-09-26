import base64
import io
import json

import pytest
from PIL import Image

from stickman.plan.llm import CUT_OFF_ERROR
from stickman.qc.vision import (
    VISION_MAX_SIDE,
    ExpectedPicture,
    VisionReport,
    parse_vision,
    retry_messages,
    vision_messages,
    vision_png,
    vision_prompt,
)

EXPECTED = ExpectedPicture("Three cavemen sit around a campfire at night.", 4, "Everyman: 1, Caveman group: 3")
GOOD = {"has_text": False, "text_seen": "", "style_ok": True, "anatomy_ok": True, "watermark_like": False,
        "character_count": 4, "matches_visual_idea": 4, "mascot_matches_sheet": None, "notes": ""}


def decoded(part):
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def test_the_prompt_is_the_spec_prompt_with_the_units_expectations():
    prompt = vision_prompt(EXPECTED, reference=False)
    assert prompt.startswith(
        "You are a strict quality checker for black-and-white stickman illustrations. Look at the image and "
        "return ONLY JSON matching the schema. Expected: Three cavemen sit around a campfire at night.\n"
        "Expected figures: 4 (Everyman: 1, Caveman group: 3).\n"
    )
    for key in ("has_text", "text_seen", "style_ok", "anatomy_ok", "watermark_like", "character_count",
                "matches_visual_idea", "mascot_matches_sheet", "notes"):
        assert f"- {key}:" in prompt
    assert "Judge head and hair only." in prompt
    schema = json.loads(prompt.split("SCHEMA:\n", 1)[1])
    assert set(schema["properties"]) == set(GOOD)


def test_a_range_of_figures_is_written_min_hyphen_max():
    expected = ExpectedPicture("Cavemen by a fire.", 4, "Everyman: 1, Caveman group: 1-3", 2)
    assert "\nExpected figures: 2-4 (Everyman: 1, Caveman group: 1-3).\n" in vision_prompt(expected, reference=False)
    same = ExpectedPicture("A man.", 1, "Everyman: 1", 1)
    assert "\nExpected figures: 1 (Everyman: 1).\n" in vision_prompt(same, reference=False)


def test_with_a_reference_the_prompt_names_the_second_image():
    assert "Look at the image and the reference character (the second image) and return" in vision_prompt(
        EXPECTED, reference=True
    )


def test_the_image_is_sent_as_a_png_at_most_1024_px_and_the_reference_after_it():
    image_png = vision_png(Image.new("RGB", (1920, 1088), "white"))
    assert Image.open(io.BytesIO(image_png)).size == (VISION_MAX_SIDE, 580)
    [message] = vision_messages("the prompt", image_png, b"reference-bytes-are-sent-as-they-are")
    assert message["role"] == "user"
    text, image, reference = message["content"]
    assert text == {"type": "text", "text": "the prompt"}
    assert decoded(image).size == (1024, 580)
    assert base64.b64decode(reference["image_url"]["url"].split(",", 1)[1]) == b"reference-bytes-are-sent-as-they-are"
    assert len(vision_messages("the prompt", image_png)[0]["content"]) == 2


def test_colour_survives_and_a_small_image_is_not_enlarged(drawings):
    image = Image.open(io.BytesIO(vision_png(drawings.colour())))
    assert (image.mode, image.size) == ("RGB", (960, 544))
    red, green, _ = image.getpixel((840, 90))  # inside the red sun
    assert red > 200 and green < 100


def test_a_fenced_reply_with_a_leading_blank_line_is_read():
    report, errors = parse_vision("\n\n```json\n" + json.dumps(GOOD) + "\n```", finish_reason="stop")
    assert errors == [] and report == VisionReport(**GOOD)


@pytest.mark.parametrize(
    ("text", "finish", "error"),
    [
        ("I can't tell.", "stop", "the reply contains no JSON object"),
        ("thinking about the image...", "length", CUT_OFF_ERROR),
        (json.dumps({**GOOD, "matches_visual_idea": 9}), "stop", "matches_visual_idea: Input should be less than or equal to 5"),
    ],
)
def test_an_unusable_reply_gives_its_errors(text, finish, error):
    report, errors = parse_vision(text, finish_reason=finish)
    assert report is None and errors == [error]


def test_nulls_for_the_text_fields_are_accepted():
    report, _ = parse_vision(json.dumps({**GOOD, "text_seen": None, "notes": None}), finish_reason="stop")
    assert report is not None and report.notes is None


def test_an_extra_key_in_the_reply_is_ignored():
    reply = "\n\n```json\n" + json.dumps({
        "has_text": False, "text_seen": "", "style_ok": True, "anatomy_ok": True, "watermark_like": False,
        "character_count": 1, "matches_visual_idea": 4, "mascot_matches_sheet": None, "notes": "",
        "confidence": 0.9,
    }) + "\n```"
    report, errors = parse_vision(reply, finish_reason="stop")
    assert errors == [] and report is not None
    assert "confidence" not in report.model_dump()


def test_a_retry_shows_the_model_its_reply_and_the_errors():
    first = vision_messages("the prompt", b"png")
    again = retry_messages(first, "not json", ["the reply contains no JSON object"])
    assert again[0] == first[0]
    assert again[1] == {"role": "assistant", "content": "not json"}
    assert again[2]["role"] == "user" and "- the reply contains no JSON object" in again[2]["content"]
