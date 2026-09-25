"""state.json: the tool's data about a project's images (spec §5.2). Written only by the tool, safely."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from stickman.fsutil import safe_write
from stickman.render.images import history_files

STATE_FILE = "state.json"

UnitStatus = Literal["planned", "generating", "generated", "needs_review", "approved", "failed", "stale"]


class StateError(Exception):
    """state.json can't be read (CLI exit code 1). Nothing is written when this is raised."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Version(_Model):
    v: int = Field(ge=1)
    file: str  # relative to the project folder: images/_history/<unit>_v<N>.png
    seed: int
    model: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fingerprint: str
    refs: list[str] = Field(default_factory=list)  # "<path>#sha256:<hex>" per reference image, slot order
    prompt_sent: str
    retry_of: int | None = None
    retry_reason: str | None = None
    qc: dict[str, Any] | None = None  # M4
    est_cost_usd: float = Field(ge=0)
    latency_s: float = Field(ge=0)
    created: AwareDatetime


class UnitState(_Model):
    status: UnitStatus = "planned"
    current_version: int | None = None
    approved_version: int | None = None
    versions: list[Version] = Field(default_factory=list)
    error: str | None = None  # [M3] the last API error of a unit that ended failed

    def version(self, v: int) -> Version | None:
        return next((item for item in self.versions if item.v == v), None)


class ProjectState(_Model):
    schema_version: Literal[1] = 1
    plan_approved: bool = False
    sheets_approved: bool = False
    test_units: list[str] = Field(default_factory=list)
    tests_approved: bool = False
    units: dict[str, UnitState] = Field(default_factory=dict)


class StateStore:
    """state.json in memory. Every change is saved at once (spec §5.2)."""

    def __init__(self, project_dir: Path, state: ProjectState) -> None:
        self.project_dir = project_dir
        self.state = state

    @property
    def path(self) -> Path:
        return self.project_dir / STATE_FILE

    @classmethod
    def load(cls, project_dir: Path) -> StateStore:
        path = project_dir / STATE_FILE
        if not path.exists():
            return cls(project_dir, ProjectState())
        try:
            return cls(project_dir, ProjectState.model_validate_json(path.read_bytes()))
        except (ValidationError, ValueError, OSError) as exc:
            raise StateError(f"{path}: {exc}") from exc

    def save(self) -> None:
        safe_write(self.path, self.state.model_dump_json(indent=2).encode("utf-8"))

    def unit(self, unit_id: str) -> UnitState:
        return self.state.units.setdefault(unit_id, UnitState())

    def set_status(self, unit_id: str, status: UnitStatus, *, error: str | None = None) -> None:
        unit = self.unit(unit_id)
        unit.status = status
        unit.error = error
        self.save()

    def add_version(self, unit_id: str, version: Version, *, status: UnitStatus) -> None:
        unit = self.unit(unit_id)
        unit.versions = sorted([*unit.versions, version], key=lambda item: item.v)
        unit.current_version = version.v
        unit.status = status
        unit.error = None
        self.save()

    def next_version(self, unit_id: str) -> int:
        """One above every version in state.json and every file in images/_history, so an image
        that a killed run left behind is never overwritten."""
        used = {version.v for version in self.unit(unit_id).versions}
        used |= set(history_files(self.project_dir).get(unit_id, {}))
        return max(used, default=0) + 1
