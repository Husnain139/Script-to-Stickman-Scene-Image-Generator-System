import pytest

from stickman.ingest.models import TimedLine
from stickman.plan.analyse import AnalyseResult, LineCorrection, check_analyse, group_texts
from stickman.plan.corrections import correct_scenes, find_whole_words, merge_check
from stickman.plan.models import MergeCheck
from stickman.split.scenes import build_scenes, merge_short_scenes

LINES = [
    TimedLine(1, 0.0, 2.0, "It's 90 at night."),
    TimedLine(2, 2.0, 4.0, "Historian Roger E."),
    TimedLine(3, 4.0, 9.0, "Kirch went digging."),
    TimedLine(4, 9.0, 10.0, "First sleep, 2 sleep."),
]
GROUPS = [[1], [2, 3], [4]]
FIXES = [
    LineCorrection(line=1, from_="90 at night", to="9 at night", reason="clock"),
    LineCorrection(line=2, from_="Roger E. Kirch", to="Roger Ekirch", reason="name"),
    LineCorrection(line=4, from_="2 sleep", to="second sleep", reason="term"),
]


def texts_of(corrected):
    return {number: scene.corrected_text for number, scene in corrected.items()}


def test_whole_words_are_found_on_word_edges_only():
    text = "In 1992, researchers named it first sleep, 2 sleep."
    assert find_whole_words(text, "2") == [(43, 44)]
    assert find_whole_words(text, "99") == []
    assert find_whole_words(text, ", 2") == [(41, 44)]  # no edge rule next to punctuation
    assert find_whole_words("a a a", "a a") == [(0, 3), (2, 5)]  # overlapping occurrences count


def test_a_short_from_is_replaced_where_it_is_a_whole_word():
    lines = [TimedLine(1, 0.0, 3.0, "In 1992, researchers named it"), TimedLine(2, 3.0, 6.0, "first sleep, 2 sleep.")]
    groups = [[1, 2]]
    fix = LineCorrection(line=2, from_="2", to="second", reason="term")
    analysed = AnalyseResult(groups=groups, corrections=[fix], cast=[])
    assert check_analyse(analysed, lines, max_lines=3, library_ids=set()) == []
    corrected, _ = correct_scenes(build_scenes(lines, groups, max_lines=3), groups, group_texts(lines, groups), [fix])
    assert texts_of(corrected) == {1: "In 1992, researchers named it first sleep, second sleep."}


def test_correct_scenes_refuses_corrections_that_were_not_checked():
    scenes = build_scenes(LINES, GROUPS, max_lines=3)
    unchecked = [LineCorrection(line=4, from_="sleep", to="rest", reason="twice in its group")]
    with pytest.raises(ValueError, match="exactly once"):
        correct_scenes(scenes, GROUPS, group_texts(LINES, GROUPS), unchecked)


def test_corrections_apply_at_scene_level_across_a_line_break():
    scenes = build_scenes(LINES, GROUPS, max_lines=3)
    corrected, stored = correct_scenes(scenes, GROUPS, group_texts(LINES, GROUPS), FIXES)
    assert texts_of(corrected) == {
        1: "It's 9 at night.",
        2: "Historian Roger Ekirch went digging.",
        3: "First sleep, second sleep.",
    }
    assert [(c.scene, c.from_, c.to) for c in stored] == [
        ("001", "90 at night", "9 at night"),
        ("002", "Roger E. Kirch", "Roger Ekirch"),
        ("003", "2 sleep", "second sleep"),
    ]


def test_corrections_follow_scenes_merged_for_being_short():
    scenes = merge_short_scenes(build_scenes(LINES, GROUPS, max_lines=3), min_scene_seconds=1.5, max_lines=3)
    corrected, stored = correct_scenes(scenes, GROUPS, group_texts(LINES, GROUPS), FIXES)
    assert texts_of(corrected) == {1: "It's 9 at night.", 2: "Historian Roger Ekirch went digging. First sleep, second sleep."}
    assert [c.scene for c in stored] == ["001", "002", "002"]


KALAHARI = [TimedLine(1, 0.0, 8.0, "Anthropologists studying the Zhuansi in the Kalahari recorded what people talk about.")]


def corrected_scene(fixes, lines=KALAHARI, groups=((1,),)):
    groups = [list(group) for group in groups]
    corrected, _ = correct_scenes(build_scenes(lines, groups, max_lines=3), groups, group_texts(lines, groups), fixes)
    return corrected[1]


def test_a_correction_inside_part_a_goes_to_part_a_only():
    scene = corrected_scene([LineCorrection(line=1, from_="Zhuansi", to="Ju/'hoansi")])
    assert scene.part_texts(7) == ("Anthropologists studying the Ju/'hoansi in the Kalahari", "recorded what people talk about.")


def test_a_correction_inside_part_b_goes_to_part_b_only():
    scene = corrected_scene([LineCorrection(line=1, from_="talk about", to="discuss")])
    assert scene.part_texts(7) == ("Anthropologists studying the Zhuansi in the Kalahari", "recorded what people discuss.")


def test_a_correction_across_the_cut_leaves_the_part_texts_to_the_llm():
    scene = corrected_scene([LineCorrection(line=1, from_="Kalahari recorded", to="Kalahari, recorded")])
    assert scene.part_texts(7) is None
    assert scene.part_texts(6) is not None and scene.part_texts(8) is not None


def test_part_texts_add_up_to_the_scene_text_at_every_cut_of_a_merged_scene():
    scenes = merge_short_scenes(build_scenes(LINES, GROUPS, max_lines=3), min_scene_seconds=1.5, max_lines=3)
    corrected, _ = correct_scenes(scenes, GROUPS, group_texts(LINES, GROUPS), FIXES)
    scene = corrected[2]  # "Historian Roger Ekirch went digging. First sleep, second sleep." from groups 2 and 3
    assert scene.part_texts(5) == ("Historian Roger Ekirch went", "digging. First sleep, second sleep.")
    across = set()
    for k in range(1, len(scenes[1].tokens)):
        parts = scene.part_texts(k)
        if parts is None:
            across.add(k)
        else:
            assert " ".join(parts) == scene.corrected_text
    assert across == {2, 3, 9}  # inside "Roger E. Kirch" and "2 sleep"


def test_merge_check_lists_disagreements_only():
    assert merge_check(LINES, [2], [[1], [2, 3], [4]]) == []
    assert merge_check(LINES, [2], [[1], [2], [3], [4]]) == [MergeCheck(line=2, rules="merge", llm="separate")]
    assert merge_check(LINES, [], [[1, 2], [3], [4]]) == [MergeCheck(line=1, rules="separate", llm="merge")]
    assert merge_check(LINES, [2, 4], [[1], [2, 3], [4]]) == []  # the last line has nothing to merge with


def test_a_hinted_last_line_does_not_hide_other_disagreements():
    assert merge_check(LINES, [2, 4], [[1], [2], [3], [4]]) == [MergeCheck(line=2, rules="merge", llm="separate")]


def test_a_correction_on_the_second_line_of_a_group_lands_in_that_group():
    scenes = build_scenes(LINES, GROUPS, max_lines=3)
    fix = [LineCorrection(line=3, from_="digging", to="searching", reason="test")]
    corrected, stored = correct_scenes(scenes, GROUPS, group_texts(LINES, GROUPS), fix)
    assert corrected[2].corrected_text == "Historian Roger E. Kirch went searching."
    assert [c.scene for c in stored] == ["002"]
