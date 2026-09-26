"""Approving a bootstrap candidate (spec §8.1): copy it into the library, make its reference copy
(spec §8.3), and for the mascot record its seed and model in config/mascot.yaml.

Everything is checked before anything is written. The files are written before the approval is recorded,
so a kill in between is put right by approving again. The mascot counts as approved once mascot.yaml has
its seed (bootstrap.store.mascot_done); its sheet must have been made with the approved anchor as it is now.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from pydantic import ValidationError
from ruamel.yaml import YAML

from stickman.bootstrap.store import BootstrapStore, Step, anchor_done, made_with
from stickman.config_files import MascotConfig
from stickman.fsutil import safe_write
from stickman.library import anchor_path, anchor_ref_path, reference_copy
from stickman.render.images import ImageDecodeError, decode_image
from stickman.render.references import ReferenceFiles
from stickman.settings import default_config_text, read_config_text


class ApprovalError(Exception):
    """A candidate can't be approved (CLI exit code 1). Nothing is written when this is raised."""


def _source(store: BootstrapStore, step: Step, n: int) -> tuple[bytes, Image.Image]:
    candidate = store.step(step).candidate(n)
    if candidate is None:
        numbers = ", ".join(str(c.n) for c in store.step(step).candidates) or "none yet"
        raise ApprovalError(f"no {step} candidate {n} (candidates: {numbers})")
    path = store.workspace / candidate.file
    try:
        data = path.read_bytes()
        return data, decode_image(data)
    except (OSError, ImageDecodeError) as exc:
        raise ApprovalError(f"can't read {candidate.file}: {exc}") from exc


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, data)


def approve_anchor(store: BootstrapStore, n: int, *, ref_max_side: int) -> list[Path]:
    data, image = _source(store, "anchor", n)
    version = store.state.style_version
    full, ref = anchor_path(store.workspace, version), anchor_ref_path(store.workspace, version)
    _write(full, data)  # the candidate as it is, with its record inside
    _write(ref, reference_copy(image, ref_max_side))
    store.approve("anchor", n)
    return [full, ref]


def mascot_version_mismatch(mascot: MascotConfig, style_version: int) -> str | None:
    """Why the mascot can't be made or approved for this style version, or None when mascot.yaml matches."""
    if mascot.style_version == style_version:
        return None
    return (
        f"config/mascot.yaml has style_version {mascot.style_version}, but bootstrap is for style v{style_version}; "
        f"set style_version: {style_version} there first"
    )


def approve_mascot(store: BootstrapStore, n: int, *, mascot: MascotConfig, ref_max_side: int) -> list[Path]:
    """Everything is checked, and mascot.yaml's new text validated, before any file is written. The anchor's
    reference copy is read through ReferenceFiles (a ConfigError if it can't be sent), so its label is the one
    the candidates were made with."""
    version = store.state.style_version
    if not anchor_done(store.workspace, version):
        raise ApprovalError("approve the style anchor first: `stickman bootstrap --approve-anchor <N>`")
    mismatch = mascot_version_mismatch(mascot, version)
    if mismatch is not None:
        raise ApprovalError(mismatch)
    data, image = _source(store, "mascot", n)
    candidate = store.step("mascot").candidate(n)
    anchor = ReferenceFiles(store.workspace, ref_max_side=ref_max_side).load(anchor_ref_path(store.workspace, version))
    if not made_with(candidate, anchor.label):
        raise ApprovalError(
            f"c{n} was made with a previous anchor; run `stickman bootstrap` to make mascot sheet candidates "
            "with the approved one"
        )
    text = mascot_approval_text(store.workspace, seed=candidate.seed, model=candidate.model)
    sheet, ref = store.workspace / mascot.sheet, store.workspace / mascot.ref
    _write(sheet, data)
    _write(ref, reference_copy(image, ref_max_side))
    write_mascot_yaml(store.workspace, text)
    store.approve("mascot", n)
    return [sheet, ref, mascot_yaml_path(store.workspace)]


def mascot_yaml_path(workspace: Path) -> Path:
    return workspace / "config" / "mascot.yaml"


def mascot_approval_text(workspace: Path, *, seed: int, model: str) -> str:
    """config/mascot.yaml's text with seed and model set, comments and key order kept (ruamel round-trip),
    made from the packaged default when the file is missing. Nothing is written. An invalid result raises
    ApprovalError; a file that can't be read raises ConfigError."""
    path = mascot_yaml_path(workspace)
    text = read_config_text(path) if path.exists() else default_config_text("mascot.yaml")
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    doc = yaml.load(text.replace("\r\n", "\n"))
    doc["seed"] = seed
    doc["model"] = model
    buffer = io.StringIO()
    yaml.dump(doc, buffer)
    result = buffer.getvalue()
    try:
        MascotConfig.model_validate(YAML(typ="safe").load(result))
    except ValidationError as exc:
        raise ApprovalError(f"{path}: the approved mascot would not be valid: {exc}") from exc
    return result


def write_mascot_yaml(workspace: Path, text: str) -> None:
    path = mascot_yaml_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, text.encode("utf-8"))


def set_mascot_approval(workspace: Path, *, seed: int, model: str) -> None:
    """seed and model in config/mascot.yaml. An invalid result is never written."""
    write_mascot_yaml(workspace, mascot_approval_text(workspace, seed=seed, model=model))
