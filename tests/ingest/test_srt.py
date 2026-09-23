import pytest

from stickman.ingest.parse import ScriptParseError, parse_script

SRT = """1
00:00:00,000 --> 00:00:02,500
It's 9 at night,
40,000 years ago.

2
00:00:02,500 --> 00:00:06,000
The sun is gone.
"""


def test_parses_srt_cues_joining_text_lines():
    lines = parse_script(SRT)
    assert [(l.number, l.start, l.text, l.srt_end) for l in lines] == [
        (1, 0.0, "It's 9 at night, 40,000 years ago.", 2.5),
        (2, 2.5, "The sun is gone.", 6.0),
    ]


def test_srt_without_index_lines_and_dot_milliseconds():
    text = "00:00:01.000 --> 00:00:02.000\nOne.\n\n00:00:03.000 --> 00:00:04.000\nTwo.\n"
    lines = parse_script(text)
    assert [(l.start, l.srt_end, l.text) for l in lines] == [(1.0, 2.0, "One."), (3.0, 4.0, "Two.")]


def test_bad_srt_time_line_reports_its_line_number():
    text = "1\n00:00:00,000 --> 00:00:01,000\nA\n\n2\n00:00:0x --> y\nB\n"
    with pytest.raises(ScriptParseError) as info:
        parse_script(text)
    assert info.value.line_number == 6


def test_srt_cue_without_text_rejected():
    with pytest.raises(ScriptParseError, match="no text"):
        parse_script("1\n00:00:00,000 --> 00:00:01,000\n")


def test_srt_starts_must_increase():
    text = "00:00:05,000 --> 00:00:06,000\nA\n\n00:00:04,000 --> 00:00:07,000\nB\n"
    with pytest.raises(ScriptParseError) as info:
        parse_script(text)
    assert info.value.line_number == 4
