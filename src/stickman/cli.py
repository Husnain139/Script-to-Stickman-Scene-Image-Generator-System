"""stickman command-line interface (spec §13)."""

from __future__ import annotations

import asyncio
import math
import shutil
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NoReturn, TypeVar

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from ruamel.yaml.comments import CommentedMap

from stickman.budget import Budget
from stickman.cf.client import CloudflareClient
from stickman.cf.errors import CFError, ErrorCategory
from stickman.ingest.parse import parse_duration, parse_script
from stickman.ingest.timing import build_timeline
from stickman.ledger import LEDGER_FILE, Ledger, utc_day_start
from stickman.meter import Meter
from stickman.plan.llm import PlanningError, StageRunner
from stickman.plan.models import CastMember, Plan, PlanValidationError
from stickman.plan.planner import (
    REPLAN_KEYS,
    PlanningContext,
    PlanOutcome,
    load_planning_context,
    plan_script,
    replan_unit,
)
from stickman.plan.store import PlanChangedError, load_plan, to_document, update_unit, write_plan
from stickman.pricing import format_usd, load_pricing, usd_neurons
from stickman.project import ProjectError, check_unplanned, choose_project_dir, create_project, resolve_project, slugify
from stickman.render.jobs import JobBuilder, JobError, RenderContext, RenderJob
from stickman.render.lock import LockHeld, ProjectLock
from stickman.render.recovery import recover
from stickman.render.renderer import Renderer, RunResult, StopReason
from stickman.render.state import StateError, StateStore
from stickman.render.summary import summary_lines
from stickman.runlog import RunLog
from stickman.settings import (
    DEFAULT_CONFIG_FILES,
    AppConfig,
    ConfigError,
    default_config_text,
    load_config,
)

T = TypeVar("T")

EXIT_USER_ERROR = 1
EXIT_PAUSED = 2
EXIT_CONFIG_ERROR = 3
ASPECTS = ("16:9", "9:16")

ENV_EXAMPLE = (
    "# Cloudflare dashboard -> Workers AI -> copy the Account ID.\n"
    '# Token: "Create a Workers AI API Token" template (Workers AI - Read + Edit).\n'
    "CF_ACCOUNT_ID=\n"
    "CF_API_TOKEN=\n"
)
TOKEN_HELP = (
    "Cloudflare rejected the token. Create one with the "
    '"Create a Workers AI API Token" template, or give a custom token '
    "Workers AI - Read and Workers AI - Edit, then update CF_API_TOKEN in .env."
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback()
def main() -> None:
    """Script-to-stickman scene image generator."""


def _today() -> date:
    """The local date that names a new project folder (tests replace it)."""
    return date.today()


def _fail(message: str, code: int) -> NoReturn:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(code)


def build_client(cfg: AppConfig) -> CloudflareClient:
    if cfg.secrets is None:
        raise ConfigError("Cloudflare credentials are not loaded")
    return CloudflareClient(
        cfg.secrets.cf_account_id,
        cfg.secrets.cf_api_token.get_secret_value(),
        plan=cfg.settings.account.plan,
        timeout_s=cfg.settings.render.timeout_s,
    )


@app.command()
def init(
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
    skip_token_check: bool = typer.Option(
        False, "--skip-token-check", help="Don't call the API to verify the token."
    ),
) -> None:
    """Create config/, library/ and projects/, then check credentials and ffmpeg."""
    root = workspace.resolve()
    _write_defaults(root)
    try:
        cfg = load_config(root)
    except ConfigError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(EXIT_CONFIG_ERROR)
    if shutil.which("ffmpeg") is None:
        console.print(
            "[yellow]ffmpeg not found on PATH. It is needed only for export: "
            "winget install ffmpeg[/yellow]"
        )
    if skip_token_check:
        console.print("Token check skipped.")
        return
    asyncio.run(_verify_token(cfg))
    console.print("[green]Cloudflare token works.[/green]")


def _write_defaults(root: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in DEFAULT_CONFIG_FILES:
        target = config_dir / name
        if target.exists():
            console.print(f"kept     config/{name}")
        else:
            target.write_text(default_config_text(name), encoding="utf-8")
            console.print(f"created  config/{name}")
    for folder in ("library", "projects"):
        (root / folder).mkdir(exist_ok=True)
    env_example = root / ".env.example"
    if not env_example.exists():
        env_example.write_text(ENV_EXAMPLE, encoding="utf-8")
        console.print("created  .env.example")


async def _verify_token(cfg: AppConfig) -> None:
    try:
        async with build_client(cfg) as client:
            await client.chat(
                cfg.settings.llm.fallback_model,
                [{"role": "user", "content": "Reply with the word OK."}],
                max_tokens=5,
            )
    except CFError as exc:
        if exc.category is ErrorCategory.AUTH:
            console.print(f"[red]{escape(TOKEN_HELP)}[/red]")
            raise typer.Exit(EXIT_CONFIG_ERROR)
        console.print(f"[red]Token check failed: {escape(str(exc))}[/red]")
        raise typer.Exit(EXIT_USER_ERROR)


def _run_llm(
    cfg: AppConfig, directory: Path, work: Callable[[StageRunner], Awaitable[T]], *, cached: bool
) -> T:
    """Run LLM work for a project and turn its failures into CLI messages and exit codes."""
    token = cfg.secrets.cf_api_token.get_secret_value() if cfg.secrets else ""
    log = RunLog.for_project(directory, secrets=(token,))
    again = " Finished stages are cached, so running the same command again continues from there." if cached else ""
    try:
        pricing = load_pricing(cfg.workspace)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    # Every planning call goes in the ledger; the weekly budget never stops planning (spec §9.7 [M3]).
    meter = Meter(project=directory.name, ledger=Ledger(cfg.workspace / LEDGER_FILE), pricing=pricing)

    async def go() -> T:
        async with build_client(cfg) as client:
            runner = StageRunner(
                client, cfg.settings.llm, cfg.settings.retry, cache_dir=directory / ".cache" / "llm", log=log,
                meter=meter,
            )
            return await work(runner)

    try:
        return asyncio.run(go())
    except PlanningError as exc:
        _fail(log.mask(f"Planning failed: {exc}. Every attempt is logged in {log.path}.{again}"), EXIT_USER_ERROR)
    except PlanValidationError as exc:
        _fail("The planned result failed validation (a bug): " + "; ".join(exc.errors[:5]), EXIT_USER_ERROR)
    except CFError as exc:
        if exc.category is ErrorCategory.DAILY_LIMIT:
            _fail(
                "The free daily allocation of 10,000 neurons is used up. "
                f"Run the same command again after the daily reset (00:00 UTC).{again}",
                EXIT_PAUSED,
            )
        if exc.category is ErrorCategory.AUTH:
            _fail(TOKEN_HELP, EXIT_CONFIG_ERROR)
        _fail(log.mask(f"Cloudflare error: {exc}"), EXIT_USER_ERROR)


def _load_planning(root: Path) -> tuple[AppConfig, PlanningContext]:
    try:
        cfg = load_config(root)
        return cfg, load_planning_context(root, cfg.settings)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)


def _write_plan(path: Path, doc: CommentedMap, *, expected_hash: str | None, again: str) -> None:
    """The final plan.yaml write. Its failures become a message and exit 1, not a traceback."""
    try:
        write_plan(path, doc, expected_hash=expected_hash)
    except PlanChangedError:
        _fail(f"{path.name} changed on disk while the LLM was working. Nothing was written; run the command again.", EXIT_USER_ERROR)
    except PlanValidationError as exc:
        _fail(f"{path.name} was not written: the result failed validation (a bug): " + "; ".join(exc.errors[:5]), EXIT_USER_ERROR)
    except OSError as exc:
        _fail(f"Can't write {path}: {exc}.{again}", EXIT_USER_ERROR)


def _print_summary(outcome: PlanOutcome, directory: Path) -> None:
    plan = outcome.plan
    split = sum(1 for scene in plan.scenes if scene.split.status == "split")
    console.print(f"Planned {len(plan.scenes)} scenes -> {len(plan.units())} units ({split} split).")
    no_cut = [scene.id for scene in plan.scenes if scene.split.status == "no_valid_cut"]
    if no_cut:
        console.print(f"No valid cut, kept whole: {', '.join(no_cut)}")
    console.print(f"Corrections ({len(plan.corrections)}):")
    for c in plan.corrections:
        console.print(escape(f'  {c.scene}  "{c.from_}" -> "{c.to}"  ({c.reason})'))
    members = [m.id if not isinstance(m, CastMember) else f"{m.id} ({m.figures} figures)" for m in plan.cast]
    console.print(escape("Cast: " + ", ".join(members)))
    for check in plan.merge_check:
        console.print(f"Check merges: line {check.line}: the rules say {check.rules}, the LLM says {check.llm}")
    for warning in outcome.warnings:
        console.print(f"[yellow]{escape(warning)}[/yellow]")
    console.print(escape(f"Wrote {directory / 'plan.yaml'}"))
    console.print(escape('Next: read plan.yaml, then run `stickman replan <unit> --hint "..."` for any unit to redesign.'))


@app.command()
def new(
    script: Path = typer.Argument(..., help="The narration script: M:SS, H:MM:SS or SRT."),
    aspect: str = typer.Option(..., "--aspect", help='"16:9" or "9:16".'),
    duration: str | None = typer.Option(None, "--duration", help="Video length (M:SS). Default: estimated."),
    name: str | None = typer.Option(None, "--name", help="Project name. Default: the script's file name."),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
) -> None:
    """Create a project from a script and plan it with the LLM (writes plan.yaml)."""
    root = workspace.resolve()
    slug = slugify(name or script.stem)
    directory, continued = choose_project_dir(root, slug, _today(), script)
    console.print(f"Project: {escape(directory.name)}")
    if continued:
        console.print(escape(f"Planning continues in {directory.name}: same script, no plan.yaml yet."))
    if aspect not in ASPECTS:
        _fail(f'--aspect must be "16:9" or "9:16", not {aspect!r}', EXIT_USER_ERROR)
    try:
        check_unplanned(directory)
    except ProjectError as exc:
        _fail(str(exc), EXIT_USER_ERROR)
    cfg, planning = _load_planning(root)
    try:
        text = script.read_text(encoding="utf-8")
        seconds = parse_duration(duration) if duration is not None else None
        build_timeline(parse_script(text), cfg.settings.timing, duration=seconds)
    except OSError as exc:
        _fail(f"Can't read the script: {exc}", EXIT_USER_ERROR)
    except ValueError as exc:  # ScriptParseError is a ValueError
        _fail(f"Script error: {exc}", EXIT_USER_ERROR)
    try:
        create_project(directory, script)
    except ProjectError as exc:
        _fail(str(exc), EXIT_USER_ERROR)
    outcome = _run_llm(
        cfg,
        directory,
        lambda runner: plan_script(runner, planning, script_text=text, project=slug, aspect=aspect, duration=seconds),
        cached=True,
    )
    _write_plan(
        directory / "plan.yaml", to_document(outcome.plan), expected_hash=None,
        again=" Finished stages are cached, so running the same command again continues from there.",
    )
    _print_summary(outcome, directory)


@app.command()
def replan(
    unit: str = typer.Argument(..., help="Unit ID, for example 006a."),
    hint: str | None = typer.Option(None, "--hint", help="What to change in the design."),
    project: Path | None = typer.Option(None, "--project", "-p", help="Project folder or name. Default: the most recent."),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
) -> None:
    """Redesign one unit with the LLM (spec §6.5). Its timing is not changed."""
    root = workspace.resolve()
    try:
        directory = resolve_project(root, project)
    except ProjectError as exc:
        console.print("Project: (none found)")
        _fail(str(exc), EXIT_USER_ERROR)
    console.print(f"Project: {escape(directory.name)}")
    cfg, planning = _load_planning(root)
    path = directory / "plan.yaml"
    try:
        loaded = load_plan(path, library_ids=planning.library_ids)
    except PlanValidationError as exc:
        _fail("plan.yaml is invalid:\n" + "\n".join(exc.errors), EXIT_USER_ERROR)
    if unit not in {u.id for u in loaded.plan.units()}:
        _fail(f"No unit {unit!r} in this plan.", EXIT_USER_ERROR)
    updated = _run_llm(
        cfg, directory, lambda runner: replan_unit(runner, planning, loaded.plan, unit, hint=hint), cached=False
    )
    fields = {key: value for key, value in updated.model_dump(mode="json").items() if key in REPLAN_KEYS}
    update_unit(loaded.doc, unit, fields)
    _write_plan(path, loaded.doc, expected_hash=loaded.hash, again=" plan.yaml was not changed; run the command again.")
    console.print(escape(f"Replanned {unit}: {updated.visual_idea}"))


TO_GENERATE = ("planned", "failed")
OUTAGE_MESSAGE = "Possible outage — run `stickman resume` later."
EXIT_INTERRUPTED = 130


async def _wait(seconds: float) -> None:
    """How a run waits before a retry (tests replace it)."""
    await asyncio.sleep(seconds)


def _reset_time(now: datetime) -> str:
    """The next 00:00 UTC, in local time."""
    return (utc_day_start(now) + timedelta(days=1)).astimezone().strftime("%H:%M") + " local time"


@app.command()
def generate(
    project: Path | None = typer.Option(None, "--project", "-p", help="Project folder or name. Default: the most recent."),
    force: bool = typer.Option(False, "--force", help="Go on past the weekly budget."),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Generate at most this many units in this run."),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
) -> None:
    """Generate an image for every unit that has none yet (spec §10.1). Run it again to continue."""
    _generate(workspace, project, force=force, limit=limit)


@app.command()
def resume(
    project: Path | None = typer.Option(None, "--project", "-p", help="Project folder or name. Default: the most recent."),
    force: bool = typer.Option(False, "--force", help="Go on past the weekly budget."),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Generate at most this many units in this run."),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
) -> None:
    """The same as generate: continue after a pause (daily limit, budget, outage) or a crash."""
    _generate(workspace, project, force=force, limit=limit)


def _generate(workspace: Path, project: Path | None, *, force: bool, limit: int | None) -> None:
    root = workspace.resolve()
    try:
        directory = resolve_project(root, project)
    except ProjectError as exc:
        console.print("Project: (none found)")
        _fail(str(exc), EXIT_USER_ERROR)
    console.print(f"Project: {escape(directory.name)}")
    try:
        cfg = load_config(root)
        ctx = RenderContext.load(root, cfg.settings)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    try:
        plan = load_plan(directory / "plan.yaml", library_ids=ctx.library_ids).plan
    except PlanValidationError as exc:
        _fail("plan.yaml is invalid:\n" + "\n".join(exc.errors), EXIT_USER_ERROR)
    try:
        builder = JobBuilder(ctx, plan)
        expected = builder.expected()
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    lock = ProjectLock(directory)
    try:
        lock.acquire()
    except LockHeld as exc:
        _fail(f"{exc}. Let it finish, or stop it, then run this again.", EXIT_USER_ERROR)
    try:
        _generate_locked(cfg, ctx, directory, plan, builder, expected, lock, force=force, limit=limit)
    except KeyboardInterrupt:
        console.print("Stopped. Finished images are kept; run `stickman resume` to continue.")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    finally:
        lock.release()


def _generate_locked(
    cfg: AppConfig,
    ctx: RenderContext,
    directory: Path,
    plan: Plan,
    builder: JobBuilder,
    expected: dict,
    lock: ProjectLock,
    *,
    force: bool,
    limit: int | None,
) -> None:
    if lock.removed_stale is not None:
        console.print(f"[yellow]Removed a stale lock left by PID {lock.removed_stale}, which is no longer running.[/yellow]")
    try:
        store = StateStore.load(directory)
    except StateError as exc:
        _fail(f"{exc}. Nothing was changed.", EXIT_USER_ERROR)
    for note in recover(store, expected):
        console.print(escape(note))
    todo = [unit for unit in plan.units() if store.unit(unit.id).status in TO_GENERATE]
    if limit is not None:
        todo = todo[:limit]
    if not todo:
        console.print("Nothing to generate: every unit has an image, or is stale (never regenerated automatically).")
        return
    try:
        jobs = builder.jobs(todo)
    except JobError as exc:
        _fail(str(exc), EXIT_USER_ERROR)
    token = cfg.secrets.cf_api_token.get_secret_value() if cfg.secrets else ""
    log = RunLog.for_project(directory, secrets=(token,))
    ledger = Ledger(cfg.workspace / LEDGER_FILE)
    now = datetime.now().astimezone()
    budget = Budget.from_ledger(
        ledger, cfg.settings.budget, now=now, force=force,
        warn=lambda message: console.print(f"[yellow]{escape(message)}[/yellow]"),
    )
    meter = Meter(project=directory.name, ledger=ledger, budget=budget, pricing=ctx.pricing)
    _print_run_start(jobs, cfg, ctx, ledger, now)
    result = asyncio.run(_render(cfg, store, meter, jobs, log))
    for line in summary_lines(
        store.state, [unit.id for unit in plan.units()], result=result, run_usd=meter.run_usd,
        possibly_billed=meter.possibly_billed, week_usd=budget.spent, weekly_usd=cfg.settings.budget.weekly_usd,
    ):
        console.print(escape(log.mask(line)))
    _exit_for(result, log)


def _print_run_start(jobs: list[RenderJob], cfg: AppConfig, ctx: RenderContext, ledger: Ledger, now: datetime) -> None:
    estimate = sum(job.estimate_usd for job in jobs)
    console.print(escape(
        f"Generating {len(jobs)} unit(s) on {jobs[0].model} ≈ {format_usd(estimate)} (≈ {usd_neurons(estimate):,.0f} neurons)."
    ))
    if cfg.settings.account.plan != "free":
        return
    used = ledger.neurons_since(utc_day_start(now))
    allowance = ctx.pricing.free_daily_neurons
    per_image = usd_neurons(estimate / len(jobs))
    fit = max(0, math.floor((allowance - used) / per_image)) if per_image > 0 else len(jobs)
    line = (
        f"Free plan: about {used:,.0f} of {allowance:,.0f} neurons used today (UTC), "
        f"so about {fit} more image(s) fit before the reset at {_reset_time(now)}."
    )
    if fit < len(jobs):
        line += " The run pauses at the daily limit; `stickman resume` continues after the reset."
    console.print(escape(line))


async def _render(cfg: AppConfig, store: StateStore, meter: Meter, jobs: list[RenderJob], log: RunLog) -> RunResult:
    columns = (TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn())
    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task("Generating", total=len(jobs))

        def done(unit_id: str) -> None:
            finished = [store.unit(job.unit_id).status for job in jobs]
            progress.update(
                task,
                advance=1,
                description=f"done {finished.count('generated')} · failed {finished.count('failed')} · "
                f"cost ≈ {format_usd(meter.run_usd)}",
            )

        async with build_client(cfg) as client:
            renderer = Renderer(
                client, store, meter, retry=cfg.settings.retry, concurrency=cfg.settings.render.concurrency,
                log=log, sleep=_wait, on_done=done,
            )
            return await renderer.run(jobs)


def _exit_for(result: RunResult, log: RunLog) -> None:
    """spec §9.5, §9.7: pauses exit 2 with how to continue; a rejected token exits 3."""
    if result.stop is None:
        return
    if result.stop is StopReason.DAILY_LIMIT:
        _fail(
            "The free daily allocation of 10,000 neurons is used up. Finished images are kept; "
            f"run `stickman resume` after the daily reset (00:00 UTC, {_reset_time(datetime.now().astimezone())}).",
            EXIT_PAUSED,
        )
    if result.stop is StopReason.BUDGET:
        _fail(
            log.mask(
                f"Weekly budget reached: {result.detail}. Nothing new was started. Run `stickman resume --force` "
                "to go on anyway, or raise budget.weekly_usd in config/settings.yaml."
            ),
            EXIT_PAUSED,
        )
    if result.stop is StopReason.CIRCUIT_BREAKER:
        _fail(OUTAGE_MESSAGE, EXIT_PAUSED)
    _fail(TOKEN_HELP, EXIT_CONFIG_ERROR)
