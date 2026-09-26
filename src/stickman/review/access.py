"""How the review server shares a project (spec §3, §12.1). One page job runs at a time and holds the
project's .lock for its whole length. While it runs, page actions change the job's own StateStore, so neither
overwrites the other. Otherwise an action reads state.json fresh and takes the lock just for the write, so a
CLI run is never overwritten: a lock another process holds is Busy (HTTP 409).

ProjectAccess is the only part of the server that takes locks (the project's and bootstrap's). It remembers the
ones this process holds and refuses to take one of them again: ProjectLock alone treats a lock with its own PID
as stale (that rule is for a PID reused by another process), so a second taker in this process would steal it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stickman.render.lock import LOCK_FILE, LockHeld, ProjectLock
from stickman.render.state import ProjectState, StateStore

VERBS = {"regenerate": "regenerating", "replan": "replanning", "candidates": "making candidates for"}
NOUNS = {"regenerate": "regeneration of {}", "replan": "replan of {}", "candidates": "{} candidates"}


class Busy(Exception):
    """Another page job is running, or another process holds the lock (HTTP 409)."""


@dataclass
class JobInfo:
    kind: str  # regenerate | replan | candidates
    unit: str | None  # the unit, or the bootstrap step for candidates
    message: str = ""

    def describe(self) -> str:
        return f"{VERBS[self.kind]} {self.unit}" if self.unit else VERBS[self.kind]

    def noun(self) -> str:
        """The job as a noun, like "regeneration of 006a" or "anchor candidates"."""
        return NOUNS[self.kind].format(self.unit or "").strip()

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "unit": self.unit, "message": self.message}


class ProjectAccess:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.job: JobInfo | None = None
        self.store: StateStore | None = None  # the running job's store, which page actions share
        self._held: dict[Path, ProjectLock] = {}  # the locks this process holds, by lock file
        self._job_holds_project = False

    @property
    def busy_unit(self) -> str | None:
        return self.job.unit if self.job is not None and self.job.kind == "regenerate" else None

    @staticmethod
    def _key(folder: Path) -> Path:
        return (folder / LOCK_FILE).resolve()

    def hold(self, folder: Path) -> None:
        """Take `folder`'s .lock until drop(folder), or raise Busy: another process holds it, or this one does."""
        key = self._key(folder)
        if key in self._held:
            doing = f"already {self.job.describe()}" if self.job is not None else "already using it"
            raise Busy(f"the page is {doing}; wait for it to finish")
        lock = ProjectLock(folder)
        try:
            lock.acquire()
        except LockHeld as exc:
            raise Busy(f"{exc}; try again when it finishes") from None
        self._held[key] = lock

    def drop(self, folder: Path) -> None:
        lock = self._held.pop(self._key(folder), None)
        if lock is not None:
            lock.release()

    @contextmanager
    def locked(self, folder: Path) -> Iterator[None]:
        self.hold(folder)
        try:
            yield
        finally:
            self.drop(folder)

    def claim(self, kind: str, unit: str | None, *, project: bool = True) -> JobInfo:
        """Start a job now, or raise Busy. `project`: hold the project's lock (and share its store) until release()."""
        if self.job is not None:
            raise Busy(f"the page is already {self.job.describe()}; wait for it to finish")
        if project:
            self.hold(self.project_dir)
            try:
                self.store = StateStore.load(self.project_dir)
            except Exception:
                self.drop(self.project_dir)
                raise
            self._job_holds_project = True
        self.job = JobInfo(kind, unit)
        return self.job

    def release(self) -> None:
        self.job = None
        self.store = None
        if self._job_holds_project:
            self._job_holds_project = False
            self.drop(self.project_dir)

    @contextmanager
    def state(self) -> Iterator[StateStore]:
        """The StateStore a page action changes. Actions are synchronous, so nothing else runs meanwhile."""
        if self.store is not None:
            yield self.store
            return
        with self.locked(self.project_dir):
            yield StateStore.load(self.project_dir)

    def read(self) -> ProjectState:
        return self.store.state if self.store is not None else StateStore.load(self.project_dir).state
