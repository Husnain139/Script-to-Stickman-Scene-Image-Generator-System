import pytest
from typing import get_args

from stickman.ingest.models import TimedLine
from stickman.split.engine import SplitDecision
from stickman.split.scenes import Scene
from stickman.split.units import Part, build_units, unit_filename

TEXT = "Anthropologists studying the Zhuansi in the Kalahari recorded what people talk about by daylight versus by firelight."


def scene6():
    return Scene(6, (TimedLine(6, 21.0, 29.0, TEXT),))


def test_split_scene_becomes_a_and_b_units():
    decision = SplitDecision("split", (7,), 7, 24.294)
    a, b = build_units([scene6()], {6: decision})
    assert (a.id, a.part, a.start, a.end) == ("006a", "1 of 2", 21.0, 24.294)
    assert (b.id, b.part, b.start, b.end) == ("006b", "2 of 2", 24.294, 29.0)
    assert a.source_text == "Anthropologists studying the Zhuansi in the Kalahari"
    assert b.source_text.startswith("recorded what people")
    assert a.scene_number == b.scene_number == 6


@pytest.mark.parametrize("status", ["none", "no_valid_cut"])
def test_unsplit_scene_is_one_unit(status):
    (unit,) = build_units([scene6()], {6: SplitDecision(status)})
    assert (unit.id, unit.part, unit.start, unit.end, unit.source_text) == ("006", None, 21.0, 29.0, TEXT)


def test_missing_decision_means_not_split():
    (unit,) = build_units([scene6()], {})
    assert unit.id == "006"


@pytest.mark.parametrize(
    "unit_id, start, expected",
    [
        ("006a", 21.0, "006a_00-21.0.png"),
        ("006b", 24.294, "006b_00-24.3.png"),
        ("028", 125.96, "028_02-06.0.png"),
        ("099", 599.97, "099_10-00.0.png"),
        ("001", 0.0, "001_00-00.0.png"),
    ],
)
def test_unit_filename(unit_id, start, expected):
    assert unit_filename(unit_id, start) == expected


def test_unit_filename_property():
    (unit,) = build_units([scene6()], {})
    assert unit.filename == "006_00-21.0.png"


def test_part_labels_are_a_fixed_list():
    assert get_args(Part) == ("1 of 2", "2 of 2")
