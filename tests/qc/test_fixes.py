import pytest

from stickman.config_files import TEXT_FREE
from stickman.qc.fixes import (
    ANATOMY_FIX,
    BACKGROUND_FIX,
    MASCOT_FIX,
    MASCOT_FIX_NO_REF,
    STYLE_FIX,
    FixContext,
    retry_prompt,
    rewrite_props,
)
from stickman.qc.vision import ExpectedPicture

STRICT = "Absolutely no text of any kind anywhere in the image."
BASE = "Style text.\n\nScene: A man checks the time.\nProps: small wall clock, open notebook.\n\n" + STRICT


def fix(**changes):
    data = dict(strict_clause=STRICT, props=("small wall clock", "open notebook"), text_free=TEXT_FREE,
                expected=ExpectedPicture("A man checks the time.", 4, "Everyman: 1, Caveman group: 3"),
                identity="round head, three hair strokes curling right", mascot_in_image_1=False, locked=False)
    return FixContext(**{**data, **changes})


def test_text_puts_the_strict_clause_first_and_rewrites_text_attracting_props():
    prompt = retry_prompt(BASE, ["text"], fix())
    assert prompt.startswith(STRICT + "\n\nStyle text.")
    assert prompt.endswith(STRICT)
    assert "Props: small wall clock (a round face with two hands and no numerals), open notebook (blank pages or a few wavy lines)." in prompt


def test_a_locked_unit_gets_the_fixed_clause_but_no_props_rewrite():
    prompt = retry_prompt(BASE, ["text"], fix(locked=True))
    assert prompt.startswith(STRICT + "\n\n")
    assert "Props: small wall clock, open notebook." in prompt


@pytest.mark.parametrize(
    ("reason", "added"),
    [
        ("background_filled", BACKGROUND_FIX),
        ("style", STYLE_FIX),
        ("anatomy", ANATOMY_FIX),
        ("character_count", "Exactly 4 stick figures in total: Everyman: 1, Caveman group: 3."),
        ("mascot_mismatch", MASCOT_FIX_NO_REF.format(identity="round head, three hair strokes curling right")),
    ],
)
def test_each_reason_adds_its_sentence_at_the_end(reason, added):
    assert retry_prompt(BASE, [reason], fix()) == BASE + "\n\n" + added


def test_the_mascot_fix_points_at_image_1_when_the_mascot_is_there():
    assert retry_prompt(BASE, ["mascot_mismatch"], fix(mascot_in_image_1=True)).endswith(
        MASCOT_FIX.format(identity="round head, three hair strokes curling right"))


def test_no_expected_figures_asks_for_none():
    prompt = retry_prompt(BASE, ["character_count"], fix(expected=ExpectedPicture("A clock.", 0, "none")))
    assert prompt.endswith("No stick figures at all.")


@pytest.mark.parametrize("reason", ["empty", "weak_idea", "safety_filtered"])
def test_reasons_fixed_by_a_new_seed_or_a_rewrite_leave_the_prompt_alone(reason):
    assert retry_prompt(BASE, [reason], fix()) == BASE


def test_watermark_puts_the_strict_clause_first_without_touching_props():
    prompt = retry_prompt(BASE, ["watermark"], fix())
    assert prompt == STRICT + "\n\n" + BASE


def test_fixes_add_up_across_a_chain_each_once_in_first_seen_order():
    prompt = retry_prompt(BASE, ["style", "anatomy", "style"], fix())
    assert prompt.endswith(STYLE_FIX + " " + ANATOMY_FIX)
    assert prompt.count(STYLE_FIX) == 1
    both = retry_prompt(BASE, ["text", "watermark"], fix())
    assert both.count(STRICT) == 2  # once at the start, once where the builder put it


def test_props_are_matched_as_whole_words_with_plurals():
    assert rewrite_props(["two clocks", "a clockwork toy", "a sign post"], TEXT_FREE) == [
        "two clocks (a round face with two hands and no numerals)",
        "a clockwork toy",
        "a sign post (a blank board with simple shapes, no writing)",
    ]


def test_a_prop_that_already_has_the_wording_is_left_alone():
    already = "clock (a round face with two hands and no numerals)"
    assert rewrite_props([already], TEXT_FREE) == [already]
