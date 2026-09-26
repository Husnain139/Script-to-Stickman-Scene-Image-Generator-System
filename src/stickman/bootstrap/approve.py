"""Approving a bootstrap candidate (spec §8.1): copy it into the library, make its reference copy
(spec §8.3), and for the mascot record its seed and model in config/mascot.yaml.

The files are written before the approval is recorded, so a kill in between is put right by approving
again. The mascot counts as approved once mascot.yaml has its seed (bootstrap.store.mascot_done).
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from pydantic import ValidationError
from ruamel.yaml import YAML

from stickman.bootstrap.store import BootstrapStore, Step, anchor_done
from stickman.config_files import MascotConfig
from stickman.fsutil import safe_write
from stickman.library import anchor_path, anchor_ref_path, reference_copy
from stickman.render.images import ImageDecodeError, decode_image
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


def approve_mascot(store: BootstrapStore, n: int, *, mascot: MascotConfig, ref_max_side: int) -> list[Path]:
    version = store.state.style_version
    if not anchor_done(store.workspace, version):
        raise ApprovalError("approve the style anchor first: `stickman bootstrap --approve-anchor <N>`")
    if mascot.style_version != version:
        raise ApprovalError(
            f"config/mascot.yaml has style_version {mascot.style_version}, but bootstrap is for style v{version}; "
            f"set style_version: {version} there first"
        )
    data, image = _source(store, "mascot", n)
    candidate = store.step("mascot").candidate(n)
    sheet, ref = store.workspace / mascot.sheet, store.workspace / mascot.ref
    _write(sheet, data)
    _write(ref, reference_copy(image, ref_max_side))
    set_mascot_approval(store.workspace, seed=candidate.seed, model=candidate.model)
    store.approve("mascot", n)
    return [sheet, ref, store.workspace / "config" / "mascot.yaml"]


def set_mascot_approval(workspace: Path, *, seed: int, model: str) -> None:
    """seed and model in config/mascot.yaml, with comments and key order kept (ruamel round-trip). The
    file is made from the packaged default when it's missing. An invalid result is never written."""
    path = workspace / "config" / "mascot.yaml"
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
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, result.encode("utf-8"))
