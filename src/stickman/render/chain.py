"""Where a unit's QC and retries stand (spec §11.3), worked out from state.json and the plan alone,
so a run stopped or killed at any point continues the same chain next time."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from stickman.qc.decide import QCReason
from stickman.render.state import UnitState, Version


@dataclass(frozen=True)
class Render:
    """Make an image. A retry links to the version it fixes; `reasons` are every failure fixed so
    far in the chain, for the prompt fixes (spec §7.5)."""

    retry_of: int | None = None
    reason: QCReason | None = None
    reasons: tuple[QCReason, ...] = ()
    rewrite: Literal["soften", "redesign"] | None = None
    notes: str = ""  # the vision model's notes, the hint of a redesign


@dataclass(frozen=True)
class Check:
    """Run QC on version `v`."""

    v: int


@dataclass(frozen=True)
class Finish:
    status: Literal["generated", "needs_review"]
    current: int | None  # the version that becomes current; None when nothing was made


Step = Render | Check | Finish


def chain_of(unit: UnitState, fingerprint: str) -> list[Version]:
    """The unit's current attempt, oldest first: its current version, back through retry_of. Empty when
    there is no current version, or it was made from other plan fields (then a new chain starts)."""
    if unit.current_version is None:
        return []
    version = unit.version(unit.current_version)
    if version is None or version.fingerprint != fingerprint:
        return []
    chain = [version]
    while version.retry_of is not None:
        earlier = unit.version(version.retry_of)
        if earlier is None or earlier in chain:
            break
        chain.append(earlier)
        version = earlier
    return chain[::-1]


def best_version(chain: Sequence[Version]) -> Version | None:
    """Passes first, then the highest idea score, then the latest (spec §11.3). Only versions made from
    the latest version's fields count: a soften changes the unit, and older images show the old design."""
    if not chain:
        return None
    latest = chain[-1].fingerprint
    checked = [v for v in chain if v.qc is not None and v.fingerprint == latest]
    if not checked:
        return chain[-1]
    return max(checked, key=lambda v: (v.qc.passed, v.qc.score, v.v))  # type: ignore[union-attr]


def finish_step(chain: Sequence[Version]) -> Finish:
    best = best_version(chain)
    if best is None:
        return Finish("needs_review", None)
    passed = best.qc is not None and best.qc.passed
    return Finish("generated" if passed else "needs_review", best.v)


def next_step(
    chain: Sequence[Version], *, qc_max: int, locked: bool, softened: bool, refused: bool = False
) -> Step:
    """`locked`: the unit's prompt_locked. `softened`: QC already softened it (rewrite.softened_by_qc).
    `refused`: the last image request was refused, which counts as safety_filtered with no image."""
    retries = max(0, len(chain) - 1)
    latest = chain[-1] if chain else None
    reasons = tuple(cast(QCReason, v.retry_reason) for v in chain[1:] if v.retry_reason)
    if refused:
        if locked or softened:
            return finish_step(chain)
        return Render(retry_of=latest.v if latest else None, reason="safety_filtered",
                      reasons=(*reasons, "safety_filtered"), rewrite="soften")
    if latest is None:
        return Render()
    if latest.qc is None:
        return Check(latest.v)
    result = latest.qc
    if result.passed or result.reason in (None, "vision_error") or retries >= qc_max:
        return finish_step(chain)
    reason = result.reason
    rewrite: Literal["soften", "redesign"] | None = None
    if reason == "safety_filtered":
        if locked or softened:
            return finish_step(chain)
        rewrite = "soften"
    elif reason == "weak_idea" and "weak_idea" in reasons:  # retry 1 was a new seed; retry 2 redesigns
        if locked:
            return finish_step(chain)
        rewrite = "redesign"
    notes = (result.vision.notes or "") if result.vision is not None else ""
    return Render(retry_of=latest.v, reason=reason, reasons=(*reasons, reason), rewrite=rewrite, notes=notes)
