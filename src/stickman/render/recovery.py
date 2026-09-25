"""Putting a project in order before a run (spec §5.2, §10.4, §16)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from stickman.fsutil import safe_write
from stickman.render.images import HISTORY_DIR, history_files, read_metadata
from stickman.render.state import STATE_FILE, StateStore, Version

HAS_IMAGE = ("generated", "needs_review", "approved", "stale")


@dataclass(frozen=True)
class ExpectedUnit:
    """What a plan unit's files should be now: its current image's name, and the fingerprint an
    image made now would have (spec §10.4)."""

    stem: str
    fingerprint: str


def recover(store: StateStore, expected: Mapping[str, ExpectedUnit]) -> list[str]:
    """Undo what a crashed or killed run left behind, then mark stale units. Returns notes to print."""
    notes: list[str] = []
    _remove_temp_files(store.project_dir)
    for unit_id in expected:
        store.unit(unit_id)
    stuck = sorted(unit_id for unit_id, unit in store.state.units.items() if unit.status == "generating")
    for unit_id in stuck:
        store.state.units[unit_id].status = "planned"
    if stuck:
        notes.append(f"Reset {len(stuck)} unit(s) a stopped run left generating: {', '.join(stuck)}")
    adopted, unknown = _adopt_saved_images(store)
    if adopted:
        notes.append(f"Kept {len(adopted)} image(s) saved just before a run stopped: {', '.join(adopted)}")
    if unknown:
        notes.append(f"Left as they are (no version record inside): {', '.join(unknown)}")
    missing = _restore_current_images(store, expected)
    if missing:
        notes.append(f"Missing image files: {', '.join(missing)}")
    stale, fresh = _mark_stale(store, expected)
    if stale:
        notes.append(f"Stale, the plan changed after the image was made (not regenerated automatically): {', '.join(stale)}")
    if fresh:
        notes.append(f"No longer stale: {', '.join(fresh)}")
    store.save()
    return notes


def _remove_temp_files(project: Path) -> None:
    """Leftovers of writes a kill interrupted. Only the generating process writes these (the .lock)."""
    (project / f"{STATE_FILE}.tmp").unlink(missing_ok=True)
    for folder in (project / "images", project / HISTORY_DIR):
        if folder.is_dir():
            for path in folder.glob("*.tmp"):
                path.unlink(missing_ok=True)


def _adopt_saved_images(store: StateStore) -> tuple[list[str], list[str]]:
    """History images that state.json doesn't list: a kill landed between saving the image and
    saving the state. Each is added from the version record inside it."""
    adopted: list[str] = []
    unknown: list[str] = []
    for unit_id, files in sorted(history_files(store.project_dir).items()):
        unit = store.unit(unit_id)
        known = {version.v for version in unit.versions}
        found: list[Version] = []
        for v, path in sorted(files.items()):
            if v in known:
                continue
            version = _saved_version(path, v)
            if version is None:
                unknown.append(path.name)
            else:
                found.append(version)
                adopted.append(path.name)
        if not found:
            continue
        unit.versions = sorted([*unit.versions, *found], key=lambda item: item.v)
        if unit.status in ("planned", "failed"):
            unit.current_version = found[-1].v
            unit.status = "generated"  # M4: its QC result decides
            unit.error = None
    return adopted, unknown


def _saved_version(path: Path, v: int) -> Version | None:
    metadata = read_metadata(path)
    if metadata is None:
        return None
    try:
        version = Version.model_validate(metadata)
    except ValidationError:
        return None
    if version.v != v or version.file != f"{HISTORY_DIR}/{path.name}":
        return None
    return version


def _restore_current_images(store: StateStore, expected: Mapping[str, ExpectedUnit]) -> list[str]:
    """images/<stem>.png is a copy of the unit's current version (spec §3)."""
    missing: list[str] = []
    for unit_id, want in expected.items():
        unit = store.state.units[unit_id]
        version = unit.version(unit.current_version) if unit.current_version is not None else None
        if version is None:
            continue
        source = store.project_dir / version.file
        if not source.is_file():
            missing.append(version.file)
            continue
        data = source.read_bytes()
        target = store.project_dir / "images" / f"{want.stem}.png"
        if not target.is_file() or target.read_bytes() != data:
            safe_write(target, data)
    return missing


def _mark_stale(store: StateStore, expected: Mapping[str, ExpectedUnit]) -> tuple[list[str], list[str]]:
    """spec §10.4: compared with the approved version when there is one, else the current one."""
    stale: list[str] = []
    fresh: list[str] = []
    for unit_id, want in expected.items():
        unit = store.state.units[unit_id]
        if unit.status not in HAS_IMAGE:
            continue
        compared = unit.approved_version or unit.current_version
        version = unit.version(compared) if compared is not None else None
        if version is None:
            continue
        if version.fingerprint != want.fingerprint:
            if unit.status != "stale":
                unit.status = "stale"
                stale.append(unit_id)
        elif unit.status == "stale":
            # M4: needs_review when the current version failed QC
            unit.status = "approved" if unit.approved_version is not None else "generated"
            fresh.append(unit_id)
    return stale, fresh
