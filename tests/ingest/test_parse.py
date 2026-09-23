import pytest

from stickman.ingest.models import TimedLine
from stickman.ingest.parse import ScriptParseError, parse_script
from stickman.ingest.words import count_words


def triples(lines):
    return [(line.number, line.start, line.text) for line in lines]


def test_parses_m_ss_lines():
    lines = parse_script("0:00: Hello there.\n0:02: Second line.\n")
    assert triples(lines) == [(1, 0.0, "Hello there."), (2, 2.0, "Second line.")]


def test_parses_h_mm_ss_fractions_and_separators():
    lines = parse_script("1:02:03 - One.\n1:02:04.5: Two.\n1:02:05,25 Three.\n")
    assert [line.start for line in lines] == [3723.0, 3724.5, 3725.25]
    assert [line.text for line in lines] == ["One.", "Two.", "Three."]


def test_blank_lines_ignored_and_bom_stripped():
    lines = parse_script("﻿0:00: A.\n\n\n0:01: B.\n")
    assert triples(lines) == [(1, 0.0, "A."), (2, 1.0, "B.")]
    assert lines[1].source_line == 4


def test_unparseable_line_reports_line_number():
    with pytest.raises(ScriptParseError) as info:
        parse_script("0:00: A.\nhello world\n")
    assert info.value.line_number == 2


def test_timestamps_must_strictly_increase():
    with pytest.raises(ScriptParseError) as info:
        parse_script("0:05: A.\n0:05: B.\n")
    assert info.value.line_number == 2


def test_decreasing_timestamp_rejected():
    with pytest.raises(ScriptParseError) as info:
        parse_script("0:05: A.\n0:03: B.\n")
    assert info.value.line_number == 2


def test_empty_script_rejected():
    with pytest.raises(ScriptParseError, match="no lines"):
        parse_script("\n\n")


@pytest.mark.parametrize("line", ["1:75:00: Too many minutes.", "0:75: Too many seconds."])
def test_out_of_range_minutes_or_seconds_rejected(line):
    with pytest.raises(ScriptParseError):
        parse_script(line + "\n")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("It's 90 at night, 40,000 years ago.", 7),
        ("80% 1992 didn't E.", 4),
        ("  spaced   out  ", 2),
        ("", 0),
    ],
)
def test_count_words(text, expected):
    assert count_words(text) == expected


def test_timed_line_words_and_duration():
    line = TimedLine(1, 2.0, 6.0, "The sun is gone.")
    assert line.words == 4
    assert line.duration == 4.0
