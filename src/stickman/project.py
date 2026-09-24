"""Project folders: projects/<YYYY-MM-DD>_<slug>/ (spec §3, §13)."""

from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import date
from pathlib import Path

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class ProjectError(Exception):
    """A project can't be created or found (CLI exit code 1)."""


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NOT_SLUG.sub("-", ascii_text.lower()).strip("-") or "project"


def project_dir(workspace: Path, slug: str, day: date) -> Path:
    return workspace / "projects" / f"{day.isoformat()}_{slug}"


def create_project(directory: Path, script: Path) -> None:
    """Create the folder and copy the script in. A folder whose planning never finished is reused."""
    if (directory / "plan.yaml").exists():
        raise ProjectError(
            f"{directory.name} already has a plan.yaml. Change units with `stickman replan`, or choose another --name."
        )
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(script, directory / "script.txt")


def resolve_project(workspace: Path, project: Path | None) -> Path:
    projects = workspace / "projects"
    if project is not None:
        for candidate in (project, workspace / project, projects / project):
            if (candidate / "plan.yaml").is_file():
                return candidate.resolve()
        raise ProjectError(f"No project with a plan.yaml at {project}.")
    planned = [d for d in projects.iterdir() if (d / "plan.yaml").is_file()] if projects.is_dir() else []
    if not planned:
        raise ProjectError("No planned projects yet. Create one with `stickman new <script> --aspect 16:9`.")
    return max(planned, key=_last_modified).resolve()


def _last_modified(directory: Path) -> float:
    paths = (directory, directory / "plan.yaml", directory / "state.json")
    return max(path.stat().st_mtime for path in paths if path.exists())
