import pytest

from stickman.ingest.models import TimedLine
from stickman.split.scenes import GroupingError, build_scenes, merge_short_scenes


def timed(*specs):
    return [TimedLine(i, start, end, text) for i, (start, end, text) in enumerate(specs, 1)]


LINES = timed(
    (0.0, 2.0, "Historian Roger E."),
    (2.0, 9.0, "Kirch went digging through old diaries."),
    (9.0, 10.0, "The watch."),
    (10.0, 11.0, "They prayed."),
    (11.0, 15.0, "They stoked the fire and talked."),
)


def line_numbers(scenes):
    return [[line.number for line in scene.lines] for scene in scenes]


def test_build_scenes_merges_groups():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    assert [s.number for s in scenes] == [1, 2, 3, 4]
    first = scenes[0]
    assert first.source_text == "Historian Roger E. Kirch went digging through old diaries."
    assert (first.start, first.end, first.duration, first.words) == (0.0, 9.0, 9.0, 9)


@pytest.mark.parametrize(
    "groups",
    [
        [[1, 2], [4], [3], [5]],  # out of order
        [[1, 2], [3], [4]],  # line 5 missing
        [[1, 2], [2, 3], [4], [5]],  # line 2 twice
        [[1, 2], [], [3], [4], [5]],  # empty group
    ],
)
def test_invalid_groups_are_rejected(groups):
    with pytest.raises(GroupingError):
        build_scenes(LINES, groups, max_lines=3)


def test_group_larger_than_max_lines_is_rejected():
    with pytest.raises(GroupingError, match="more than 3"):
        build_scenes(LINES, [[1, 2, 3, 4], [5]], max_lines=3)


def test_short_scene_merge_is_off_at_zero():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    assert line_numbers(merge_short_scenes(scenes, min_scene_seconds=0, max_lines=3)) == [[1, 2], [3], [4], [5]]


def test_short_scenes_merge_into_previous_within_max_lines():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    merged = merge_short_scenes(scenes, min_scene_seconds=2.0, max_lines=3)
    # [3] (1 s) joins [1,2]; [4] (1 s) would make 4 lines, so it stays on its own.
    assert line_numbers(merged) == [[1, 2, 3], [4], [5]]
    assert [s.number for s in merged] == [1, 2, 3]


def test_short_first_scene_merges_into_next():
    lines = timed((0.0, 1.0, "Look."), (1.0, 6.0, "The sun is gone for good tonight."))
    scenes = build_scenes(lines, [[1], [2]], max_lines=3)
    assert line_numbers(merge_short_scenes(scenes, min_scene_seconds=2.0, max_lines=3)) == [[1, 2]]


def test_scenes_record_their_group_positions():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    assert [s.groups for s in scenes] == [(1,), (2,), (3,), (4,)]


def test_scene_tokens_are_the_original_words_in_order():
    scene = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)[0]
    assert scene.tokens == ("Historian", "Roger", "E.", "Kirch", "went", "digging", "through", "old", "diaries.")
    assert len(scene.tokens) == scene.words


def test_merging_short_scenes_carries_group_positions():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    merged = merge_short_scenes(scenes, min_scene_seconds=2.0, max_lines=3)
    assert line_numbers(merged) == [[1, 2, 3], [4], [5]]
    assert [s.groups for s in merged] == [(1, 2), (3,), (4,)]


def test_a_short_first_scene_carries_its_group_into_the_next():
    scenes = build_scenes(LINES, [[1], [2], [3], [4], [5]], max_lines=4)
    merged = merge_short_scenes(scenes, min_scene_seconds=2.5, max_lines=4)
    assert line_numbers(merged) == [[1, 2, 3, 4], [5]]
    assert [s.groups for s in merged] == [(1, 2, 3, 4), (5,)]
