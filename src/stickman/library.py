"""The character library (spec §5.3). M2 only reads it; sheets are made in M5/M7."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from stickman.config_files import MascotConfig, read_config_text
from stickman.plan.models import MASCOT, CastMember, MascotEntry
from stickman.prompt.builder import ReferenceAvailability
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
            data = YAML(typ="safe").load(read_config_text(path)) or {}
            entry = LibraryCharacter.model_validate(data)
        except (YAMLError, ValidationError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        if entry.id != path.parent.name:
            raise ConfigError(f"{path}: id {entry.id!r} must match its folder {path.parent.name!r}")
        entries.append(entry)
    return entries


def anchor_ref_path(workspace: Path, style_version: int) -> Path:
    """The style anchor's reference copy (spec §2.2, §8.1)."""
    return workspace / "library" / "style" / f"anchor_v{style_version}_ref.png"


def character_ref_path(workspace: Path, entry: LibraryCharacter) -> Path:
    return workspace / "library" / "characters" / entry.id / entry.ref


def find_references(
    workspace: Path,
    *,
    use_references: bool,
    style_version: int,
    mascot: MascotConfig,
    cast: Sequence[MascotEntry | CastMember],
    library: Sequence[LibraryCharacter],
) -> ReferenceAvailability:
    """Which approved reference images exist for this style version (spec §7.4, §8.3)."""
    anchor = anchor_ref_path(workspace, style_version)
    if not use_references or not anchor.is_file():
        return ReferenceAvailability()
    sheets: set[str] = set()
    if mascot.seed is not None and mascot.style_version == style_version and (workspace / mascot.ref).is_file():
        sheets.add(MASCOT)
    entries = {entry.id: entry for entry in library}
    for member in cast:
        if not isinstance(member, CastMember) or member.library_ref is None:
            continue
        entry = entries.get(member.library_ref)
        if entry is None or entry.style_version != style_version:
            continue
        if character_ref_path(workspace, entry).is_file():
            sheets.add(member.id)
    return ReferenceAvailability(anchor=True, sheets=frozenset(sheets))
