"""The end-of-run summary (spec §10.5)."""

from __future__ import annotations

from collections.abc import Sequence

from stickman.pricing import format_usd, usd_neurons
from stickman.render.renderer import RunResult, StopReason
from stickman.render.state import ProjectState

PAUSE_NAMES: dict[StopReason, str] = {
    StopReason.DAILY_LIMIT: "daily limit",
    StopReason.BUDGET: "weekly budget",
    StopReason.CIRCUIT_BREAKER: "possible outage",
    StopReason.AUTH: "token rejected",
}


def format_duration(seconds: float) -> str:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def summary_lines(
    state: ProjectState,
    unit_ids: Sequence[str],
    *,
    result: RunResult,
    run_usd: float,
    possibly_billed: int,
    week_usd: float,
    weekly_usd: float,
) -> list[str]:
    """Counts cover every unit of the plan. `skipped` is a unit still `planned` at the end."""
    status = {unit_id: state.units[unit_id].status for unit_id in unit_ids if unit_id in state.units}

    def having(*names: str) -> list[str]:
        return [unit_id for unit_id in unit_ids if status.get(unit_id) in names]

    head = "Run finished" if result.stop is None else f"Run paused ({PAUSE_NAMES[result.stop]})"
    cost = f"≈ {usd_neurons(run_usd):,.0f} neurons" + (f", {possibly_billed} possibly billed" if possibly_billed else "")
    lines = [
        f"{head} · {len(unit_ids)} units · done {len(having('generated', 'approved'))} · "
        f"needs_review {len(having('needs_review'))} · failed {len(having('failed'))} · "
        f"stale {len(having('stale'))} · skipped {len(having('planned'))}",
        f"Cost this run ≈ {format_usd(run_usd)} ({cost}) · week ≈ {format_usd(week_usd)} / {format_usd(weekly_usd)} · "
        f"time {format_duration(result.elapsed_s)}",
    ]
    failed = having("failed")
    if failed:
        lines.append("Failed: " + "   ".join(f"{u} ({state.units[u].error or 'unknown error'})" for u in failed))
    stale = having("stale")
    if stale:
        lines.append("Stale (not regenerated automatically): " + ", ".join(stale))
    return lines
