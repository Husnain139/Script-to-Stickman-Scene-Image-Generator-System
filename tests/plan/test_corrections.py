from stickman.ingest.models import TimedLine
from stickman.plan.analyse import GroupCorrection, group_texts
from stickman.plan.corrections import apply_corrections, correct_scenes, merge_check
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
    GroupCorrection(group=1, from_="90 at night", to="9 at night", reason="clock"),
    GroupCorrection(group=2, from_="Roger E. Kirch", to="Roger Ekirch", reason="name"),
    GroupCorrection(group=3, from_="2 sleep", to="second sleep", reason="term"),
]


def test_apply_corrections_replaces_the_first_occurrence_in_order():
    assert apply_corrections("a b a b", [("a", "x"), ("b", "y"), ("a", "z")]) == "x y z b"


def test_corrections_apply_at_scene_level_across_a_line_break():
    scenes = build_scenes(LINES, GROUPS, max_lines=3)
    corrected, stored = correct_scenes(scenes, group_texts(LINES, GROUPS), FIXES)
    assert corrected == {
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
    corrected, stored = correct_scenes(scenes, group_texts(LINES, GROUPS), FIXES)
    assert corrected == {1: "It's 9 at night.", 2: "Historian Roger Ekirch went digging. First sleep, second sleep."}
    assert [c.scene for c in stored] == ["001", "002", "002"]


def test_merge_check_lists_disagreements_only():
    assert merge_check(LINES, [2], [[1], [2, 3], [4]]) == []
    assert merge_check(LINES, [2], [[1], [2], [3], [4]]) == [MergeCheck(line=2, rules="merge", llm="separate")]
    assert merge_check(LINES, [], [[1, 2], [3], [4]]) == [MergeCheck(line=1, rules="separate", llm="merge")]
    assert merge_check(LINES, [2, 4], [[1], [2, 3], [4]]) == []  # the last line has nothing to merge with


def test_a_hinted_last_line_does_not_hide_other_disagreements():
    assert merge_check(LINES, [2, 4], [[1], [2], [3], [4]]) == [MergeCheck(line=2, rules="merge", llm="separate")]
