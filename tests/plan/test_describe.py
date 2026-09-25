import asyncio
import json

import pytest

from stickman.plan.cast import CastInfo
from stickman.plan.describe import (
    DescribedUnit,
    DescribeResult,
    PreviousUnit,
    UnitContext,
    check_describe,
    describe_batch,
    describe_units,
    describe_user_message,
    text_word_errors,
)

CAST = {
    "mascot": CastInfo("mascot", "Everyman", 1, "the main character"),
    "caveman_group": CastInfo("caveman_group", "Caveman group", 3, "three cavemen"),
}
RULES = ["Clocks: a round face with two hands, no numerals."]


def design_json(unit_id, corrected="text", **overrides):
    fields = {
        "id": unit_id, "corrected_text": corrected, "visual_idea": f"Idea {unit_id}",
        "visual_type": "literal", "shot": "wide", "time_of_day": "day",
        "characters": [{"ref": "mascot", "action": "waving", "emotion": "happy"}],
        "mood": None, "setting": [], "props": [], "composition": "centred", "energy_marks": [],
        "softened": False, "softened_reason": None,
    }
    fields.update(overrides)
    return fields


def ctx(unit_id, part=None, text="It's 9 at night.", scene_text="It's 9 at night."):
    return UnitContext(unit_id, 0.0, 2.0, text, scene_text, part)


def reply_for(model, messages):
    """A valid describe reply for whatever UNITS the request lists."""
    line = next(l for l in messages[1]["content"].splitlines() if l.startswith("UNITS: "))
    units = json.loads(line[len("UNITS: "):])
    return json.dumps({"units": [design_json(u["id"], u["scene_corrected_text"]) for u in units]})


@pytest.mark.parametrize(
    "field, value",
    [
        ("props", ["a sign with text"]),
        ("visual_idea", "He sleeps, Zzz above him"),
        ("setting", ["a shop sign that says OPEN"]),
        ("composition", "caption at the bottom"),
        ("characters", [{"ref": "mascot", "action": "writing a label", "emotion": "calm"}]),
    ],
)
def test_text_words_in_prompt_fields_fail(field, value):
    errors = text_word_errors(DescribedUnit.model_validate(design_json("001", **{field: value})), "units[0]")
    assert errors and errors[0].startswith(f"units[0].{field}")


@pytest.mark.parametrize(
    "field, value",
    [("props", ["labelled jars", "medical texts on a shelf"]), ("visual_idea", "Context matters here")],
)
def test_whole_word_matching_lets_similar_words_through(field, value):
    assert text_word_errors(DescribedUnit.model_validate(design_json("001", **{field: value})), "units[0]") == []


def test_the_filter_never_checks_the_narration_text():
    unit = DescribedUnit.model_validate(design_json("014", corrected="He dug through medical texts and a text"))
    assert text_word_errors(unit, "units[0]") == []


def result(*units):
    return DescribeResult.model_validate({"units": list(units)})


def test_ids_must_match_the_batch():
    errors = check_describe(result(design_json("001")), [ctx("001"), ctx("002")], set(CAST))
    assert any(error.startswith("units: ids must be exactly") for error in errors)


def test_characters_must_be_in_the_cast():
    bad = design_json("001", characters=[{"ref": "anthropologist", "action": "a", "emotion": "b"}])
    assert any("units[0].characters[0].ref" in e for e in check_describe(result(bad), [ctx("001")], set(CAST)))


def test_a_part_whose_text_code_derives_is_not_checked():
    """Code sets such a part's text, so the answer's value is ignored (cached answers stay valid)."""
    scene = "Anthropologists studying the Ju/'hoansi in the Kalahari recorded what people talk about."
    part = ctx("006a", part="1 of 2", text="Anthropologists studying the Zhuansi in the Kalahari", scene_text=scene)
    assert check_describe(result(design_json("006a", corrected=scene)), [part], set(CAST)) == []


ACROSS = "Historian Roger Ekirch went digging."  # "Roger E. Kirch" -> "Roger Ekirch" straddles the cut


def across_the_cut():
    return [
        UnitContext("002a", 0.0, 2.0, "Historian Roger", ACROSS, "1 of 2", text_from_llm=True),
        UnitContext("002b", 2.0, 4.0, "E. Kirch went digging.", ACROSS, "2 of 2", text_from_llm=True),
    ]


def test_a_part_across_a_correction_must_not_return_the_whole_scene():
    errors = check_describe(result(design_json("002a", ACROSS), design_json("002b", ACROSS)), across_the_cut(), set(CAST))
    assert errors == [
        "units[0].corrected_text: return only this part's words, not the whole scene",
        "units[1].corrected_text: return only this part's words, not the whole scene",
    ]


def test_a_part_across_a_correction_is_the_start_or_the_end_of_the_scene_text():
    ok = result(design_json("002a", "Historian  Roger Ekirch"), design_json("002b", "went digging."))
    assert check_describe(ok, across_the_cut(), set(CAST)) == []
    swapped = result(design_json("002a", "went digging."), design_json("002b", "Historian Roger Ekirch"))
    assert check_describe(swapped, across_the_cut(), set(CAST)) == [
        "units[0].corrected_text: return only this part's words: the start of the scene's corrected text, up to the cut",
        "units[1].corrected_text: return only this part's words: the end of the scene's corrected text, from the cut",
    ]
    mid_word = result(design_json("002a", "Historian Roger Ek"), design_json("002b", "irch went digging."))
    assert len(check_describe(mid_word, across_the_cut(), set(CAST))) == 2


def test_the_user_message_lists_the_context_sections():
    message = describe_user_message(
        [ctx("002", text="Then we got fire.", scene_text="Then we got fire.")],
        previous=[PreviousUnit("001", "A dark night", "wide")],
        following=[("003", "Fire changed everything.")],
        cast=CAST,
        rules=RULES,
        hint="show the fire big",
    )
    lines = message.splitlines()
    assert lines[0] == (
        'CAST: [{"id": "mascot", "name": "Everyman", "figures": 1, "description": "the main character"}, '
        '{"id": "caveman_group", "name": "Caveman group", "figures": 3, "description": "three cavemen"}]'
    )
    assert lines[1].startswith("MASCOT RULE: the mascot appears only")
    assert lines[2:4] == ["VISUAL RULES:", "- Clocks: a round face with two hands, no numerals."]
    assert lines[4] == 'PREVIOUS: [{"id": "001", "visual_idea": "A dark night", "shot": "wide"}]'
    assert lines[5] == 'NEXT: [{"id": "003", "text": "Fire changed everything."}]'
    assert lines[6] == (
        'UNITS: [{"id": "002", "start": 0.0, "end": 2.0, "part": null, '
        '"source_text": "Then we got fire.", "scene_corrected_text": "Then we got fire."}]'
    )
    assert lines[7] == "HINT: show the fire big"


def test_units_are_described_in_batches_with_previous_and_next(fake_chat, stage_runner):
    chat = fake_chat(reply_for)
    contexts = [ctx(f"{n:03d}", text=f"Line {n}.", scene_text=f"Line {n}.") for n in range(1, 6)]
    designs = asyncio.run(describe_units(stage_runner(chat), contexts, cast=CAST, rules=RULES, batch_size=2))
    assert list(designs) == ["001", "002", "003", "004", "005"]
    assert len(chat.calls) == 3
    assert chat.calls[0]["messages"][0]["content"].startswith("You design one illustration per unit")
    first = chat.calls[0]["messages"][1]["content"]
    assert "PREVIOUS: []" in first and "HINT" not in first
    second = chat.calls[1]["messages"][1]["content"]
    assert ('PREVIOUS: [{"id": "001", "visual_idea": "Idea 001", "shot": "wide"}, '
            '{"id": "002", "visual_idea": "Idea 002", "shot": "wide"}]') in second
    assert 'NEXT: [{"id": "005", "text": "Line 5."}]' in second


def test_describe_batch_returns_units_in_batch_order(fake_chat, stage_runner):
    chat = fake_chat([json.dumps({"units": [design_json("002"), design_json("001")]})])
    units = asyncio.run(describe_batch(stage_runner(chat), [ctx("001"), ctx("002")], previous=[], following=[], cast=CAST, rules=RULES))
    assert [unit.id for unit in units] == ["001", "002"]
