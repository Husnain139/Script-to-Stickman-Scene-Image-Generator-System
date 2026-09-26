"""The review page's data (spec §12.2): one JSON-ready dict built from the plan, state.json, the bootstrap
store and the ledger. It only reads, so a page open during a CLI run never touches state.json; stale is
worked out for display (recovery.stale_status) and written by the next generate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from stickman.bootstrap.store import BootstrapError, BootstrapStore, anchor_done, made_with, mascot_done, ranked
from stickman.budget import Budget
from stickman.ledger import LEDGER_FILE, Ledger
from stickman.library import anchor_ref_path, find_references
from stickman.plan.cast import cast_infos
from stickman.plan.models import CastMember, Plan, PlanUnit
from stickman.pricing import format_usd, image_cost_usd, usd_neurons
from stickman.qc.decide import QCResult
from stickman.render.images import image_stem
from stickman.render.jobs import JobBuilder, JobError, RenderContext
from stickman.render.recovery import stale_status
from stickman.render.references import ReferenceFiles
from stickman.render.state import ProjectState, UnitState, Version
from stickman.render.summary import review_reason
from stickman.runtime import check_usd
from stickman.settings import ConfigError, Settings

FLAGGED = ("needs_review", "stale", "failed")  # the gallery's order (spec §12.2)
GROUP_SHEET = (1024, 768)  # a cast entry with more than one figure (spec §8.2)


def file_url(workspace: Path, path: Path) -> str:
    return "/files/" + path.resolve().relative_to(workspace.resolve()).as_posix()


def qc_view(qc: QCResult | None) -> dict[str, Any] | None:
    if qc is None:
        return None
    vision = qc.vision
    return {
        "passed": qc.passed,
        "reason": qc.reason,
        "score": qc.score if vision is not None else None,
        "notes": (vision.notes or "") if vision is not None else (qc.vision_error or ""),
        "text_seen": (vision.text_seen or "") if vision is not None else "",
    }


def _version_view(workspace: Path, project_dir: Path, version: Version) -> dict[str, Any]:
    return {
        "v": version.v, "url": file_url(workspace, project_dir / version.file), "seed": version.seed,
        "retry_of": version.retry_of, "retry_reason": version.retry_reason, "latency_s": version.latency_s,
        "created": version.created.isoformat(), "qc": qc_view(version.qc),
    }


def unit_view(
    workspace: Path, project_dir: Path, plan: Plan, unit: PlanUnit, state: UnitState, fingerprint: str | None
) -> dict[str, Any]:
    """`fingerprint` None: it couldn't be worked out (a reference image can't be read), so the stored status shows."""
    status = stale_status(state, fingerprint) if fingerprint is not None else state.status
    return {
        "id": unit.id, "part": unit.part, "start": unit.start, "end": unit.end, "stem": image_stem(unit.id, unit.start),
        "source_text": unit.source_text, "corrected_text": unit.corrected_text, "visual_idea": unit.visual_idea,
        "visual_type": unit.visual_type, "shot": unit.shot, "time_of_day": unit.time_of_day,
        "characters": [character.model_dump() for character in unit.characters],
        "setting": list(unit.setting), "props": list(unit.props), "image_prompt": unit.image_prompt,
        "prompt_locked": unit.prompt_locked, "softened": unit.softened, "split": plan.scene_of(unit.id).split.status,
        "status": status, "error": state.error,
        "review_reason": review_reason(state) if status == "needs_review" else None,
        "current_version": state.current_version, "approved_version": state.approved_version,
        # The side-by-side pair (spec §12.2): only once the new image differs from the kept one.
        "compare_with": state.compare_with if state.compare_with not in (None, state.current_version) else None,
        "versions": [_version_view(workspace, project_dir, version) for version in state.versions],
    }


def gallery_order(units: Sequence[Mapping[str, Any]]) -> list[str]:
    rank = {status: index for index, status in enumerate(FLAGGED)}
    ordered = sorted(enumerate(units), key=lambda pair: (rank.get(pair[1]["status"], len(FLAGGED)), pair[0]))
    return [unit["id"] for _, unit in ordered]


def estimate_view(plan: Plan, builder: JobBuilder, ctx: RenderContext) -> dict[str, Any]:
    """spec §12.2: images × (1 + expected retry rate), their checks, extras' sheets; time from the
    measured seconds per image and the concurrency. Planning is done, so the LLM costs nothing more here."""
    settings, pricing = ctx.settings, ctx.pricing
    per_image: list[float] = []
    for unit in plan.units():
        if not unit.image_prompt.strip():
            continue
        try:
            per_image.append(builder.job(unit).estimate_usd)
        except JobError:
            continue
    factor = 1 + settings.budget.expected_retry_rate
    images = sum(per_image) * factor
    checks = check_usd(settings, pricing) * len(per_image) * factor
    price = pricing.image(plan.image_model)
    extras = [m for m in plan.cast if isinstance(m, CastMember) and m.library_ref is None]
    sheets = sum(
        2 * image_cost_usd(price, GROUP_SHEET if member.figures > 1 else tuple(settings.image.sheet_size))
        for member in extras
    )
    total = images + checks + sheets
    minutes = len(per_image) * factor * settings.render.est_seconds_per_image / settings.render.concurrency / 60
    return {
        "units": len(per_image), "images_usd": images, "checks_usd": checks, "sheets_usd": sheets, "llm_usd": 0.0,
        "total_usd": total, "total_text": format_usd(total), "neurons": round(usd_neurons(total)),
        "minutes": round(minutes, 1),
        "free_note": f"Up to {format_usd(pricing.free_daily_usd)} of today's usage may be covered by the free daily allocation.",
    }


def budget_view(workspace: Path, settings: Settings, now: datetime) -> dict[str, Any]:
    spent = Budget.from_ledger(Ledger(workspace / LEDGER_FILE), settings.budget, now=now).spent
    weekly = settings.budget.weekly_usd
    return {
        "week_usd": spent, "weekly_usd": weekly, "warn": spent >= settings.budget.warn_ratio * weekly,
        "text": f"{format_usd(spent)} of {format_usd(weekly)} this week",
    }


def bootstrap_view(workspace: Path, ctx: RenderContext) -> dict[str, Any]:
    """The anchor and mascot candidates for the current style version (spec §12.2 Sheets, "bootstrap only").
    Mascot candidates made with a previous anchor are marked; they can't be approved (M5)."""
    version = ctx.style.style_version
    store = BootstrapStore.load(workspace, version)
    label: str | None = None
    ref = anchor_ref_path(workspace, version)
    if ref.is_file():
        try:
            label = ReferenceFiles(workspace, ref_max_side=ctx.settings.image.ref_max_side).load(ref).label
        except ConfigError:
            label = None

    def step(name: str) -> dict[str, Any]:
        state = store.step(name)  # type: ignore[arg-type]
        return {
            "approved": state.approved,
            "candidates": [
                {
                    "n": c.n, "url": file_url(workspace, workspace / c.file), "seed": c.seed, "qc": qc_view(c.qc),
                    "approved": state.approved == c.n,
                    "previous_anchor": name == "mascot" and label is not None and not made_with(c, label),
                }
                for c in ranked(state.candidates)
            ],
        }

    return {
        "style_version": version, "anchor_done": anchor_done(workspace, version),
        "mascot_done": mascot_done(workspace, ctx.mascot, version), "anchor": step("anchor"), "mascot": step("mascot"),
    }


def cast_view(workspace: Path, plan: Plan, ctx: RenderContext) -> list[dict[str, Any]]:
    available = find_references(
        workspace, use_references=True, style_version=plan.style_version, mascot=ctx.mascot, cast=plan.cast,
        library=ctx.library,
    )
    table = cast_infos(plan.cast, ctx.mascot)
    return [
        {
            "id": member.id, "name": table[member.id].name, "figures": table[member.id].figures,
            "description": table[member.id].description, "library_ref": getattr(member, "library_ref", None),
            "sheet": member.id in available.sheets,
        }
        for member in plan.cast
    ]


def empty_view(project_dir: Path, problems: Sequence[str]) -> dict[str, Any]:
    """What the page gets when the config can't be read: the problems, and nothing to act on."""
    return {
        "project": project_dir.name, "plan_hash": None, "errors": [], "problems": list(problems), "job": None,
        "approvals": {"plan": False, "sheets": False, "tests": False}, "test_units": [], "budget": None,
        "aspect": None, "duration_end": None, "units": [], "order": [], "estimate": None, "corrections": [],
        "merge_check": [], "cast": [], "bootstrap": None,
    }


def project_view(
    *,
    workspace: Path,
    project_dir: Path,
    ctx: RenderContext,
    plan: Plan | None,
    plan_hash: str | None,
    errors: Sequence[str],
    state: ProjectState,
    now: datetime,
    job: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`plan` is the last valid plan (None before there is one); `errors` are the current file's validation errors."""
    view = empty_view(project_dir, [])
    view.update(
        plan_hash=plan_hash, errors=list(errors), job=dict(job) if job else None,
        approvals={"plan": state.plan_approved, "sheets": state.sheets_approved, "tests": state.tests_approved},
        test_units=list(state.test_units), budget=budget_view(workspace, ctx.settings, now),
    )
    problems: list[str] = []
    try:
        view["bootstrap"] = bootstrap_view(workspace, ctx)
    except (BootstrapError, OSError) as exc:  # an unreadable bootstrap.json is shown, never fatal to the page
        problems.append(str(exc))
    if plan is not None:
        builder: JobBuilder | None = None
        fingerprints: dict[str, str] = {}
        try:
            builder = JobBuilder(ctx, plan)
            fingerprints = {unit.id: builder.fingerprint(unit) for unit in plan.units()}
        except ConfigError as exc:
            problems.append(str(exc))
        units = [
            unit_view(workspace, project_dir, plan, unit, state.units.get(unit.id, UnitState()), fingerprints.get(unit.id))
            for unit in plan.units()
        ]
        view.update(
            aspect=plan.aspect, duration_end=plan.duration_end, units=units, order=gallery_order(units),
            corrections=[{"scene": c.scene, "from": c.from_, "to": c.to, "reason": c.reason} for c in plan.corrections],
            merge_check=[check.model_dump() for check in plan.merge_check],
            cast=cast_view(workspace, plan, ctx),
        )
        if builder is not None:
            try:
                view["estimate"] = estimate_view(plan, builder, ctx)
            except ConfigError as exc:
                problems.append(str(exc))
    view["problems"] = problems
    return view
