"""state.json: the tool's data about a project's images (spec §5.2). Written only by the tool, safely."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from stickman.fsutil import safe_write
from stickman.qc.decide import QCResult
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
    qc: QCResult | None = None  # [M4] null until QC has checked this version
    est_cost_usd: float = Field(ge=0)
    latency_s: float = Field(ge=0)
    created: AwareDatetime


class UnitState(_Model):
    status: UnitStatus = "planned"
    current_version: int | None = None
    approved_version: int | None = None
    versions: list[Version] = Field(default_factory=list)
    error: str | None = None  # [M3] the last API error of a unit that ended failed
    compare_with: int | None = None  # [M6] shown beside the current version after a regeneration, until one is chosen

    def version(self, v: int) -> Version | None:
        return next((item for item in self.versions if item.v == v), None)


TO_RENDER = ("planned", "failed")


def unchecked(unit: UnitState) -> bool:
    """The unit's current image has no QC result: made before M4, or saved just before a kill."""
    version = unit.version(unit.current_version) if unit.current_version is not None else None
    return version is not None and version.qc is None


def needs_work(unit: UnitState) -> bool:
    """The units a run takes up: no image yet or a failed one, or a generated image QC hasn't checked."""
    return unit.status in TO_RENDER or (unit.status == "generated" and unchecked(unit))


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

    def set_qc(self, unit_id: str, v: int, qc: QCResult) -> None:
        version = self.unit(unit_id).version(v)
        if version is None:
            raise KeyError(f"{unit_id} has no version {v}")
        version.qc = qc
        self.save()

    def finish(
        self, unit_id: str, status: UnitStatus, *, current: int | None, clear_current: bool = False,
        error: str | None = None,
    ) -> None:
        """A unit's final status after QC. `current` None keeps the current version as it is, unless
        `clear_current`: then the unit has none (no version shows its latest design); the versions stay."""
        unit = self.unit(unit_id)
        unit.status = status
        if clear_current:
            unit.current_version = None
        elif current is not None:
            unit.current_version = current
        unit.error = error
        self.save()

    def approve(self, unit_id: str) -> None:
        unit = self.unit(unit_id)
        if unit.current_version is None:
            raise ValueError(f"{unit_id} has no image to approve")
        unit.approved_version = unit.current_version
        unit.status = "approved"
        unit.compare_with = None
        unit.error = None
        self.save()

    def select_version(self, unit_id: str, v: int) -> None:
        """Make version v current (a history pick, or 1/2 in the side-by-side view, spec §12.2). The
        approval stays only if v is the approved version; otherwise the status comes from v's QC."""
        unit = self.unit(unit_id)
        version = unit.version(v)
        if version is None:
            raise KeyError(f"{unit_id} has no version {v}")
        unit.current_version = v
        unit.compare_with = None
        unit.error = None
        if unit.approved_version is not None and unit.approved_version != v:
            unit.approved_version = None
        if unit.approved_version == v:
            unit.status = "approved"
        elif version.qc is not None and not version.qc.passed:
            unit.status = "needs_review"
        else:
            unit.status = "generated"  # an unchecked version is checked by the next run
        self.save()

    def begin_regeneration(self, unit_id: str) -> None:
        """Before a regeneration: the current version is kept to compare with the new one, and the
        approval is cleared, since the approved image won't be the one shown."""
        unit = self.unit(unit_id)
        unit.compare_with = unit.current_version
        unit.approved_version = None
        self.save()

    def next_version(self, unit_id: str) -> int:
        """One above every version in state.json and every file in images/_history, so an image
        that a killed run left behind is never overwritten."""
        used = {version.v for version in self.unit(unit_id).versions}
        used |= set(history_files(self.project_dir).get(unit_id, {}))
        return max(used, default=0) + 1
