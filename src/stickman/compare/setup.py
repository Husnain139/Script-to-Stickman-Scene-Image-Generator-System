"""A model comparison's folder (spec §14.4 [M5]): projects/<date>_compare_<source slug>/.

    compare.json        the source project, the picked units, the runs
    source_plan.yaml    the source's plan.yaml as it was when the comparison started (never written again)
    runs/<run id>/      state.json, images/, logs/ of one model/size/reference setting
    export/compare.html the report

There is no plan.yaml at the top, so resolve_project never takes the folder for a project.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from stickman.fsutil import safe_write
from stickman.plan.models import Plan, PlanValidationError
from stickman.plan.store import load_plan
from stickman.settings import Aspect, Settings

COMPARE_FILE = "compare.json"
FROZEN_PLAN = "source_plan.yaml"
RUNS_DIR = "runs"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
# plan.md's small-size candidate: about 44% of 1920x1088's area; both sides are multiples of 16.
SMALL_SIZES: dict[Aspect, tuple[int, int]] = {"16:9": (1280, 720), "9:16": (720, 1280)}


class CompareError(Exception):
    """A comparison's files can't be read (CLI exit code 1)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompareRun(_Model):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    model: str
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    references: bool


class ComparePick(_Model):
    category: str
    unit: str
    filled: bool = False


class CompareSetup(_Model):
    schema_version: Literal[1] = 1
    source: str  # the source project's folder name
    created: AwareDatetime
    picks: list[ComparePick]
    runs: list[CompareRun]


def default_runs(settings: Settings, aspect: Aspect) -> list[CompareRun]:
    """Klein 4B at the project's size with and without references, and at the small size with them
    (the user's choice, 2026-09-26: Klein 9B is left out)."""
    width, height = settings.image.sizes[aspect]
    small_w, small_h = SMALL_SIZES[aspect]
    return [
        CompareRun(id="klein-4b-refs", model=KLEIN_4B, width=width, height=height, references=True),
        CompareRun(id="klein-4b-no-refs", model=KLEIN_4B, width=width, height=height, references=False),
        CompareRun(id="klein-4b-small-refs", model=KLEIN_4B, width=small_w, height=small_h, references=True),
    ]


def compare_dir(workspace: Path, source: Path, day: date) -> Path:
    """A new folder's path: -2, -3 … is added when one of that name exists."""
    slug = source.name.split("_", 1)[-1]
    base = workspace / "projects" / f"{day.isoformat()}_compare_{slug}"
    folder, number = base, 1
    while folder.exists():
        number += 1
        folder = base.with_name(f"{base.name}-{number}")
    return folder


def _read_setup(folder: Path) -> CompareSetup:
    try:
        return CompareSetup.model_validate_json((folder / COMPARE_FILE).read_bytes())
    except (ValidationError, ValueError, OSError) as exc:
        raise CompareError(f"{folder / COMPARE_FILE}: {exc}") from exc


def find_compare(workspace: Path, source: Path) -> Path | None:
    """The newest comparison of this source project."""
    projects = workspace / "projects"
    found: list[Path] = []
    if projects.is_dir():
        for folder in projects.glob("*_compare_*"):
            if not (folder / COMPARE_FILE).is_file():
                continue
            try:
                setup = _read_setup(folder)
            except CompareError:
                continue
            if setup.source == source.name:
                found.append(folder)
    return max(found, key=lambda folder: (folder.stat().st_mtime, folder.name)) if found else None


def create_compare(folder: Path, source: Path, setup: CompareSetup) -> None:
    """The frozen plan first, then compare.json, whose presence marks a finished setup. A folder that
    exists (another run created it first) raises FileExistsError, and nothing is written."""
    folder.mkdir(parents=True, exist_ok=False)
    safe_write(folder / FROZEN_PLAN, (source / "plan.yaml").read_bytes())
    safe_write(folder / COMPARE_FILE, setup.model_dump_json(indent=2).encode("utf-8"))


def load_compare(folder: Path, *, library_ids: Collection[str] | None) -> tuple[CompareSetup, Plan]:
    setup = _read_setup(folder)
    try:
        plan = load_plan(folder / FROZEN_PLAN, library_ids=library_ids).plan
    except (PlanValidationError, OSError) as exc:
        raise CompareError(f"{folder / FROZEN_PLAN}: {exc}") from exc
    return setup, plan
