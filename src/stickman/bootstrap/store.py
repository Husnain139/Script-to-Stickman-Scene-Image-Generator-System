"""Bootstrap's candidates and approvals (spec §8.1), in library/_bootstrap/v<style_version>/.

bootstrap.json lists every candidate with its QC result and each step's approved candidate. Like a
history image, each candidate PNG carries its own record, so one saved just before a kill is added back.
Written only through BootstrapStore, safely, after every change.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from stickman.config_files import MascotConfig
from stickman.fsutil import safe_write
from stickman.library import anchor_path, anchor_ref_path
from stickman.qc.decide import QCResult
from stickman.render.images import read_metadata

BOOTSTRAP_DIR = "library/_bootstrap"
STATE_FILE = "bootstrap.json"
STEPS = ("anchor", "mascot")
Step = Literal["anchor", "mascot"]
_NAME = re.compile(r"^c(?P<n>[1-9]\d*)\.png$")


class BootstrapError(Exception):
    """bootstrap.json can't be read (CLI exit code 1). Nothing is written when this is raised."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Candidate(_Model):
    n: int = Field(ge=1)
    file: str  # relative to the workspace: library/_bootstrap/v1/anchor/c1.png
    seed: int
    model: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    prompt_sent: str
    refs: list[str] = Field(default_factory=list)  # "<path>#sha256:<hex>" per reference image, slot order
    qc: QCResult | None = None  # null until checked
    est_cost_usd: float = Field(ge=0)
    latency_s: float = Field(ge=0)
    created: AwareDatetime


class StepState(_Model):
    candidates: list[Candidate] = Field(default_factory=list)
    approved: int | None = None  # the approved candidate's n

    def candidate(self, n: int) -> Candidate | None:
        return next((c for c in self.candidates if c.n == n), None)


class BootstrapState(_Model):
    schema_version: Literal[1] = 1
    style_version: int = Field(ge=1)
    anchor: StepState = Field(default_factory=StepState)
    mascot: StepState = Field(default_factory=StepState)


class BootstrapStore:
    def __init__(self, workspace: Path, state: BootstrapState) -> None:
        self.workspace = workspace
        self.state = state

    @property
    def folder(self) -> Path:
        return self.workspace / BOOTSTRAP_DIR / f"v{self.state.style_version}"

    @property
    def path(self) -> Path:
        return self.folder / STATE_FILE

    @classmethod
    def load(cls, workspace: Path, style_version: int) -> BootstrapStore:
        store = cls(workspace, BootstrapState(style_version=style_version))
        if store.path.exists():
            try:
                store.state = BootstrapState.model_validate_json(store.path.read_bytes())
            except (ValidationError, ValueError, OSError) as exc:
                raise BootstrapError(f"{store.path}: {exc}") from exc
        return store

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)  # safe_write doesn't create folders
        safe_write(self.path, self.state.model_dump_json(indent=2).encode("utf-8"))

    def step(self, step: Step) -> StepState:
        return self.state.anchor if step == "anchor" else self.state.mascot

    def candidate_path(self, step: Step, n: int) -> Path:
        return self.folder / step / f"c{n}.png"

    def relative(self, path: Path) -> str:
        return path.relative_to(self.workspace).as_posix()

    def _files(self, step: Step) -> dict[int, Path]:
        folder = self.folder / step
        found: dict[int, Path] = {}
        if folder.is_dir():
            for path in folder.iterdir():
                match = _NAME.match(path.name)
                if match:
                    found[int(match["n"])] = path
        return found

    def next_number(self, step: Step) -> int:
        """One above every recorded candidate and every file, so a file a killed run left is never overwritten."""
        used = {c.n for c in self.step(step).candidates} | set(self._files(step))
        return max(used, default=0) + 1

    def add(self, step: Step, candidate: Candidate) -> None:
        state = self.step(step)
        state.candidates = sorted([*state.candidates, candidate], key=lambda c: c.n)
        self.save()

    def set_qc(self, step: Step, n: int, qc: QCResult) -> None:
        candidate = self.step(step).candidate(n)
        if candidate is None:
            raise KeyError(f"no {step} candidate {n}")
        candidate.qc = qc
        self.save()

    def approve(self, step: Step, n: int) -> None:
        if self.step(step).candidate(n) is None:
            raise KeyError(f"no {step} candidate {n}")
        self.step(step).approved = n
        self.save()

    def recover(self) -> list[str]:
        """Remove what an interrupted write left, and add back candidates saved just before a kill."""
        notes: list[str] = []
        self.path.with_name(STATE_FILE + ".tmp").unlink(missing_ok=True)
        adopted: list[str] = []
        unknown: list[str] = []
        for step in STEPS:
            folder = self.folder / step
            if folder.is_dir():
                for leftover in folder.glob("*.tmp"):
                    leftover.unlink(missing_ok=True)
            known = {c.n for c in self.step(step).candidates}
            for n, path in sorted(self._files(step).items()):
                if n in known:
                    continue
                candidate = self._saved(path, n)
                if candidate is None:
                    unknown.append(self.relative(path))
                    continue
                self.step(step).candidates = sorted([*self.step(step).candidates, candidate], key=lambda c: c.n)
                adopted.append(self.relative(path))
        if adopted:
            self.save()
            notes.append(f"Kept {len(adopted)} candidate(s) saved just before a run stopped: {', '.join(adopted)}")
        if unknown:
            notes.append(f"Left as they are (no record inside): {', '.join(unknown)}")
        return notes

    def _saved(self, path: Path, n: int) -> Candidate | None:
        metadata = read_metadata(path)
        if metadata is None:
            return None
        try:
            candidate = Candidate.model_validate(metadata)
        except ValidationError:
            return None
        if candidate.n != n or candidate.file != self.relative(path):
            return None
        return candidate.model_copy(update={"qc": None})


def ranked(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Best first: passed, then the highest idea score, then checked before unchecked, then the oldest."""
    def key(c: Candidate) -> tuple[bool, int, bool, int]:
        return (c.qc is not None and c.qc.passed, c.qc.score if c.qc is not None else 0, c.qc is not None, -c.n)

    return sorted(candidates, key=key, reverse=True)


def anchor_done(workspace: Path, style_version: int) -> bool:
    return anchor_path(workspace, style_version).is_file() and anchor_ref_path(workspace, style_version).is_file()


def mascot_done(workspace: Path, mascot: MascotConfig, style_version: int) -> bool:
    """Approved for this style version: the seed is set at approval, and both files exist (spec §8.1)."""
    return (
        mascot.seed is not None
        and mascot.style_version == style_version
        and (workspace / mascot.sheet).is_file()
        and (workspace / mascot.ref).is_file()
    )


def pending_step(workspace: Path, mascot: MascotConfig, style_version: int) -> Step | None:
    if not anchor_done(workspace, style_version):
        return "anchor"
    if not mascot_done(workspace, mascot, style_version):
        return "mascot"
    return None
