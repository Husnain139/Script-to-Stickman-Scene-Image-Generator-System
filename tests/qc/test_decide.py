import pytest

from stickman.qc.decide import REASON_ORDER, QCResult, decide, figures_match
from stickman.qc.pixel import PixelResult
from stickman.qc.vision import VisionReport


def pixel(reason=None):
    return PixelResult(reason=reason, lum_std=30.0, lum_mean=250.0, ink_fraction=0.02, lap_var=900.0,
                       white_fraction=0.97, colour_fraction=0.0, black_fraction=0.01)


def report(**changes):
    data = dict(has_text=False, text_seen="", style_ok=True, anatomy_ok=True, watermark_like=False,
                character_count=1, matches_visual_idea=4, mascot_matches_sheet=None, notes="")
    return VisionReport(**{**data, **changes})


def check(vision=None, pixel_reason=None, *, expected=1, reference=False, error=None):
    return decide(pixel(pixel_reason), vision, expected_figures=expected, reference=reference, min_idea_score=3,
                  vision_error=error)


def test_a_clean_image_with_a_good_report_passes():
    result = check(report())
    assert (result.passed, result.reason, result.score) == (True, None, 4)


def test_the_reason_order_is_the_specs():
    assert REASON_ORDER == ("safety_filtered", "empty", "text", "background_filled", "style", "watermark",
                            "anatomy", "character_count", "mascot_mismatch", "weak_idea", "vision_error")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"has_text": True}, "text"),
        ({"style_ok": False}, "style"),
        ({"watermark_like": True}, "watermark"),
        ({"anatomy_ok": False}, "anatomy"),
        ({"character_count": 2}, "character_count"),
        ({"matches_visual_idea": 2}, "weak_idea"),
        ({"has_text": True, "style_ok": False, "matches_visual_idea": 1}, "text"),  # the first in REASON_ORDER
        ({"anatomy_ok": False, "character_count": 3}, "anatomy"),
    ],
)
def test_the_main_reason_is_the_first_failure_in_order(changes, reason):
    result = check(report(**changes))
    assert (result.passed, result.reason) == (False, reason)


def test_a_pixel_failure_outranks_the_vision_report():
    assert check(report(has_text=True), "background_filled").reason == "background_filled"


def test_a_pixel_failure_needs_no_vision_report():
    result = check(None, "safety_filtered")
    assert (result.passed, result.reason, result.vision, result.score) == (False, "safety_filtered", None, 0)


def test_a_vision_error_fails_without_a_report():
    result = check(None, error="invalid reply: the reply contains no JSON object")
    assert (result.passed, result.reason) == (False, "vision_error")


def test_without_a_vision_check_the_pixel_checks_decide():
    assert check(None).passed is True


@pytest.mark.parametrize(("expected", "seen", "ok"), [(0, 0, True), (1, 1, True), (3, 2, False), (3, 4, False),
                                                      (4, 5, True), (4, 3, True), (4, 6, False), (6, 5, True)])
def test_figures_match_exactly_up_to_three_and_within_one_above(expected, seen, ok):
    assert figures_match(expected, seen) is ok


def test_the_mascot_check_counts_only_when_a_reference_was_sent():
    assert check(report(mascot_matches_sheet=False)).passed is True
    assert check(report(mascot_matches_sheet=False), reference=True).reason == "mascot_mismatch"
    assert check(report(mascot_matches_sheet=None), reference=True).passed is True


def test_the_result_round_trips_as_json():
    result = check(report(has_text=True, text_seen="ZZZ"), expected=1)
    assert QCResult.model_validate_json(result.model_dump_json()) == result
    assert result.model_dump()["passed"] is False
