import asyncio
import json
from pathlib import Path

import pytest

from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import load_style
from stickman.plan.planner import load_planning_context, plan_script, replan_unit
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.settings import Settings

SAMPLE = Path(__file__).parents[1] / "fixtures" / "scripts" / "first-sleep.txt"
STAGES = (("You plan", "analyse"), ("Each scene", "cut"), ("You design", "describe"))


def stage_of(call):
    system = call["messages"][0]["content"]
    return next(name for prefix, name in STAGES if system.startswith(prefix))


def plan_sample(runner, workspace, **settings):
    ctx = load_planning_context(workspace, Settings(**settings))
    text = SAMPLE.read_text(encoding="utf-8")
    return asyncio.run(plan_script(runner, ctx, script_text=text, project="first-sleep", aspect="16:9", duration=None))


@pytest.fixture
def outcome(sample_chat, stage_runner, tmp_path):
    return plan_sample(stage_runner(sample_chat, cache_dir=tmp_path / "cache"), tmp_path)


def test_the_sample_plan_matches_the_golden_split(outcome):
    plan = outcome.plan
    assert (len(plan.scenes), len(plan.units())) == (28, 36)
    assert (plan.duration_end, plan.pace_wps) == (133.852, 2.6746)
    s13 = plan.scenes[12]
    assert (s13.id, s13.lines, s13.split.status, s13.units[0].end) == ("013", [13, 14], "split", 66.5)
    assert (plan.scenes[4].split.status, plan.scenes[4].split.candidates) == ("no_valid_cut", [6, 15])
    assert [u.id for u in plan.scenes[5].units] == ["006a", "006b"]


def test_corrections_are_stored_by_scene(outcome):
    plan = outcome.plan
    assert [(c.scene, c.from_) for c in plan.corrections] == [
        ("001", "90 at night"), ("006", "Zhuansi"), ("013", "Roger E. Kirch"), ("014", "2 sleep"), ("023", "Thomas Ware"),
    ]
    assert "Roger Ekirch" in plan.scenes[12].corrected_text
    assert plan.scenes[0].units[0].corrected_text == "It's 9 at night, 40,000 years ago."


def test_every_unit_gets_a_prompt_with_the_style_and_strict_clause(outcome, tmp_path):
    style = load_style(tmp_path)
    for unit in outcome.plan.units():
        assert unit.image_prompt.startswith(style.style_text.strip())
        assert unit.image_prompt.endswith(style.strict_clause.strip())
        assert "Reference images" not in unit.image_prompt  # no style anchor exists yet
        assert unit.prompt_locked is False


def test_cast_merge_check_and_warnings(outcome):
    assert [member.id for member in outcome.plan.cast] == ["mascot", "caveman_group", "historian"]
    assert outcome.plan.merge_check == []
    assert outcome.warnings == []


def test_stages_run_in_order_with_describe_batches(sample_chat, stage_runner, tmp_path):
    plan_sample(stage_runner(sample_chat), tmp_path)
    assert [stage_of(call) for call in sample_chat.calls] == ["analyse", "cut"] + ["describe"] * 5  # 36 units / 8


def test_a_second_run_is_served_from_the_cache(sample_chat, stage_runner, tmp_path, fake_chat):
    first = plan_sample(stage_runner(sample_chat, cache_dir=tmp_path / "cache"), tmp_path)
    offline = fake_chat([])
    second = plan_sample(stage_runner(offline, cache_dir=tmp_path / "cache"), tmp_path)
    assert second.plan == first.plan
    assert offline.calls == []


def test_after_a_daily_limit_the_next_run_continues_from_the_cache(stage_runner, tmp_path, fake_chat, sample_reply):
    def limited(model, messages):
        if messages[0]["content"].startswith("You design one illustration"):
            raise CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation")
        return sample_reply(model, messages)

    with pytest.raises(CFError):
        plan_sample(stage_runner(fake_chat(limited), cache_dir=tmp_path / "cache"), tmp_path)
    resumed = fake_chat(sample_reply)
    plan_sample(stage_runner(resumed, cache_dir=tmp_path / "cache"), tmp_path)
    assert [stage_of(call) for call in resumed.calls] == ["describe"] * 5


def test_the_plan_writes_and_loads_back(outcome, tmp_path):
    path = tmp_path / "plan.yaml"
    write_plan(path, to_document(outcome.plan), expected_hash=None)
    assert load_plan(path).plan == outcome.plan


def test_short_scene_merging_carries_corrections(sample_chat, stage_runner, tmp_path):
    plan = plan_sample(stage_runner(sample_chat), tmp_path, merge={"min_scene_seconds": 2.0}).plan
    assert [17, 18, 19] in [scene.lines for scene in plan.scenes]
    ware = next(c for c in plan.corrections if c.from_ == "Thomas Ware")
    scene = next(s for s in plan.scenes if s.id == ware.scene)
    assert 24 in scene.lines
    assert "Thomas Wehr" in scene.corrected_text


REPLANNED = {
    "id": "006a", "corrected_text": "Anthropologists studying the Ju/'hoansi in the Kalahari",
    "visual_idea": "An anthropologist sketches in a notebook", "visual_type": "literal", "shot": "close-up",
    "time_of_day": "day", "characters": [{"ref": "historian", "action": "sketching", "emotion": "curious"}],
    "mood": None, "setting": [], "props": ["notebook with blank pages"],
    "composition": "notebook large in the foreground", "energy_marks": [], "softened": False, "softened_reason": None,
}


def test_replan_redesigns_one_unit_and_keeps_its_timing(outcome, tmp_path, fake_chat, stage_runner):
    chat = fake_chat([json.dumps({"units": [REPLANNED]})])
    ctx = load_planning_context(tmp_path, Settings())
    unit = asyncio.run(replan_unit(stage_runner(chat, cache_dir=tmp_path / "cache"), ctx, outcome.plan, "006a", hint="show the notebook"))
    assert unit.visual_idea == "An anthropologist sketches in a notebook"
    assert (unit.start, unit.end, unit.part) == (21.0, 24.294, "1 of 2")
    assert unit.corrected_text == "Anthropologists studying the Ju/'hoansi in the Kalahari"
    assert unit.prompt_locked is False
    assert "Scene: An anthropologist sketches in a notebook." in unit.image_prompt
    user = chat.calls[0]["messages"][1]["content"]
    assert user.splitlines()[-1] == "HINT: show the notebook"
    assert '"id": "005"' in user and '"id": "006b"' in user  # PREVIOUS and NEXT context
