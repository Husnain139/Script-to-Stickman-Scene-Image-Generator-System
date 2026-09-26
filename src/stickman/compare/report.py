"""The model comparison's report (spec §14.4): per run, the QC pass rate, the no-text failure rate,
character_count failures, the time and cost per image; export/compare.html shows every image with its
QC result. With no QC retries in a comparison, every version is a first try."""

from __future__ import annotations

import html
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stickman.compare.setup import RUNS_DIR, CompareRun, CompareSetup
from stickman.fsutil import safe_write
from stickman.plan.models import Plan
from stickman.pricing import format_usd, usd_neurons
from stickman.render.state import StateStore, Version

REPORT = "export/compare.html"
MIN_TIMEOUT_S = 30


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest rank: the smallest value with at least a share `q` of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


@dataclass(frozen=True)
class RunStats:
    run: CompareRun
    images: int
    checked: int
    passed: int
    vision_checked: int
    text_failures: int
    count_failures: int
    median_s: float | None
    p90_s: float | None
    usd_per_image: float | None

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.checked if self.checked else None

    @property
    def text_rate(self) -> float | None:
        """The no-text failure rate: images where the vision model saw text, of those it looked at."""
        return self.text_failures / self.vision_checked if self.vision_checked else None


@dataclass(frozen=True)
class Cell:
    status: str
    version: Version | None  # the current version, else the latest
    image: str | None  # the image's path from export/, with forward slashes


def collect(folder: Path, setup: CompareSetup) -> tuple[list[RunStats], dict[tuple[str, str], Cell]]:
    picked = [pick.unit for pick in setup.picks]
    stats: list[RunStats] = []
    cells: dict[tuple[str, str], Cell] = {}
    for run in setup.runs:
        state = StateStore.load(folder / RUNS_DIR / run.id).state
        versions: list[Version] = []
        for unit_id in picked:
            unit = state.units.get(unit_id)
            if unit is None or not unit.versions:
                cells[(run.id, unit_id)] = Cell(unit.status if unit is not None else "planned", None, None)
                continue
            versions += unit.versions
            current = unit.version(unit.current_version) if unit.current_version is not None else None
            shown = current or unit.versions[-1]
            cells[(run.id, unit_id)] = Cell(unit.status, shown, f"../{RUNS_DIR}/{run.id}/{shown.file}")
        checked = [v for v in versions if v.qc is not None]
        looked = [v for v in checked if v.qc.vision is not None]
        seconds = [v.latency_s for v in versions]
        stats.append(RunStats(
            run=run,
            images=len(versions),
            checked=len(checked),
            passed=sum(v.qc.passed for v in checked),
            vision_checked=len(looked),
            text_failures=sum(v.qc.vision.has_text for v in looked),
            count_failures=sum(v.qc.reason == "character_count" for v in checked),
            median_s=statistics.median(seconds) if seconds else None,
            p90_s=percentile(seconds, 0.9),
            usd_per_image=sum(v.est_cost_usd for v in versions) / len(versions) if versions else None,
        ))
    return stats, cells


def suggestion(stats: Sequence[RunStats]) -> tuple[str, int, int] | None:
    """render.timeout_s ≈ 3 × the 90th percentile, rounded up to 10 s (at least 30), and
    render.est_seconds_per_image ≈ the median, from the first run that has times (spec §14.4)."""
    for item in stats:
        if item.p90_s is not None and item.median_s is not None:
            timeout = max(MIN_TIMEOUT_S, math.ceil(3 * item.p90_s / 10) * 10)
            return item.run.id, timeout, max(1, round(item.median_s))
    return None


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def _secs(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}s"


def _cost(value: float | None) -> str:
    return "-" if value is None else f"{format_usd(value)} (≈ {usd_neurons(value):,.0f} neurons)"


def report_lines(stats: Sequence[RunStats]) -> list[str]:
    lines = [
        f"{'Run':<22} {'size':<10} {'refs':<5} {'images':>6} {'QC pass':>8} {'no-text fail':>13} "
        f"{'count fail':>11} {'median':>8} {'p90':>8}  per image"
    ]
    for item in stats:
        size = f"{item.run.width}x{item.run.height}"
        lines.append(
            f"{item.run.id:<22} {size:<10} {'yes' if item.run.references else 'no':<5} {item.images:>6} "
            f"{_pct(item.pass_rate):>8} {_pct(item.text_rate):>13} {item.count_failures:>11} "
            f"{_secs(item.median_s):>8} {_secs(item.p90_s):>8}  {_cost(item.usd_per_image)}"
        )
    found = suggestion(stats)
    if found is not None:
        run_id, timeout, seconds = found
        lines.append(
            f"Suggested for config/settings.yaml (from {run_id}): render.timeout_s: {timeout}, "
            f"render.est_seconds_per_image: {seconds}. Times were measured with render.concurrency requests at once, "
            "so they include waiting in Cloudflare's queue."
        )
    return lines


_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--pass:#1a7f37;--fail:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#161618;--fg:#f2f2f7;--muted:#a1a1a6;--line:#3a3a3c;--pass:#4ac26b;--fail:#ff6b61}}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
table{border-collapse:collapse}th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
.stats td{font-variant-numeric:tabular-nums;white-space:nowrap}.scroll{overflow-x:auto}
.grid img{display:block;width:320px;max-width:40vw;height:auto;background:#fff;border:1px solid var(--line)}
.muted,.empty{color:var(--muted)}.pass{color:var(--pass)}.fail{color:var(--fail)}
.cat{font-weight:600}.unit{font-family:ui-monospace,monospace}.idea{max-width:260px;color:var(--muted)}
"""


def _cell(cell: Cell) -> str:
    if cell.version is None or cell.image is None:
        return f"<td class=empty>{html.escape('not made yet' if cell.status == 'planned' else cell.status)}</td>"
    qc = cell.version.qc
    if qc is None:
        verdict, css = "not checked", "muted"
    elif qc.passed:
        verdict, css = "passed", "pass"
    else:
        verdict, css = f"failed: {qc.reason}", "fail"
    idea = f" · idea {qc.score}/5" if qc is not None and qc.vision is not None else ""
    src = html.escape(cell.image, quote=True)
    return (
        f'<td><a href="{src}"><img src="{src}" loading=lazy alt=""></a>'
        f"<div class={css}>{html.escape(verdict)}{idea} · {cell.version.latency_s:.1f}s</div></td>"
    )


def write_report(
    folder: Path, setup: CompareSetup, plan: Plan, stats: Sequence[RunStats],
    cells: dict[tuple[str, str], Cell], *, now: datetime,
) -> Path:
    e = html.escape
    ideas = {unit.id: unit.visual_idea for unit in plan.units()}
    stat_rows = "\n".join(
        f"<tr><th scope=row>{e(item.run.id)}</th><td>{item.run.width}×{item.run.height}</td>"
        f"<td>{'yes' if item.run.references else 'no'}</td><td>{item.images}</td><td>{_pct(item.pass_rate)}</td>"
        f"<td>{_pct(item.text_rate)}</td><td>{item.count_failures}</td><td>{_secs(item.median_s)}</td>"
        f"<td>{_secs(item.p90_s)}</td><td>{e(_cost(item.usd_per_image))}</td></tr>"
        for item in stats
    )
    found = suggestion(stats)
    advice = (
        f"<p>Suggested for config/settings.yaml (from {e(found[0])}): <code>render.timeout_s: {found[1]}</code>, "
        f"<code>render.est_seconds_per_image: {found[2]}</code>. Times include waiting in Cloudflare's queue.</p>"
        if found is not None else ""
    )
    head = "".join(f"<th scope=col>{e(run.id)}</th>" for run in setup.runs)
    rows = []
    for pick in setup.picks:
        label = e(pick.category.replace("_", " ")) + (" <span class=muted>(stand-in)</span>" if pick.filled else "")
        row_cells = "".join(_cell(cells[(run.id, pick.unit)]) for run in setup.runs)
        rows.append(
            f"<tr><th scope=row><div class=cat>{label}</div><div class=unit>{e(pick.unit)}</div>"
            f"<div class=idea>{e(ideas.get(pick.unit, ''))}</div></th>{row_cells}</tr>"
        )
    page = "".join([
        "<!doctype html>\n<html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width, initial-scale=1">',
        f"<title>Model comparison · {e(setup.source)}</title><style>{_CSS}</style></head><body>",
        f"<h1>Model comparison</h1><p class=muted>Source: {e(setup.source)} · {len(setup.picks)} units × "
        f"{len(setup.runs)} runs · written {e(now.isoformat(timespec='minutes'))}. "
        "No QC retries: every image is a first try.</p>",
        "<h2>Per run</h2><div class=scroll><table class=stats><thead><tr><th>Run</th><th>Size</th><th>References</th>"
        "<th>Images</th><th>QC pass rate</th><th>No-text failure rate</th><th>character_count failures</th>"
        "<th>Median time</th><th>90th percentile</th><th>Cost per image</th></tr></thead><tbody>",
        stat_rows,
        "</tbody></table></div>",
        advice,
        "<h2>Images</h2><div class=scroll><table class=grid><thead><tr><th>Unit</th>",
        head,
        "</tr></thead><tbody>",
        "\n".join(rows),
        "</tbody></table></div></body></html>\n",
    ])
    path = folder / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, page.encode("utf-8"))
    return path
