"""stickman command-line interface (spec §13)."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import NoReturn, TypeVar

import typer
from rich.console import Console
from rich.markup import escape

from stickman.cf.client import CloudflareClient
from stickman.cf.errors import CFError, ErrorCategory
from stickman.ingest.parse import parse_duration, parse_script
from stickman.ingest.timing import build_timeline
from stickman.plan.llm import PlanningError, StageRunner
from stickman.plan.models import CastMember, PlanValidationError
from stickman.plan.planner import REPLAN_KEYS, PlanOutcome, load_planning_context, plan_script, replan_unit
from stickman.plan.store import PlanChangedError, load_plan, to_document, update_unit, write_plan
from stickman.project import ProjectError, create_project, project_dir, resolve_project, slugify
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

    async def go() -> T:
        async with build_client(cfg) as client:
            runner = StageRunner(
                client, cfg.settings.llm, cfg.settings.retry, cache_dir=directory / ".cache" / "llm", log=log
            )
            return await work(runner)

    try:
        return asyncio.run(go())
    except PlanningError as exc:
        _fail(f"Planning failed: {exc}. Every attempt is logged in {log.path}.{again}", EXIT_USER_ERROR)
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
        _fail(f"Cloudflare error: {exc}", EXIT_USER_ERROR)


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
    directory = project_dir(root, slug, date.today())
    console.print(f"Project: {escape(directory.name)}")
    if aspect not in ASPECTS:
        _fail(f'--aspect must be "16:9" or "9:16", not {aspect!r}', EXIT_USER_ERROR)
    try:
        cfg = load_config(root)
        planning = load_planning_context(root, cfg.settings)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
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
    write_plan(directory / "plan.yaml", to_document(outcome.plan), expected_hash=None)
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
        _fail(str(exc), EXIT_USER_ERROR)
    console.print(f"Project: {escape(directory.name)}")
    try:
        cfg = load_config(root)
        planning = load_planning_context(root, cfg.settings)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
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
    try:
        write_plan(path, loaded.doc, expected_hash=loaded.hash)
    except PlanChangedError:
        _fail("plan.yaml changed on disk while the LLM was working. Nothing was written; run the command again.", EXIT_USER_ERROR)
    console.print(escape(f"Replanned {unit}: {updated.visual_idea}"))
