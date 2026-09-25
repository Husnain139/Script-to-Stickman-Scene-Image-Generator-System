import copy

import pytest

from stickman.plan.models import VISUAL_FIELDS, PlanValidationError, is_cast_id, parse_plan


def unit(data, index):
    return [u for scene in data["scenes"] for u in scene["units"]][index]


def test_the_sample_plan_is_valid(plan_data):
    plan = parse_plan(plan_data)
    assert [u.id for u in plan.units()] == ["001", "002a", "002b"]
    assert plan.scene_of("002b").id == "002"
    assert plan.corrections[0].from_ == "90 at night"


def test_visual_fields_are_the_stage_3_fields_in_order():
    assert VISUAL_FIELDS == (
        "visual_idea", "visual_type", "shot", "time_of_day", "characters", "mood",
        "setting", "props", "composition", "energy_marks", "softened", "softened_reason",
    )


def test_cast_ids():
    assert is_cast_id("caveman_group")
    assert not is_cast_id("mascot")
    assert not is_cast_id("Caveman Group")


def gap(d):
    d["scenes"][1]["start"] = 5.0
    d["scenes"][1]["units"][0]["start"] = 5.0


def unit_gap(d):
    d["scenes"][1]["units"][1]["start"] = 7.5


def unknown_character(d):
    unit(d, 0)["characters"][0]["ref"] = "anthropologist"


def wrong_split_units(d):
    d["scenes"][1]["split"] = {"status": "none"}


def correction_text_missing(d):
    d["corrections"][0]["from"] = "80 at night"


def correction_scene_missing(d):
    d["corrections"][0]["scene"] = "099"


def no_mascot(d):
    d["cast"] = d["cast"][1:]


def duplicate_cast(d):
    d["cast"].append(copy.deepcopy(d["cast"][1]))


def lines_skip(d):
    d["scenes"][1]["lines"] = [3]


def end_mismatch(d):
    d["duration_end"] = 13.0


CROSS_PLAN = [
    (gap, "scenes[1].start"),
    (unit_gap, "scenes[1].units[1].start"),
    (unknown_character, "scenes[0].units[0].characters[0].ref"),
    (wrong_split_units, "scenes[1].units"),
    (correction_text_missing, "corrections[0].from"),
    (correction_scene_missing, "corrections[0].scene"),
    (no_mascot, "cast"),
    (duplicate_cast, "duplicate"),
    (lines_skip, "scenes[1].lines"),
    (end_mismatch, "duration_end"),
]


@pytest.mark.parametrize("mutate, where", CROSS_PLAN, ids=[m.__name__ for m, _ in CROSS_PLAN])
def test_whole_plan_rules(plan_data, mutate, where):
    mutate(plan_data)
    with pytest.raises(PlanValidationError) as info:
        parse_plan(plan_data)
    assert any(where in error for error in info.value.errors), info.value.errors


SCHEMA = [
    ("bad_shot", lambda d: unit(d, 0).update(shot="extreme"), "scenes[0].units[0].shot"),
    ("three_settings", lambda d: unit(d, 0)["setting"].extend(["a", "b"]), "scenes[0].units[0].setting"),
    ("four_characters", lambda d: unit(d, 0)["characters"].extend([{"ref": "mascot", "action": "a", "emotion": "b"}] * 3),
     "scenes[0].units[0].characters"),
    ("id_as_number", lambda d: d["scenes"][0].update(id=1), "scenes[0].id"),
    ("extra_field", lambda d: unit(d, 0).update(colour="red"), "scenes[0].units[0].colour"),
    ("bad_cast_id", lambda d: d["cast"][1].update(id="Caveman Group"), "cast[1]"),
]


@pytest.mark.parametrize("mutate, where", [(m, w) for _, m, w in SCHEMA], ids=[n for n, _, _ in SCHEMA])
def test_schema_errors_carry_yaml_paths(plan_data, mutate, where):
    mutate(plan_data)
    with pytest.raises(PlanValidationError) as info:
        parse_plan(plan_data)
    assert any(error.startswith(where) for error in info.value.errors), info.value.errors


def test_library_refs_are_checked_when_the_library_is_known(plan_data):
    plan_data["cast"][1]["library_ref"] = "cavemen_v1"
    assert parse_plan(plan_data)
    with pytest.raises(PlanValidationError, match="library"):
        parse_plan(plan_data, library_ids=set())
    assert parse_plan(plan_data, library_ids={"cavemen_v1"})
