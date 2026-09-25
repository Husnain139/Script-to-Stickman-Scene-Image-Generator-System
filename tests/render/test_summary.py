from datetime import datetime, timezone

from stickman.qc.decide import decide
from stickman.qc.pixel import PixelResult
from stickman.render.renderer import RunResult, StopReason
from stickman.render.state import ProjectState, UnitState, Version
from stickman.render.summary import format_duration, summary_lines


def state(**statuses):
    return ProjectState(units={unit: UnitState(status=status) for unit, status in statuses.items()})


def test_durations_read_like_the_spec():
    assert [format_duration(s) for s in (45.2, 372, 3720)] == ["45s", "6m12s", "1h02m"]


def test_a_finished_run():
    project = state(**{"001": "generated", "002a": "approved", "002b": "failed", "003": "stale", "004": "planned"})
    project.units["002b"].error = "bad_request: invalid prompt"
    lines = summary_lines(project, ["001", "002a", "002b", "003", "004"], result=RunResult(None, "", 372.0),
                          run_usd=0.74, possibly_billed=2, week_usd=6.12, weekly_usd=15.0)
    assert lines == [
        "Run finished · 5 units · done 2 · needs_review 0 · failed 1 · stale 1 · skipped 1",
        "Cost this run ≈ $0.74 (≈ 67,273 neurons, 2 possibly billed) · week ≈ $6.12 / $15.00 · time 6m12s",
        "Failed: 002b (bad_request: invalid prompt)",
        "Stale (not regenerated automatically): 003",
    ]


def test_a_paused_run_names_its_reason_and_small_costs_stay_readable():
    lines = summary_lines(state(**{"001": "generated", "002a": "planned"}), ["001", "002a"],
                          result=RunResult(StopReason.DAILY_LIMIT, "daily free allocation", 45.0),
                          run_usd=0.00228, possibly_billed=0, week_usd=0.00228, weekly_usd=15.0)
    assert lines == [
        "Run paused (daily limit) · 2 units · done 1 · needs_review 0 · failed 0 · stale 0 · skipped 1",
        "Cost this run ≈ $0.0023 (≈ 207 neurons) · week ≈ $0.0023 / $15.00 · time 45s",
    ]


def reviewed(reason):
    pixel = PixelResult(reason=reason, lum_std=0.0, lum_mean=0.0, ink_fraction=1.0, lap_var=0.0,
                        white_fraction=0.0, colour_fraction=0.0, black_fraction=1.0)
    qc = decide(pixel, None, expected_figures=1, min_idea_score=3)
    version = Version(v=1, file="images/_history/013a_v1.png", seed=1, model="m", width=8, height=8,
                      fingerprint="sha256:x", prompt_sent="p", qc=qc, est_cost_usd=0.0, latency_s=1.0,
                      created=datetime(2026, 9, 25, tzinfo=timezone.utc))
    return UnitState(status="needs_review", current_version=1, versions=[version])


def test_units_that_need_review_are_listed_with_their_reason_and_softened_units_are_named():
    project = state(**{"001": "generated"})
    project.units["013a"] = reviewed("background_filled")
    project.units["020"] = UnitState(status="needs_review", error="refused: flagged")
    lines = summary_lines(project, ["001", "013a", "020"], result=RunResult(None, "", 60.0), run_usd=0.01,
                          possibly_billed=0, week_usd=0.01, weekly_usd=15.0, softened=["020"])
    assert lines[0] == "Run finished · 3 units · done 1 · needs_review 2 · failed 0 · stale 0 · skipped 0"
    assert lines[2:] == [
        "Needs review: 013a (background_filled)   020 (safety_filtered)",
        "Softened after a safety filter (softened: true in plan.yaml): 020",
    ]
