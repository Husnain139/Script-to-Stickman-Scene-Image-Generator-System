"""stickman command-line interface (spec §13)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import typer
from rich.console import Console

from stickman.cf.client import CloudflareClient
from stickman.cf.errors import CFError, ErrorCategory
from stickman.settings import (
    DEFAULT_CONFIG_FILES,
    AppConfig,
    ConfigError,
    default_config_text,
    load_config,
)

EXIT_USER_ERROR = 1
EXIT_CONFIG_ERROR = 3

ENV_EXAMPLE = (
    "# Cloudflare dashboard -> Workers AI -> copy the Account ID.\n"
    '# Token: "Create a Workers AI API Token" template (Workers AI - Read + Edit).\n'
    "CF_ACCOUNT_ID=\n"
    "CF_API_TOKEN=\n"
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback()
def main() -> None:
    """Script-to-stickman scene image generator."""


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
        console.print(f"[red]{exc}[/red]")
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
            console.print(
                "[red]Cloudflare rejected the token. Create one with the "
                '"Create a Workers AI API Token" template, or give a custom token '
                "Workers AI - Read and Workers AI - Edit, then update CF_API_TOKEN in .env.[/red]"
            )
            raise typer.Exit(EXIT_CONFIG_ERROR)
        console.print(f"[red]Token check failed: {exc}[/red]")
        raise typer.Exit(EXIT_USER_ERROR)
