"""Lenient JSON extraction and schema helpers for LLM calls (spec §9.3)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel


class NoJSONError(ValueError):
    """The reply holds no valid JSON object."""


def extract_json(text: str) -> Any:
    """Parse the JSON from the first `{` to the last `}` of a reply.

    This tolerates text or a ```json fence around it (gpt-oss and qwen both add some).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        raise NoJSONError("the reply contains no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise NoJSONError(f"the reply is not valid JSON: {exc.msg} at character {exc.pos}") from exc


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with every `$ref` replaced by its definition (for JSON mode)."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(definitions[node["$ref"].rsplit("/", 1)[-1]])
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def error_path(loc: Sequence[str | int]) -> str:
    """A Pydantic error location written like a YAML path: scenes[0].units[1].shot."""
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "(root)"
