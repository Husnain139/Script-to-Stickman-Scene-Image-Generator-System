"""stickman command-line interface (spec §13)."""

from __future__ import annotations

import asyncio
import io
import math
import shutil
import socket
import sys
import threading
import webbrowser
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NoReturn, TypeVar

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from ruamel.yaml.comments import CommentedMap

from stickman.bootstrap.approve import ApprovalError, approve_anchor, approve_mascot, mascot_version_mismatch
from stickman.bootstrap.generate import BOOTSTRAP_PROJECT, CandidateJob, CandidateMaker, StepPlan, step_plan
from stickman.bootstrap.store import (
    BootstrapError,
    BootstrapStore,
    Candidate,
    Step,
    bootstrap_folder,
    mascot_done,
    pending_step,
    ranked,
)
from stickman.budget import Budget
from stickman.cf.errors import CFError, ErrorCategory
from stickman.compare.report import collect, report_lines, write_report
from stickman.compare.runs import PreparedRun, prepare_run, render_runs
from stickman.compare.setup import (
    CompareError,
    ComparePick,
    CompareSetup,
    compare_dir,
    create_compare,
    default_runs,
    find_compare,
    load_compare,
)
from stickman.config_files import MascotConfig, StyleConfig, load_mascot, load_style
from stickman.ingest.parse import parse_duration, parse_script
from stickman.ingest.timing import build_timeline
from stickman.ledger import LEDGER_FILE, Ledger, utc_day_start
from stickman.library import anchor_path, find_references
from stickman.meter import Meter
from stickman.plan.cast import cast_infos
from stickman.plan.llm import PlanningError, StageRunner
from stickman.plan.models import CastMember, Plan, PlanValidationError
from stickman.plan.picking import pick_compare_units
from stickman.plan.planner import (
    REPLAN_KEYS,
    PlanningContext,
    PlanOutcome,
    load_planning_context,
    plan_script,
    replan_unit,
)
from stickman.plan.refresh import refresh_plan_file
from stickman.plan.store import LoadedPlan, PlanChangedError, load_plan, to_document, update_unit, write_plan
from stickman.pricing import PricingConfig, format_usd, load_pricing, usd_neurons
from stickman.project import ProjectError, check_unplanned, choose_project_dir, create_project, resolve_project, slugify
from stickman.render.calls import Calls
from stickman.render.chain import chain_of
from stickman.render.jobs import JobBuilder, JobError, RenderContext, RenderJob, random_seed
from stickman.render.lock import LockHeld, ProjectLock
from stickman.render.recovery import recover
from stickman.render.references import ReferenceFiles
from stickman.render.renderer import GuardedChat, Renderer, RunControl, RunResult, StopReason
from stickman.render.rewrite import PlanRewriter
from stickman.render.state import StateError, StateStore, needs_work
from stickman.render.summary import summary_lines
from stickman.runlog import RunLog, mask
from stickman.runtime import build_client, check_usd, secrets_of
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


def _utf8_output() -> None:
    """Windows gives a redirected stdout the ANSI code page (cp1252), which can't encode ≈ or LLM text
    such as U+2011. Write UTF-8 instead, and replace anything that still can't be written."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if isinstance(stream, io.TextIOWrapper) and encoding != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.callback()
def main() -> None:
    """Script-to-stickman scene image generator."""
    _utf8_output()


def _today() -> date:
    """The local date that names a new project folder (tests replace it)."""
    return date.today()


def _fail(message: str, code: int) -> NoReturn:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(code)


def _secrets(cfg: AppConfig) -> tuple[str, ...]:
    return secrets_of(cfg)


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
        console.print(f"[red]Token check failed: {escape(mask(str(exc), _secrets(cfg)))}[/red]")
        raise typer.Exit(EXIT_USER_ERROR)


def _run_llm(
    cfg: AppConfig, directory: Path, work: Callable[[StageRunner], Awaitable[T]], *, cached: bool
) -> T:
    """Run LLM work for a project and turn its failures into CLI messages and exit codes."""
    log = RunLog.for_project(directory, secrets=_secrets(cfg))
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


def _write_plan(
    path: Path, doc: CommentedMap, *, expected_hash: str | None, again: str, during: str = "the LLM was working"
) -> None:
    """The final plan.yaml write. Its failures become a message and exit 1, not a traceback. `during`:
    what was happening while plan.yaml could change on disk."""
    try:
        write_plan(path, doc, expected_hash=expected_hash)
    except PlanChangedError:
        _fail(f"{path.name} changed on disk while {during}. Nothing was written; run the command again.", EXIT_USER_ERROR)
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


OUTAGE_MESSAGE = "Possible outage — run `stickman resume` later."  # what _exit_for prints for generate (tests use it)
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


STEP_NAMES: dict[str, str] = {"anchor": "style anchor", "mascot": "mascot sheet"}


@app.command()
def bootstrap(
    candidates: int | None = typer.Option(
        None, "--candidates", min=1,
        help="How many candidates the current step should have; more than it has adds more. "
             "Default: bootstrap.anchor_candidates, or bootstrap.mascot_candidates.",
    ),
    approve_anchor_n: int | None = typer.Option(None, "--approve-anchor", min=1, help="Approve this style-anchor candidate."),
    approve_mascot_n: int | None = typer.Option(None, "--approve-mascot", min=1, help="Approve this mascot-sheet candidate."),
    force: bool = typer.Option(False, "--force", help="Go on past the weekly budget."),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
) -> None:
    """Make the style anchor, then the mascot sheet (spec §8.1): run it, approve a candidate, run it again."""
    root = workspace.resolve()
    approving = approve_anchor_n is not None or approve_mascot_n is not None
    try:
        cfg = load_config(root, need_secrets=not approving)
        style, mascot, pricing = load_style(root), load_mascot(root), load_pricing(root)
    except ConfigError as exc:
        console.print("Bootstrap")
        _fail(str(exc), EXIT_CONFIG_ERROR)
    console.print(f"Bootstrap: style v{style.style_version}")
    if approve_anchor_n is not None and approve_mascot_n is not None:
        _fail("Approve one candidate at a time.", EXIT_USER_ERROR)
    folder = bootstrap_folder(root, style.style_version)
    folder.mkdir(parents=True, exist_ok=True)
    lock = ProjectLock(folder)
    try:
        lock.acquire()
    except LockHeld as exc:
        _fail(f"{exc}. Let it finish, then run this again.\nIf no stickman is running, delete `{lock.path}`.", EXIT_USER_ERROR)
    try:
        if lock.removed_stale is not None:
            console.print(f"[yellow]Removed a stale lock left by PID {lock.removed_stale}, which is no longer running.[/yellow]")
        try:
            store = BootstrapStore.load(root, style.style_version)  # under the lock: no other run changes it now
        except BootstrapError as exc:
            _fail(f"{exc}. Nothing was changed.", EXIT_USER_ERROR)
        for note in store.recover():
            console.print(escape(note))
        if approve_anchor_n is not None:
            _approve(cfg, store, mascot, "anchor", approve_anchor_n)
        elif approve_mascot_n is not None:
            _approve(cfg, store, mascot, "mascot", approve_mascot_n)
        else:
            _bootstrap_step(cfg, store, style, mascot, pricing, candidates=candidates, force=force)
    except KeyboardInterrupt:
        console.print("Stopped. Finished candidates are kept; run `stickman bootstrap` to continue.")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    finally:
        lock.release()


def _approve(cfg: AppConfig, store: BootstrapStore, mascot: MascotConfig, step: Step, n: int) -> None:
    max_side = cfg.settings.image.ref_max_side
    previous = store.step(step).approved
    try:
        if step == "anchor":
            written = approve_anchor(store, n, ref_max_side=max_side)
        else:
            written = approve_mascot(store, n, mascot=mascot, ref_max_side=max_side)
    except ApprovalError as exc:
        _fail(str(exc), EXIT_USER_ERROR)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    candidate = store.step(step).candidate(n)
    if candidate.qc is None:
        console.print(f"[yellow]c{n} was never checked by QC; approved anyway.[/yellow]")
    elif not candidate.qc.passed:
        console.print(f"[yellow]c{n} failed QC ({candidate.qc.reason}); approved anyway.[/yellow]")
    console.print(escape(f"Approved {STEP_NAMES[step]} candidate c{n}: " + ", ".join(store.relative(p) for p in written)))
    if previous is not None and previous != n:
        console.print("Images made with the previous one become stale (their reference images changed).")
    if step == "anchor":
        if not mascot_done(store.workspace, mascot, store.state.style_version):
            console.print("Next: `stickman bootstrap` makes the mascot sheet candidates, with this anchor as their reference.")
        elif previous != n:
            console.print(
                "The approved mascot sheet was made with the previous anchor. To redo it, clear `seed` in "
                "config/mascot.yaml and run `stickman bootstrap`."
            )
    else:
        console.print(escape(
            f"Bootstrap is complete: the mascot's seed ({candidate.seed}) and model are in config/mascot.yaml. "
            "`stickman generate` now sends the anchor and the mascot sheet with each image, and "
            "`stickman compare -p <project>` runs the model comparison."
        ))


def _bootstrap_step(
    cfg: AppConfig, store: BootstrapStore, style: StyleConfig, mascot: MascotConfig, pricing: PricingConfig, *,
    candidates: int | None, force: bool,
) -> None:
    root, settings = store.workspace, cfg.settings
    step = pending_step(root, mascot, style.style_version)
    if step is None:
        console.print(escape(
            f"Bootstrap is complete: {store.relative(anchor_path(root, style.style_version))} and {mascot.sheet} "
            f"(seed {mascot.seed}, {mascot.model})."
        ))
        return
    mismatch = mascot_version_mismatch(mascot, style.style_version) if step == "mascot" else None
    if mismatch is not None:
        _fail(mismatch, EXIT_USER_ERROR)  # before any candidate is made: it could never be approved
    wanted = candidates or (settings.bootstrap.anchor_candidates if step == "anchor" else settings.bootstrap.mascot_candidates)
    try:
        plan = step_plan(step, workspace=root, settings=settings, style=style, mascot=mascot, pricing=pricing,
                         files=ReferenceFiles(root, ref_max_side=settings.image.ref_max_side))
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    current = plan.current(store.step(step).candidates)  # a mascot candidate made with a previous anchor doesn't count
    jobs = plan.jobs(store, max(0, wanted - len(current)))
    unchecked = [c for c in current if c.qc is None]
    errors: dict[str, str] = {}
    result: RunResult | None = None
    log = RunLog.for_project(store.folder, secrets=_secrets(cfg))
    if jobs or unchecked:
        ledger = Ledger(root / LEDGER_FILE)
        now = datetime.now().astimezone()
        budget = Budget.from_ledger(
            ledger, settings.budget, now=now, force=force,
            warn=lambda message: console.print(f"[yellow]{escape(message)}[/yellow]"),
        )
        meter = Meter(project=BOOTSTRAP_PROJECT, ledger=ledger, budget=budget, pricing=pricing)
        _print_bootstrap_start(cfg, pricing, ledger, now, plan, jobs, unchecked)
        result, errors = asyncio.run(_make_candidates(cfg, store, meter, log, plan, jobs, len(jobs) + len(unchecked)))
    _print_candidates(store, plan, errors)
    if result is not None:
        _exit_for(result, log, again="stickman bootstrap")
    current = plan.current(store.step(step).candidates)
    if not current:
        _fail("No candidate could be made (see the errors above). Run `stickman bootstrap` again.", EXIT_USER_ERROR)
    option = "--approve-anchor" if step == "anchor" else "--approve-mascot"
    console.print(escape(
        f"Look at the candidates, then approve one: `stickman bootstrap {option} <N>`. "
        f"`stickman bootstrap --candidates {len(current) + 2}` adds more."
    ))
    raise typer.Exit(EXIT_PAUSED)


def _print_bootstrap_start(
    cfg: AppConfig, pricing: PricingConfig, ledger: Ledger, now: datetime, plan: StepPlan,
    jobs: list[CandidateJob], unchecked: list[Candidate],
) -> None:
    check = _check_usd(cfg, pricing)
    how = f", each checked by {cfg.settings.llm.vision_model}" if cfg.settings.qc.vision else ", pixel checks only"
    if jobs:
        estimate = (plan.estimate_usd + check) * len(jobs)
        console.print(escape(
            f"{STEP_NAMES[plan.step].capitalize()}: making {len(jobs)} candidate(s) on {plan.model} at "
            f"{plan.size[0]}x{plan.size[1]}{how} ≈ {format_usd(estimate)} (≈ {usd_neurons(estimate):,.0f} neurons)."
        ))
    if unchecked:
        console.print(escape(f"Checking {len(unchecked)} candidate(s) with no check yet: " + ", ".join(f"c{c.n}" for c in unchecked)))
    line = _free_plan_line(
        cfg, pricing, ledger, now, per_item_usd=plan.estimate_usd + check, count=len(jobs), noun="candidate(s)",
        what="image and check" if cfg.settings.qc.vision else "image", again="stickman bootstrap",
    )
    if line is not None and jobs:
        console.print(escape(line))


async def _make_candidates(
    cfg: AppConfig, store: BootstrapStore, meter: Meter, log: RunLog, plan: StepPlan, jobs: list[CandidateJob], total: int,
) -> tuple[RunResult, dict[str, str]]:
    columns = (TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn())
    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task("Candidates", total=total)
        async with build_client(cfg) as client:
            control = RunControl(cfg.settings.retry.circuit_breaker)
            calls = Calls(client, meter, log, control, retry=cfg.settings.retry, qc=cfg.settings.qc,
                          vision_model=cfg.settings.llm.vision_model, sleep=_wait)
            maker = CandidateMaker(calls, store, concurrency=cfg.settings.render.concurrency,
                                   on_done=lambda: progress.update(task, advance=1))
            return await maker.run(plan, jobs), maker.errors


def _print_candidates(store: BootstrapStore, plan: StepPlan, errors: dict[str, str]) -> None:
    step = plan.step
    state = store.step(step)
    current = {c.n for c in plan.current(state.candidates)}
    if state.candidates:
        console.print(f"{STEP_NAMES[step].capitalize()} candidates, best first:")
    for c in ranked(state.candidates):
        if c.qc is None:
            verdict = "not checked yet"
        elif c.qc.passed:
            verdict = "passed"
        else:
            verdict = f"failed: {c.qc.reason}"
        idea = f"idea {c.qc.score}/5" if c.qc is not None and c.qc.vision is not None else "idea -"
        approved = "  (approved)" if state.approved == c.n else ""
        previous = "" if c.n in current else "  (made with a previous anchor)"
        console.print(escape(f"  c{c.n:<3} {verdict:<26} {idea:<9} {c.file}{approved}{previous}"))
    for label, error in errors.items():
        console.print(f"[yellow]{escape(f'{label}: {error}')}[/yellow]")


@app.command()
def compare(
    project: Path | None = typer.Option(
        None, "--project", "-p", help="The planned project whose units are compared. Default: the most recent."
    ),
    new: bool = typer.Option(False, "--new", help="Start a new comparison even if this project has one."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Start without asking to confirm the estimate."),
    force: bool = typer.Option(False, "--force", help="Go on past the weekly budget."),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
) -> None:
    """Compare Klein 4B with and without reference images, and at 1280x720 (spec §14.4). Run it again to continue."""
    root = workspace.resolve()
    try:
        source = resolve_project(root, project)
    except ProjectError as exc:
        console.print("Project: (none found)")
        _fail(str(exc), EXIT_USER_ERROR)
    folder = None if new else find_compare(root, source)
    creating = folder is None
    if folder is None:
        folder = compare_dir(root, source, _today())
    console.print(f"Project: {escape(folder.name)}")
    console.print(escape(f"Source: {source.name}"))
    try:
        cfg = load_config(root)
        ctx = RenderContext.load(root, cfg.settings)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    if pending_step(root, ctx.mascot, ctx.style.style_version) is not None:
        _fail(
            "Bootstrap isn't complete: run `stickman bootstrap` first. The comparison measures images with and "
            "without the style anchor and the mascot sheet.",
            EXIT_USER_ERROR,
        )
    if creating:
        _create_compare(source, folder, ctx)
    lock = ProjectLock(folder)
    try:
        lock.acquire()
    except LockHeld as exc:
        _fail(f"{exc}. Let it finish, then run this again.\nIf no stickman is running, delete `{lock.path}`.", EXIT_USER_ERROR)
    try:
        _compare_locked(cfg, ctx, source, folder, lock, yes=yes, force=force)
    except KeyboardInterrupt:
        console.print(escape(f"Stopped. Finished images are kept; run `stickman compare -p {source.name}` to continue."))
        raise typer.Exit(EXIT_INTERRUPTED) from None
    finally:
        lock.release()


def _create_compare(source: Path, folder: Path, ctx: RenderContext) -> None:
    try:
        plan = load_plan(source / "plan.yaml", library_ids=ctx.library_ids).plan
    except PlanValidationError as exc:
        _fail("plan.yaml is invalid:\n" + "\n".join(exc.errors), EXIT_USER_ERROR)
    figures = {ref: info.figures for ref, info in cast_infos(plan.cast, ctx.mascot).items()}
    picks = pick_compare_units(plan, figures)
    setup = CompareSetup(
        source=source.name, created=datetime.now().astimezone(),
        picks=[ComparePick(category=p.category, unit=p.unit_id, filled=p.filled, seed=random_seed()) for p in picks],
        runs=default_runs(ctx.settings, plan.aspect),
    )
    try:
        create_compare(folder, source, setup)
    except FileExistsError:
        _fail(f"Another stickman created {folder.name} just now. Nothing was written; run the command again.", EXIT_USER_ERROR)
    console.print(escape("Compared units: " + ", ".join(
        f"{p.unit_id} ({p.category.replace('_', ' ')}{', stand-in' if p.filled else ''})" for p in picks
    )))


def _compare_locked(
    cfg: AppConfig, ctx: RenderContext, source: Path, folder: Path, lock: ProjectLock, *, yes: bool, force: bool,
) -> None:
    if lock.removed_stale is not None:
        console.print(f"[yellow]Removed a stale lock left by PID {lock.removed_stale}, which is no longer running.[/yellow]")
    again = f"stickman compare -p {source.name}"
    try:
        setup, plan = load_compare(folder, library_ids=ctx.library_ids)
        prepared = [prepare_run(folder, setup, plan, run, ctx) for run in setup.runs]
    except (CompareError, JobError) as exc:
        _fail(str(exc), EXIT_USER_ERROR)
    except StateError as exc:
        _fail(f"{exc}. Nothing was changed.", EXIT_USER_ERROR)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    for run in prepared:
        for note in run.notes:
            console.print(escape(f"{run.run.id}: {note}"))
    jobs = [job for run in prepared for job in run.jobs]
    result: RunResult | None = None
    if jobs:
        ledger = Ledger(cfg.workspace / LEDGER_FILE)
        now = datetime.now().astimezone()
        # A job whose image is made but unchecked costs only its check (the same rule as generate's estimate).
        to_make = []
        for run in prepared:
            check_only = _check_only(run.store, run.jobs)
            to_make += [job for job in run.jobs if job.unit_id not in check_only]
        estimate = sum(job.estimate_usd for job in to_make) + _check_usd(cfg, ctx.pricing) * len(jobs)
        how = f", each checked by {cfg.settings.llm.vision_model}" if cfg.settings.qc.vision else ", pixel checks only"
        per_run = ", ".join(f"{run.run.id} {len(run.jobs)}" for run in prepared if run.jobs)
        check_only_count = len(jobs) - len(to_make)
        counts = (
            f"{len(to_make)} image(s) to make and {check_only_count} to check" if check_only_count
            else f"{len(jobs)} image(s) to make"
        )
        console.print(escape(
            f"Comparing {len(setup.picks)} unit(s) × {len(setup.runs)} run(s): {counts} "
            f"({per_run}){how} ≈ {format_usd(estimate)} (≈ {usd_neurons(estimate):,.0f} neurons). "
            "No QC retries: the report measures first images."
        ))
        line = _free_plan_line(
            cfg, ctx.pricing, ledger, now, per_item_usd=estimate / len(jobs), count=len(jobs), noun="image(s)",
            what="image and check" if cfg.settings.qc.vision else "image", again=again,
        )
        if line is not None:
            console.print(escape(line))
        if not yes and not typer.confirm("Start?", default=False):
            console.print("Nothing was generated.")
            return
        budget = Budget.from_ledger(
            ledger, cfg.settings.budget, now=now, force=force,
            warn=lambda message: console.print(f"[yellow]{escape(message)}[/yellow]"),
        )
        meter = Meter(project=folder.name, ledger=ledger, budget=budget, pricing=ctx.pricing)
        result = asyncio.run(_compare_render(cfg, prepared, meter, len(jobs)))
        console.print(escape(f"Cost this run ≈ {format_usd(meter.run_usd)} (≈ {usd_neurons(meter.run_usd):,.0f} neurons)."))
    stats, cells = collect(folder, setup)
    path = write_report(folder, setup, plan, stats, cells, now=datetime.now().astimezone())
    for row in report_lines(stats):
        console.print(escape(row))
    console.print(escape(f"Report: {path}"))
    if result is not None:
        _exit_for(result, RunLog(None, secrets=_secrets(cfg)), again=again)


async def _compare_render(cfg: AppConfig, prepared: list[PreparedRun], meter: Meter, total: int) -> RunResult:
    columns = (TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn())
    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task("Comparing", total=total)
        async with build_client(cfg) as client:
            return await render_runs(
                client, prepared, meter, secrets=_secrets(cfg), control=RunControl(cfg.settings.retry.circuit_breaker),
                sleep=_wait, on_done=lambda unit_id: progress.update(task, advance=1),
            )


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


# The page always holds an event stream open, and uvicorn would wait for it forever on Ctrl+C: open
# connections get two seconds, then they're closed.
SERVE_OPTIONS = {"log_level": "warning", "timeout_graceful_shutdown": 2}
BROWSER_DELAY_S = 0.8  # the server is listening by then; opening sooner can show "connection refused"


def _open_browser(url: str) -> None:
    webbrowser.open(url)


def _open_when_listening(url: str) -> threading.Timer:
    timer = threading.Timer(BROWSER_DELAY_S, _open_browser, args=(url,))
    timer.daemon = True
    timer.start()
    return timer


def _serve(site: object, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(site, host=host, port=port, **SERVE_OPTIONS)  # type: ignore[arg-type]


@app.command()
def review(
    project: Path | None = typer.Option(None, "--project", "-p", help="Project folder or name. Default: the most recent."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open the browser."),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace folder."),
) -> None:
    """Open the review page (spec §12): the plan, sheets, tests and the gallery, on 127.0.0.1 only."""
    root = workspace.resolve()
    try:
        directory = resolve_project(root, project)
    except ProjectError as exc:
        console.print("Project: (none found)")
        _fail(str(exc), EXIT_USER_ERROR)
    console.print(f"Project: {escape(directory.name)}")
    if not directory.is_relative_to(root):
        _fail(
            f"{directory} is outside the workspace {root}. The review page serves only projects inside its "
            "workspace: run `stickman review` with -w set to the project's workspace.",
            EXIT_USER_ERROR,
        )
    try:
        cfg = load_config(root)
    except ConfigError:
        try:
            cfg = load_config(root, need_secrets=False)  # a settings error is raised again here, and exits 3
        except ConfigError as exc:
            _fail(str(exc), EXIT_CONFIG_ERROR)
        console.print(
            "[yellow]No Cloudflare credentials in .env: approving and editing work; regenerating, replanning and "
            "making candidates need CF_ACCOUNT_ID and CF_API_TOKEN.[/yellow]"
        )
    host, port = cfg.settings.review.host, cfg.settings.review.port
    if not _port_free(host, port):
        _fail(
            f"Port {port} on {host} is in use (another `stickman review`?). Stop it, or set review.port in "
            "config/settings.yaml.",
            EXIT_USER_ERROR,
        )
    from stickman.review.app import create_app  # the web stack loads only for this command

    site = create_app(root, directory, cfg.settings, client_factory=lambda: build_client(cfg), secrets=_secrets(cfg))
    url = f"http://{host}:{port}/"
    console.print(escape(f"Review page: {url} (Ctrl+C stops it)"))
    opener = None if no_browser else _open_when_listening(url)
    try:
        _serve(site, host, port)
    except KeyboardInterrupt:
        pass
    finally:
        if opener is not None:
            opener.cancel()  # the server stopped before the browser opened: there's no page to show
    console.print("Review page stopped.")


HAND_EDITED_NOTE = (
    "These prompts don't match their fields (edited by hand?), so they were left as they are, although their "
    "images are sent with reference images the prompt doesn't describe. `stickman replan <unit>` rebuilds one; "
    "`prompt_locked: true` keeps it: "
)


def _refresh_prompts(path: Path, ctx: RenderContext, loaded: LoadedPlan) -> Plan:
    """Tool-built prompts rebuilt for the reference images that exist now, written to plan.yaml
    hash-checked (spec §7.4 [M5]). Hand-edited and locked prompts are left as they are."""
    plan = loaded.plan
    references = find_references(
        ctx.workspace, use_references=ctx.settings.image.use_references, style_version=plan.style_version,
        mascot=ctx.mascot, cast=plan.cast, library=ctx.library,
    )
    try:
        plan, refresh, _ = refresh_plan_file(path, loaded, style=ctx.style, mascot=ctx.mascot, references=references,
                                             write=write_plan)
    except PlanChangedError:
        _fail(f"{path.name} changed on disk while the prompts were being rebuilt. Nothing was written; run the command again.",
              EXIT_USER_ERROR)
    except PlanValidationError as exc:
        _fail(f"{path.name} was not written: the result failed validation (a bug): " + "; ".join(exc.errors[:5]), EXIT_USER_ERROR)
    except OSError as exc:
        _fail(f"Can't write {path}: {exc}. Nothing was generated; run the command again.", EXIT_USER_ERROR)
    if refresh.hand_edited:
        console.print(f"[yellow]{escape(HAND_EDITED_NOTE + ', '.join(refresh.hand_edited))}[/yellow]")
    if refresh.rebuilt:
        console.print(escape(
            f"Rebuilt the image prompts of {len(refresh.rebuilt)} unit(s) for the reference images that now exist: "
            + ", ".join(refresh.rebuilt)
        ))
    return plan


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
        loaded = load_plan(directory / "plan.yaml", library_ids=ctx.library_ids)
    except PlanValidationError as exc:
        _fail("plan.yaml is invalid:\n" + "\n".join(exc.errors), EXIT_USER_ERROR)
    lock = ProjectLock(directory)
    try:
        lock.acquire()
    except LockHeld as exc:
        # Windows can give a crashed run's PID to another process, so the lock may only look held.
        _fail(
            f"{exc}. Let it finish, or stop it, then run this again.\n"
            f"If no stickman is running, delete `{lock.path}`.",
            EXIT_USER_ERROR,
        )
    try:
        plan = _refresh_prompts(directory / "plan.yaml", ctx, loaded)
        try:
            builder = JobBuilder(ctx, plan)
            expected = builder.expected()
        except ConfigError as exc:
            _fail(str(exc), EXIT_CONFIG_ERROR)
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
    todo = [unit for unit in plan.units() if needs_work(store.unit(unit.id))]
    if limit is not None:
        todo = todo[:limit]
    if not todo:
        console.print("Nothing to generate: every unit has a checked image, or is stale (never regenerated automatically).")
        return
    try:
        jobs = builder.jobs(todo)  # also reads the vision check's mascot reference, through ReferenceFiles
    except JobError as exc:
        _fail(str(exc), EXIT_USER_ERROR)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    log = RunLog.for_project(directory, secrets=_secrets(cfg))
    ledger = Ledger(cfg.workspace / LEDGER_FILE)
    now = datetime.now().astimezone()
    budget = Budget.from_ledger(
        ledger, cfg.settings.budget, now=now, force=force,
        warn=lambda message: console.print(f"[yellow]{escape(message)}[/yellow]"),
    )
    meter = Meter(project=directory.name, ledger=ledger, budget=budget, pricing=ctx.pricing)
    _print_run_start(jobs, store, cfg, ctx, ledger, now)
    result, softened = asyncio.run(_render(cfg, ctx, directory, store, meter, builder, jobs, log))
    for line in summary_lines(
        store.state, [unit.id for unit in plan.units()], result=result, run_usd=meter.run_usd,
        possibly_billed=meter.possibly_billed, week_usd=budget.spent, weekly_usd=cfg.settings.budget.weekly_usd,
        softened=softened,
    ):
        console.print(escape(log.mask(line)))
    _exit_for(result, log)


def _check_usd(cfg: AppConfig, pricing: PricingConfig) -> float:
    return check_usd(cfg.settings, pricing)


def _check_only(store: StateStore, jobs: list[RenderJob]) -> set[str]:
    """Units whose next step is a check of an image they already have: made before QC, saved just
    before a kill, or left unchecked because the checker couldn't be reached (spec §9.4 [M4])."""
    ids = set()
    for job in jobs:
        chain = chain_of(store.unit(job.unit_id), job.fingerprint)
        if chain and chain[-1].qc is None:
            ids.add(job.unit_id)
    return ids


def _print_run_start(jobs: list[RenderJob], store: StateStore, cfg: AppConfig, ctx: RenderContext, ledger: Ledger, now: datetime) -> None:
    check_only = _check_only(store, jobs)
    to_make = [job for job in jobs if job.unit_id not in check_only]
    check = _check_usd(cfg, ctx.pricing)
    estimate = sum(job.estimate_usd for job in to_make) + check * len(jobs)
    how = (f", each checked by {cfg.settings.llm.vision_model}" if cfg.settings.qc.vision
           else ", pixel checks only (qc.vision is off)")
    cost = f"≈ {format_usd(estimate)} (≈ {usd_neurons(estimate):,.0f} neurons; retries not included)"
    if to_make:
        console.print(escape(f"Generating {len(to_make)} unit(s) on {to_make[0].model}{how} {cost}."))
    if check_only:
        console.print(escape(
            f"Checking {len(check_only)} image(s) that have no check yet (made before QC, or left unchecked when "
            "the checker couldn't be reached). Each is checked, not made again, unless it fails its check; then it "
            "is retried like any other."
        ))
        if not to_make:
            console.print(escape(f"Cost {cost}."))
    line = _free_plan_line(
        cfg, ctx.pricing, ledger, now, per_item_usd=estimate / len(jobs), count=len(jobs), noun="unit(s)",
        what="image and check" if cfg.settings.qc.vision else "image", again="stickman resume",
    )
    if line is not None:
        console.print(escape(line))


def _free_plan_line(
    cfg: AppConfig, pricing: PricingConfig, ledger: Ledger, now: datetime, *,
    per_item_usd: float, count: int, noun: str, what: str, again: str,
) -> str | None:
    """How many more images fit in today's free allocation (spec §10.1 [M3]); None on the paid plan."""
    if cfg.settings.account.plan != "free":
        return None
    used = ledger.neurons_since(utc_day_start(now))
    allowance = pricing.free_daily_neurons
    per_item = usd_neurons(per_item_usd)
    fit = max(0, math.floor((allowance - used) / per_item)) if per_item > 0 else count
    line = (
        f"Free plan: about {used:,.0f} of {allowance:,.0f} neurons used today (UTC), "
        f"so about {fit} more {noun} fit ({what}) before the reset at {_reset_time(now)}."
    )
    if fit < count:
        line += f" The run pauses at the daily limit; `{again}` continues after the reset."
    return line


async def _render(
    cfg: AppConfig, ctx: RenderContext, directory: Path, store: StateStore, meter: Meter, builder: JobBuilder,
    jobs: list[RenderJob], log: RunLog,
) -> tuple[RunResult, list[str]]:
    columns = (TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn())
    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task("Generating", total=len(jobs))

        def done(unit_id: str) -> None:
            finished = [store.unit(job.unit_id).status for job in jobs]
            progress.update(
                task,
                advance=1,
                description=f"done {finished.count('generated')} · review {finished.count('needs_review')} · "
                f"failed {finished.count('failed')} · cost ≈ {format_usd(meter.run_usd)}",
            )

        async with build_client(cfg) as client:
            # A soften or redesign reruns stage 3 for one unit, on the run's meter: its calls are budget-checked.
            # They go through GuardedChat: none starts once the run is stopping, and their temporary
            # errors count toward the run's circuit breaker (kind `llm`).
            control = RunControl(cfg.settings.retry.circuit_breaker)
            runner = StageRunner(
                GuardedChat(client, control), cfg.settings.llm, cfg.settings.retry,
                cache_dir=directory / ".cache" / "llm", log=log, meter=meter, sleep=_wait,
            )
            planning = PlanningContext(ctx.workspace, ctx.settings, ctx.style, ctx.mascot, ctx.rules, ctx.library)
            renderer = Renderer(
                client, store, meter, builder, qc=cfg.settings.qc, vision_model=cfg.settings.llm.vision_model,
                retry=cfg.settings.retry, concurrency=cfg.settings.render.concurrency, log=log,
                rewriter=PlanRewriter(runner, planning, directory / "plan.yaml"), sleep=_wait, on_done=done,
                control=control,
            )
            return await renderer.run(jobs), renderer.softened


def _exit_for(result: RunResult, log: RunLog, *, again: str = "stickman resume") -> None:
    """spec §9.5, §9.7: pauses exit 2 with how to continue; a rejected token exits 3."""
    if result.stop is None:
        return
    if result.stop is StopReason.DAILY_LIMIT:
        _fail(
            "The free daily allocation of 10,000 neurons is used up. Finished images are kept; "
            f"run `{again}` after the daily reset (00:00 UTC, {_reset_time(datetime.now().astimezone())}).",
            EXIT_PAUSED,
        )
    if result.stop is StopReason.BUDGET:
        _fail(
            log.mask(
                f"Weekly budget reached: {result.detail}. Nothing new was started. Run `{again} --force` "
                "to go on anyway, or raise budget.weekly_usd in config/settings.yaml."
            ),
            EXIT_PAUSED,
        )
    if result.stop is StopReason.CIRCUIT_BREAKER:
        _fail(f"Possible outage — run `{again}` later.", EXIT_PAUSED)
    _fail(TOKEN_HELP, EXIT_CONFIG_ERROR)
