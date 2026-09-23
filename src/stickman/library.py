"""The character library (spec §5.3). M2 only reads it; sheets are made in M5/M7."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from stickman.settings import ConfigError


class LibraryCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    figures: int = Field(ge=1)
    description: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    style_version: int = Field(ge=1)
    model: str
    seed: int | None = None
    sheet: str
    ref: str
    approved: date


def load_library(workspace: Path) -> list[LibraryCharacter]:
    root = workspace / "library" / "characters"
    if not root.is_dir():
        return []
    entries = []
    for path in sorted(root.glob("*/character.yaml")):
        try:
            data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
            entry = LibraryCharacter.model_validate(data)
        except (YAMLError, ValidationError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        if entry.id != path.parent.name:
            raise ConfigError(f"{path}: id {entry.id!r} must match its folder {path.parent.name!r}")
        entries.append(entry)
    return entries
