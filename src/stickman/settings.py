"""Settings (config/settings.yaml) and secrets (.env) — spec §2.2–2.3."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

DEFAULT_CONFIG_FILES: tuple[str, ...] = (
    "settings.yaml",
    "style.yaml",
    "visual_rules.yaml",
    "mascot.yaml",
    "pricing.yaml",
)

Aspect = Literal["16:9", "9:16"]


class ConfigError(Exception):
    """A config or secrets problem the user must fix (CLI exit code 3)."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountSettings(_Section):
    plan: Literal["free", "paid"] = "paid"


class InputSettings(_Section):
    max_minutes: float = Field(5.0, gt=0)


class ProjectSettings(_Section):
    recent_hours: float = Field(24.0, gt=0)


class TimingSettings(_Section):
    fallback_wps: float = Field(2.5, gt=0)
    min_pace_lines: int = Field(5, ge=2)


class MergeSettings(_Section):
    max_lines: int = Field(3, ge=1)
    min_scene_seconds: float = Field(0.0, ge=0)


class SplitSettings(_Section):
    split_seconds: float = Field(7.0, gt=0)
    split_words: int = Field(20, ge=2)
    min_part_seconds: float = Field(2.5, gt=0)
    max_parts: int = 2

    @field_validator("max_parts")
    @classmethod
    def _only_two_parts(cls, value: int) -> int:
        if value != 2:
            raise ValueError("v1 supports only split.max_parts = 2")
        return value


class LLMSettings(_Section):
    planner_model: str = "@cf/openai/gpt-oss-120b"
    fallback_model: str = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    vision_model: str = "@cf/qwen/qwen3.8-27b"
    batch_size: int = Field(8, ge=1)
    temperature: float = Field(0.4, ge=0, le=2)


class ImageSettings(_Section):
    model: str = "@cf/black-forest-labs/flux-2-klein-9b"
    steps: int = Field(25, ge=1)
    use_references: bool = True
    sizes: dict[Aspect, tuple[int, int]] = Field(
        default_factory=lambda: {"16:9": (1920, 1088), "9:16": (1088, 1920)}
    )
    sheet_size: tuple[int, int] = (768, 1024)
    anchor_size: tuple[int, int] = (1024, 768)
    ref_max_side: int = Field(512, ge=64)


class RenderSettings(_Section):
    concurrency: int = Field(4, ge=1)
    timeout_s: float = Field(120.0, gt=0)
    est_seconds_per_image: float = Field(10.0, gt=0)


class RetrySettings(_Section):
    rate_limit_max: int = Field(6, ge=0)
    transient_max: int = Field(3, ge=0)
    qc_max: int = Field(2, ge=0)
    circuit_breaker: int = Field(5, ge=1)


class BudgetSettings(_Section):
    weekly_usd: float = Field(15.0, gt=0)
    warn_ratio: float = Field(0.8, gt=0, le=1)
    expected_retry_rate: float = Field(0.25, ge=0)


class QCSettings(_Section):
    near_white_lum: int = Field(215, ge=0, le=255)
    color_sat: float = Field(0.25, ge=0, le=1)
    max_color_fraction: float = Field(0.01, ge=0, le=1)
    min_white_fraction: float = Field(0.55, ge=0, le=1)
    black_lum: int = Field(50, ge=0, le=255)
    max_black_blob_fraction: float = Field(0.04, ge=0, le=1)
    min_ink_fraction: float = Field(0.005, ge=0, le=1)
    uniform_std_max: float = Field(6.0, ge=0)
    blur_lap_var_min: float = Field(15.0, ge=0)
    min_idea_score: int = Field(3, ge=1, le=5)


class TrialSettings(_Section):
    """The `test:` section: test images generated before the full batch."""

    count: int = Field(3, ge=1)


class BootstrapSettings(_Section):
    anchor_candidates: int = Field(4, ge=1)
    model: str = "@cf/black-forest-labs/flux-2-klein-9b"


class ExportSettings(_Section):
    fps: int = Field(30, ge=1)
    zoom: bool = False
    zoom_max: float = Field(1.06, ge=1.0)
    captions: Literal["source", "corrected"] = "corrected"
    caption_max_chars: int = Field(42, ge=10)


class ReviewSettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        if value != "127.0.0.1":
            raise ValueError("review.host must be 127.0.0.1 (the review page is local-only)")
        return value


class Settings(_Section):
    schema_version: Literal[1] = 1
    account: AccountSettings = Field(default_factory=AccountSettings)
    input: InputSettings = Field(default_factory=InputSettings)
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    timing: TimingSettings = Field(default_factory=TimingSettings)
    merge: MergeSettings = Field(default_factory=MergeSettings)
    split: SplitSettings = Field(default_factory=SplitSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    image: ImageSettings = Field(default_factory=ImageSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    retry: RetrySettings = Field(default_factory=RetrySettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    qc: QCSettings = Field(default_factory=QCSettings)
    test: TrialSettings = Field(default_factory=TrialSettings)
    bootstrap: BootstrapSettings = Field(default_factory=BootstrapSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)


class Secrets(BaseSettings):
    """CF_ACCOUNT_ID and CF_API_TOKEN from the environment or .env."""

    # Exception to extra="forbid": env/.env contain unrelated variables (PATH, etc.).
    model_config = SettingsConfigDict(extra="ignore")

    cf_account_id: str
    cf_api_token: SecretStr

    @field_validator("cf_account_id")
    @classmethod
    def _account_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty")
        return value.strip()

    @field_validator("cf_api_token")
    @classmethod
    def _token_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("empty")
        return SecretStr(value.get_secret_value().strip())


@dataclass(frozen=True)
class AppConfig:
    workspace: Path
    settings: Settings
    secrets: Secrets | None


def _format_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
    )


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {_format_errors(exc)}") from exc


def load_secrets(env_file: Path) -> Secrets:
    try:
        return Secrets(_env_file=env_file if env_file.exists() else None)
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors()})
        raise ConfigError(
            f"Missing or empty {', '.join(names)}. Copy .env.example to .env in "
            f"{env_file.parent} and fill in your Cloudflare Account ID and a Workers AI API token."
        ) from None


def load_config(workspace: Path, *, need_secrets: bool = True) -> AppConfig:
    settings = load_settings(workspace / "config" / "settings.yaml")
    secrets = load_secrets(workspace / ".env") if need_secrets else None
    return AppConfig(workspace=workspace, settings=settings, secrets=secrets)


def default_config_text(name: str) -> str:
    if name not in DEFAULT_CONFIG_FILES:
        raise ValueError(f"unknown default config file {name!r}")
    return (files("stickman") / "defaults" / name).read_text(encoding="utf-8")
