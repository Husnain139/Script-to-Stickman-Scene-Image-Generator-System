import asyncio
import json

import pytest

from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.models import parse_plan
from stickman.plan.planner import load_planning_context
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.render.rewrite import SOFTEN_HINT, SOFTEN_PREFIX, PlanRewriter, RewriteFailed, softened_by_qc
from stickman.settings import Settings


def design(unit_id, **changes):
    data = {"id": unit_id, "corrected_text": "It's 9 at night.", "visual_idea": "A moon rises over a quiet hut",
            "visual_type": "metaphor", "shot": "wide", "time_of_day": "night",
            "characters": [{"ref": "mascot", "action": "sitting calmly", "emotion": "calm"}],
            "mood": None, "setting": ["hut"], "props": ["small moon"], "composition": "hut centred, lots of sky",
            "energy_marks": [], "softened": False, "softened_reason": None}
    return {**data, **changes}


def describe_reply(**changes):
    """Plays the planner's describe stage for whichever unit the message asks about."""
    def reply(model, messages):
        line = next(row for row in messages[1]["content"].splitlines() if row.startswith("UNITS: "))
        [unit] = json.loads(line[len("UNITS: "):])
        return json.dumps({"units": [design(unit["id"], **changes)]})
    return reply


@pytest.fixture
def project(tmp_path, plan_data):
    path = tmp_path / "projects" / "2026-09-25_demo" / "plan.yaml"
    path.parent.mkdir(parents=True)
    write_plan(path, to_document(parse_plan(plan_data)), expected_hash=None)
    path.write_text("# my note\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def rewriter(tmp_path, project, chat, stage_runner):
    return PlanRewriter(stage_runner(chat), load_planning_context(tmp_path, Settings()), project)


def test_soften_rewrites_the_unit_marks_it_softened_and_keeps_your_comments(tmp_path, project, fake_chat, stage_runner):
    chat = fake_chat(describe_reply(softened=True, softened_reason="the hut replaces the scene"))
    unit = asyncio.run(rewriter(tmp_path, project, chat, stage_runner).soften("001", ""))
    assert unit.visual_idea == "A moon rises over a quiet hut"
    assert (unit.softened, unit.softened_reason) == (True, SOFTEN_PREFIX + "the hut replaces the scene")
    assert "Scene: A moon rises over a quiet hut." in unit.image_prompt and unit.prompt_locked is False
    assert f"HINT: {SOFTEN_HINT}" in chat.calls[0]["messages"][1]["content"]
    saved = load_plan(project).plan.units()[0]
    assert saved == unit
    assert project.read_text(encoding="utf-8").startswith("# my note\n")
    assert softened_by_qc(saved)


def test_a_soften_reason_is_given_when_the_llm_gives_none(tmp_path, project, fake_chat, stage_runner):
    unit = asyncio.run(rewriter(tmp_path, project, fake_chat(describe_reply()), stage_runner).soften("001", ""))
    assert unit.softened_reason == SOFTEN_PREFIX + "made more symbolic and tasteful"


def test_softened_at_planning_time_is_not_softened_by_qc(plan_data):
    unit = parse_plan(plan_data).units()[0].model_copy(update={"softened": True, "softened_reason": "toned down"})
    assert softened_by_qc(unit) is False


def test_redesign_passes_the_checkers_notes_and_leaves_softened_alone(tmp_path, project, fake_chat, stage_runner):
    chat = fake_chat(describe_reply())
    unit = asyncio.run(rewriter(tmp_path, project, chat, stage_runner).redesign("002a", "the fire is missing"))
    assert "HINT: The last images did not show the idea clearly: the fire is missing" in chat.calls[0]["messages"][1]["content"]
    assert (unit.id, unit.softened, unit.start) == ("002a", False, 4.0)
    assert load_plan(project).plan.units()[1].visual_idea == "A moon rises over a quiet hut"


def test_a_locked_unit_is_never_rewritten(tmp_path, project, fake_chat, stage_runner):
    text = project.read_text(encoding="utf-8").replace("prompt_locked: false", "prompt_locked: true", 1)
    project.write_text(text, encoding="utf-8")
    chat = fake_chat(describe_reply())
    with pytest.raises(RewriteFailed, match="locked"):
        asyncio.run(rewriter(tmp_path, project, chat, stage_runner).soften("001", ""))
    assert chat.calls == [] and project.read_text(encoding="utf-8") == text


def test_a_plan_edited_during_the_rewrite_is_not_overwritten(tmp_path, project, fake_chat, stage_runner):
    answer = describe_reply()

    def edit_then_answer(model, messages):
        project.write_text(project.read_text(encoding="utf-8") + "# edited meanwhile\n", encoding="utf-8")
        return answer(model, messages)

    with pytest.raises(RewriteFailed, match="changed on disk"):
        asyncio.run(rewriter(tmp_path, project, fake_chat(edit_then_answer), stage_runner).soften("001", ""))
    assert project.read_text(encoding="utf-8").endswith("# edited meanwhile\n")


def test_a_planning_failure_is_a_failed_rewrite(tmp_path, project, fake_chat, stage_runner):
    before = project.read_bytes()
    with pytest.raises(RewriteFailed, match="describe stage failed"):
        asyncio.run(rewriter(tmp_path, project, fake_chat(lambda model, messages: "not json"), stage_runner).soften("001", ""))
    assert project.read_bytes() == before


def test_an_invalid_plan_is_a_failed_rewrite(tmp_path, project, fake_chat, stage_runner):
    project.write_text("scenes: [", encoding="utf-8")
    with pytest.raises(RewriteFailed, match="plan.yaml can't be read"):
        asyncio.run(rewriter(tmp_path, project, fake_chat(describe_reply()), stage_runner).soften("001", ""))


def test_the_daily_limit_is_not_swallowed(tmp_path, project, fake_chat, stage_runner):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    with pytest.raises(CFError):
        asyncio.run(rewriter(tmp_path, project, fake_chat([daily]), stage_runner).soften("001", ""))
