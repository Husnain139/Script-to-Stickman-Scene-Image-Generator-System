"""The reference images sent with a unit's request (spec §7.4, §8.3; acceptance §18 #7 and #9)."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from stickman.config_files import MascotConfig
from stickman.library import LibraryCharacter, anchor_ref_path, character_ref_path
from stickman.plan.models import MASCOT, CastMember, MascotEntry
from stickman.settings import ConfigError

FORBIDDEN_DIR = "style_refs"  # the stock images: for humans only, never sent to any model (spec §2.2)


@dataclass(frozen=True)
class RefImage:
    path: str  # relative to the workspace, with forward slashes
    sha256: str  # "sha256:<hex>" of the file's bytes
    data: bytes
    size: tuple[int, int]

    @property
    def label(self) -> str:
        """How state.json and the run log name it (spec §5.2 `refs`)."""
        return f"{self.path}#{self.sha256}"


def reference_paths(
    workspace: Path,
    slots: Sequence[str] | None,
    *,
    style_version: int,
    mascot: MascotConfig,
    cast: Sequence[MascotEntry | CastMember],
    library: Sequence[LibraryCharacter],
) -> list[Path]:
    """Slot 0 is the style anchor, then one reference per cast id in `slots` (spec §7.4). `slots`
    is None when there is no anchor, and then nothing is sent."""
    if slots is None:
        return []
    members = {member.id: member for member in cast if isinstance(member, CastMember)}
    entries = {entry.id: entry for entry in library}
    paths = [anchor_ref_path(workspace, style_version)]
    for ref in slots:
        if ref == MASCOT:
            paths.append(workspace / mascot.ref)
        else:
            paths.append(character_ref_path(workspace, entries[str(members[ref].library_ref)]))
    return paths


class ReferenceFiles:
    """Reads each reference copy once per run, and refuses one that must never be sent."""

    def __init__(self, workspace: Path, *, ref_max_side: int) -> None:
        self._workspace = workspace.resolve()
        self._max_side = ref_max_side
        self._loaded: dict[Path, RefImage] = {}

    def load(self, path: Path) -> RefImage:
        resolved = path.resolve()
        if resolved not in self._loaded:
            self._loaded[resolved] = self._read(path, resolved)
        return self._loaded[resolved]

    def _read(self, path: Path, resolved: Path) -> RefImage:
        if resolved.is_relative_to(self._workspace / FORBIDDEN_DIR):
            raise ConfigError(f"{path}: images in {FORBIDDEN_DIR}/ are never sent to any API")
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            raise ConfigError(f"{path}: can't read the reference image: {exc.strerror or exc}") from exc
        try:
            with Image.open(io.BytesIO(data)) as image:
                size, kind = image.size, image.format
        except (OSError, SyntaxError, ValueError) as exc:
            raise ConfigError(f"{path}: not an image ({exc})") from exc
        if kind != "PNG":
            raise ConfigError(f"{path}: reference copies must be PNG, not {kind}")
        if max(size) > self._max_side:
            raise ConfigError(
                f"{path}: {size[0]}x{size[1]} is larger than image.ref_max_side ({self._max_side} px); "
                "reference copies are made by the tool"
            )
        inside = resolved.is_relative_to(self._workspace)
        shown = resolved.relative_to(self._workspace).as_posix() if inside else resolved.as_posix()
        return RefImage(shown, "sha256:" + hashlib.sha256(data).hexdigest(), data, size)
