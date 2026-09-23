import pytest

from stickman.ingest.models import RawLine
from stickman.ingest.parse import ScriptParseError, parse_duration
from stickman.ingest.timing import build_timeline, length_warning, measure_pace
from stickman.settings import TimingSettings

TIMING = TimingSettings()


def raw(*pairs, srt_end=None):
    lines = [RawLine(i, start, text, source_line=i) for i, (start, text) in enumerate(pairs, 1)]
    if srt_end is not None:
        last = lines[-1]
        lines[-1] = RawLine(last.number, last.start, last.text, srt_end=srt_end, source_line=last.number)
    return lines


FIVE = raw(
    (0.0, "one two three"),
    (2.0, "four five six"),
    (4.0, "seven eight nine"),
    (6.0, "ten eleven twelve"),
    (8.0, "a b c d e f"),
)


def test_each_line_ends_where_the_next_starts():
    timeline = build_timeline(FIVE, TIMING)
    assert [(l.start, l.end) for l in timeline.lines[:-1]] == [(0, 2), (2, 4), (4, 6), (6, 8)]


def test_pace_is_measured_from_all_but_the_last_line():
    assert measure_pace(FIVE, TIMING) == (pytest.approx(1.5), True)


def test_last_line_end_uses_measured_pace():
    timeline = build_timeline(FIVE, TIMING)
    assert timeline.pace_wps == pytest.approx(1.5)
    assert timeline.end == pytest.approx(12.0)  # 6 words / 1.5 wps after 8.0
    assert timeline.lines[-1].end == timeline.end


def test_duration_overrides_the_estimate():
    assert build_timeline(FIVE, TIMING, duration=10.0).end == 10.0


def test_duration_before_last_start_is_rejected():
    with pytest.raises(ScriptParseError, match="duration"):
        build_timeline(FIVE, TIMING, duration=7.0)


def test_srt_last_cue_end_is_used_when_no_duration():
    lines = raw((0.0, "a b"), (2.0, "c d"), (4.0, "e f"), (6.0, "g h"), (8.0, "i j"), srt_end=9.5)
    assert build_timeline(lines, TIMING).end == 9.5
    assert build_timeline(lines, TIMING, duration=11.0).end == 11.0


def test_fallback_pace_when_too_few_lines():
    timeline = build_timeline(raw((0.0, "hello there"), (3.0, "a b c d e")), TIMING)
    assert (timeline.pace_wps, timeline.pace_measured) == (2.5, False)
    assert timeline.end == pytest.approx(5.0)  # 5 words / 2.5 wps after 3.0


def test_first_line_is_stretched_back_to_zero():
    lines = raw((3.0, "a b"), (5.0, "c d"), (7.0, "e f"), (9.0, "g h"), (11.0, "i j"))
    timeline = build_timeline(lines, TIMING)
    assert timeline.lines[0].start == 0.0
    assert timeline.pace_wps == pytest.approx(1.0)  # 8 words over 8 s, from the original times


def test_length_warning():
    long_line = raw((0.0, "a b"), (300.0, "c"))
    warning = length_warning(build_timeline(long_line, TIMING, duration=301.0), max_minutes=5)
    assert warning is not None and "5-minute" in warning and "5:01" in warning
    assert length_warning(build_timeline(FIVE, TIMING), max_minutes=5) is None


@pytest.mark.parametrize(
    "value, expected", [("4:48", 288.0), ("1:02:03", 3723.0), ("95.5", 95.5), (" 0:30 ", 30.0)]
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["abc", "1:2:3:4", "-5", "", "inf", "nan", "Infinity", "1e400", "1:inf"])
def test_parse_duration_rejects_bad_values(value):
    with pytest.raises(ValueError):
        parse_duration(value)
