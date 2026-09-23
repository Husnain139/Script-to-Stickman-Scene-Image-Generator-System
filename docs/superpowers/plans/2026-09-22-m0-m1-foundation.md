# Stickman M0 + M1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the project skeleton, configuration, Cloudflare client and `stickman init` (M0), plus the whole script-reading and split pipeline. The pipeline must pass the spec §4.7 golden test with no API calls (M1). Finish with the live M0 checks that answer spec §15.

**Architecture:** A Python package `stickman`, using the `src/` layout.
- **`ingest`** turns script text into `RawLine`s, then into a `Timeline` of `TimedLine`s.
- **`split`** groups lines into `Scene`s and decides the splits using plain code (same result every time). It produces `Unit`s, which are the images to generate.
- **`cf`** is the only code that talks to Cloudflare. It raises `CFError`s that are already sorted into categories.
- **`settings`** loads `config/settings.yaml` and `.env`.

Scene planning, rendering, QC, the review page and export (M2–M9) get their own plans later.

**Tech Stack:** Python 3.12 (managed by uv), typer, rich, pydantic v2, pydantic-settings, ruamel.yaml, httpx (async), pytest. Pillow is a dev-only dependency, used by the M0 check script.

**Source documents:** `spec.md` (the specification) and `plan.md` (milestones). Section numbers like "§4.7" refer to `spec.md`.

## Global Constraints

- Python **3.12** (`.python-version`), package `stickman` in `src/stickman/`, managed with **uv**. Run everything with `uv run …`.
- Runtime dependencies for M0/M1 are limited to: `typer`, `rich`, `pydantic>=2`, `pydantic-settings`, `ruamel.yaml`, `httpx`. Dev: `pytest`, plus `pillow` (for the M0 check script only).
- Secrets live **only** in `.env` as `CF_ACCOUNT_ID` and `CF_API_TOKEN`. Never write them to config, logs or test fixtures. `.env` is listed in `.gitignore`.
- The stock images in `style_refs/` are **never** sent to any API.
- Times are float seconds. A **word** is a whitespace-separated token of the **original** text (`4`, `80%`, `40,000`, `didn't` and `E.` each count as one). Word counts are never taken from corrected text.
- Unit IDs are `NNN` (for example `006`), or `NNNa`/`NNNb` for split parts. File names are `<unit>_<MM-SS.s>.png`, for example `006b_00-24.3.png`.
- All Pydantic models use `extra="forbid"`. Every YAML config file has `schema_version: 1`.
- Settings rules: `split.max_parts` must be `2`, and `review.host` must be `127.0.0.1`.
- CLI exit codes: `0` success, `1` user or validation error, `2` paused, `3` auth or config error.
- Tests: `pytest`. Tests that call the real API are marked `live` and are excluded by default (`-m 'not live'`).
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Pass it as a second `-m`.
- Shell: the commands below work in both PowerShell and Git Bash, run from the workspace root `C:\huSSNAIN PROJECTS\Tan Project`.

## File Map

```
pyproject.toml, .python-version, .gitignore
src/stickman/__init__.py                 version
src/stickman/settings.py                 Settings/Secrets models, loaders, default config access
src/stickman/defaults/*.yaml             settings, style, visual_rules, mascot, pricing (copied by `init`)
src/stickman/cf/errors.py                ErrorCategory, CFError, classify()
src/stickman/cf/client.py                CloudflareClient (chat, generate_image)
src/stickman/cli.py                      typer app; `init`
src/stickman/ingest/models.py            RawLine, TimedLine
src/stickman/ingest/words.py             count_words()
src/stickman/ingest/parse.py             parse_script / parse_timestamped / parse_srt / parse_duration
src/stickman/ingest/timing.py            Timeline, measure_pace, build_timeline, length_warning
src/stickman/ingest/fragments.py         is_likely_fragment, fragment_hints
src/stickman/split/scenes.py             Scene, GroupingError, build_scenes, merge_short_scenes
src/stickman/split/engine.py             SplitDecision, needs_split, cut_time, decide_split
src/stickman/split/units.py              Unit, unit_filename, build_units
scripts/m0_probe.py                      live checks against the real API (manual, costs ~$1)
docs/m0-findings.md                      M0 answers (written in Task 14)
tests/...                                mirrors src; tests/fixtures/scripts/first-sleep.txt; tests/fixtures/cf/*.json
```

---

### Task 1: Project setup (uv, git, package, first test)

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `src/stickman/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: the importable package `stickman` with `stickman.__version__ == "0.1.0"`, and the console script `stickman` → `stickman.cli:app` (created in Task 5).

- [ ] **Step 1: Install uv and Python 3.12**

Run: `winget install --id=astral-sh.uv -e`. If winget isn't available, run `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` instead. Open a new terminal afterwards.
Run: `uv --version`. Expected: `uv 0.x.y`.
Run: `uv python install 3.12`. Expected: it installs, or reports it's already installed.

- [ ] **Step 2: Start the git repository**

Run: `git init` (in `C:\huSSNAIN PROJECTS\Tan Project`). Expected: `Initialized empty Git repository`.

- [ ] **Step 3: Write `.python-version`, `.gitignore` and `pyproject.toml`**

`.python-version`:
```
3.12
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
projects/
library/
ledger.jsonl
m0_out/
style_refs/
```

`pyproject.toml`:
```toml
[project]
name = "stickman"
version = "0.1.0"
description = "Script-to-stickman scene image generator (Cloudflare Workers AI)"
requires-python = ">=3.12"
dependencies = [
  "typer>=0.12",
  "rich>=13",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "ruamel.yaml>=0.18",
  "httpx>=0.27",
]

[project.scripts]
stickman = "stickman.cli:app"

[dependency-groups]
dev = ["pytest>=8", "pillow>=10"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/stickman"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: calls the real Cloudflare API and costs money (run with -m live)"]
addopts = "-m 'not live' --import-mode=importlib"
```

- [ ] **Step 4: Write the failing smoke test**

`tests/test_smoke.py`:
```python
import stickman


def test_package_imports_with_version():
    assert stickman.__version__ == "0.1.0"
```

- [ ] **Step 5: Install and run the test to see it fail**

Run: `uv sync`, then `uv run pytest tests/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman'`. That's because the `src/stickman` folder doesn't exist yet, so the build can't include it. If `uv sync` itself fails for the same reason, that also counts as the expected failure.

- [ ] **Step 6: Create the package**

`src/stickman/__init__.py`:
```python
"""Script-to-stickman scene image generator."""

__version__ = "0.1.0"
```

- [ ] **Step 7: Run the test to see it pass**

Run: `uv sync`, then `uv run pytest tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 8: Commit** (this includes the existing design documents)

```bash
git add .python-version .gitignore pyproject.toml uv.lock src/stickman/__init__.py tests/test_smoke.py plan.md spec.md docs/superpowers/plans/2026-09-22-m0-m1-foundation.md
git commit -m "chore: scaffold stickman package with uv and pytest" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Settings, secrets and default config files

**Files:**
- Create: `src/stickman/settings.py`
- Create: `src/stickman/defaults/settings.yaml`, `style.yaml`, `visual_rules.yaml`, `mascot.yaml`, `pricing.yaml`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class ConfigError(Exception)`
  - `class Settings` with sections `.account .input .project .timing .merge .split .llm .image .render .retry .budget .qc .test .bootstrap .export .review`
  - `class TimingSettings(fallback_wps: float, min_pace_lines: int)`
  - `class MergeSettings(max_lines: int, min_scene_seconds: float)`
  - `class SplitSettings(split_seconds: float, split_words: int, min_part_seconds: float, max_parts: int)`
  - `class Secrets(cf_account_id: str, cf_api_token: SecretStr)`
  - `@dataclass AppConfig(workspace: Path, settings: Settings, secrets: Secrets | None)`
  - `load_settings(path: Path) -> Settings`
  - `load_secrets(env_file: Path) -> Secrets`
  - `load_config(workspace: Path, *, need_secrets: bool = True) -> AppConfig`
  - `DEFAULT_CONFIG_FILES: tuple[str, ...]`
  - `default_config_text(name: str) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_settings.py`:
```python
import pytest
from ruamel.yaml import YAML

from stickman.settings import (
    DEFAULT_CONFIG_FILES,
    ConfigError,
    Settings,
    default_config_text,
    load_config,
    load_secrets,
    load_settings,
)


@pytest.fixture(autouse=True)
def _no_cf_env(monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_packaged_default_settings_match_model_defaults(tmp_path):
    path = write(tmp_path / "settings.yaml", default_config_text("settings.yaml"))
    assert load_settings(path).model_dump() == Settings().model_dump()


def test_missing_settings_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.yaml") == Settings()


def test_partial_file_overrides_only_given_keys(tmp_path):
    s = load_settings(write(tmp_path / "s.yaml", "split:\n  split_seconds: 5\n"))
    assert s.split.split_seconds == 5.0
    assert s.split.split_words == 20
    assert s.image.sizes["16:9"] == (1920, 1080)


def test_max_parts_other_than_two_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="max_parts"):
        load_settings(write(tmp_path / "s.yaml", "split:\n  max_parts: 3\n"))


def test_review_host_must_be_loopback(tmp_path):
    with pytest.raises(ConfigError, match="127.0.0.1"):
        load_settings(write(tmp_path / "s.yaml", "review:\n  host: 0.0.0.0\n"))


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="split_secs"):
        load_settings(write(tmp_path / "s.yaml", "split:\n  split_secs: 5\n"))


def test_invalid_yaml_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_settings(write(tmp_path / "s.yaml", "split: [unclosed\n"))


def test_secrets_are_read_from_env_file(tmp_path):
    env = write(tmp_path / ".env", "CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n")
    secrets = load_secrets(env)
    assert secrets.cf_account_id == "acc123"
    assert secrets.cf_api_token.get_secret_value() == "tok-secret"
    assert "tok-secret" not in repr(secrets)


def test_missing_secret_is_named_in_the_error(tmp_path):
    env = write(tmp_path / ".env", "CF_ACCOUNT_ID=acc123\n")
    with pytest.raises(ConfigError, match="CF_API_TOKEN"):
        load_secrets(env)


def test_empty_secret_is_rejected(tmp_path):
    env = write(tmp_path / ".env", "CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=\n")
    with pytest.raises(ConfigError, match="CF_API_TOKEN"):
        load_secrets(env)


def test_load_config_without_secrets(tmp_path):
    cfg = load_config(tmp_path, need_secrets=False)
    assert cfg.secrets is None
    assert cfg.settings == Settings()


@pytest.mark.parametrize("name", DEFAULT_CONFIG_FILES)
def test_every_default_config_file_parses_with_schema_version(name):
    data = YAML(typ="safe").load(default_config_text(name))
    assert data["schema_version"] == 1
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.settings'`.

- [ ] **Step 3: Write `src/stickman/settings.py`**

```python
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
        default_factory=lambda: {"16:9": (1920, 1080), "9:16": (1080, 1920)}
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
    model: str = "@cf/black-forest-labs/flux-2-dev"


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
```

- [ ] **Step 4: Write the five default config files**

`src/stickman/defaults/settings.yaml`:
```yaml
# stickman settings (spec §2.3). Every key is optional; omitted keys use these defaults.
schema_version: 1
account:
  plan: paid                  # free | paid — daily-limit stop only applies on free
input:
  max_minutes: 5              # design limit; longer scripts run with a warning
project:
  recent_hours: 24            # generate/resume/regen/export need -p if >1 project changed in this window
timing:
  fallback_wps: 2.5           # words per second when pace can't be measured
  min_pace_lines: 5
merge:
  max_lines: 3
  min_scene_seconds: 0        # 0 = off
split:
  split_seconds: 7.0
  split_words: 20
  min_part_seconds: 2.5
  max_parts: 2                # v1 accepts only 2
llm:
  planner_model: "@cf/openai/gpt-oss-120b"
  fallback_model: "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
  vision_model: "@cf/qwen/qwen3.8-27b"
  batch_size: 8
  temperature: 0.4
image:
  model: "@cf/black-forest-labs/flux-2-klein-9b"
  steps: 25                   # sent only to models that accept it (FLUX.2 dev)
  use_references: true
  sizes:
    "16:9": [1920, 1080]
    "9:16": [1080, 1920]
  sheet_size: [768, 1024]
  anchor_size: [1024, 768]
  ref_max_side: 512
render:
  concurrency: 4
  timeout_s: 120              # replace with a measured value after M5
  est_seconds_per_image: 10
retry:
  rate_limit_max: 6
  transient_max: 3
  qc_max: 2
  circuit_breaker: 5
budget:
  weekly_usd: 15.0            # usage only; the plan fee is not included
  warn_ratio: 0.8
  expected_retry_rate: 0.25
qc:
  near_white_lum: 215
  color_sat: 0.25
  max_color_fraction: 0.01
  min_white_fraction: 0.55
  black_lum: 50
  max_black_blob_fraction: 0.04
  min_ink_fraction: 0.005
  uniform_std_max: 6
  blur_lap_var_min: 15
  min_idea_score: 3
test:
  count: 3
bootstrap:
  anchor_candidates: 4
  model: "@cf/black-forest-labs/flux-2-dev"
export:
  fps: 30
  zoom: false
  zoom_max: 1.06
  captions: corrected         # source | corrected
  caption_max_chars: 42
review:
  host: 127.0.0.1             # must stay 127.0.0.1
  port: 8765
```

`src/stickman/defaults/style.yaml`:
```yaml
schema_version: 1
style_version: 1
style_text: >-
  Hand-drawn cartoon stickman illustration, clean black ink line art on a plain white background.
  Thin, even line weight. Characters have large round circle heads, simple dot eyes, short curved
  eyebrows, simple expressive mouths, and 2 to 4 short hair strokes on top of the head. Bodies,
  arms and legs are single thin lines. Hands are small rounded mitten shapes without fingers.
  Feet are small outlined ovals. A light hatched shadow under the feet or a simple ground line.
  Minimal clutter and lots of white space. No colour, no grey fills, no shading, no gradients.
strict_clause: >-
  Absolutely no text of any kind anywhere in the image: no letters, no words, no numbers, no
  captions, no labels, no signatures, no logos, no watermarks. The background stays plain white
  and unfilled.
negative_prompt: >-
  text, letters, words, numbers, caption, label, signature, watermark, logo, colour, colored,
  shading, grey fill, gradient, photorealistic, 3d render, dark background, filled background,
  extra limbs, extra heads, fingers
```

`src/stickman/defaults/visual_rules.yaml`:
```yaml
schema_version: 1
rules:
  - "Anything that normally contains text is drawn with shapes only, no letters or numbers."
  - "Daytime: a small sun in an upper corner. Night: a small crescent moon and a few stars. Never fill or darken the background."
  - "Firelight: flame lines around the fire and short straight lines radiating outward."
  - "Sleep: closed eyes (curved lines), lying pose, a simple blanket. Never 'Zzz'."
  - "Clocks: a round face with two hands, no numerals."
  - "Books and papers: blank pages or a few wavy lines. Screens: blank or simple icons/shapes."
  - "Charts: bars or an arrow line without labels. Money: coins or bills without numbers or symbols."
  - "Maps and signs: simple shapes; no names or writing."
  - "Thoughts and dreams: a cloud bubble containing a small picture, never words."
  - "Sensitive content: tasteful and symbolic, never graphic."
```

`src/stickman/defaults/mascot.yaml`:
```yaml
schema_version: 1
id: mascot
name: "Everyman"
figures: 1
# identity: visible in every shot (close-ups, lying under a blanket, etc.)
identity: >-
  the main character: an average-height stickman with a large round head, exactly three short
  hair strokes curling to the right on top of the head, dot eyes and short curved eyebrows
# default_outfit: optional costume; may be hidden (blanket, close-up) or omitted per scene
default_outfit: >-
  a small solid-black necktie and solid-black shoes
sheet: library/mascot/sheet_v1.png
ref: library/mascot/ref_v1.png
seed: null            # set at bootstrap approval
model: null
style_version: 1
```

`src/stickman/defaults/pricing.yaml`:
```yaml
# Cost estimates (spec §9.6). Tiles = ceil(w/512) * ceil(h/512); MP rounded up to 2 decimals.
schema_version: 1
free_daily_usd: 0.11          # 10,000 neurons x $0.011 / 1k
models:
  "@cf/black-forest-labs/flux-2-klein-4b":
    {kind: image, out_tile: 0.000287, in_tile: 0.000059, supports_negative_prompt: false, supports_steps: false}
  "@cf/black-forest-labs/flux-2-klein-9b":
    {kind: image, first_mp: 0.015, extra_mp: 0.002, in_mp: 0.002, supports_negative_prompt: false, supports_steps: false}
  "@cf/black-forest-labs/flux-2-dev":
    {kind: image, out_tile_step: 0.00041, in_tile_step: 0.00021, supports_negative_prompt: false, supports_steps: true}
  "@cf/openai/gpt-oss-120b": {kind: llm, in_per_m: 0.35, out_per_m: 0.75}
  "@cf/meta/llama-3.3-70b-instruct-fp8-fast": {kind: llm, in_per_m: 0.293, out_per_m: 2.253}
  "@cf/qwen/qwen3.8-27b": {kind: llm, in_per_m: 0.45, out_per_m: 3.20}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv sync`, then `uv run pytest tests/test_settings.py -v`
Expected: all PASS. If `test_packaged_default_settings_match_model_defaults` fails, the YAML and the model defaults differ. Fix the YAML so it matches the spec §2.3 values.

- [ ] **Step 6: Commit**

```bash
git add src/stickman/settings.py src/stickman/defaults tests/test_settings.py
git commit -m "feat: settings, secrets and default config files" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Cloudflare error categories

**Files:**
- Create: `src/stickman/cf/__init__.py` (empty), `src/stickman/cf/errors.py`
- Test: `tests/cf/test_errors.py`

**Interfaces:**
- Produces:
  - `class ErrorCategory(StrEnum)` with the values `RATE_LIMITED="rate_limited"`, `DAILY_LIMIT="daily_limit"`, `AUTH="auth"`, `BAD_REQUEST="bad_request"`, `REFUSED="refused"`, `TRANSIENT="transient"`
  - `class CFError(Exception)` with the attributes `.category: ErrorCategory`, `.message: str`, `.status: int | None`, `.possibly_billed: bool`, `.retry_after: float | None`
  - `classify(status: int, body: str, *, plan: str) -> ErrorCategory`

- [ ] **Step 1: Write the failing tests**

`tests/cf/test_errors.py`:
```python
import pytest

from stickman.cf.errors import CFError, ErrorCategory, classify

DAILY = "You have used up your daily free allocation of 10,000 neurons, please upgrade"


@pytest.mark.parametrize(
    "status, body, plan, expected",
    [
        (401, "", "paid", ErrorCategory.AUTH),
        (403, "forbidden", "paid", ErrorCategory.AUTH),
        (429, "Too many requests", "paid", ErrorCategory.RATE_LIMITED),
        (429, DAILY, "free", ErrorCategory.DAILY_LIMIT),
        (429, DAILY, "paid", ErrorCategory.RATE_LIMITED),
        (400, DAILY, "free", ErrorCategory.DAILY_LIMIT),
        (500, "internal error", "paid", ErrorCategory.TRANSIENT),
        (503, "", "paid", ErrorCategory.TRANSIENT),
        (400, "width must be at most 1920", "paid", ErrorCategory.BAD_REQUEST),
        (400, "Input was flagged as NSFW", "paid", ErrorCategory.REFUSED),
        (422, "Blocked by content policy", "paid", ErrorCategory.REFUSED),
    ],
)
def test_classify(status, body, plan, expected):
    assert classify(status, body, plan=plan) is expected


def test_cf_error_carries_details():
    err = CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True)
    assert err.category is ErrorCategory.TRANSIENT
    assert err.possibly_billed is True
    assert err.status is None
    assert "transient" in str(err)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/cf/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.cf'`.

- [ ] **Step 3: Implement**

`src/stickman/cf/__init__.py`: an empty file.

`src/stickman/cf/errors.py`:
```python
"""Cloudflare error categories (spec §9.5)."""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    RATE_LIMITED = "rate_limited"
    DAILY_LIMIT = "daily_limit"
    AUTH = "auth"
    BAD_REQUEST = "bad_request"
    REFUSED = "refused"
    TRANSIENT = "transient"


# Lower-case substrings of Cloudflare error bodies. Refine them from the real
# responses recorded in M0 (Task 14; tests/fixtures/cf/).
DAILY_LIMIT_MARKERS: tuple[str, ...] = ("daily free allocation", "daily limit")
REFUSAL_MARKERS: tuple[str, ...] = ("nsfw", "content policy", "safety", "flagged")


class CFError(Exception):
    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        status: int | None = None,
        possibly_billed: bool = False,
        retry_after: float | None = None,
    ) -> None:
        self.category = category
        self.message = message
        self.status = status
        self.possibly_billed = possibly_billed
        self.retry_after = retry_after
        where = f" (HTTP {status})" if status is not None else ""
        super().__init__(f"{category}{where}: {message}")


def classify(status: int, body: str, *, plan: str) -> ErrorCategory:
    text = body.lower()
    if plan == "free" and status < 500 and any(m in text for m in DAILY_LIMIT_MARKERS):
        return ErrorCategory.DAILY_LIMIT
    if status in (401, 403):
        return ErrorCategory.AUTH
    if status == 429:
        return ErrorCategory.RATE_LIMITED
    if status >= 500:
        return ErrorCategory.TRANSIENT
    if 400 <= status < 500:
        if any(m in text for m in REFUSAL_MARKERS):
            return ErrorCategory.REFUSED
        return ErrorCategory.BAD_REQUEST
    return ErrorCategory.TRANSIENT
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/cf/test_errors.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/cf/__init__.py src/stickman/cf/errors.py tests/cf/test_errors.py
git commit -m "feat: classify Cloudflare errors into categories" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Cloudflare client (chat and image generation)

**Files:**
- Create: `src/stickman/cf/client.py`
- Test: `tests/cf/test_client.py`

**Interfaces:**
- Consumes: `ErrorCategory`, `CFError` and `classify` from Task 3.
- Produces:
  - `DEFAULT_BASE_URL = "https://api.cloudflare.com/client/v4"`
  - `@dataclass LLMResult(text: str, input_tokens: int | None, output_tokens: int | None, raw: dict)`
  - `@dataclass ImageResult(image_bytes: bytes)`
  - `class CloudflareClient(account_id: str, api_token: str, *, plan: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None, base_url: str = DEFAULT_BASE_URL)`, an async context manager with these methods:
    - `async chat(model: str, messages: list[dict], *, temperature: float = 0.4, max_tokens: int = 4096, response_format: dict | None = None) -> LLMResult`
    - `async generate_image(model: str, *, prompt: str, width: int, height: int, seed: int, steps: int | None = None, guidance: float | None = None, input_images: Sequence[bytes] = ()) -> ImageResult`
    - `async aclose()`
  - **Error behaviour:** raises `CFError` on HTTP errors (sorted by category), on timeouts (`TRANSIENT`, `possibly_billed=True`), on network errors (`TRANSIENT`), and on responses with an unexpected shape (`BAD_REQUEST`). **There are no retries here.** Retry policy belongs to the render module (M3).

- [ ] **Step 1: Write the failing tests**

`tests/cf/test_client.py`:
```python
import asyncio
import base64

import httpx
import pytest

from stickman.cf.client import CloudflareClient
from stickman.cf.errors import CFError, ErrorCategory

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def make_client(handler, plan="paid"):
    return CloudflareClient(
        "acc123", "tok-secret", plan=plan, timeout_s=5, transport=httpx.MockTransport(handler)
    )


def generate(handler, **overrides):
    kwargs = dict(prompt="a stickman", width=1920, height=1080, seed=42)
    kwargs.update(overrides)

    async def go():
        async with make_client(handler) as client:
            return await client.generate_image(KLEIN_4B, **kwargs)

    return asyncio.run(go())


def chat(handler, **overrides):
    async def go():
        async with make_client(handler) as client:
            return await client.chat("@cf/openai/gpt-oss-120b", [{"role": "user", "content": "hi"}], **overrides)

    return asyncio.run(go())


def test_generate_image_sends_multipart_and_decodes_base64():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"success": True, "result": {"image": base64.b64encode(PNG).decode()}})

    result = generate(handler, input_images=[b"ref-zero"])
    assert result.image_bytes == PNG
    assert seen["url"] == f"https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/{KLEIN_4B}"
    assert seen["auth"] == "Bearer tok-secret"
    assert seen["ctype"].startswith("multipart/form-data")
    for field in (b'name="prompt"', b'name="width"', b'name="height"', b'name="seed"'):
        assert field in seen["body"]
    assert b'name="input_image_0"; filename="input_image_0.png"' in seen["body"]
    assert b'name="steps"' not in seen["body"]


def test_prompt_only_request_is_still_multipart():
    seen = {}

    def handler(request):
        seen["ctype"] = request.headers["content-type"]
        request.read()
        return httpx.Response(200, json={"result": {"image": base64.b64encode(PNG).decode()}})

    generate(handler)
    assert seen["ctype"].startswith("multipart/form-data")


def test_steps_are_sent_when_given():
    seen = {}

    def handler(request):
        seen["body"] = request.read()
        return httpx.Response(200, json={"result": {"image": base64.b64encode(PNG).decode()}})

    generate(handler, steps=25)
    assert b'name="steps"' in seen["body"]


def test_binary_image_response_is_accepted():
    result = generate(lambda request: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    assert result.image_bytes == PNG


def test_response_without_image_is_bad_request():
    with pytest.raises(CFError) as info:
        generate(lambda request: httpx.Response(200, json={"success": True, "result": {}}))
    assert info.value.category is ErrorCategory.BAD_REQUEST


def test_more_than_four_reference_images_is_rejected():
    with pytest.raises(ValueError, match="at most 4"):
        generate(lambda request: httpx.Response(200), input_images=[b"x"] * 5)


def test_chat_parses_openai_shape_and_sends_response_format():
    seen = {}

    def handler(request):
        seen["json"] = request.read()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
    result = chat(handler, response_format=fmt)
    assert result.text == '{"ok": true}'
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    assert b'"response_format"' in seen["json"]
    assert b'"model":"@cf/openai/gpt-oss-120b"' in seen["json"].replace(b" ", b"")


def test_chat_accepts_result_envelope():
    body = {"result": {"choices": [{"message": {"content": "OK"}}]}, "success": True}
    result = chat(lambda request: httpx.Response(200, json=body))
    assert result.text == "OK"
    assert result.input_tokens is None


def test_chat_endpoint_url():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    chat(handler)
    assert seen["url"] == "https://api.cloudflare.com/client/v4/accounts/acc123/ai/v1/chat/completions"


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (401, "bad token", ErrorCategory.AUTH),
        (429, "slow down", ErrorCategory.RATE_LIMITED),
        (500, "oops", ErrorCategory.TRANSIENT),
        (400, "bad width", ErrorCategory.BAD_REQUEST),
    ],
)
def test_http_errors_are_classified(status, body, expected):
    with pytest.raises(CFError) as info:
        generate(lambda request: httpx.Response(status, text=body))
    assert info.value.category is expected
    assert info.value.status == status


def test_retry_after_header_is_parsed():
    with pytest.raises(CFError) as info:
        generate(lambda request: httpx.Response(429, text="slow", headers={"retry-after": "7"}))
    assert info.value.retry_after == 7.0


def test_timeout_is_transient_and_possibly_billed():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(CFError) as info:
        generate(handler)
    assert info.value.category is ErrorCategory.TRANSIENT
    assert info.value.possibly_billed is True


def test_network_error_is_transient_not_billed():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(CFError) as info:
        generate(handler)
    assert info.value.category is ErrorCategory.TRANSIENT
    assert info.value.possibly_billed is False
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/cf/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.cf.client'`.

- [ ] **Step 3: Implement `src/stickman/cf/client.py`**

```python
"""The only module that talks to Cloudflare Workers AI (spec §9)."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from stickman.cf.errors import CFError, ErrorCategory, classify

DEFAULT_BASE_URL = "https://api.cloudflare.com/client/v4"
MAX_REFERENCE_IMAGES = 4


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ImageResult:
    image_bytes: bytes


class CloudflareClient:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        *,
        plan: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._account_url = f"{base_url}/accounts/{account_id}"
        self._plan = plan
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def __aenter__(self) -> CloudflareClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format
        response = await self._post(f"{self._account_url}/ai/v1/chat/completions", json=body)
        data = response.json()
        if isinstance(data, dict) and "choices" not in data and isinstance(data.get("result"), dict):
            data = data["result"]
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise CFError(
                ErrorCategory.BAD_REQUEST, f"unexpected chat response shape: {str(data)[:300]}"
            ) from exc
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw=data,
        )

    async def generate_image(
        self,
        model: str,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        steps: int | None = None,
        guidance: float | None = None,
        input_images: Sequence[bytes] = (),
    ) -> ImageResult:
        if len(input_images) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"at most {MAX_REFERENCE_IMAGES} reference images are allowed")
        # (None, value) tuples make httpx send plain multipart fields; FLUX.2 requires
        # multipart even when there are no reference images.
        fields: list[tuple[str, tuple[str | None, Any] | tuple[str, bytes, str]]] = [
            ("prompt", (None, prompt)),
            ("width", (None, str(width))),
            ("height", (None, str(height))),
            ("seed", (None, str(seed))),
        ]
        if steps is not None:
            fields.append(("steps", (None, str(steps))))
        if guidance is not None:
            fields.append(("guidance", (None, str(guidance))))
        for index, image in enumerate(input_images):
            name = f"input_image_{index}"
            fields.append((name, (f"{name}.png", image, "image/png")))
        response = await self._post(f"{self._account_url}/ai/run/{model}", files=fields)
        return ImageResult(image_bytes=_extract_image(response))

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise CFError(ErrorCategory.TRANSIENT, f"timeout: {exc!r}", possibly_billed=True) from exc
        except httpx.TransportError as exc:
            raise CFError(ErrorCategory.TRANSIENT, f"network error: {exc!r}") from exc
        if response.status_code >= 400:
            raise CFError(
                classify(response.status_code, response.text, plan=self._plan),
                response.text[:500],
                status=response.status_code,
                retry_after=_retry_after(response),
            )
        return response


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _extract_image(response: httpx.Response) -> bytes:
    if response.headers.get("content-type", "").startswith("image/"):
        return response.content
    try:
        data = response.json()
    except ValueError as exc:
        raise CFError(ErrorCategory.BAD_REQUEST, "image response is neither an image nor JSON") from exc
    result = data.get("result") if isinstance(data, dict) else None
    encoded = (result.get("image") if isinstance(result, dict) else None) or (
        data.get("image") if isinstance(data, dict) else None
    )
    if not encoded:
        raise CFError(ErrorCategory.BAD_REQUEST, f"no image in response: {str(data)[:300]}")
    try:
        return base64.b64decode(encoded)
    except (binascii.Error, ValueError) as exc:
        raise CFError(ErrorCategory.BAD_REQUEST, "image field is not valid base64") from exc
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/cf -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/cf/client.py tests/cf/test_client.py
git commit -m "feat: Cloudflare client for chat and FLUX.2 image generation" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `stickman init`

**Files:**
- Create: `src/stickman/cli.py`
- Test: `tests/test_cli_init.py`

**Interfaces:**
- Consumes:
  - From Task 2: `load_config`, `ConfigError`, `DEFAULT_CONFIG_FILES`, `default_config_text`, `AppConfig`
  - From Task 4: `CloudflareClient`
  - From Task 3: `CFError`, `ErrorCategory`
- Produces:
  - `app: typer.Typer`
  - `build_client(cfg: AppConfig) -> CloudflareClient`. Tests replace it with a fake.
  - The command `stickman init [-w PATH] [--skip-token-check]`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_init.py`:
```python
import pytest
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.settings import DEFAULT_CONFIG_FILES

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_cf_env(monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.models = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def chat(self, model, messages, **kwargs):
        self.models.append(model)
        if self.error:
            raise self.error


def write_env(tmp_path):
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")


def test_help_lists_init():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output


def test_init_creates_config_and_folders_then_fails_without_secrets(tmp_path):
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 3
    assert "CF_ACCOUNT_ID" in result.output
    for name in DEFAULT_CONFIG_FILES:
        assert (tmp_path / "config" / name).exists()
    assert (tmp_path / "library").is_dir()
    assert (tmp_path / "projects").is_dir()
    assert "CF_API_TOKEN=" in (tmp_path / ".env.example").read_text(encoding="utf-8")


def test_init_keeps_existing_config(tmp_path):
    (tmp_path / "config").mkdir()
    custom = "schema_version: 1\nsplit:\n  split_seconds: 5\n"
    (tmp_path / "config" / "settings.yaml").write_text(custom, encoding="utf-8")
    runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert (tmp_path / "config" / "settings.yaml").read_text(encoding="utf-8") == custom


def test_init_verifies_token_with_fallback_model(tmp_path, monkeypatch):
    write_env(tmp_path)
    fake = FakeClient()
    monkeypatch.setattr(cli, "build_client", lambda cfg: fake)
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert fake.models == ["@cf/meta/llama-3.3-70b-instruct-fp8-fast"]
    assert "token works" in result.output


def test_init_rejected_token_exits_3(tmp_path, monkeypatch):
    write_env(tmp_path)
    fake = FakeClient(CFError(ErrorCategory.AUTH, "bad token", status=401))
    monkeypatch.setattr(cli, "build_client", lambda cfg: fake)
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 3
    assert "CF_API_TOKEN" in result.output  # single token: safe from rich line-wrapping


def test_init_other_api_error_exits_1(tmp_path, monkeypatch):
    write_env(tmp_path)
    fake = FakeClient(CFError(ErrorCategory.TRANSIENT, "timeout"))
    monkeypatch.setattr(cli, "build_client", lambda cfg: fake)
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 1


def test_init_skip_token_check(tmp_path, monkeypatch):
    write_env(tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda cfg: pytest.fail("must not call the API"))
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path), "--skip-token-check"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_cli_init.py -v`
Expected: FAIL with `ImportError: cannot import name 'cli' from 'stickman'`.

- [ ] **Step 3: Implement `src/stickman/cli.py`**

```python
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
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_cli_init.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/stickman/cli.py tests/test_cli_init.py
git commit -m "feat: stickman init writes config and verifies the Cloudflare token" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Reading timestamped scripts and counting words

**Files:**
- Create: `src/stickman/ingest/__init__.py` (empty), `src/stickman/ingest/models.py`, `src/stickman/ingest/words.py`, `src/stickman/ingest/parse.py`
- Test: `tests/ingest/test_parse.py`

**Interfaces:**
- Produces:
  - `count_words(text: str) -> int`
  - `@dataclass(frozen=True) RawLine(number: int, start: float, text: str, srt_end: float | None = None, source_line: int = 0)`
  - `@dataclass(frozen=True) TimedLine(number: int, start: float, end: float, text: str)`, with the properties `.words -> int` and `.duration -> float`
  - `class ScriptParseError(ValueError)` with `.line_number: int | None`
  - `parse_timestamped(text: str) -> list[RawLine]`
  - `parse_script(text: str) -> list[RawLine]`. It strips a BOM and checks the input isn't empty and that timestamps strictly increase. SRT detection is added in Task 7.

- [ ] **Step 1: Write the failing tests**

`tests/ingest/test_parse.py`:
```python
import pytest

from stickman.ingest.models import TimedLine
from stickman.ingest.parse import ScriptParseError, parse_script
from stickman.ingest.words import count_words


def triples(lines):
    return [(line.number, line.start, line.text) for line in lines]


def test_parses_m_ss_lines():
    lines = parse_script("0:00: Hello there.\n0:02: Second line.\n")
    assert triples(lines) == [(1, 0.0, "Hello there."), (2, 2.0, "Second line.")]


def test_parses_h_mm_ss_fractions_and_separators():
    lines = parse_script("1:02:03 - One.\n1:02:04.5: Two.\n1:02:05,25 Three.\n")
    assert [line.start for line in lines] == [3723.0, 3724.5, 3725.25]
    assert [line.text for line in lines] == ["One.", "Two.", "Three."]


def test_blank_lines_ignored_and_bom_stripped():
    lines = parse_script("\ufeff0:00: A.\n\n\n0:01: B.\n")
    assert triples(lines) == [(1, 0.0, "A."), (2, 1.0, "B.")]
    assert lines[1].source_line == 4


def test_unparseable_line_reports_line_number():
    with pytest.raises(ScriptParseError) as info:
        parse_script("0:00: A.\nhello world\n")
    assert info.value.line_number == 2


def test_timestamps_must_strictly_increase():
    with pytest.raises(ScriptParseError) as info:
        parse_script("0:05: A.\n0:05: B.\n")
    assert info.value.line_number == 2


def test_decreasing_timestamp_rejected():
    with pytest.raises(ScriptParseError) as info:
        parse_script("0:05: A.\n0:03: B.\n")
    assert info.value.line_number == 2


def test_empty_script_rejected():
    with pytest.raises(ScriptParseError, match="no lines"):
        parse_script("\n\n")


@pytest.mark.parametrize("line", ["1:75:00: Too many minutes.", "0:75: Too many seconds."])
def test_out_of_range_minutes_or_seconds_rejected(line):
    with pytest.raises(ScriptParseError):
        parse_script(line + "\n")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("It's 90 at night, 40,000 years ago.", 7),
        ("80% 1992 didn't E.", 4),
        ("  spaced   out  ", 2),
        ("", 0),
    ],
)
def test_count_words(text, expected):
    assert count_words(text) == expected


def test_timed_line_words_and_duration():
    line = TimedLine(1, 2.0, 6.0, "The sun is gone.")
    assert line.words == 4
    assert line.duration == 4.0
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/ingest/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.ingest'`.

- [ ] **Step 3: Implement**

`src/stickman/ingest/__init__.py`: an empty file.

`src/stickman/ingest/words.py`:
```python
"""Word counting (spec §4.2): whitespace tokens of the original text."""


def count_words(text: str) -> int:
    return len(text.split())
```

`src/stickman/ingest/models.py`:
```python
"""Script line models."""

from __future__ import annotations

from dataclasses import dataclass

from stickman.ingest.words import count_words


@dataclass(frozen=True)
class RawLine:
    """One parsed script entry before end times are known."""

    number: int
    start: float
    text: str
    srt_end: float | None = None
    source_line: int = 0


@dataclass(frozen=True)
class TimedLine:
    """One script line with its start and end time."""

    number: int
    start: float
    end: float
    text: str

    @property
    def words(self) -> int:
        return count_words(self.text)

    @property
    def duration(self) -> float:
        return self.end - self.start
```

`src/stickman/ingest/parse.py`:
```python
"""Parse narration scripts into RawLines (spec §4.1)."""

from __future__ import annotations

import re

from stickman.ingest.models import RawLine

_TIMESTAMPED = re.compile(
    r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\s*[:\-–]?\s+(.+)$"
)


class ScriptParseError(ValueError):
    def __init__(self, message: str, line_number: int | None = None) -> None:
        self.line_number = line_number
        super().__init__(f"line {line_number}: {message}" if line_number else message)


def _fraction(digits: str | None) -> float:
    return int(digits.ljust(3, "0")) / 1000 if digits else 0.0


def parse_timestamped(text: str) -> list[RawLine]:
    lines: list[RawLine] = []
    for source_line, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        match = _TIMESTAMPED.match(raw)
        if match is None:
            raise ScriptParseError(f"not a timestamped line: {raw.strip()[:40]!r}", source_line)
        hours, minutes, seconds, fraction, body = match.groups()
        minutes_i, seconds_i = int(minutes), int(seconds)
        if seconds_i >= 60 or (hours is not None and minutes_i >= 60):
            raise ScriptParseError("minutes and seconds must be below 60", source_line)
        start = int(hours or 0) * 3600 + minutes_i * 60 + seconds_i + _fraction(fraction)
        lines.append(
            RawLine(number=len(lines) + 1, start=start, text=body.strip(), source_line=source_line)
        )
    return lines


def _check_increasing(lines: list[RawLine]) -> None:
    for previous, current in zip(lines, lines[1:]):
        if current.start <= previous.start:
            raise ScriptParseError(
                f"timestamp {current.start:.3f}s is not after the previous line ({previous.start:.3f}s)",
                current.source_line,
            )


def parse_script(text: str) -> list[RawLine]:
    text = text.lstrip("\ufeff")
    lines = parse_timestamped(text)
    if not lines:
        raise ScriptParseError("the script contains no lines")
    _check_increasing(lines)
    return lines
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/ingest/test_parse.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/ingest tests/ingest/test_parse.py
git commit -m "feat: parse timestamped scripts and count words" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Reading SRT files and detecting the format

**Files:**
- Modify: `src/stickman/ingest/parse.py` (add `parse_srt` and SRT detection in `parse_script`)
- Test: `tests/ingest/test_srt.py`

**Interfaces:**
- Consumes: `RawLine` and `ScriptParseError` from Task 6.
- Produces: `parse_srt(text: str) -> list[RawLine]`, which sets `srt_end` on every line. After this task, `parse_script` treats any text containing `-->` as SRT.

- [ ] **Step 1: Write the failing tests**

`tests/ingest/test_srt.py`:
```python
import pytest

from stickman.ingest.parse import ScriptParseError, parse_script

SRT = """1
00:00:00,000 --> 00:00:02,500
It's 9 at night,
40,000 years ago.

2
00:00:02,500 --> 00:00:06,000
The sun is gone.
"""


def test_parses_srt_cues_joining_text_lines():
    lines = parse_script(SRT)
    assert [(l.number, l.start, l.text, l.srt_end) for l in lines] == [
        (1, 0.0, "It's 9 at night, 40,000 years ago.", 2.5),
        (2, 2.5, "The sun is gone.", 6.0),
    ]


def test_srt_without_index_lines_and_dot_milliseconds():
    text = "00:00:01.000 --> 00:00:02.000\nOne.\n\n00:00:03.000 --> 00:00:04.000\nTwo.\n"
    lines = parse_script(text)
    assert [(l.start, l.srt_end, l.text) for l in lines] == [(1.0, 2.0, "One."), (3.0, 4.0, "Two.")]


def test_bad_srt_time_line_reports_its_line_number():
    text = "1\n00:00:00,000 --> 00:00:01,000\nA\n\n2\n00:00:0x --> y\nB\n"
    with pytest.raises(ScriptParseError) as info:
        parse_script(text)
    assert info.value.line_number == 6


def test_srt_cue_without_text_rejected():
    with pytest.raises(ScriptParseError, match="no text"):
        parse_script("1\n00:00:00,000 --> 00:00:01,000\n")


def test_srt_starts_must_increase():
    text = "00:00:05,000 --> 00:00:06,000\nA\n\n00:00:04,000 --> 00:00:07,000\nB\n"
    with pytest.raises(ScriptParseError) as info:
        parse_script(text)
    assert info.value.line_number == 4
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/ingest/test_srt.py -v`
Expected: FAIL. The SRT text is read as timestamped lines and fails with `not a timestamped line`.

- [ ] **Step 3: Implement.** Add this to `src/stickman/ingest/parse.py`, below `parse_timestamped`:

```python
_SRT_TIMES = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _hms(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + _fraction(millis)


def parse_srt(text: str) -> list[RawLine]:
    rows = text.splitlines()
    cues: list[RawLine] = []
    i = 0
    while i < len(rows):
        if not rows[i].strip():
            i += 1
            continue
        block_start = i + 1  # 1-based line number of the block's first line
        block: list[str] = []
        while i < len(rows) and rows[i].strip():
            block.append(rows[i])
            i += 1
        offset = 1 if block[0].strip().isdigit() else 0
        time_line = block_start + offset
        match = _SRT_TIMES.search(block[offset]) if offset < len(block) else None
        if match is None:
            raise ScriptParseError(
                "expected an SRT time line 'HH:MM:SS,mmm --> HH:MM:SS,mmm'", time_line
            )
        body = " ".join(row.strip() for row in block[offset + 1 :] if row.strip())
        if not body:
            raise ScriptParseError("SRT cue has no text", time_line)
        cues.append(
            RawLine(
                number=len(cues) + 1,
                start=_hms(*match.group(1, 2, 3, 4)),
                text=body,
                srt_end=_hms(*match.group(5, 6, 7, 8)),
                source_line=time_line,
            )
        )
    return cues
```

Then replace `parse_script` with:

```python
def parse_script(text: str) -> list[RawLine]:
    text = text.lstrip("\ufeff")
    lines = parse_srt(text) if "-->" in text else parse_timestamped(text)
    if not lines:
        raise ScriptParseError("the script contains no lines")
    _check_increasing(lines)
    return lines
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/ingest -v`
Expected: all PASS (both test_parse and test_srt).

- [ ] **Step 5: Commit**

```bash
git add src/stickman/ingest/parse.py tests/ingest/test_srt.py
git commit -m "feat: parse SRT scripts and auto-detect the format" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Timeline (pace, end times, duration option, length warning)

**Files:**
- Create: `src/stickman/ingest/timing.py`
- Modify: `src/stickman/ingest/parse.py` (add `parse_duration`)
- Test: `tests/ingest/test_timing.py`

**Interfaces:**
- Consumes:
  - From Task 6: `RawLine`, `TimedLine`, `count_words`, `ScriptParseError`
  - From Task 2: `TimingSettings`
- Produces:
  - `@dataclass(frozen=True) Timeline(lines: tuple[TimedLine, ...], pace_wps: float, end: float, pace_measured: bool)`
  - `measure_pace(raw: Sequence[RawLine], timing: TimingSettings) -> tuple[float, bool]`
  - `build_timeline(raw: Sequence[RawLine], timing: TimingSettings, *, duration: float | None = None) -> Timeline`
  - `length_warning(timeline: Timeline, max_minutes: float) -> str | None`
  - `parse_duration(value: str) -> float`

**Rules (spec §4.3):**
- A line ends where the next one starts.
- The last line's end is chosen in this order: `duration`, then the SRT `srt_end` of the last cue, then `start + words / pace`.
- `pace` = words of all lines except the last ÷ (last start − first start). It falls back to `fallback_wps` when there are fewer than `min_pace_lines` lines.
- The first line's start is set to 0.0, so the video starts at 0:00. Pace is measured from the original times.

- [ ] **Step 1: Write the failing tests**

`tests/ingest/test_timing.py`:
```python
import pytest

from stickman.ingest.models import RawLine
from stickman.ingest.parse import ScriptParseError, parse_duration
from stickman.ingest.timing import build_timeline, length_warning, measure_pace
from stickman.settings import TimingSettings

TIMING = TimingSettings()


def raw(*pairs, srt_end=None):
    lines = [RawLine(i, start, text, source_line=i) for i, (start, text) in enumerate(pairs, 1)]
    if srt_end is not None:
        last = lines[-1]
        lines[-1] = RawLine(last.number, last.start, last.text, srt_end=srt_end, source_line=last.number)
    return lines


FIVE = raw(
    (0.0, "one two three"),
    (2.0, "four five six"),
    (4.0, "seven eight nine"),
    (6.0, "ten eleven twelve"),
    (8.0, "a b c d e f"),
)


def test_each_line_ends_where_the_next_starts():
    timeline = build_timeline(FIVE, TIMING)
    assert [(l.start, l.end) for l in timeline.lines[:-1]] == [(0, 2), (2, 4), (4, 6), (6, 8)]


def test_pace_is_measured_from_all_but_the_last_line():
    assert measure_pace(FIVE, TIMING) == (pytest.approx(1.5), True)


def test_last_line_end_uses_measured_pace():
    timeline = build_timeline(FIVE, TIMING)
    assert timeline.pace_wps == pytest.approx(1.5)
    assert timeline.end == pytest.approx(12.0)  # 6 words / 1.5 wps after 8.0
    assert timeline.lines[-1].end == timeline.end


def test_duration_overrides_the_estimate():
    assert build_timeline(FIVE, TIMING, duration=10.0).end == 10.0


def test_duration_before_last_start_is_rejected():
    with pytest.raises(ScriptParseError, match="duration"):
        build_timeline(FIVE, TIMING, duration=7.0)


def test_srt_last_cue_end_is_used_when_no_duration():
    lines = raw((0.0, "a b"), (2.0, "c d"), (4.0, "e f"), (6.0, "g h"), (8.0, "i j"), srt_end=9.5)
    assert build_timeline(lines, TIMING).end == 9.5
    assert build_timeline(lines, TIMING, duration=11.0).end == 11.0


def test_fallback_pace_when_too_few_lines():
    timeline = build_timeline(raw((0.0, "hello there"), (3.0, "a b c d e")), TIMING)
    assert (timeline.pace_wps, timeline.pace_measured) == (2.5, False)
    assert timeline.end == pytest.approx(5.0)  # 5 words / 2.5 wps after 3.0


def test_first_line_is_stretched_back_to_zero():
    lines = raw((3.0, "a b"), (5.0, "c d"), (7.0, "e f"), (9.0, "g h"), (11.0, "i j"))
    timeline = build_timeline(lines, TIMING)
    assert timeline.lines[0].start == 0.0
    assert timeline.pace_wps == pytest.approx(1.0)  # 8 words over 8 s, from the original times


def test_length_warning():
    long_line = raw((0.0, "a b"), (300.0, "c"))
    warning = length_warning(build_timeline(long_line, TIMING, duration=301.0), max_minutes=5)
    assert warning is not None and "5-minute" in warning and "5:01" in warning
    assert length_warning(build_timeline(FIVE, TIMING), max_minutes=5) is None


@pytest.mark.parametrize(
    "value, expected", [("4:48", 288.0), ("1:02:03", 3723.0), ("95.5", 95.5), (" 0:30 ", 30.0)]
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["abc", "1:2:3:4", "-5", ""])
def test_parse_duration_rejects_bad_values(value):
    with pytest.raises(ValueError):
        parse_duration(value)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/ingest/test_timing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.ingest.timing'`.

- [ ] **Step 3: Implement**

Add this to the end of `src/stickman/ingest/parse.py`:
```python
def parse_duration(value: str) -> float:
    """Parse --duration: M:SS, H:MM:SS or plain seconds."""
    parts = value.strip().split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        raise ValueError(f"invalid duration {value!r}; use M:SS, H:MM:SS or seconds") from None
    if len(numbers) > 3 or any(n < 0 for n in numbers):
        raise ValueError(f"invalid duration {value!r}; use M:SS, H:MM:SS or seconds")
    total = 0.0
    for number in numbers:
        total = total * 60 + number
    return total
```

`src/stickman/ingest/timing.py`:
```python
"""Line end times and narrator pace (spec §4.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stickman.ingest.models import RawLine, TimedLine
from stickman.ingest.parse import ScriptParseError
from stickman.ingest.words import count_words
from stickman.settings import TimingSettings


@dataclass(frozen=True)
class Timeline:
    lines: tuple[TimedLine, ...]
    pace_wps: float
    end: float
    pace_measured: bool


def measure_pace(raw: Sequence[RawLine], timing: TimingSettings) -> tuple[float, bool]:
    if len(raw) < timing.min_pace_lines:
        return timing.fallback_wps, False
    span = raw[-1].start - raw[0].start
    words = sum(count_words(line.text) for line in raw[:-1])
    if span <= 0 or words == 0:
        return timing.fallback_wps, False
    return words / span, True


def build_timeline(
    raw: Sequence[RawLine], timing: TimingSettings, *, duration: float | None = None
) -> Timeline:
    pace, measured = measure_pace(raw, timing)
    last = raw[-1]
    if duration is not None:
        if duration <= last.start:
            raise ScriptParseError(
                f"--duration {duration:.3f}s must be after the last line's start ({last.start:.3f}s)"
            )
        end = duration
    elif last.srt_end is not None and last.srt_end > last.start:
        end = last.srt_end
    else:
        end = last.start + count_words(last.text) / pace
    timed = []
    for index, line in enumerate(raw):
        start = 0.0 if index == 0 else line.start
        stop = raw[index + 1].start if index + 1 < len(raw) else end
        timed.append(TimedLine(line.number, start, stop, line.text))
    return Timeline(lines=tuple(timed), pace_wps=pace, end=end, pace_measured=measured)


def length_warning(timeline: Timeline, max_minutes: float) -> str | None:
    if timeline.end <= max_minutes * 60:
        return None
    minutes, seconds = divmod(round(timeline.end), 60)
    return (
        f"Script runs {minutes}:{seconds:02d}, longer than the {max_minutes:g}-minute design limit. "
        "It will be processed, but it's outside the tested range."
    )
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/ingest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/ingest/timing.py src/stickman/ingest/parse.py tests/ingest/test_timing.py
git commit -m "feat: timeline with measured pace, end times and length warning" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Fragment hints

**Files:**
- Create: `src/stickman/ingest/fragments.py`
- Test: `tests/ingest/test_fragments.py`

**Interfaces:**
- Consumes: `TimedLine` from Task 6.
- Produces:
  - `is_likely_fragment(text: str, next_text: str | None) -> bool`
  - `fragment_hints(lines: Sequence[TimedLine]) -> list[int]`, which returns the line numbers of likely fragments

**Rules (spec §4.4):** a line is flagged when any of these is true:
- (a) its last character isn't one of `. ! ? … " ” ' )`
- (b) its last token is a single capital initial like `E.`, or a known abbreviation
- (c) the next line starts with a lowercase letter

- [ ] **Step 1: Write the failing tests**

`tests/ingest/test_fragments.py`:
```python
import pytest

from stickman.ingest.fragments import fragment_hints, is_likely_fragment
from stickman.ingest.models import TimedLine


@pytest.mark.parametrize(
    "text, next_text, expected",
    [
        ("Historian Roger E.", "Kirch went digging.", True),  # (b) initial
        ("He went to see Dr.", "Smith about it.", True),  # (b) abbreviation
        ("and then the fire", "Changed everything.", True),  # (a) no terminal punctuation
        ("He left.", "and never came back.", True),  # (c) next starts lowercase
        ("Then we got fire and everything changed.", "Fire didn't just push.", False),
        ('She said "stop."', "Nobody listened.", False),
        ("Is this real?", None, False),
        ("The watch.", "And people used it.", False),
    ],
)
def test_is_likely_fragment(text, next_text, expected):
    assert is_likely_fragment(text, next_text) is expected


def test_fragment_hints_returns_line_numbers():
    lines = [
        TimedLine(1, 0.0, 1.0, "Historian Roger E."),
        TimedLine(2, 1.0, 2.0, "Kirch went digging."),
        TimedLine(3, 2.0, 3.0, "The end."),
    ]
    assert fragment_hints(lines) == [1]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/ingest/test_fragments.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.ingest.fragments'`.

- [ ] **Step 3: Implement `src/stickman/ingest/fragments.py`**

```python
"""Rule-based fragment hints for the analyse stage (spec §4.4)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from stickman.ingest.models import TimedLine

_TERMINAL = (".", "!", "?", "…", '"', "”", "'", ")")
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.", "vs.", "etc.", "e.g.", "i.e.", "u.s."}
_INITIAL = re.compile(r"^[A-Z]\.$")


def is_likely_fragment(text: str, next_text: str | None) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if not stripped.endswith(_TERMINAL):
        return True
    last_token = stripped.split()[-1]
    if _INITIAL.match(last_token) or last_token.lower() in _ABBREVIATIONS:
        return True
    return bool(next_text and next_text.lstrip()[:1].islower())


def fragment_hints(lines: Sequence[TimedLine]) -> list[int]:
    hints = []
    for index, line in enumerate(lines):
        next_text = lines[index + 1].text if index + 1 < len(lines) else None
        if is_likely_fragment(line.text, next_text):
            hints.append(line.number)
    return hints
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/ingest/test_fragments.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/ingest/fragments.py tests/ingest/test_fragments.py
git commit -m "feat: rule-based fragment hints" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Scenes (grouping lines and merging short scenes)

**Files:**
- Create: `src/stickman/split/__init__.py` (empty), `src/stickman/split/scenes.py`
- Test: `tests/split/test_scenes.py`

**Interfaces:**
- Consumes: `TimedLine` from Task 6.
- Produces:
  - `@dataclass(frozen=True) Scene(number: int, lines: tuple[TimedLine, ...])`, with the properties `.start`, `.end`, `.duration`, `.words` and `.source_text` (the lines' texts joined with single spaces)
  - `class GroupingError(ValueError)`
  - `build_scenes(lines: Sequence[TimedLine], groups: Sequence[Sequence[int]], *, max_lines: int) -> list[Scene]`
  - `merge_short_scenes(scenes: Sequence[Scene], *, min_scene_seconds: float, max_lines: int) -> list[Scene]`. It numbers the scenes again from 1. `min_scene_seconds <= 0` switches it off.

- [ ] **Step 1: Write the failing tests**

`tests/split/test_scenes.py`:
```python
import pytest

from stickman.ingest.models import TimedLine
from stickman.split.scenes import GroupingError, build_scenes, merge_short_scenes


def timed(*specs):
    return [TimedLine(i, start, end, text) for i, (start, end, text) in enumerate(specs, 1)]


LINES = timed(
    (0.0, 2.0, "Historian Roger E."),
    (2.0, 9.0, "Kirch went digging through old diaries."),
    (9.0, 10.0, "The watch."),
    (10.0, 11.0, "They prayed."),
    (11.0, 15.0, "They stoked the fire and talked."),
)


def line_numbers(scenes):
    return [[line.number for line in scene.lines] for scene in scenes]


def test_build_scenes_merges_groups():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    assert [s.number for s in scenes] == [1, 2, 3, 4]
    first = scenes[0]
    assert first.source_text == "Historian Roger E. Kirch went digging through old diaries."
    assert (first.start, first.end, first.duration, first.words) == (0.0, 9.0, 9.0, 9)


@pytest.mark.parametrize(
    "groups",
    [
        [[1, 2], [4], [3], [5]],  # out of order
        [[1, 2], [3], [4]],  # line 5 missing
        [[1, 2], [2, 3], [4], [5]],  # line 2 twice
        [[1, 2], [], [3], [4], [5]],  # empty group
    ],
)
def test_invalid_groups_are_rejected(groups):
    with pytest.raises(GroupingError):
        build_scenes(LINES, groups, max_lines=3)


def test_group_larger_than_max_lines_is_rejected():
    with pytest.raises(GroupingError, match="more than 3"):
        build_scenes(LINES, [[1, 2, 3, 4], [5]], max_lines=3)


def test_short_scene_merge_is_off_at_zero():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    assert line_numbers(merge_short_scenes(scenes, min_scene_seconds=0, max_lines=3)) == [[1, 2], [3], [4], [5]]


def test_short_scenes_merge_into_previous_within_max_lines():
    scenes = build_scenes(LINES, [[1, 2], [3], [4], [5]], max_lines=3)
    merged = merge_short_scenes(scenes, min_scene_seconds=2.0, max_lines=3)
    # [3] (1 s) joins [1,2]; [4] (1 s) would make 4 lines, so it stays on its own.
    assert line_numbers(merged) == [[1, 2, 3], [4], [5]]
    assert [s.number for s in merged] == [1, 2, 3]


def test_short_first_scene_merges_into_next():
    lines = timed((0.0, 1.0, "Look."), (1.0, 6.0, "The sun is gone for good tonight."))
    scenes = build_scenes(lines, [[1], [2]], max_lines=3)
    assert line_numbers(merge_short_scenes(scenes, min_scene_seconds=2.0, max_lines=3)) == [[1, 2]]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/split/test_scenes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.split'`.

- [ ] **Step 3: Implement**

`src/stickman/split/__init__.py`: an empty file.

`src/stickman/split/scenes.py`:
```python
"""Scenes: script lines merged into one picture's worth of narration (spec §4.4)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stickman.ingest.models import TimedLine


class GroupingError(ValueError):
    """The line groups don't form a valid partition of the script."""


@dataclass(frozen=True)
class Scene:
    number: int
    lines: tuple[TimedLine, ...]

    @property
    def start(self) -> float:
        return self.lines[0].start

    @property
    def end(self) -> float:
        return self.lines[-1].end

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def words(self) -> int:
        return sum(line.words for line in self.lines)

    @property
    def source_text(self) -> str:
        return " ".join(line.text for line in self.lines)


def build_scenes(
    lines: Sequence[TimedLine], groups: Sequence[Sequence[int]], *, max_lines: int
) -> list[Scene]:
    for group in groups:
        if not group:
            raise GroupingError("groups must not be empty")
        if len(group) > max_lines:
            raise GroupingError(f"group {list(group)} has more than {max_lines} lines")
    expected = [line.number for line in lines]
    listed = [number for group in groups for number in group]
    if listed != expected:
        raise GroupingError("groups must list every line exactly once, in order")
    by_number = {line.number: line for line in lines}
    return [
        Scene(index, tuple(by_number[n] for n in group)) for index, group in enumerate(groups, 1)
    ]


def merge_short_scenes(
    scenes: Sequence[Scene], *, min_scene_seconds: float, max_lines: int
) -> list[Scene]:
    if min_scene_seconds <= 0:
        return list(scenes)
    merged: list[list[TimedLine]] = []
    for scene in scenes:
        lines = list(scene.lines)
        if (
            merged
            and scene.duration < min_scene_seconds
            and len(merged[-1]) + len(lines) <= max_lines
        ):
            merged[-1].extend(lines)
        else:
            merged.append(lines)
    if len(merged) >= 2:
        first = merged[0]
        if first[-1].end - first[0].start < min_scene_seconds and len(first) + len(merged[1]) <= max_lines:
            merged[1] = first + merged[1]
            merged.pop(0)
    return [Scene(index, tuple(lines)) for index, lines in enumerate(merged, 1)]
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/split/test_scenes.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/split tests/split/test_scenes.py
git commit -m "feat: build scenes from line groups and merge short scenes" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Split engine, units and file names

**Files:**
- Create: `src/stickman/split/engine.py`, `src/stickman/split/units.py`
- Test: `tests/split/test_engine.py`, `tests/split/test_units.py`

**Interfaces:**
- Consumes:
  - From Task 10: `Scene`
  - From Task 2: `SplitSettings`
  - From Task 6: `TimedLine`
- Produces (engine):
  - `@dataclass(frozen=True) SplitDecision(status: Literal["none", "split", "no_valid_cut"], candidates: tuple[int, ...] = (), cut_after_word: int | None = None, cut_time: float | None = None)`
  - `needs_split(scene: Scene, s: SplitSettings) -> bool`
  - `cut_time(scene: Scene, k: int) -> float`. It raises `ValueError` unless `1 <= k <= words-1`.
  - `decide_split(scene: Scene, candidates: Sequence[int], s: SplitSettings) -> SplitDecision`
- Produces (units):
  - `@dataclass(frozen=True) Unit(id: str, scene_number: int, part: str | None, start: float, end: float, source_text: str)`, with the property `.filename`
  - `unit_filename(unit_id: str, start: float) -> str`
  - `build_units(scenes: Sequence[Scene], decisions: Mapping[int, SplitDecision]) -> list[Unit]`

**Rules (spec §4.5, per-line timing):**
- A scene is a candidate if `duration >= split_seconds` or `words >= split_words`.
- `t_cut` is calculated **inside the original line that contains word k**: `L.start + (j / L.words) × (L.end − L.start)`, where j is word k's position within line L.
- A candidate is valid if both parts are at least `min_part_seconds` long, allowing a 1e-9 margin for floating-point rounding.
- The first valid candidate wins. If no candidate is valid, or there are none, the result is `no_valid_cut`.
- Candidates outside `1..words-1` are skipped.

- [ ] **Step 1: Write the failing engine tests**

`tests/split/test_engine.py`:
```python
import pytest

from stickman.ingest.models import TimedLine
from stickman.settings import SplitSettings
from stickman.split.engine import cut_time, decide_split, needs_split
from stickman.split.scenes import Scene

SPLIT = SplitSettings()


def words(n):
    return " ".join(f"w{i}" for i in range(1, n + 1))


def scene_of(*specs, number=1):
    return Scene(number, tuple(TimedLine(i, s, e, t) for i, (s, e, t) in enumerate(specs, 1)))


def test_needs_split_by_duration():
    assert needs_split(scene_of((0.0, 7.0, words(10))), SPLIT) is True
    assert needs_split(scene_of((0.0, 6.9, words(10))), SPLIT) is False


def test_needs_split_by_words():
    assert needs_split(scene_of((0.0, 3.0, words(20))), SPLIT) is True
    assert needs_split(scene_of((0.0, 3.0, words(19))), SPLIT) is False


def test_cut_time_single_line_is_proportional():
    assert cut_time(scene_of((10.0, 18.0, words(10))), 5) == pytest.approx(14.0)


def test_cut_time_uses_the_line_containing_the_cut_word():
    scene = scene_of((61.0, 63.0, "Historian Roger E."), (63.0, 70.0, words(20)))
    assert cut_time(scene, 13) == pytest.approx(66.5)  # word 10 of line 2's 20 words
    assert cut_time(scene, 3) == pytest.approx(63.0)  # last word of line 1 -> its end


@pytest.mark.parametrize("k", [0, 10])
def test_cut_time_rejects_out_of_range_k(k):
    with pytest.raises(ValueError):
        cut_time(scene_of((0.0, 8.0, words(10))), k)


def test_not_a_candidate_gives_none():
    decision = decide_split(scene_of((0.0, 3.0, words(5))), [2], SPLIT)
    assert decision.status == "none"


def test_first_valid_candidate_wins():
    decision = decide_split(scene_of((0.0, 8.0, words(10))), [2, 5, 6], SPLIT)
    assert (decision.status, decision.cut_after_word) == ("split", 5)
    assert decision.cut_time == pytest.approx(4.0)
    assert decision.candidates == (2, 5, 6)


def test_no_valid_cut_when_every_part_would_be_too_short():
    decision = decide_split(scene_of((15.0, 21.0, words(20))), [6, 15], SPLIT)
    assert decision.status == "no_valid_cut"
    assert decision.candidates == (6, 15)
    assert decision.cut_time is None


def test_no_candidates_means_no_valid_cut():
    assert decide_split(scene_of((0.0, 8.0, words(10))), [], SPLIT).status == "no_valid_cut"


def test_out_of_range_candidates_are_skipped():
    decision = decide_split(scene_of((0.0, 8.0, words(10))), [0, 10, 5], SPLIT)
    assert decision.cut_after_word == 5


def test_part_exactly_min_length_is_valid():
    decision = decide_split(scene_of((0.0, 5.0, words(20))), [10], SPLIT)
    assert decision.status == "split"
    assert decision.cut_time == pytest.approx(2.5)


def test_settings_change_the_result():
    scene = scene_of((0.0, 8.0, words(10)))
    assert decide_split(scene, [5], SplitSettings(split_seconds=10.0)).status == "none"
    assert decide_split(scene, [5], SplitSettings(min_part_seconds=4.5)).status == "no_valid_cut"
```

- [ ] **Step 2: Write the failing units tests**

`tests/split/test_units.py`:
```python
import pytest

from stickman.ingest.models import TimedLine
from stickman.split.engine import SplitDecision
from stickman.split.scenes import Scene
from stickman.split.units import build_units, unit_filename

TEXT = "Anthropologists studying the Zhuansi in the Kalahari recorded what people talk about by daylight versus by firelight."


def scene6():
    return Scene(6, (TimedLine(6, 21.0, 29.0, TEXT),))


def test_split_scene_becomes_a_and_b_units():
    decision = SplitDecision("split", (7,), 7, 24.294)
    a, b = build_units([scene6()], {6: decision})
    assert (a.id, a.part, a.start, a.end) == ("006a", "1 of 2", 21.0, 24.294)
    assert (b.id, b.part, b.start, b.end) == ("006b", "2 of 2", 24.294, 29.0)
    assert a.source_text == "Anthropologists studying the Zhuansi in the Kalahari"
    assert b.source_text.startswith("recorded what people")
    assert a.scene_number == b.scene_number == 6


@pytest.mark.parametrize("status", ["none", "no_valid_cut"])
def test_unsplit_scene_is_one_unit(status):
    (unit,) = build_units([scene6()], {6: SplitDecision(status)})
    assert (unit.id, unit.part, unit.start, unit.end, unit.source_text) == ("006", None, 21.0, 29.0, TEXT)


def test_missing_decision_means_not_split():
    (unit,) = build_units([scene6()], {})
    assert unit.id == "006"


@pytest.mark.parametrize(
    "unit_id, start, expected",
    [
        ("006a", 21.0, "006a_00-21.0.png"),
        ("006b", 24.294, "006b_00-24.3.png"),
        ("028", 125.96, "028_02-06.0.png"),
        ("099", 599.97, "099_10-00.0.png"),
        ("001", 0.0, "001_00-00.0.png"),
    ],
)
def test_unit_filename(unit_id, start, expected):
    assert unit_filename(unit_id, start) == expected


def test_unit_filename_property():
    (unit,) = build_units([scene6()], {})
    assert unit.filename == "006_00-21.0.png"
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/split/test_engine.py tests/split/test_units.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.split.engine'`.

- [ ] **Step 4: Implement `src/stickman/split/engine.py`**

```python
"""Split engine: decides long-scene splits with plain code (spec §4.5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from stickman.settings import SplitSettings
from stickman.split.scenes import Scene

SplitStatus = Literal["none", "split", "no_valid_cut"]
_EPSILON = 1e-9


@dataclass(frozen=True)
class SplitDecision:
    status: SplitStatus
    candidates: tuple[int, ...] = ()
    cut_after_word: int | None = None
    cut_time: float | None = None


def needs_split(scene: Scene, s: SplitSettings) -> bool:
    return scene.duration >= s.split_seconds or scene.words >= s.split_words


def cut_time(scene: Scene, k: int) -> float:
    """Time of a cut after word k, interpolated inside the line that holds word k."""
    if not 1 <= k <= scene.words - 1:
        raise ValueError(f"cut after word {k} is outside 1..{scene.words - 1}")
    words_before = 0
    for line in scene.lines:
        if k <= words_before + line.words:
            position = k - words_before
            return line.start + (position / line.words) * (line.end - line.start)
        words_before += line.words
    raise AssertionError("unreachable: k is within the scene's word count")


def decide_split(scene: Scene, candidates: Sequence[int], s: SplitSettings) -> SplitDecision:
    listed = tuple(candidates)
    if not needs_split(scene, s):
        return SplitDecision("none")
    for k in listed:
        if not 1 <= k <= scene.words - 1:
            continue
        t = cut_time(scene, k)
        if t - scene.start >= s.min_part_seconds - _EPSILON and scene.end - t >= s.min_part_seconds - _EPSILON:
            return SplitDecision("split", listed, k, t)
    return SplitDecision("no_valid_cut", listed)
```

- [ ] **Step 5: Implement `src/stickman/split/units.py`**

```python
"""Units: one image each, a whole scene or one part of a split scene (spec §3, §4.6)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stickman.split.engine import SplitDecision
from stickman.split.scenes import Scene


def unit_filename(unit_id: str, start: float) -> str:
    """`<unit>_<MM-SS.s>.png`, start rounded to 0.1 s."""
    tenths = round(start * 10)
    minutes, remainder = divmod(tenths, 600)
    return f"{unit_id}_{minutes:02d}-{remainder / 10:04.1f}.png"


@dataclass(frozen=True)
class Unit:
    id: str
    scene_number: int
    part: str | None
    start: float
    end: float
    source_text: str

    @property
    def filename(self) -> str:
        return unit_filename(self.id, self.start)


def build_units(scenes: Sequence[Scene], decisions: Mapping[int, SplitDecision]) -> list[Unit]:
    units: list[Unit] = []
    for scene in scenes:
        decision = decisions.get(scene.number, SplitDecision("none"))
        base = f"{scene.number:03d}"
        if decision.status == "split" and decision.cut_after_word and decision.cut_time is not None:
            words = scene.source_text.split()
            k = decision.cut_after_word
            units.append(Unit(f"{base}a", scene.number, "1 of 2", scene.start, decision.cut_time, " ".join(words[:k])))
            units.append(Unit(f"{base}b", scene.number, "2 of 2", decision.cut_time, scene.end, " ".join(words[k:])))
        else:
            units.append(Unit(base, scene.number, None, scene.start, scene.end, scene.source_text))
    return units
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/split -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/stickman/split/engine.py src/stickman/split/units.py tests/split/test_engine.py tests/split/test_units.py
git commit -m "feat: split engine with per-line cut timing, units and file names" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Golden test on the sample script (M1 exit gate)

**Files:**
- Create: `tests/fixtures/scripts/first-sleep.txt`, `tests/test_golden_sample.py`

**Interfaces:**
- Consumes:
  - `parse_script` (Tasks 6–7)
  - `build_timeline` (Task 8)
  - `fragment_hints` (Task 9)
  - `build_scenes` (Task 10)
  - `needs_split`, `decide_split` and `build_units` (Task 11)
  - `TimingSettings` and `SplitSettings` (Task 2)
- Produces: the regression gate for spec §4.7 and acceptance criteria 2 and 3.

- [ ] **Step 1: Write the sample script fixture exactly as given in the brief**

`tests/fixtures/scripts/first-sleep.txt`:
```
0:00: It's 90 at night, 40,000 years ago.
0:02: The sun is gone, and there is no such thing as a light switch.
0:06: For almost all of human history, night wasn't time you filled, it was time you survived.
0:12: Then we got fire and everything changed.
0:15: Fire didn't just push predators back, it added 4 or 5 hours to the day, and those hours were different.
0:21: Anthropologists studying the Zhuansi in the Kalahari recorded what people talk about by daylight versus by firelight.
0:29: Daytime talk is logistics, who's hunting, where the water is, complaints.
0:34: But at the fire, roughly 80% of conversation turns into stories, spirits, ancestors, people who aren't there.
0:43: Night is where human imagination got its training ground.
0:46: Fire also cooked our food, which meant more calories for less chewing, smaller guts, bigger brains.
0:52: We literally burned our way into being us, but here's the part that should mess with you.
0:58: They did not sleep the way you sleep.
1:01: Historian Roger E.
1:03: Kirch went digging through diaries, court records, and medical texts, and found the same phrase over and over across centuries.
1:10: First sleep, 2 sleep.
1:14: People went down not long after dark, slept about 4 hours, woke around midnight, stayed up for an hour or two, then slept again until dawn.
1:22: That gap in the middle had a name.
1:24: The watch.
1:25: And people used it.
1:26: They prayed.
1:27: They talked, they had sex.
1:29: They stoked the fire.
1:30: They interpreted the dreams they had just walked out of while the dreams were still warm.
1:35: Then in 1992, a psychiatrist named Thomas Ware took volunteers and gave them 14 hours of darkness every night, no screens, no lamps.
1:44: Within weeks, their sleep cracked cleanly in two, with a quiet waking window in the middle, exactly as described, and in that window, their blood filled with prolactin.
1:54: A hormone associated with deep calm.
1:57: So the next time you snap awake at 3 in the morning and lie there convinced something is wrong with you, consider the alternative.
2:04: Nothing is broken.
2:06: You're just running very old firmware in a world that turned the lights on and never asked your body for permission.
```

- [ ] **Step 2: Write the golden test**

`tests/test_golden_sample.py`:
```python
"""Spec §4.7 golden test: the sample script through the split engine with fixed candidates."""

from pathlib import Path

import pytest

from stickman.ingest.fragments import fragment_hints
from stickman.ingest.parse import parse_script
from stickman.ingest.timing import build_timeline
from stickman.settings import SplitSettings, TimingSettings
from stickman.split.engine import decide_split, needs_split
from stickman.split.scenes import build_scenes
from stickman.split.units import build_units

FIXTURE = Path(__file__).parent / "fixtures" / "scripts" / "first-sleep.txt"
GROUPS = [[n] for n in range(1, 13)] + [[13, 14]] + [[n] for n in range(15, 30)]
CANDIDATES = {5: [6, 15], 6: [7], 8: [10], 13: [13], 15: [11], 23: [10], 24: [19], 26: [12], 28: [6, 9]}
EXPECTED_CUTS = {6: 24.294, 8: 39.294, 13: 66.500, 15: 77.385, 23: 98.913, 24: 110.786, 26: 120.500, 28: 129.365}


def run(split=SplitSettings()):
    timeline = build_timeline(parse_script(FIXTURE.read_text(encoding="utf-8")), TimingSettings())
    scenes = build_scenes(timeline.lines, GROUPS, max_lines=3)
    decisions = {s.number: decide_split(s, CANDIDATES.get(s.number, []), split) for s in scenes}
    return timeline, scenes, decisions, build_units(scenes, decisions)


@pytest.fixture(scope="module")
def result():
    return run()


def test_29_lines_pace_and_end(result):
    timeline, *_ = result
    assert len(timeline.lines) == 29
    assert timeline.pace_measured is True
    assert timeline.pace_wps == pytest.approx(337 / 126, abs=1e-4)  # 2.6746
    assert timeline.end == pytest.approx(133.852, abs=1e-3)


def test_fragment_hints_flag_line_13_only(result):
    timeline, *_ = result
    assert fragment_hints(timeline.lines) == [13]


def test_28_scenes_with_lines_13_and_14_merged(result):
    _, scenes, _, _ = result
    assert len(scenes) == 28
    s13 = scenes[12]
    assert [line.number for line in s13.lines] == [13, 14]
    assert (s13.start, s13.end, s13.words) == (61.0, 70.0, 23)


def test_split_candidates(result):
    _, scenes, _, _ = result
    assert [s.number for s in scenes if needs_split(s, SplitSettings())] == [5, 6, 8, 13, 15, 23, 24, 26, 28]


def test_scene_5_has_no_valid_cut(result):
    _, _, decisions, _ = result
    assert decisions[5].status == "no_valid_cut"


@pytest.mark.parametrize("scene, expected", sorted(EXPECTED_CUTS.items()))
def test_cut_times(result, scene, expected):
    _, _, decisions, _ = result
    assert decisions[scene].status == "split"
    assert decisions[scene].cut_time == pytest.approx(expected, abs=1e-3)


def test_scene_28_falls_back_to_second_candidate(result):
    _, _, decisions, _ = result
    assert decisions[28].cut_after_word == 9


def test_36_units_in_order_covering_the_video(result):
    timeline, _, _, units = result
    expected_ids = []
    for n in range(1, 29):
        expected_ids += [f"{n:03d}a", f"{n:03d}b"] if n in EXPECTED_CUTS else [f"{n:03d}"]
    assert [u.id for u in units] == expected_ids
    assert len(units) == 36
    assert units[0].start == 0.0
    assert all(a.end == b.start for a, b in zip(units, units[1:]))
    assert units[-1].end == timeline.end


def test_file_names_for_scene_6(result):
    *_, units = result
    by_id = {u.id: u for u in units}
    assert by_id["006a"].filename == "006a_00-21.0.png"
    assert by_id["006b"].filename == "006b_00-24.3.png"


def test_changing_split_seconds_changes_the_result():
    _, _, decisions, units = run(SplitSettings(split_seconds=10.0))
    assert decisions[6].status == "none"  # 8 s, 17 words
    assert decisions[8].status == "none"  # 9 s, 17 words
    assert decisions[26].status == "split"  # 24 words still triggers
    assert len(units) == 34
```

- [ ] **Step 3: Run the golden test**

Run: `uv run pytest tests/test_golden_sample.py -v`
Expected: all PASS.

**If any test fails, treat it as a real bug:**
- First check that the fixture matches the brief character for character. Word counts depend on it.
- Then fix the code. Don't edit the expected numbers: the user checked them independently.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS, with no network calls.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/scripts/first-sleep.txt tests/test_golden_sample.py
git commit -m "test: spec 4.7 golden test on the sample script (M1 exit gate)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: M0 live check script (real API calls, about $1)

**Files:**
- Create: `scripts/m0_probe.py`

**Interfaces:**
- Consumes: `load_config` from Task 2, and the default config files (`config/style.yaml`, created by `stickman init`).
- Produces:
  - `tests/fixtures/cf/<step>_<name>.json`: recorded responses (status, headers, time taken, and the body with long base64 strings shortened). The M3 client tests will reuse them.
  - `m0_out/<step>/*.png`: images for you to inspect.

**Before running:** this task spends real money (about $1 in total) and needs your credentials.
1. Create `.env` from `.env.example` with `CF_ACCOUNT_ID` and `CF_API_TOKEN`.
2. Run `uv run stickman init` and confirm it prints `Cloudflare token works.`
3. Get the user's go-ahead before running the style and anchor-leak steps. These are the most expensive (about $0.65), because they use FLUX.2 dev.

- [ ] **Step 1: Write `scripts/m0_probe.py`**

```python
"""M0 live checks: answers spec §15 against the real Cloudflare API (about $1 in total).

Run one step at a time from the workspace root:
    uv run python scripts/m0_probe.py ids
    uv run python scripts/m0_probe.py llm
    uv run python scripts/m0_probe.py image
    uv run python scripts/m0_probe.py refs
    uv run python scripts/m0_probe.py vision [--model ID]
    uv run python scripts/m0_probe.py errors
    uv run python scripts/m0_probe.py style
    uv run python scripts/m0_probe.py anchor-leak
Raw responses -> tests/fixtures/cf/<step>_<name>.json (long strings truncated).
Images -> m0_out/<step>/<name>.png
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from ruamel.yaml import YAML

from stickman.settings import load_config

ROOT = Path.cwd()
FIXTURES = ROOT / "tests" / "fixtures" / "cf"
OUT = ROOT / "m0_out"
BASE = "https://api.cloudflare.com/client/v4"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
DEV = "@cf/black-forest-labs/flux-2-dev"
MESSAGES = [
    {"role": "system", "content": "Return only JSON."},
    {"role": "user", "content": "Give one animal and its number of legs."},
]
SCHEMA = {
    "name": "probe",
    "schema": {
        "type": "object",
        "properties": {"animal": {"type": "string"}, "legs": {"type": "integer"}},
        "required": ["animal", "legs"],
    },
}
STYLE_SCENES = {
    "fire_night": (
        "A group of three cavemen stickmen in simple fur loincloths sit around a campfire at night, "
        "telling stories. Flame lines around the fire and short lines radiating outward. A small "
        "crescent moon and a few stars; the background stays white. Wide shot."
    ),
    "clock_3am": (
        "The main character, a stickman with a large round head and exactly three short hair strokes "
        "curling to the right, lies awake in bed under a simple blanket, eyes wide open, staring at a "
        "round wall clock that has two hands and no numerals. A small crescent moon in the window. "
        "Medium shot."
    ),
    "old_firmware": (
        "Visual metaphor: a stickman whose chest is an old boxy computer screen showing a loading bar, "
        "standing in a modern room under a switched-on ceiling lamp. Surprise lines above his head. "
        "Medium shot."
    ),
}
SINGLE_FIGURE_SCENES = [
    "one stickman jogging",
    "one stickman reading a book with blank pages",
    "one stickman sleeping under a blanket",
    "one stickman pointing at the sky",
    "one stickman sitting on a rock, thinking",
]


def truncate(value):
    if isinstance(value, dict):
        return {key: truncate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [truncate(item) for item in value]
    if isinstance(value, str) and len(value) > 200:
        return value[:60] + f"...<{len(value)} chars>"
    return value


def record(step: str, name: str, response: httpx.Response, elapsed: float) -> None:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        body = {"binary_image_bytes": len(response.content)}
    else:
        try:
            body = truncate(response.json())
        except ValueError:
            body = response.text[:2000]
    headers = {k: v for k, v in response.headers.items() if k.lower() != "set-cookie"}
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{step}_{name}.json").write_text(
        json.dumps(
            {"status": response.status_code, "elapsed_s": round(elapsed, 2), "headers": headers, "body": body},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[{step}/{name}] HTTP {response.status_code} in {elapsed:.1f}s  {content_type}")
    if response.status_code >= 400:
        print("    ", response.text[:300])


def extract_image(response: httpx.Response) -> bytes | None:
    if response.status_code >= 400:
        return None
    if response.headers.get("content-type", "").startswith("image/"):
        return response.content
    data = response.json()
    result = data.get("result") if isinstance(data, dict) else None
    encoded = (result.get("image") if isinstance(result, dict) else None) or data.get("image")
    return base64.b64decode(encoded) if encoded else None


def test_png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.3, height * 0.1, width * 0.7, height * 0.5), outline="black", width=4)
    draw.line((width * 0.5, height * 0.5, width * 0.5, height * 0.9), fill="black", width=4)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def fit(png: bytes, max_side: int) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class Probe:
    def __init__(self) -> None:
        cfg = load_config(ROOT)
        self.settings = cfg.settings
        self.account = cfg.secrets.cf_account_id
        token = cfg.secrets.cf_api_token.get_secret_value()
        self.http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=180)
        style = YAML(typ="safe").load((ROOT / "config" / "style.yaml").read_text(encoding="utf-8"))
        self.style_text = style["style_text"]
        self.strict = style["strict_clause"]

    async def call(self, step, name, method, path, **kwargs) -> httpx.Response:
        started = time.perf_counter()
        response = await self.http.request(method, f"{BASE}/accounts/{self.account}{path}", **kwargs)
        record(step, name, response, time.perf_counter() - started)
        return response

    async def image(self, step, name, model, prompt, width, height, *, seed=1, refs=(), extra=None):
        fields = [
            ("prompt", (None, prompt)),
            ("width", (None, str(width))),
            ("height", (None, str(height))),
            ("seed", (None, str(seed))),
        ]
        for key, value in (extra or {}).items():
            fields.append((key, (None, str(value))))
        for index, ref in enumerate(refs):
            fields.append((f"input_image_{index}", (f"input_image_{index}.png", ref, "image/png")))
        response = await self.call(step, name, "POST", f"/ai/run/{model}", files=fields)
        data = extract_image(response)
        if data:
            image = Image.open(io.BytesIO(data))
            print(f"     returned {image.format} {image.size[0]}x{image.size[1]}")
            path = OUT / step / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, "PNG")
        return data

    def scene_prompt(self, scene: str) -> str:
        return f"{self.style_text}\n\nScene: {scene}\n\n{self.strict}"


async def step_ids(p: Probe) -> None:
    for term in ("gpt-oss", "qwen", "llama-3.3", "flux-2"):
        response = await p.call("ids", term.replace(".", "_"), "GET", "/ai/models/search", params={"search": term})
        if response.status_code == 200:
            for model in response.json().get("result", []):
                print("    ", model.get("name"), "|", (model.get("task") or {}).get("name"))


async def step_llm(p: Probe) -> None:
    for label, model in (("planner", p.settings.llm.planner_model), ("fallback", p.settings.llm.fallback_model)):
        chat = {"model": model, "messages": MESSAGES, "max_tokens": 300}
        await p.call("llm", f"{label}_chat", "POST", "/ai/v1/chat/completions", json=chat)
        with_schema = {**chat, "response_format": {"type": "json_schema", "json_schema": SCHEMA}}
        await p.call("llm", f"{label}_chat_schema", "POST", "/ai/v1/chat/completions", json=with_schema)
        await p.call("llm", f"{label}_native", "POST", f"/ai/run/{model}", json={"messages": MESSAGES, "max_tokens": 300})


async def step_image(p: Probe) -> None:
    prompt = p.scene_prompt("one stickman waving hello.")
    await p.image("image", "klein4b_1920x1080", KLEIN_4B, prompt, 1920, 1080)
    await p.image("image", "klein4b_1080x1920", KLEIN_4B, prompt, 1080, 1920)
    await p.image("image", "klein4b_1920x1088", KLEIN_4B, prompt, 1920, 1088)
    await p.image("image", "klein4b_with_steps", KLEIN_4B, prompt, 1024, 768, extra={"steps": 4})
    await p.image("image", "klein9b_1920x1080", KLEIN_9B, prompt, 1920, 1080)
    await p.image("image", "dev_1920x1080", DEV, prompt, 1920, 1080, extra={"steps": 25})
    await p.image("image", "klein4b_same_seed_repeat", KLEIN_4B, prompt, 1920, 1080)
    first, again = OUT / "image" / "klein4b_1920x1080.png", OUT / "image" / "klein4b_same_seed_repeat.png"
    if first.exists() and again.exists():
        same = Image.open(first).tobytes() == Image.open(again).tobytes()
        print(f"     same seed reproduces identical pixels: {same}")


async def step_refs(p: Probe) -> None:
    prompt = p.scene_prompt("the character from image 0, waving.")
    await p.image("refs", "one_ref_512", KLEIN_4B, prompt, 1024, 768, refs=[test_png(512, 512)])
    await p.image("refs", "one_ref_513", KLEIN_4B, prompt, 1024, 768, refs=[test_png(513, 513)])
    await p.image("refs", "four_refs_512", KLEIN_4B, prompt, 1024, 768, refs=[test_png(512, 512)] * 4)
    await p.image("refs", "five_refs_512", KLEIN_4B, prompt, 1024, 768, refs=[test_png(512, 512)] * 5)


async def step_vision(p: Probe, model: str) -> None:
    first = OUT / "image" / "klein4b_1920x1080.png"
    second = OUT / "refs" / "one_ref_512.png"
    if not first.exists():
        sys.exit("Run the image step first (it creates m0_out/image/klein4b_1920x1080.png).")
    second = second if second.exists() else first

    def part(path: Path) -> dict:
        encoded = base64.b64encode(fit(path.read_bytes(), 768)).decode()
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}

    question = {
        "type": "text",
        "text": 'How many stick figures are in each image, and is there any text? Answer as JSON {"counts": [], "has_text": []}.',
    }
    for name, content in (("one_image", [question, part(first)]), ("two_images", [question, part(first), part(second)])):
        body = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 300}
        await p.call("vision", name, "POST", "/ai/v1/chat/completions", json=body)


async def step_errors(p: Probe) -> None:
    async with httpx.AsyncClient(headers={"Authorization": "Bearer not-a-real-token"}, timeout=60) as bad:
        started = time.perf_counter()
        response = await bad.post(
            f"{BASE}/accounts/{p.account}/ai/v1/chat/completions",
            json={"model": p.settings.llm.fallback_model, "messages": MESSAGES, "max_tokens": 5},
        )
        record("errors", "bad_token", response, time.perf_counter() - started)
    await p.image("errors", "width_too_big", KLEIN_4B, "a stickman", 5000, 1080)
    await p.image("errors", "empty_prompt", KLEIN_4B, "", 1024, 768)
    await p.call("errors", "unknown_model", "POST", "/ai/run/@cf/black-forest-labs/does-not-exist", json={"prompt": "x"})
    print("429, daily-limit and refusal bodies can't be triggered safely; record them when they first occur (spec §15 #7).")


async def step_style(p: Probe) -> None:
    for label, model, extra in (("klein4b", KLEIN_4B, None), ("klein9b", KLEIN_9B, None), ("dev", DEV, {"steps": 25})):
        for scene, text in STYLE_SCENES.items():
            await p.image("style", f"{label}_{scene}", model, p.scene_prompt(text), 1920, 1080, extra=extra)
    print(f"Open {OUT / 'style'} and judge style, consistency and text-free output by eye.")


async def step_anchor_leak(p: Probe) -> None:
    anchor_prompt = p.scene_prompt(
        "two stickmen talking; the taller one gestures with an open palm, the shorter one listens. "
        "A light hatched ground shadow."
    )
    anchor = await p.image("anchor_leak", "anchor_two_figures", DEV, anchor_prompt, 1024, 768, extra={"steps": 25})
    if not anchor:
        sys.exit("Anchor generation failed; see the recorded response.")
    ref = fit(anchor, p.settings.image.ref_max_side)
    for index, scene in enumerate(SINGLE_FIGURE_SCENES, 1):
        prompt = (
            f"{p.style_text}\n\nScene: {scene}. Exactly one stick figure in the image.\n\n"
            "Reference images: image 0 shows the drawing style only — match its line weight and look, "
            f"not its content, and do not copy its figures.\n\n{p.strict}"
        )
        await p.image("anchor_leak", f"single_{index}", KLEIN_9B, prompt, 1920, 1080, seed=100 + index, refs=[ref])
    print("Count the figures in m0_out/anchor_leak/single_*.png. More than one figure anywhere = the anchor leaks.")


STEPS = {
    "ids": step_ids,
    "llm": step_llm,
    "image": step_image,
    "refs": step_refs,
    "errors": step_errors,
    "style": step_style,
    "anchor-leak": step_anchor_leak,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=[*STEPS, "vision"])
    parser.add_argument("--model", help="vision model ID for the vision step (default: llm.vision_model)")
    args = parser.parse_args()

    async def run() -> None:
        probe = Probe()
        try:
            if args.step == "vision":
                await step_vision(probe, args.model or probe.settings.llm.vision_model)
            else:
                await STEPS[args.step](probe)
        finally:
            await probe.http.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Check it loads, with no API calls**

Run: `uv run python scripts/m0_probe.py --help`
Expected: usage text listing the steps.

- [ ] **Step 3: Run the cheap steps in order**

Run each of these and read its output:
1. `uv run python scripts/m0_probe.py ids`. Note the exact IDs of gpt-oss-120b, qwen3.8-27b and the FLUX.2 models.
2. If the gpt-oss or qwen ID differs from the defaults, update `config/settings.yaml` → `llm.planner_model` / `llm.vision_model` before continuing.
3. `uv run python scripts/m0_probe.py llm`
4. `uv run python scripts/m0_probe.py image`
5. `uv run python scripts/m0_probe.py refs`
6. `uv run python scripts/m0_probe.py vision`, adding `--model <id>` if the `ids` step found a different qwen ID.
7. `uv run python scripts/m0_probe.py errors`

Expected: a `tests/fixtures/cf/*.json` file for every call. Images appear in `m0_out/image` and `m0_out/refs`.

- [ ] **Step 4: Run the paid visual steps, after the user agrees**

Run: `uv run python scripts/m0_probe.py style`, then `uv run python scripts/m0_probe.py anchor-leak`.
Expected: 9 style images in `m0_out/style/`, plus 1 anchor and 5 single-figure images in `m0_out/anchor_leak/`.

- [ ] **Step 5: Make sure no secrets reached the fixtures, then commit the script and fixtures**

Run in PowerShell: `Select-String -Path tests/fixtures/cf/*.json -Pattern (Get-Content .env | Select-String 'CF_API_TOKEN=(.+)').Matches.Groups[1].Value -SimpleMatch`
Expected: no output. Only the response is recorded, never the request headers.
```bash
git add scripts/m0_probe.py tests/fixtures/cf
git commit -m "chore: M0 live probe script and recorded Cloudflare responses" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Record the M0 findings and update the spec, defaults and error markers

**Files:**
- Create: `docs/m0-findings.md`, `tests/cf/test_recorded_errors.py`
- Modify:
  - `spec.md` §15 (mark each question answered)
  - `src/stickman/defaults/settings.yaml` and `src/stickman/settings.py` (the model IDs, `image.sizes` and `ref_max_side` defaults, if M0 found different values)
  - `src/stickman/cf/errors.py` (`DAILY_LIMIT_MARKERS` and `REFUSAL_MARKERS`, if real bodies were recorded)

**Interfaces:**
- Consumes: the `tests/fixtures/cf/*.json` files and `m0_out/` images from Task 13.
- Produces: settled answers to spec §15, which M2 and M3 build on.

- [ ] **Step 1: Write a test for the recorded auth error**

`tests/cf/test_recorded_errors.py`:
```python
import json
from pathlib import Path

from stickman.cf.errors import ErrorCategory, classify

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cf"


def load(name):
    record = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return record["status"], json.dumps(record["body"])


def test_recorded_bad_token_is_auth():
    status, body = load("errors_bad_token.json")
    assert classify(status, body, plan="paid") is ErrorCategory.AUTH


def test_recorded_oversized_width_is_bad_request():
    status, body = load("errors_width_too_big.json")
    assert classify(status, body, plan="paid") is ErrorCategory.BAD_REQUEST
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/cf/test_recorded_errors.py -v`
Expected: PASS.

**If it fails**, the real status or body differs from what we assumed. For example, Cloudflare might return 400 for a bad token, or a 200 with `success: false`. Change `classify` in `src/stickman/cf/errors.py` so it recognises the recorded body, then add that body as a new case in the parametrized `tests/cf/test_errors.py`. Rerun until everything passes.

- [ ] **Step 3: Write `docs/m0-findings.md` from the fixtures and images**

Use this structure, filling in every cell from the recorded evidence:
```markdown
# M0 findings — <date>

| # | Question (spec §15) | Answer | Evidence | Change made |
|---|---|---|---|---|
| 1 | FLUX.2 response shape and image format | e.g. `result.image` base64, JPEG | tests/fixtures/cf/image_klein4b_1920x1080.json | none / client change |
| 2 | 1080 accepted? | | image_klein4b_1920x1080.json, image_klein4b_1920x1088.json | image.sizes |
| 3 | Reference size limit (512 vs 513), max count (4 vs 5) | | refs_*.json | image.ref_max_side |
| 4 | `steps` on Klein | | image_klein4b_with_steps.json | pricing supports_steps |
| 5 | gpt-oss on /ai/v1/chat/completions, response_format | | llm_planner_*.json | llm settings / client |
| 6 | qwen ID, image format, multiple images | | ids_qwen.json, vision_*.json | llm.vision_model; composite fallback needed? |
| 7 | Error bodies (401, 400; 429/daily/refusal pending) | | errors_*.json | errors.py markers |
| 8 | Cost/neuron count in responses? | | headers/body in image_*.json | ledger |
| 9 | Latency per model (single sample) | klein4b … s, klein9b … s, dev … s | elapsed_s in image_*.json | render.est_seconds_per_image |
| 10 | Commercial use (Cloudflare written confirmation for Klein 9B and dev) | USER ACTION: support ticket # / answer | — | if not confirmed: Klein 4B only |
| 11 | Workers Paid monthly fee | USER ACTION | dashboard | plan.md §4 |
| 12 | Anchor leak (count figures in m0_out/anchor_leak/single_*.png) | | m0_out/anchor_leak | single-figure anchor? |

## Style feasibility (m0_out/style)
| Model | fire_night | clock_3am | old_firmware | Any text? | Verdict |
|---|---|---|---|---|---|
| klein4b | | | | | |
| klein9b | | | | | |
| dev | | | | | |

## Same-seed reproducibility
(result printed by the image step)

## Decisions
- M0 exit: at least one model produces the style acceptably — yes/no. **If no, stop and discuss with the user before M2.**
```

- [ ] **Step 4: Apply the findings**

For each row of the findings table whose "Change made" column isn't "none":
- Update the matching default in **both** `src/stickman/defaults/settings.yaml` and `src/stickman/settings.py`. `test_packaged_default_settings_match_model_defaults` checks that they match.
- Update `spec.md` §15, replacing that question with its answer and a link to `docs/m0-findings.md`.

- [ ] **Step 5: Ask the user to do the two things only they can do**

- Open a Cloudflare support ticket asking whether images from `@cf/black-forest-labs/flux-2-klein-9b` and `flux-2-dev` may be used commercially (a monetised YouTube channel). Record the answer in row 10.
- Check the Workers Paid plan's monthly fee in the dashboard. Record it in row 11.

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: all PASS.
```bash
git add docs/m0-findings.md tests/cf/test_recorded_errors.py spec.md src/stickman
git commit -m "docs: M0 findings; settle spec 15 answers and defaults" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## After this plan

- **M1 exit:** Task 12 passes.
- **M0 exit:** Task 14 is complete, the style is judged achievable, and the commercial-use answer is recorded.
- **Next plan:** M2 (planning with the LLM). It will turn `plan.yaml`, stages 1–3, scene-level corrections, the prompt builder, `replan`, and the M2 planning checklist into tasks, using the settled model IDs and endpoint format from `docs/m0-findings.md`.
