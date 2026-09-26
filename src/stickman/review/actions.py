"""What the review page changes (spec §12.2, §12.4, §12.5). Each action raises ActionError(status, message),
which the server sends as that HTTP status with the message; nothing is changed when one is raised."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from stickman.bootstrap.approve import ApprovalError, approve_anchor, approve_mascot
from stickman.bootstrap.store import BootstrapError, BootstrapStore, bootstrap_folder
from stickman.config_files import MascotConfig, StyleConfig
from stickman.plan.models import PlanValidationError
from stickman.plan.store import PlanChangedError, load_plan, update_unit, write_plan
from stickman.render.lock import LockHeld, ProjectLock
from stickman.render.state import StateStore
from stickman.settings import ConfigError, Settings

PLAN_CHANGED = "plan.yaml changed on disk since this page loaded — reload before saving"
PLAN_HAS_ERRORS = "plan.yaml has validation errors; fix them first."
EXTRAS_LATER = "Sheets for extras come in M7; until then they're drawn from their description."
TESTS_LATER = "There are no test units yet: M7 picks them."


class ActionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _unit(unit_id: str, unit_ids: Collection[str], busy: str | None) -> None:
    if unit_id not in unit_ids:
        raise ActionError(404, f"No unit {unit_id} in this plan.")
    if busy == unit_id:
        raise ActionError(409, f"{unit_id} is being regenerated; wait for it to finish.")


def approve_unit(store: StateStore, unit_id: str, *, unit_ids: Collection[str], busy: str | None = None) -> None:
    _unit(unit_id, unit_ids, busy)
    if store.unit(unit_id).current_version is None:
        raise ActionError(409, f"{unit_id} has no image to approve.")
    store.approve(unit_id)


def approve_remaining(store: StateStore, statuses: Mapping[str, str], *, busy: str | None = None) -> list[str]:
    """spec §12.2: every unit showing `generated`; needs_review, stale and failed units are skipped."""
    approved: list[str] = []
    for unit_id, status in statuses.items():
        if status == "generated" and unit_id != busy and store.unit(unit_id).current_version is not None:
            store.approve(unit_id)
            approved.append(unit_id)
    return approved


def select_version(
    store: StateStore, unit_id: str, v: int, *, unit_ids: Collection[str], busy: str | None = None
) -> None:
    _unit(unit_id, unit_ids, busy)
    try:
        store.select_version(unit_id, v)
    except KeyError:
        raise ActionError(404, f"{unit_id} has no version {v}.") from None


def approve_plan(store: StateStore, errors: Sequence[str]) -> None:
    if errors:
        raise ActionError(409, PLAN_HAS_ERRORS)
    store.state.plan_approved = True
    store.save()


def approve_tests(store: StateStore) -> None:
    if not store.state.test_units:
        raise ActionError(409, TESTS_LATER)
    store.state.tests_approved = True
    store.save()


def edit_prompt(plan_path: Path, unit_id: str, prompt: str, plan_hash: str, *, library_ids: Collection[str]) -> str:
    """spec §12.4: refused (409) when plan.yaml changed on disk since `plan_hash` was read; otherwise the
    prompt is written with ruamel (comments kept) and prompt_locked: true. Returns the new file hash."""
    text = prompt.replace("\r\n", "\n")
    if not text.strip():
        raise ActionError(422, "The prompt can't be empty.")
    try:
        loaded = load_plan(plan_path, library_ids=library_ids)
    except PlanValidationError:
        raise ActionError(409, PLAN_HAS_ERRORS) from None
    if loaded.hash != plan_hash:
        raise ActionError(409, PLAN_CHANGED)
    if unit_id not in {unit.id for unit in loaded.plan.units()}:
        raise ActionError(404, f"No unit {unit_id} in this plan.")
    update_unit(loaded.doc, unit_id, {"image_prompt": text, "prompt_locked": True})
    try:
        return write_plan(plan_path, loaded.doc, expected_hash=loaded.hash)
    except PlanChangedError:
        raise ActionError(409, PLAN_CHANGED) from None
    except PlanValidationError as exc:
        raise ActionError(422, "; ".join(exc.errors[:3])) from None


def approve_sheet(
    workspace: Path, char_id: str, n: int, *, settings: Settings, style: StyleConfig, mascot: MascotConfig
) -> list[str]:
    """The anchor or the mascot sheet (spec §8.1), under bootstrap's lock. Extras' sheets come in M7."""
    if char_id not in ("anchor", "mascot"):
        raise ActionError(400, EXTRAS_LATER)
    folder = bootstrap_folder(workspace, style.style_version)
    folder.mkdir(parents=True, exist_ok=True)
    lock = ProjectLock(folder)
    try:
        lock.acquire()
    except LockHeld as exc:
        raise ActionError(409, f"{exc}; try again when it finishes.") from None
    try:
        store = BootstrapStore.load(workspace, style.style_version)
        max_side = settings.image.ref_max_side
        if char_id == "anchor":
            written = approve_anchor(store, n, ref_max_side=max_side)
        else:
            written = approve_mascot(store, n, mascot=mascot, ref_max_side=max_side)
    except (ApprovalError, BootstrapError, ConfigError) as exc:
        raise ActionError(409, str(exc)) from None
    finally:
        lock.release()
    return [store.relative(path) for path in written]
