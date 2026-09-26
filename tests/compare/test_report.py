from datetime import datetime, timedelta, timezone

import pytest

from stickman.compare.report import collect, percentile, report_lines, suggestion, write_report
from stickman.compare.setup import ComparePick, CompareSetup, default_runs
from stickman.plan.models import parse_plan
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.qc.vision import VisionReport
from stickman.render.state import StateStore, Version
from stickman.settings import QCSettings, Settings

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
SETUP = CompareSetup(
    source="2026-09-25_demo", created=datetime(2026, 9, 26, 9, 0, tzinfo=PK),
    picks=[ComparePick(category="mascot", unit="001", seed=1),
           ComparePick(category="extras", unit="002a", filled=True, seed=2),
           ComparePick(category="night_or_fire", unit="002b", filled=True, seed=3)],
    runs=default_runs(Settings(), "16:9"),
)


def qc_of(drawings, *, text=False, count=1):
    report = VisionReport(has_text=text, style_ok=True, anatomy_ok=True, watermark_like=False,
                          character_count=count, matches_visual_idea=4)
    return decide(pixel_check(drawings.clean(), QCSettings()), report, expected_figures=1, min_idea_score=3)


def fill(folder, run_id, entries):
    """entries: unit -> (seconds, qc result)."""
    run_dir = folder / "runs" / run_id
    run_dir.mkdir(parents=True)
    store = StateStore.load(run_dir)
    for unit, (seconds, qc) in entries.items():
        version = Version(v=1, file=f"images/_history/{unit}_v1.png", seed=1, model=KLEIN_4B, width=1920,
                          height=1088, fingerprint="sha256:x", prompt_sent="p", qc=qc, est_cost_usd=0.0023,
                          latency_s=seconds, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
        store.add_version(unit, version, status="generated" if qc.passed else "needs_review")


def test_percentile_is_the_nearest_rank():
    assert percentile([float(n) for n in range(1, 11)], 0.9) == 9.0
    assert percentile([5.0], 0.9) == 5.0
    assert percentile([], 0.9) is None


def test_collect_counts_passes_text_failures_times_and_cost(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {
        "001": (10.0, qc_of(drawings)), "002a": (20.0, qc_of(drawings)), "002b": (30.0, qc_of(drawings, text=True)),
    })
    stats, cells = collect(tmp_path, SETUP)
    refs, no_refs, small = stats
    assert (refs.images, refs.checked, refs.passed, refs.vision_checked, refs.text_failures) == (3, 3, 2, 3, 1)
    assert refs.pass_rate == pytest.approx(2 / 3) and refs.text_rate == pytest.approx(1 / 3)
    assert (refs.median_s, refs.p90_s) == (20.0, 30.0)
    assert refs.usd_per_image == pytest.approx(0.0023)
    assert (no_refs.images, no_refs.pass_rate, no_refs.median_s) == (0, None, None)
    assert cells[("klein-4b-refs", "001")].image == "../runs/klein-4b-refs/images/_history/001_v1.png"
    assert cells[("klein-4b-no-refs", "001")].status == "planned"
    assert cells[("klein-4b-no-refs", "001")].image is None


def test_character_count_failures_are_counted(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {"001": (10.0, qc_of(drawings, count=3))})
    [refs, *_] = collect(tmp_path, SETUP)[0]
    assert refs.count_failures == 1


def test_a_count_failure_is_counted_even_when_text_is_the_reason_shown(tmp_path, drawings):
    both = qc_of(drawings, text=True, count=3)
    assert both.reason == "text"
    fill(tmp_path, "klein-4b-refs", {"001": (10.0, both), "002a": (10.0, qc_of(drawings, text=True))})
    [refs, *_] = collect(tmp_path, SETUP)[0]
    assert (refs.count_failures, refs.text_failures) == (1, 2)


def version_of(unit, v, qc, seconds):
    return Version(v=v, file=f"images/_history/{unit}_v{v}.png", seed=1, model=KLEIN_4B, width=1920, height=1088,
                   fingerprint=f"sha256:{v}", prompt_sent="p", qc=qc, est_cost_usd=0.0023 * v, latency_s=seconds,
                   created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))


def test_only_each_units_shown_version_counts_and_a_refusal_is_a_first_try_failure(tmp_path, drawings, plan_data):
    run_dir = tmp_path / "runs" / "klein-4b-refs"
    run_dir.mkdir(parents=True)
    store = StateStore.load(run_dir)
    store.add_version("001", version_of("001", 1, qc_of(drawings, text=True), 50.0), status="generating")
    store.add_version("001", version_of("001", 2, qc_of(drawings), 10.0), status="generating")
    store.finish("001", "generated", current=2)  # v1 is an older image, never counted
    store.add_version("002a", version_of("002a", 1, qc_of(drawings, count=3), 20.0), status="needs_review")
    store.finish("002a", "needs_review", current=None, clear_current=True)  # no current version: the latest shows
    store.set_status("002b", "needs_review", error="refused: flagged by the safety system ***")
    stats, cells = collect(tmp_path, SETUP)
    refs = stats[0]
    assert (refs.images, refs.checked, refs.passed, refs.refusals, refs.count_failures) == (2, 2, 1, 1, 1)
    assert refs.pass_rate == pytest.approx(1 / 3)  # the refusal counts as a failed first try
    assert refs.text_rate == 0.0
    assert (refs.median_s, refs.usd_per_image) == (15.0, pytest.approx(0.0023 * 1.5))
    assert cells[("klein-4b-refs", "001")].version.v == 2
    text = "\n".join(report_lines(stats))
    assert "refused" in text.splitlines()[0] and "33%" in text
    page = write_report(tmp_path, SETUP, parse_plan(plan_data), stats, cells,
                        now=datetime(2026, 9, 26, 12, 0, tzinfo=PK)).read_text(encoding="utf-8")
    assert "<th>Refusals</th>" in page


def test_the_lines_show_each_run_and_suggest_the_timeout(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {
        "001": (10.0, qc_of(drawings)), "002a": (20.0, qc_of(drawings)), "002b": (30.0, qc_of(drawings, text=True)),
    })
    stats, _ = collect(tmp_path, SETUP)
    assert suggestion(stats) == ("klein-4b-refs", 90, 20)
    text = "\n".join(report_lines(stats))
    assert "klein-4b-refs" in text and "67%" in text and "33%" in text
    assert "klein-4b-small-refs" in text
    assert "render.timeout_s: 90, render.est_seconds_per_image: 20" in text


def test_the_timeout_suggestion_is_at_least_30_seconds(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {"001": (3.0, qc_of(drawings))})
    assert suggestion(collect(tmp_path, SETUP)[0]) == ("klein-4b-refs", 30, 3)


def test_no_suggestion_without_any_image(tmp_path):
    assert suggestion(collect(tmp_path, SETUP)[0]) is None


def test_the_html_report_shows_every_image_and_escapes_text(tmp_path, drawings, plan_data):
    plan_data["scenes"][0]["units"][0]["visual_idea"] = "A <b>bold</b> idea"
    fill(tmp_path, "klein-4b-refs", {"001": (10.0, qc_of(drawings))})
    stats, cells = collect(tmp_path, SETUP)
    path = write_report(tmp_path, SETUP, parse_plan(plan_data), stats, cells, now=datetime(2026, 9, 26, 12, 0, tzinfo=PK))
    assert path == tmp_path / "export" / "compare.html"
    page = path.read_text(encoding="utf-8")
    assert '<img src="../runs/klein-4b-refs/images/_history/001_v1.png"' in page
    assert "A &lt;b&gt;bold&lt;/b&gt; idea" in page and "<b>bold</b>" not in page
    assert "No-text failure rate" in page and "QC pass rate" in page
    assert "(stand-in)" in page and "not made yet" in page
    assert "http" not in page  # no external resources
