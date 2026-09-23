"""Spec §4.7 golden test: the sample script through the split engine with fixed candidates."""

from pathlib import Path

import pytest

from stickman.ingest.fragments import fragment_hints
from stickman.ingest.parse import parse_script
from stickman.ingest.timing import build_timeline
from stickman.settings import SplitSettings, TimingSettings
from stickman.split.engine import decide_split, needs_split
from stickman.split.scenes import build_scenes
from stickman.split.units import build_units

FIXTURE = Path(__file__).parent / "fixtures" / "scripts" / "first-sleep.txt"
GROUPS = [[n] for n in range(1, 13)] + [[13, 14]] + [[n] for n in range(15, 30)]
CANDIDATES = {5: [6, 15], 6: [7], 8: [10], 13: [13], 15: [11], 23: [10], 24: [19], 26: [12], 28: [6, 9]}
EXPECTED_CUTS = {6: 24.294, 8: 39.294, 13: 66.500, 15: 77.385, 23: 98.913, 24: 110.786, 26: 120.500, 28: 129.365}


def run(split=SplitSettings()):
    timeline = build_timeline(parse_script(FIXTURE.read_text(encoding="utf-8")), TimingSettings())
    scenes = build_scenes(timeline.lines, GROUPS, max_lines=3)
    decisions = {s.number: decide_split(s, CANDIDATES.get(s.number, []), split) for s in scenes}
    return timeline, scenes, decisions, build_units(scenes, decisions)


@pytest.fixture(scope="module")
def result():
    return run()


def test_29_lines_pace_and_end(result):
    timeline, *_ = result
    assert len(timeline.lines) == 29
    assert timeline.pace_measured is True
    assert timeline.pace_wps == pytest.approx(337 / 126, abs=1e-4)  # 2.6746
    assert timeline.end == pytest.approx(133.852, abs=1e-3)


def test_fragment_hints_flag_line_13_only(result):
    timeline, *_ = result
    assert fragment_hints(timeline.lines) == [13]


def test_28_scenes_with_lines_13_and_14_merged(result):
    _, scenes, _, _ = result
    assert len(scenes) == 28
    s13 = scenes[12]
    assert [line.number for line in s13.lines] == [13, 14]
    assert (s13.start, s13.end, s13.words) == (61.0, 70.0, 23)


def test_split_candidates(result):
    _, scenes, _, _ = result
    assert [s.number for s in scenes if needs_split(s, SplitSettings())] == [5, 6, 8, 13, 15, 23, 24, 26, 28]


def test_scene_5_has_no_valid_cut(result):
    _, _, decisions, _ = result
    assert decisions[5].status == "no_valid_cut"


@pytest.mark.parametrize("scene, expected", sorted(EXPECTED_CUTS.items()))
def test_cut_times(result, scene, expected):
    _, _, decisions, _ = result
    assert decisions[scene].status == "split"
    assert decisions[scene].cut_time == pytest.approx(expected, abs=1e-3)


def test_scene_28_falls_back_to_second_candidate(result):
    _, _, decisions, _ = result
    assert decisions[28].cut_after_word == 9


def test_36_units_in_order_covering_the_video(result):
    timeline, _, _, units = result
    expected_ids = []
    for n in range(1, 29):
        expected_ids += [f"{n:03d}a", f"{n:03d}b"] if n in EXPECTED_CUTS else [f"{n:03d}"]
    assert [u.id for u in units] == expected_ids
    assert len(units) == 36
    assert units[0].start == 0.0
    assert all(a.end == b.start for a, b in zip(units, units[1:]))
    assert units[-1].end == timeline.end


def test_file_names_for_scene_6(result):
    *_, units = result
    by_id = {u.id: u for u in units}
    assert by_id["006a"].filename == "006a_00-21.0.png"
    assert by_id["006b"].filename == "006b_00-24.3.png"


def test_changing_split_seconds_changes_the_result():
    _, _, decisions, units = run(SplitSettings(split_seconds=10.0))
    assert decisions[6].status == "none"  # 8 s, 17 words
    assert decisions[8].status == "none"  # 9 s, 17 words
    assert decisions[26].status == "split"  # 24 words still triggers
    assert len(units) == 34
