"""Reading and writing plan.yaml with ruamel round-trip mode (spec §5.1, §12.4).

Your comments and field order survive every write the tool makes. Every write is
validated first, is refused if the file changed on disk since it was loaded, and is
written safely (temp file, fsync, replace).
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString

from stickman.fsutil import safe_write
from stickman.plan.models import VISUAL_FIELDS, Plan, PlanValidationError, parse_plan

UNIT_KEYS: tuple[str, ...] = (
    "id", "part", "start", "end", "source_text", "corrected_text",
    *VISUAL_FIELDS,
    "seed", "image_prompt", "prompt_locked",
)
_FLOW_LISTS = frozenset({"lines", "candidates", "setting", "props", "energy_marks"})
_FLOW_MAPS = frozenset({"split", "characters[]"})
_DOUBLE_QUOTED = frozenset({"aspect"})


class PlanChangedError(Exception):
    """plan.yaml changed on disk since it was loaded, so nothing was written."""


@dataclass
class LoadedPlan:
    plan: Plan
    doc: CommentedMap
    hash: str


def file_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _represent_null(representer: Any, _value: None) -> Any:
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


def _yaml() -> YAML:
    yaml = YAML()
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    yaml.representer.add_representer(type(None), _represent_null)
    return yaml


def _node(value: Any, key: str | None = None) -> Any:
    """Plain data -> ruamel nodes, in the flow style the spec's layout uses (§5.1)."""
    if isinstance(value, dict):
        node = CommentedMap()
        for k, v in value.items():
            node[k] = _node(v, k)
        if key in _FLOW_MAPS:
            node.fa.set_flow_style()
        return node
    if isinstance(value, list):
        item_key = "characters[]" if key == "characters" else None
        seq = CommentedSeq(_node(item, item_key) for item in value)
        if key in _FLOW_LISTS:
            seq.fa.set_flow_style()
        return seq
    if isinstance(value, str):
        if key == "image_prompt" and "\n" in value:
            return LiteralScalarString(value)
        if key in _DOUBLE_QUOTED:
            return DoubleQuotedScalarString(value)
    return value


def _plain(node: Any) -> Any:
    """ruamel nodes -> plain Python data for Pydantic."""
    if isinstance(node, Mapping):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(item) for item in node]
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, str):
        return str(node)
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    return node


def to_document(plan: Plan) -> CommentedMap:
    data = plan.model_dump(mode="json", by_alias=True)
    for scene in data["scenes"]:
        scene["split"] = {k: v for k, v in scene["split"].items() if v is not None and v != []}
        scene["units"] = [{key: unit[key] for key in UNIT_KEYS} for unit in scene["units"]]
    return _node(data)


def dump_document(doc: CommentedMap) -> str:
    buffer = io.StringIO()
    _yaml().dump(doc, buffer)
    return buffer.getvalue()


def _load_document(text: str) -> CommentedMap:
    try:
        # CRLF is normalised first: ruamel keeps the CR inside comments otherwise.
        doc = _yaml().load(text.replace("\r\n", "\n"))
    except YAMLError as exc:
        raise PlanValidationError([f"invalid YAML: {exc}"]) from exc
    if not isinstance(doc, CommentedMap):
        raise PlanValidationError(["(root): plan.yaml must be a mapping of keys"])
    return doc


def load_plan(path: Path, *, library_ids: Collection[str] | None = None) -> LoadedPlan:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PlanValidationError([f"plan.yaml is not UTF-8: {exc}"]) from exc
    doc = _load_document(text)
    return LoadedPlan(parse_plan(_plain(doc), library_ids=library_ids), doc, file_hash(raw))


def find_unit_node(doc: CommentedMap, unit_id: str) -> CommentedMap:
    for scene in doc.get("scenes") or []:
        for unit in scene.get("units") or []:
            if unit.get("id") == unit_id:
                return unit
    raise KeyError(unit_id)


def update_unit(doc: CommentedMap, unit_id: str, fields: Mapping[str, Any]) -> None:
    node = find_unit_node(doc, unit_id)
    for key, value in fields.items():
        node[key] = _node(value, key)


def write_plan(path: Path, doc: CommentedMap, *, expected_hash: str | None) -> str:
    text = dump_document(doc)
    parse_plan(_plain(_load_document(text)))  # never write an invalid plan
    if expected_hash is not None:
        current = file_hash(path.read_bytes()) if path.exists() else None
        if current != expected_hash:
            raise PlanChangedError(f"{path.name} changed on disk since it was loaded")
    data = text.encode("utf-8")
    safe_write(path, data)
    return file_hash(data)
