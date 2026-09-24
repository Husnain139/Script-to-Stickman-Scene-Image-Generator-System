"""The M2 planning checklist for the sample script (spec §17). A report, not a strict test."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stickman.ingest.fragments import is_likely_fragment
from stickman.ingest.models import TimedLine
from stickman.plan.models import MASCOT, CastMember, Correction, Plan

EXPECTED_CORRECTIONS: tuple[tuple[str, str], ...] = (
    ("90 at night", "9 at night"),
    ("Roger E. Kirch", "Roger Ekirch"),
    ("Thomas Ware", "Thomas Wehr"),
    ("2 sleep", "second sleep"),
    ("Zhuansi", "Ju/'hoansi"),
)
MASCOT_LINE_STARTS = (58.0, 117.0, 124.0, 126.0)  # 0:58, 1:57, 2:04, 2:06: the mascot should appear
HISTORICAL_RANGES = ((0.0, 52.0), (61.0, 114.0))  # lines starting 0:00–0:52 and 1:01–1:54: no mascot
CORRECTIONS_TARGET = 4
MASCOT_TARGET = 0.9
NEURON_USD = 0.011 / 1000


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("'", "'").replace("'", "'").split())


@dataclass(frozen=True)
class ExpectedCorrection:
    source: str
    target: str
    found: Correction | None
    exact: bool  # the "to" text matches as well


@dataclass(frozen=True)
class ChecklistReport:
    expected: list[ExpectedCorrection]
    other_corrections: list[Correction]
    lines_13_14_merged: bool
    wrongly_merged: list[str]  # scene ids that merge a complete sentence
    mascot_checked: int
    mascot_misses: list[str]  # unit ids that break the mascot rule
    caveman_entries: list[str]
    caveman_units: list[str]

    @property
    def corrections_found(self) -> int:
        return sum(1 for item in self.expected if item.exact)

    @property
    def mascot_score(self) -> float:
        return 1 - len(self.mascot_misses) / self.mascot_checked if self.mascot_checked else 0.0

    @property
    def meets_target(self) -> bool:
        """Other corrections are judged by a person, so they aren't counted here."""
        return (
            self.corrections_found >= CORRECTIONS_TARGET
            and self.lines_13_14_merged
            and not self.wrongly_merged
            and self.mascot_score >= MASCOT_TARGET
        )


def _mascot_expected(scene_start: float) -> bool | None:
    if any(abs(scene_start - t) < 1e-6 for t in MASCOT_LINE_STARTS):
        return True
    if any(low <= scene_start <= high for low, high in HISTORICAL_RANGES):
        return False
    return None


def evaluate(plan: Plan, lines: Sequence[TimedLine]) -> ChecklistReport:
    expected = []
    matched: list[Correction] = []
    for source, target in EXPECTED_CORRECTIONS:
        hit = next((c for c in plan.corrections if _norm(source) in _norm(c.from_)), None)
        if hit is not None:
            matched.append(hit)
        expected.append(ExpectedCorrection(source, target, hit, hit is not None and _norm(target) in _norm(hit.to)))
    others = [c for c in plan.corrections if not any(c is m for m in matched)]

    texts = {line.number: line.text for line in lines}
    wrongly_merged = [
        scene.id
        for scene in plan.scenes
        if any(not is_likely_fragment(texts[n], texts.get(n + 1)) for n in scene.lines[:-1])
    ]
    merged = any(13 in scene.lines and 14 in scene.lines for scene in plan.scenes)

    checked = 0
    misses = []
    for scene in plan.scenes:
        want = _mascot_expected(scene.start)
        if want is None:
            continue
        for unit in scene.units:
            checked += 1
            if any(c.ref == MASCOT for c in unit.characters) != want:
                misses.append(unit.id)

    cavemen = [
        m.id for m in plan.cast if isinstance(m, CastMember) and ("cave" in m.id or "cave" in m.name.lower())
    ]
    caveman_units = [u.id for u in plan.units() if any(c.ref in cavemen for c in u.characters)]
    return ChecklistReport(expected, others, merged, wrongly_merged, checked, misses, cavemen, caveman_units)


def tuning_tasks(report: ChecklistReport) -> list[str]:
    tasks = []
    for item in report.expected:
        if not item.exact:
            problem = "given the wrong target" if item.found else "not found"
            tasks.append(f'Corrections: "{item.source}" → "{item.target}" was {problem}.')
    if not report.lines_13_14_merged:
        tasks.append("Merges: lines 13 and 14 were not merged.")
    if report.wrongly_merged:
        tasks.append(f"Merges: complete sentences were merged in scenes {', '.join(report.wrongly_merged)}.")
    if report.mascot_misses:
        tasks.append(
            f"Mascot rule: the mascot is wrong in {len(report.mascot_misses)} units ({', '.join(report.mascot_misses)})."
        )
    if len(report.caveman_entries) != 1:
        tasks.append(f"Extras: expected one caveman cast entry, got {len(report.caveman_entries)}.")
    return tasks


def render_markdown(report: ChecklistReport, *, model: str, neurons: float | None) -> str:
    cost = "unknown" if neurons is None else f"{neurons:.0f} neurons (about ${neurons * NEURON_USD:.4f})"
    lines = [
        "# M2 planning checklist (spec §17)",
        "",
        f"Sample script: `tests/fixtures/scripts/first-sleep.txt`. Planner: `{model}`. LLM cost of this run: {cost}.",
        "",
        f"**Target met: {'yes' if report.meets_target else 'no'}.** The target is at least {CORRECTIONS_TARGET} of "
        f"{len(report.expected)} expected corrections, lines 13 and 14 merged, no complete sentence merged, and the "
        f"mascot rule followed in at least {MASCOT_TARGET:.0%} of units. Other corrections are judged by hand below.",
        "",
        f"## Expected corrections: {report.corrections_found} of {len(report.expected)}",
        "",
        "| Expected | Found | Result |",
        "|---|---|---|",
    ]
    for item in report.expected:
        found = f'scene {item.found.scene}: "{item.found.from_}" → "{item.found.to}"' if item.found else "—"
        result = "found" if item.exact else ("wrong target" if item.found else "missing")
        lines.append(f'| "{item.source}" → "{item.target}" | {found} | {result} |')
    lines += ["", "## Other corrections", ""]
    if report.other_corrections:
        lines += ["| Scene | From | To | Reason | Judgement (reasonable extra / wrong) |", "|---|---|---|---|---|"]
        lines += [f'| {c.scene} | "{c.from_}" | "{c.to}" | {c.reason} | _to fill in_ |' for c in report.other_corrections]
    else:
        lines.append("None.")
    lines += [
        "",
        "## Merges",
        "",
        f"- Lines 13 and 14 merged: {'yes' if report.lines_13_14_merged else 'no'}",
        f"- Complete sentences wrongly merged: {', '.join(report.wrongly_merged) or 'none'}",
        "",
        f"## Mascot rule: {report.mascot_score:.0%} of {report.mascot_checked} units",
        "",
        f"- Units breaking it: {', '.join(report.mascot_misses) or 'none'}",
        "",
        "## Extras",
        "",
        f"- Caveman cast entries: {', '.join(report.caveman_entries) or 'none'}",
        f"- Units using them: {', '.join(report.caveman_units) or 'none'}",
        "",
        "## Prompt-tuning tasks",
        "",
    ]
    lines += [f"- {task}" for task in tuning_tasks(report)] or ["None."]
    return "\n".join(lines) + "\n"
