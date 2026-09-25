"""A unit's fingerprint: what its image was made from (spec §10.4)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from stickman.plan.models import PlanUnit

FINGERPRINT_FIELDS: tuple[str, ...] = (
    "visual_idea",
    "visual_type",
    "shot",
    "time_of_day",
    "characters",
    "mood",
    "setting",
    "props",
    "composition",
    "energy_marks",
)


def canonical_json(data: object) -> str:
    """Sorted keys, UTF-8 and no whitespace (spec §10.4)."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(
    unit: PlanUnit,
    *,
    cast_descriptions: Mapping[str, str],
    model: str,
    aspect: str,
    size: tuple[int, int],
    style_version: int,
    reference_hashes: Sequence[str],
) -> str:
    """From the parsed plan, so comments and formatting never change it. `reference_hashes` are the
    sha256 of each reference file sent, in slot order."""
    drawn = sorted({character.ref for character in unit.characters})
    payload = {
        **unit.model_dump(mode="json", include=set(FINGERPRINT_FIELDS)),
        "cast": {ref: cast_descriptions[ref] for ref in drawn},
        "image_prompt": unit.image_prompt,
        "seed": unit.seed,
        "model": model,
        "aspect": aspect,
        "width": size[0],
        "height": size[1],
        "style_version": style_version,
        "references": list(reference_hashes),
    }
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
