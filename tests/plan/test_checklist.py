import asyncio
from pathlib import Path

import pytest

from stickman.ingest.parse import parse_script
from stickman.ingest.timing import build_timeline
from stickman.plan.checklist import evaluate, render_markdown
from stickman.plan.models import CharacterRef, Correction
from stickman.plan.planner import load_planning_context, plan_script
from stickman.settings import Settings, TimingSettings

SAMPLE = Path(__file__).parents[1] / "fixtures" / "scripts" / "first-sleep.txt"
LINES = build_timeline(parse_script(SAMPLE.read_text(encoding="utf-8")), TimingSettings()).lines
MASCOT_UNITS = ["012", "026a", "026b", "027", "028a", "028b"]  # 0:58, 1:57, 2:04, 2:06


@pytest.fixture
def plan(sample_chat, stage_runner, tmp_path):
    """The sample planned by FakeChat: every correction found, but no mascot anywhere."""
    ctx = load_planning_context(tmp_path, Settings())
    text = SAMPLE.read_text(encoding="utf-8")
    outcome = asyncio.run(plan_script(stage_runner(sample_chat), ctx, script_text=text, project="first-sleep", aspect="16:9", duration=None))
    return outcome.plan


def test_expected_corrections_merges_and_extras(plan):
    report = evaluate(plan, LINES)
    assert report.corrections_found == 5
    assert report.other_corrections == []
    assert report.lines_13_14_merged is True
    assert report.wrongly_merged == []
    assert report.caveman_entries == ["caveman_group"]
    assert len(report.caveman_units) == 36


def test_the_mascot_rule_is_scored_per_unit(plan):
    report = evaluate(plan, LINES)
    assert report.mascot_checked == 36
    assert report.mascot_misses == MASCOT_UNITS
    assert report.meets_target is False  # 30 of 36 is 83%, below 90%
    for unit in plan.units():
        if unit.id in MASCOT_UNITS:
            unit.characters.append(CharacterRef(ref="mascot", action="lying awake", emotion="worried"))
    fixed = evaluate(plan, LINES)
    assert fixed.mascot_score == 1.0
    assert fixed.meets_target is True


def test_missing_wrong_and_extra_corrections_are_reported(plan):
    plan.corrections = [c for c in plan.corrections if c.from_ != "Zhuansi"]
    plan.corrections[0] = plan.corrections[0].model_copy(update={"to": "nine at night"})
    plan.corrections.append(Correction(scene="002", from_="light switch", to="lamp", reason="style"))
    report = evaluate(plan, LINES)
    assert report.corrections_found == 3
    by_source = {e.source: e for e in report.expected}
    assert by_source["Zhuansi"].found is None
    assert by_source["90 at night"].found is not None and by_source["90 at night"].exact is False
    assert [c.from_ for c in report.other_corrections] == ["light switch"]


def test_the_report_lists_results_and_tuning_tasks(plan):
    text = render_markdown(evaluate(plan, LINES), model="@cf/openai/gpt-oss-120b", neurons=1234.0)
    assert text.startswith("# M2 planning checklist (spec §17)\n")
    assert "1234 neurons" in text
    assert "**Target met: no.**" in text
    assert "## Expected corrections: 5 of 5" in text
    assert "## Mascot rule: 83% of 36 units" in text
    assert "- Units breaking it: 012, 026a, 026b, 027, 028a, 028b" in text
    assert "- Mascot rule: the mascot is wrong in 6 units (012, 026a, 026b, 027, 028a, 028b)." in text


def test_curly_apostrophes_still_match(plan):
    plan.corrections = [
        c.model_copy(update={"to": "Ju'hoansi".replace("Ju'", "Ju/'")}) if c.from_ == "Zhuansi" else c
        for c in plan.corrections
    ]
    report = evaluate(plan, LINES)
    assert next(e for e in report.expected if e.source == "Zhuansi").exact is True
