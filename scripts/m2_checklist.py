"""Live M2 planning checklist (spec §17): the real planner on the sample script.

It makes LLM calls only: a few thousand neurons, inside the free daily allocation.
Run it from the workspace root: uv run python scripts/m2_checklist.py
It writes m2_out/plan.yaml, m2_out/logs/ and docs/m2-planning-checklist.md.
Stages already answered are cached in m2_out/.cache/, so a rerun after a prompt change
pays only for the stages whose prompt changed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from stickman.cf.errors import CFError
from stickman.cli import build_client
from stickman.ingest.parse import parse_script
from stickman.ingest.timing import build_timeline
from stickman.plan.checklist import evaluate, render_markdown
from stickman.plan.llm import PlanningError, StageRunner
from stickman.plan.planner import load_planning_context, plan_script
from stickman.plan.store import to_document, write_plan
from stickman.runlog import RunLog
from stickman.settings import load_config

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "fixtures" / "scripts" / "first-sleep.txt"
OUT = ROOT / "m2_out"
REPORT = ROOT / "docs" / "m2-planning-checklist.md"


async def plan_sample(cfg, log: RunLog):
    ctx = load_planning_context(ROOT, cfg.settings)
    async with build_client(cfg) as client:
        runner = StageRunner(client, cfg.settings.llm, cfg.settings.retry, cache_dir=OUT / ".cache" / "llm", log=log)
        return await plan_script(
            runner, ctx, script_text=SAMPLE.read_text(encoding="utf-8"), project="first-sleep", aspect="16:9", duration=None
        )


def total_neurons(log: RunLog) -> float:
    if log.path is None or not log.path.exists():
        return 0.0
    rows = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    return sum(row.get("neurons") or 0.0 for row in rows)


def main() -> int:
    cfg = load_config(ROOT)
    OUT.mkdir(exist_ok=True)
    log = RunLog.for_project(OUT, secrets=(cfg.secrets.cf_api_token.get_secret_value(),))
    try:
        outcome = asyncio.run(plan_sample(cfg, log))
    except (CFError, PlanningError) as exc:
        print(f"Planning stopped: {exc}. See {log.path}.")
        return 2
    write_plan(OUT / "plan.yaml", to_document(outcome.plan), expected_hash=None)
    lines = build_timeline(parse_script(SAMPLE.read_text(encoding="utf-8")), cfg.settings.timing).lines
    report = evaluate(outcome.plan, lines)
    REPORT.write_text(
        render_markdown(report, model=cfg.settings.llm.planner_model, neurons=total_neurons(log)), encoding="utf-8"
    )
    print(
        f"corrections {report.corrections_found}/5 · lines 13+14 merged: {report.lines_13_14_merged} · "
        f"merges to judge: {len(report.merges_to_judge)} · mascot {report.mascot_score:.0%} · target met: {report.meets_target}"
    )
    print(f"report: {REPORT}")
    return 0 if report.meets_target else 1


if __name__ == "__main__":
    sys.exit(main())
