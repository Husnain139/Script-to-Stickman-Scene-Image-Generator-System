"""LLM rewrites of one unit during a run (spec §7.5): soften after a safety filter, redesign after a
second weak idea. plan.yaml is loaded fresh and written hash-checked, so your edits and comments survive."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from stickman.plan.llm import PlanningError, StageRunner
from stickman.plan.models import PlanUnit, PlanValidationError
from stickman.plan.planner import REPLAN_KEYS, PlanningContext, replan_unit
from stickman.plan.store import PlanChangedError, load_plan, update_unit, write_plan

SOFTEN_HINT = "make it more symbolic and tasteful"
SOFTEN_PREFIX = "safety filter: "  # a softened_reason QC's soften wrote; a unit is softened only once
REDESIGN_HINT = "The last images did not show the idea clearly"


def softened_by_qc(unit: PlanUnit) -> bool:
    """QC already softened this unit (a softened_reason the planner wrote doesn't count)."""
    return unit.softened and (unit.softened_reason or "").startswith(SOFTEN_PREFIX)


class RewriteFailed(Exception):
    """A rewrite couldn't be made or saved. The unit goes to needs_review with this message."""


UnitCheck = Callable[[PlanUnit], None]
"""Called with the rewritten unit before plan.yaml is written; raises RewriteFailed to write nothing."""


class Rewriter(Protocol):
    async def soften(self, unit_id: str, notes: str, check: UnitCheck | None = None) -> PlanUnit: ...

    async def redesign(self, unit_id: str, notes: str, check: UnitCheck | None = None) -> PlanUnit: ...


class PlanRewriter:
    """Reruns stage 3 for one unit (like `stickman replan`, spec §6.5) and writes the result."""

    def __init__(self, runner: StageRunner, ctx: PlanningContext, plan_path: Path) -> None:
        self._runner = runner
        self._ctx = ctx
        self._path = plan_path
        self._lock = asyncio.Lock()  # one load-and-write of plan.yaml at a time

    async def soften(self, unit_id: str, notes: str, check: UnitCheck | None = None) -> PlanUnit:
        def mark(loaded: PlanUnit, unit: PlanUnit) -> PlanUnit:
            reason = (unit.softened_reason or "").strip() or "made more symbolic and tasteful"
            return unit.model_copy(update={"softened": True, "softened_reason": SOFTEN_PREFIX + reason})

        hint = f"{SOFTEN_HINT}. {notes}" if notes else SOFTEN_HINT
        return await self._rewrite(unit_id, lambda loaded: hint, mark, check)

    async def redesign(self, unit_id: str, notes: str, check: UnitCheck | None = None) -> PlanUnit:
        """A unit QC already softened keeps its soften marker (soften-once rests on it, spec §7.5) and
        stays symbolic: the hint carries SOFTEN_HINT too."""
        base = f"{REDESIGN_HINT}: {notes}" if notes else f"{REDESIGN_HINT}; make the idea clearer in one simple picture"

        def hint(loaded: PlanUnit) -> str:
            return f"{SOFTEN_HINT}. {base}" if softened_by_qc(loaded) else base

        def keep_soften(loaded: PlanUnit, unit: PlanUnit) -> PlanUnit:
            if not softened_by_qc(loaded):
                return unit
            return unit.model_copy(update={"softened": True, "softened_reason": loaded.softened_reason})

        return await self._rewrite(unit_id, hint, keep_soften, check)

    async def _rewrite(
        self,
        unit_id: str,
        hint: Callable[[PlanUnit], str],
        finish: Callable[[PlanUnit, PlanUnit], PlanUnit],
        check: UnitCheck | None,
    ) -> PlanUnit:
        """`hint` and `finish` see the unit as plan.yaml holds it now; `finish` gets it and the new design."""
        async with self._lock:
            try:
                loaded = load_plan(self._path, library_ids=self._ctx.library_ids)
            except (PlanValidationError, OSError) as exc:
                raise RewriteFailed(f"plan.yaml can't be read: {exc}") from exc
            unit = next((u for u in loaded.plan.units() if u.id == unit_id), None)
            if unit is None:
                raise RewriteFailed(f"plan.yaml has no unit {unit_id}")
            if unit.prompt_locked:
                raise RewriteFailed(f"{unit_id} has a locked prompt, so the LLM never rewrites it")
            try:
                updated = finish(unit, await replan_unit(self._runner, self._ctx, loaded.plan, unit_id, hint=hint(unit)))
            except PlanningError as exc:
                raise RewriteFailed(str(exc)) from exc
            if check is not None:
                check(updated)  # e.g. the new image request can be built; else nothing is written
            fields = {key: value for key, value in updated.model_dump(mode="json").items() if key in REPLAN_KEYS}
            update_unit(loaded.doc, unit_id, fields)
            try:
                write_plan(self._path, loaded.doc, expected_hash=loaded.hash)
            except PlanChangedError as exc:
                raise RewriteFailed("plan.yaml changed on disk during the rewrite, so nothing was written") from exc
            except (PlanValidationError, OSError) as exc:
                raise RewriteFailed(f"plan.yaml was not written: {exc}") from exc
            return updated
