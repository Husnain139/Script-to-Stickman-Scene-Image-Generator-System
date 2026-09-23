import pytest

from stickman.ingest.models import TimedLine
from stickman.settings import SplitSettings
from stickman.split.engine import cut_time, decide_split, needs_split
from stickman.split.scenes import Scene

SPLIT = SplitSettings()


def words(n):
    return " ".join(f"w{i}" for i in range(1, n + 1))


def scene_of(*specs, number=1):
    return Scene(number, tuple(TimedLine(i, s, e, t) for i, (s, e, t) in enumerate(specs, 1)))


def test_needs_split_by_duration():
    assert needs_split(scene_of((0.0, 7.0, words(10))), SPLIT) is True
    assert needs_split(scene_of((0.0, 6.9, words(10))), SPLIT) is False


def test_needs_split_by_words():
    assert needs_split(scene_of((0.0, 3.0, words(20))), SPLIT) is True
    assert needs_split(scene_of((0.0, 3.0, words(19))), SPLIT) is False


def test_cut_time_single_line_is_proportional():
    assert cut_time(scene_of((10.0, 18.0, words(10))), 5) == pytest.approx(14.0)


def test_cut_time_uses_the_line_containing_the_cut_word():
    scene = scene_of((61.0, 63.0, "Historian Roger E."), (63.0, 70.0, words(20)))
    assert cut_time(scene, 13) == pytest.approx(66.5)  # word 10 of line 2's 20 words
    assert cut_time(scene, 3) == pytest.approx(63.0)  # last word of line 1 -> its end


@pytest.mark.parametrize("k", [0, 10])
def test_cut_time_rejects_out_of_range_k(k):
    with pytest.raises(ValueError):
        cut_time(scene_of((0.0, 8.0, words(10))), k)


def test_not_a_candidate_gives_none():
    decision = decide_split(scene_of((0.0, 3.0, words(5))), [2], SPLIT)
    assert decision.status == "none"


def test_first_valid_candidate_wins():
    decision = decide_split(scene_of((0.0, 8.0, words(10))), [2, 5, 6], SPLIT)
    assert (decision.status, decision.cut_after_word) == ("split", 5)
    assert decision.cut_time == pytest.approx(4.0)
    assert decision.candidates == (2, 5, 6)


def test_no_valid_cut_when_every_part_would_be_too_short():
    decision = decide_split(scene_of((15.0, 21.0, words(20))), [6, 15], SPLIT)
    assert decision.status == "no_valid_cut"
    assert decision.candidates == (6, 15)
    assert decision.cut_time is None


def test_no_candidates_means_no_valid_cut():
    assert decide_split(scene_of((0.0, 8.0, words(10))), [], SPLIT).status == "no_valid_cut"


def test_out_of_range_candidates_are_skipped():
    decision = decide_split(scene_of((0.0, 8.0, words(10))), [0, 10, 5], SPLIT)
    assert decision.cut_after_word == 5


def test_part_exactly_min_length_is_valid():
    decision = decide_split(scene_of((0.0, 5.0, words(20))), [10], SPLIT)
    assert decision.status == "split"
    assert decision.cut_time == pytest.approx(2.5)


def test_settings_change_the_result():
    scene = scene_of((0.0, 8.0, words(10)))
    assert decide_split(scene, [5], SplitSettings(split_seconds=10.0)).status == "none"
    assert decide_split(scene, [5], SplitSettings(min_part_seconds=4.5)).status == "no_valid_cut"
