import asyncio
from datetime import date

import pytest

from stickman.ingest.models import TimedLine
from stickman.library import LibraryCharacter
from stickman.plan.analyse import AnalyseResult, analyse, analyse_user_message, check_analyse, format_clock

LINES = [
    TimedLine(1, 0.0, 2.0, "It's 90 at night."),
    TimedLine(2, 2.0, 6.0, "Historian Roger E."),
    TimedLine(3, 6.0, 13.0, "Kirch went digging through diaries."),
]
LIBRARY = [
    LibraryCharacter(
        id="cavemen_v1", name="Caveman group", figures=3, description="three cavemen", tags=["fire"],
        style_version=1, model="@cf/black-forest-labs/flux-2-klein-9b", sheet="sheet.png", ref="ref.png",
        approved=date(2026, 9, 22),
    )
]
HISTORIAN = {"id": "historian", "name": "Historian", "figures": 1, "description": "a stickman with round glasses", "library_ref": None}


def result(**overrides):
    data = {
        "groups": [[1], [2, 3]],
        "corrections": [{"line": 2, "from": "Roger E. Kirch", "to": "Roger Ekirch", "reason": "name split by speech-to-text"}],
        "cast": [HISTORIAN],
    }
    data.update(overrides)
    return AnalyseResult.model_validate(data)


def test_user_message_format():
    assert analyse_user_message(LINES, [2], LIBRARY) == (
        "LINES\n"
        "[1] 0:00 (2.0 s, 4 words) It's 90 at night.\n"
        "[2] 0:02 (4.0 s, 3 words) Historian Roger E.\n"
        "[3] 0:06 (7.0 s, 5 words) Kirch went digging through diaries.\n"
        "HINTS: [2]\n"
        'LIBRARY: [{"id": "cavemen_v1", "name": "Caveman group", "figures": 3, "description": "three cavemen", "tags": ["fire"]}]'
    )


def test_format_clock():
    assert [format_clock(s) for s in (0, 61.0, 125.9, 3725)] == ["0:00", "1:01", "2:05", "1:02:05"]


def test_a_valid_result_passes():
    assert check_analyse(result(), LINES, max_lines=3, library_ids=set()) == []


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"groups": [[1], [3]]}, "groups:"),
        ({"corrections": [{"line": 9, "from": "x", "to": "y", "reason": "r"}]}, "corrections[0].line"),
        ({"corrections": [{"line": 1, "from": "Roger E. Kirch", "to": "Roger Ekirch", "reason": "r"}]}, "corrections[0].from"),
        ({"cast": [{**HISTORIAN, "id": "mascot"}]}, "cast[0].id"),
        ({"cast": [{**HISTORIAN, "id": "Historian"}]}, "cast[0].id"),
        ({"cast": [HISTORIAN, HISTORIAN]}, "cast: duplicate"),
        ({"cast": [{**HISTORIAN, "library_ref": "nope"}]}, "cast[0].library_ref"),
    ],
)
def test_stage_checks(overrides, expected):
    errors = check_analyse(result(**overrides), LINES, max_lines=3, library_ids={"cavemen_v1"})
    assert any(error.startswith(expected) for error in errors), errors


def test_groups_longer_than_max_lines_fail():
    errors = check_analyse(result(groups=[[1, 2, 3]], corrections=[]), LINES, max_lines=2, library_ids=set())
    assert errors[0].startswith("groups:")


def test_analyse_sends_the_prompt_hints_and_library(fake_chat, stage_runner):
    chat = fake_chat([result().model_dump_json(by_alias=True)])
    got = asyncio.run(analyse(stage_runner(chat), LINES, hints=[2], library=LIBRARY, max_lines=3))
    assert got == result()
    system, user = chat.calls[0]["messages"]
    assert system["content"].startswith("You plan illustrations for a stickman explainer video.")
    assert "max 3 lines per group" in system["content"]
    assert "MASCOT RULE: the mascot appears only" in system["content"]
    assert "HINTS: [2]" in user["content"]


def test_a_correction_may_name_any_line_of_its_group():
    later_line = {"line": 3, "from": "Roger E. Kirch", "to": "Roger Ekirch", "reason": "name split by speech-to-text"}
    assert check_analyse(result(corrections=[later_line]), LINES, max_lines=3, library_ids=set()) == []


FIRST_SLEEP = [
    TimedLine(1, 0.0, 3.0, "In 1992, researchers named it"),
    TimedLine(2, 3.0, 6.0, "first sleep, 2 sleep."),
]


def check_fix(fix, lines=FIRST_SLEEP, groups=((1, 2),)):
    analysed = AnalyseResult.model_validate({"groups": [list(g) for g in groups], "corrections": [fix], "cast": []})
    return check_analyse(analysed, lines, max_lines=3, library_ids=set())


def test_a_short_from_that_is_one_whole_word_in_its_group_passes():
    assert check_fix({"line": 2, "from": "2", "to": "second"}) == []


def test_a_from_that_occurs_twice_as_whole_words_fails():
    [error] = check_fix({"line": 2, "from": "sleep", "to": "rest"})
    assert error.startswith('corrections[0].from: "sleep" must appear exactly once as whole words in its group')
    assert "quote enough surrounding words to make it unique" in error


def test_a_from_found_only_inside_a_longer_word_fails():
    [error] = check_fix({"line": 1, "from": "99", "to": "98"})
    assert error.startswith('corrections[0].from: "99" does not occur as whole words')


@pytest.mark.parametrize("phrase", [", 2", "2 sleep.", "sleep, 2", "it first"])
def test_a_from_with_punctuation_at_either_edge_still_matches(phrase):
    assert check_fix({"line": 1 if phrase == "it first" else 2, "from": phrase, "to": "x"}) == []


def test_two_corrections_may_not_overlap_in_one_group():
    analysed = result(corrections=[
        {"line": 2, "from": "Roger E.", "to": "Roger", "reason": "r"},
        {"line": 2, "from": "E. Kirch", "to": "Ekirch", "reason": "r"},
    ])
    [error] = check_analyse(analysed, LINES, max_lines=3, library_ids=set())
    assert error.startswith('corrections[1].from: "E. Kirch" overlaps corrections[0].from ("Roger E.")')


def test_the_prompt_asks_for_line_numbers():
    from stickman.plan.prompts import analyse_system

    text = analyse_system(3)
    assert 'give "line" (the number of the line where the "from" text starts)' in text
    assert "1-based position" not in text
