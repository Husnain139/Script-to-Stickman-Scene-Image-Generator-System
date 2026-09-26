from stickman.config_files import load_mascot, load_style
from stickman.plan.cast import cast_infos
from stickman.plan.models import parse_plan
from stickman.plan.refresh import refresh_prompts, with_prompts, without_references
from stickman.prompt.builder import ReferenceAvailability, build_prompt

NONE = ReferenceAvailability()
ANCHOR_AND_MASCOT = ReferenceAvailability(anchor=True, sheets=frozenset({"mascot"}))


def refresh(tmp_path, plan, references):
    return refresh_prompts(plan, style=load_style(tmp_path), mascot=load_mascot(tmp_path), references=references)


def test_tool_built_prompts_get_the_reference_paragraph(tmp_path, plan_data, built_prompts):
    result = refresh(tmp_path, parse_plan(built_prompts(plan_data)), ANCHOR_AND_MASCOT)
    assert list(result.rebuilt) == ["001", "002a", "002b"] and result.hand_edited == []
    prompt = result.rebuilt["001"]
    assert "Reference images: image 0 shows the drawing style only" in prompt
    assert "Image 1 shows Everyman:" in prompt
    assert prompt.endswith(load_style(tmp_path).strict_clause.strip())


def test_prompts_that_already_match_are_left_alone(tmp_path, plan_data, built_prompts):
    result = refresh(tmp_path, parse_plan(built_prompts(plan_data)), NONE)
    assert (result.rebuilt, result.hand_edited) == ({}, [])


def test_a_prompt_built_with_references_goes_back_when_they_are_gone(tmp_path, plan_data, built_prompts):
    plan = parse_plan(built_prompts(plan_data))
    with_refs = with_prompts(plan, refresh(tmp_path, plan, ANCHOR_AND_MASCOT).rebuilt)
    back = refresh(tmp_path, with_refs, NONE)
    assert back.rebuilt == {unit.id: unit.image_prompt for unit in plan.units()}


def test_locked_prompts_are_never_touched(tmp_path, plan_data, built_prompts):
    data = built_prompts(plan_data)
    data["scenes"][0]["units"][0]["prompt_locked"] = True
    result = refresh(tmp_path, parse_plan(data), ANCHOR_AND_MASCOT)
    assert "001" not in result.rebuilt and "001" not in result.hand_edited


def test_a_hand_edited_prompt_is_left_and_reported_only_when_references_go_with_it(tmp_path, plan_data):
    plan = parse_plan(plan_data)  # its prompts are "prompt for <unit>": not what the builder makes
    assert refresh(tmp_path, plan, ANCHOR_AND_MASCOT).hand_edited == ["001", "002a", "002b"]
    assert refresh(tmp_path, plan, ANCHOR_AND_MASCOT).rebuilt == {}
    assert refresh(tmp_path, plan, NONE).hand_edited == []  # no reference images are sent with them


def test_an_empty_prompt_is_neither_rebuilt_nor_reported(tmp_path, plan_data):
    plan_data["scenes"][0]["units"][0]["image_prompt"] = ""
    result = refresh(tmp_path, parse_plan(plan_data), ANCHOR_AND_MASCOT)
    assert "001" not in result.rebuilt and "001" not in result.hand_edited


def test_without_references_removes_only_the_reference_paragraph(tmp_path, plan_data):
    style, mascot = load_style(tmp_path), load_mascot(tmp_path)
    plan = parse_plan(plan_data)
    unit = plan.units()[0]
    table = cast_infos(plan.cast, mascot)
    with_refs = build_prompt(unit, style=style, cast=table, references=["mascot"])
    bare = build_prompt(unit, style=style, cast=table, references=None)
    assert with_refs != bare
    assert without_references(with_refs) == bare
    assert without_references(bare) == bare


def test_with_prompts_replaces_only_the_given_units(plan_data):
    plan = parse_plan(plan_data)
    changed = with_prompts(plan, {"002a": "new prompt"})
    assert [unit.image_prompt for unit in changed.units()] == ["prompt for 001", "new prompt", "prompt for 002b"]
    assert [unit.image_prompt for unit in plan.units()] == ["prompt for 001", "prompt for 002a", "prompt for 002b"]
