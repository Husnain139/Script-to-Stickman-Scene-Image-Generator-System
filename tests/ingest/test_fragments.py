import pytest

from stickman.ingest.fragments import fragment_hints, is_likely_fragment
from stickman.ingest.models import TimedLine


@pytest.mark.parametrize(
    "text, next_text, expected",
    [
        ("Historian Roger E.", "Kirch went digging.", True),  # (b) initial
        ("He went to see Dr.", "Smith about it.", True),  # (b) abbreviation
        ("and then the fire", "Changed everything.", True),  # (a) no terminal punctuation
        ("He left.", "and never came back.", True),  # (c) next starts lowercase
        ("Then we got fire and everything changed.", "Fire didn't just push.", False),
        ('She said "stop."', "Nobody listened.", False),
        ("Is this real?", None, False),
        ("The watch.", "And people used it.", False),
        ("He said “", "Stop.", True),  # (a) opening curly quote → fragment
        ('He said “stop.”', "Nobody moved.", False),  # (a) closing curly quote → complete
    ],
)
def test_is_likely_fragment(text, next_text, expected):
    assert is_likely_fragment(text, next_text) is expected


def test_fragment_hints_returns_line_numbers():
    lines = [
        TimedLine(1, 0.0, 1.0, "Historian Roger E."),
        TimedLine(2, 1.0, 2.0, "Kirch went digging."),
        TimedLine(3, 2.0, 3.0, "The end."),
    ]
    assert fragment_hints(lines) == [1]
