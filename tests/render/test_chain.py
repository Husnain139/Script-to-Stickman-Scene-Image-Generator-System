from datetime import datetime, timezone

import pytest

from stickman.qc.decide import QCResult
from stickman.qc.pixel import PixelResult
from stickman.qc.vision import VisionReport
from stickman.render.chain import Check, Finish, Render, best_version, chain_of, finish_step, next_step
from stickman.render.state import UnitState, Version

PIXEL = PixelResult(reason=None, lum_std=30.0, lum_mean=250.0, ink_fraction=0.02, lap_var=900.0,
                    white_fraction=0.97, colour_fraction=0.0, black_fraction=0.01)


def qc(reason=None, score=4, notes=""):
    vision = VisionReport(has_text=False, style_ok=True, anatomy_ok=True, watermark_like=False, character_count=1,
                          matches_visual_idea=score, notes=notes)
    return QCResult(pixel=PIXEL, vision=vision, expected_figures=1, passed=reason is None, reason=reason)


def version(v, *, retry_of=None, reason=None, result=None, fp="sha256:a"):
    return Version(v=v, file=f"images/_history/001_v{v}.png", seed=v, model="m", width=8, height=8, fingerprint=fp,
                   prompt_sent="p", retry_of=retry_of, retry_reason=reason, qc=result, est_cost_usd=0.0,
                   latency_s=1.0, created=datetime(2026, 9, 25, tzinfo=timezone.utc))


def step(chain, *, qc_max=2, locked=False, softened=False, refused=False):
    return next_step(chain, qc_max=qc_max, locked=locked, softened=softened, refused=refused)


def test_the_chain_runs_from_the_current_version_back_through_retry_of():
    unit = UnitState(current_version=3, versions=[
        version(1, result=qc("text")), version(2, retry_of=1, reason="text", result=qc("style")),
        version(3, retry_of=2, reason="style"), version(4, fp="sha256:old"),
    ])
    assert [v.v for v in chain_of(unit, "sha256:a")] == [1, 2, 3]


def test_no_chain_without_a_current_version_or_when_the_plan_changed():
    assert chain_of(UnitState(), "sha256:a") == []
    assert chain_of(UnitState(current_version=1, versions=[version(1)]), "sha256:new") == []


def test_a_broken_link_ends_the_chain():
    unit = UnitState(current_version=2, versions=[version(2, retry_of=1, reason="text")])
    assert [v.v for v in chain_of(unit, "sha256:a")] == [2]


def test_a_new_unit_is_rendered_and_an_unchecked_image_is_checked():
    assert step([]) == Render()
    assert step([version(1)]) == Check(1)


def test_a_passing_image_finishes_the_unit():
    assert step([version(1, result=qc())]) == Finish("generated", 1)


def test_a_failure_with_retries_left_is_retried_with_every_reason_so_far():
    chain = [version(1, result=qc("text")), version(2, retry_of=1, reason="text", result=qc("style"))]
    assert step(chain[:1]) == Render(retry_of=1, reason="text", reasons=("text",))
    assert step(chain, qc_max=3) == Render(retry_of=2, reason="style", reasons=("text", "style"))


def test_after_qc_max_retries_the_best_version_is_kept_for_review():
    chain = [version(1, result=qc("text", 3)), version(2, retry_of=1, reason="text", result=qc("text", 5)),
             version(3, retry_of=2, reason="text", result=qc("text", 4))]
    assert step(chain) == Finish("needs_review", 2)
    assert step(chain[:1], qc_max=0) == Finish("needs_review", 1)


def test_a_safety_filtered_image_is_softened_once():
    black = [version(1, result=qc("safety_filtered"))]
    assert step(black) == Render(retry_of=1, reason="safety_filtered", reasons=("safety_filtered",), rewrite="soften")
    assert step(black, softened=True) == Finish("needs_review", 1)
    assert step(black, locked=True) == Finish("needs_review", 1)


def test_the_second_weak_idea_is_redesigned_with_the_checkers_notes():
    first = version(1, result=qc("weak_idea", 2, "the fire is missing"))
    assert step([first]) == Render(retry_of=1, reason="weak_idea", reasons=("weak_idea",), notes="the fire is missing")
    chain = [first, version(2, retry_of=1, reason="weak_idea", result=qc("weak_idea", 2, "still no fire"))]
    assert step(chain, qc_max=3) == Render(retry_of=2, reason="weak_idea", reasons=("weak_idea", "weak_idea"),
                                           rewrite="redesign", notes="still no fire")
    assert step(chain, qc_max=3, locked=True) == Finish("needs_review", 2)


def test_a_vision_error_is_not_retried():
    error = QCResult(pixel=PIXEL, vision_error="invalid reply", expected_figures=1, passed=False, reason="vision_error")
    assert step([version(1, result=error)]) == Finish("needs_review", 1)


def test_a_refusal_is_softened_and_a_second_one_is_left_for_review():
    assert step([], refused=True) == Render(reason="safety_filtered", reasons=("safety_filtered",), rewrite="soften")
    assert step([], refused=True, softened=True) == Finish("needs_review", None)
    assert step([], refused=True, locked=True) == Finish("needs_review", None)
    chain = [version(1, result=qc("text"))]
    assert step(chain, refused=True) == Render(retry_of=1, reason="safety_filtered",
                                               reasons=("safety_filtered",), rewrite="soften")


def test_the_best_version_passes_first_then_scores_highest_then_is_latest():
    passing = version(1, result=qc(None, 3))
    better = version(2, retry_of=1, reason="text", result=qc("text", 5))
    assert best_version([passing, better]).v == 1
    tie = [version(1, result=qc("text", 4)), version(2, retry_of=1, reason="text", result=qc("text", 4))]
    assert best_version(tie).v == 2


def test_only_versions_of_the_latest_fields_can_become_current():
    old = version(1, result=qc("safety_filtered", 1), fp="sha256:before-soften")
    new = version(2, retry_of=1, reason="safety_filtered", result=qc("text", 2), fp="sha256:after-soften")
    assert best_version([old, new]).v == 2


def test_finishing_with_nothing_made_leaves_the_unit_for_review():
    assert finish_step([]) == Finish("needs_review", None)
