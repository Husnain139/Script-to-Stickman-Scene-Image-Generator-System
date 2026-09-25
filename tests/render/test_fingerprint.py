import pytest

from stickman.plan.models import CharacterRef, parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.render.fingerprint import fingerprint

KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
DESCRIPTIONS = {"mascot": "the main character", "caveman_group": "three cavemen"}


def fp(unit, **changes):
    args = dict(cast_descriptions=DESCRIPTIONS, model=KLEIN_4B, aspect="16:9", size=(1920, 1088),
                style_version=1, reference_hashes=[])
    return fingerprint(unit, **{**args, **changes})


def first_unit(plan_data):
    return parse_plan(plan_data).units()[0]  # 001 draws only the mascot


def test_the_fingerprint_is_a_stable_sha256(plan_data):
    unit = first_unit(plan_data)
    value = fp(unit)
    assert value.startswith("sha256:") and len(value) == len("sha256:") + 64
    assert fp(first_unit(plan_data)) == value


@pytest.mark.parametrize(
    "change",
    [
        {"props": ["a small campfire"]},
        {"shot": "close-up"},
        {"composition": "figures on the left"},
        {"image_prompt": "edited by hand"},
        {"seed": 42},
        {"characters": [CharacterRef(ref="mascot", action="sleeping", emotion="calm")]},
    ],
)
def test_a_visual_field_the_prompt_or_a_pinned_seed_changes_it(plan_data, change):
    unit = first_unit(plan_data)
    assert fp(unit.model_copy(update=change)) != fp(unit)


@pytest.mark.parametrize(
    "change",
    [
        {"model": "@cf/black-forest-labs/flux-2-klein-9b"},
        {"aspect": "9:16", "size": (1088, 1920)},
        {"style_version": 2},
        {"reference_hashes": ["sha256:" + "0" * 64]},
        {"cast_descriptions": {**DESCRIPTIONS, "mascot": "a different mascot"}},
    ],
)
def test_the_model_size_style_references_or_a_drawn_characters_description_change_it(plan_data, change):
    unit = first_unit(plan_data)
    assert fp(unit, **change) != fp(unit)


def test_text_timing_and_characters_not_drawn_never_change_it(plan_data):
    unit = first_unit(plan_data)
    same = unit.model_copy(update={"corrected_text": "Other words.", "start": 0.5, "softened": True})
    assert fp(same) == fp(unit)
    assert fp(unit, cast_descriptions={**DESCRIPTIONS, "caveman_group": "someone else"}) == fp(unit)


def test_comments_and_formatting_never_change_it(tmp_path, plan_data):
    path = tmp_path / "plan.yaml"
    write_plan(path, to_document(parse_plan(plan_data)), expected_hash=None)
    edited = tmp_path / "edited.yaml"
    text = path.read_text(encoding="utf-8")
    edited.write_text("# my notes\n" + text.replace("shot: wide", "shot: wide   # keep it wide", 1), encoding="utf-8")
    assert fp(load_plan(edited).plan.units()[0]) == fp(load_plan(path).plan.units()[0])
