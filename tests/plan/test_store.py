import pytest

from stickman.plan.models import PlanValidationError, parse_plan
from stickman.plan.store import (
    PlanChangedError,
    file_hash,
    load_plan,
    to_document,
    update_unit,
    write_plan,
)


def new_plan_file(tmp_path, plan_data):
    path = tmp_path / "plan.yaml"
    write_plan(path, to_document(parse_plan(plan_data)), expected_hash=None)
    return path


def test_a_written_plan_loads_back_equal(tmp_path, plan_data):
    path = new_plan_file(tmp_path, plan_data)
    loaded = load_plan(path)
    assert loaded.plan == parse_plan(plan_data)
    assert loaded.hash == file_hash(path.read_bytes())


def test_the_layout_follows_the_spec(tmp_path, plan_data):
    text = new_plan_file(tmp_path, plan_data).read_text(encoding="utf-8")
    assert text.startswith('schema_version: 1\nproject: demo\naspect: "16:9"\n')
    assert "cast:\n  - id: mascot\n" in text
    assert "id: '001'" in text
    assert "lines: [1]" in text
    assert "split: {status: none}" in text
    assert "split: {status: split, cut_after_word: 3, candidates: [3]}" in text
    assert "- {ref: mascot, action: waving, emotion: happy}" in text
    assert "setting: [ground line]" in text
    assert "mood: null" in text
    assert "from: 90 at night" in text


def test_unit_keys_are_in_spec_order(plan_data):
    unit = to_document(parse_plan(plan_data))["scenes"][0]["units"][0]
    keys = list(unit)
    assert keys[:6] == ["id", "part", "start", "end", "source_text", "corrected_text"]
    assert keys[-3:] == ["seed", "image_prompt", "prompt_locked"]


def test_multi_line_prompts_are_block_scalars(tmp_path, plan_data):
    plan_data["scenes"][0]["units"][0]["image_prompt"] = "line one\nline two"
    path = new_plan_file(tmp_path, plan_data)
    assert "image_prompt: |-\n" in path.read_text(encoding="utf-8")
    assert load_plan(path).plan.units()[0].image_prompt == "line one\nline two"


def test_updating_one_unit_keeps_comments_and_hand_edits(tmp_path, plan_data):
    path = new_plan_file(tmp_path, plan_data)
    text = path.read_text(encoding="utf-8")
    edited = "# my notes\n" + text.replace("visual_idea: Idea 002b", "visual_idea: My own idea  # hand edit")
    path.write_text(edited, encoding="utf-8")  # CRLF on Windows, like a real editor
    loaded = load_plan(path)
    update_unit(loaded.doc, "001", {"visual_idea": "Replanned idea", "setting": ["one small tree"]})
    write_plan(path, loaded.doc, expected_hash=loaded.hash)
    out = path.read_bytes().decode("utf-8")
    assert "\r" not in out
    assert out.startswith("# my notes\n")
    assert "visual_idea: My own idea  # hand edit" in out
    assert "visual_idea: Replanned idea" in out
    assert "setting: [one small tree]" in out
    assert load_plan(path).plan.units()[2].visual_idea == "My own idea"


def test_writing_after_the_file_changed_on_disk_is_refused(tmp_path, plan_data):
    path = new_plan_file(tmp_path, plan_data)
    loaded = load_plan(path)
    path.write_bytes(path.read_bytes() + b"# edited meanwhile\n")
    before = path.read_bytes()
    update_unit(loaded.doc, "001", {"visual_idea": "Replanned idea"})
    with pytest.raises(PlanChangedError):
        write_plan(path, loaded.doc, expected_hash=loaded.hash)
    assert path.read_bytes() == before


def test_the_tool_never_writes_an_invalid_plan(tmp_path, plan_data):
    path = new_plan_file(tmp_path, plan_data)
    loaded = load_plan(path)
    before = path.read_bytes()
    update_unit(loaded.doc, "001", {"shot": "huge"})
    with pytest.raises(PlanValidationError):
        write_plan(path, loaded.doc, expected_hash=loaded.hash)
    assert path.read_bytes() == before


def test_an_invalid_plan_file_reports_yaml_paths(tmp_path, plan_data):
    path = new_plan_file(tmp_path, plan_data)
    path.write_text(path.read_text(encoding="utf-8").replace("shot: wide", "shot: huge", 1), encoding="utf-8")
    with pytest.raises(PlanValidationError) as info:
        load_plan(path)
    assert any(error.startswith("scenes[0].units[0].shot") for error in info.value.errors)


def test_an_unquoted_scene_id_is_caught(tmp_path, plan_data):
    path = new_plan_file(tmp_path, plan_data)
    path.write_text(path.read_text(encoding="utf-8").replace("id: '001'", "id: 001", 1), encoding="utf-8")
    with pytest.raises(PlanValidationError) as info:
        load_plan(path)
    assert any(error.startswith("scenes[0].id") for error in info.value.errors)


def test_broken_yaml_is_a_validation_error(tmp_path):
    path = tmp_path / "plan.yaml"
    path.write_text("scenes: [unclosed\n", encoding="utf-8")
    with pytest.raises(PlanValidationError, match="invalid YAML"):
        load_plan(path)


def test_update_unit_rejects_unknown_ids(tmp_path, plan_data):
    loaded = load_plan(new_plan_file(tmp_path, plan_data))
    with pytest.raises(KeyError):
        update_unit(loaded.doc, "999", {"visual_idea": "x"})
