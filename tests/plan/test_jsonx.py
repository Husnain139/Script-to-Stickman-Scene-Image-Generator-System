import json

import pytest
from pydantic import BaseModel

from stickman.plan.jsonx import NoJSONError, error_path, extract_json, inline_schema


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '\n\n{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! {"a": 1} Hope that helps.'],
)
def test_extract_json_tolerates_text_around_the_object(text):
    assert extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", '{"a": }'])
def test_extract_json_rejects_replies_without_valid_json(text):
    with pytest.raises(NoJSONError):
        extract_json(text)


class Inner(BaseModel):
    x: int


class Outer(BaseModel):
    items: list[Inner]
    best: Inner | None = None


def test_inline_schema_has_no_references():
    schema = inline_schema(Outer)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert schema["properties"]["items"]["items"]["properties"]["x"]["type"] == "integer"


def test_error_path_reads_like_yaml():
    assert error_path(("scenes", 0, "units", 1, "shot")) == "scenes[0].units[1].shot"
    assert error_path(()) == "(root)"
