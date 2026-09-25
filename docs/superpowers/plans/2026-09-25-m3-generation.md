# Stickman M3 (Image Generation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `stickman generate` makes an image with FLUX.2 on Workers AI for every unit of a planned project that has none yet. It sends reference images when they exist, runs several requests at once, and checks the weekly budget before every call. Every call goes in the cost ledger. After every change it saves `state.json` safely. It pauses on the daily limit, the budget or 5 temporary errors in a row, and ends with the run summary. `stickman resume` continues after a pause or a crash. If the process is killed at any point, the next run finishes with no duplicate and no lost images.

**Architecture:**
- **Metering (top-level modules):**
  - `pricing.py` holds the cost formulas.
  - `ledger.py` is the global append-only `ledger.jsonl`.
  - `budget.py` handles the weekly budget and in-flight reservations.
  - `meter.py` checks the budget before each API call and adds a ledger entry after it. Planning's LLM calls also go through the meter, without a budget stop.
- **The new `stickman.render` package:**
  - `images.py`: PNG files and their names.
  - `fingerprint.py`: what an image was made from.
  - `state.py`: `state.json`.
  - `recovery.py`: puts a project in order after a crash.
  - `lock.py`: the project `.lock`.
  - `references.py`: the reference-slot files.
  - `jobs.py`: each unit's request.
  - `renderer.py`: the parallel runs, error categories and stop reasons.
  - `summary.py`: the end-of-run summary.
- **`cli.py`** gets `generate` and `resume`.

Quality control (M4), bootstrap (M5), the review page (M6) and the approval flow (M7) stay in later plans.

**Tech Stack:** Python 3.12 (uv), typer, rich, pydantic v2, ruamel.yaml, httpx, **Pillow** (moved from dev to runtime in Task 1), pytest.

**Source documents:** `spec.md` (§2–§3, §5.2, §5.4, §7.4, §9, §10, §13, §16–§18) and `docs/m0-findings.md`. Section numbers like "§9.5" refer to `spec.md`.

## Global Constraints

- **Python and packaging:** Python **3.12**, package `stickman` in `src/stickman/`, managed with **uv**. Run everything with `uv run …`.
- **Runtime dependencies:** `typer`, `rich`, `pydantic>=2`, `pydantic-settings`, `ruamel.yaml`, `httpx`, and **`pillow`** (added in Task 1). Nothing else; numpy comes in M4. Dev dependency: `pytest`.
- **Secrets:**
  - They live **only** in `.env` as `CF_ACCOUNT_ID` and `CF_API_TOKEN`. Never write them to config, logs, caches, `state.json`, the ledger or test fixtures.
  - Run logs, errors kept in `state.json`, and console messages built from Cloudflare errors all mask the token as `***`.
- **`style_refs/`:** the stock images there are **never** sent to any API.
- **IDs and names:** unit IDs are `NNN`, or `NNNa`/`NNNb` for split parts. File names follow spec §3:
  - `images/<unit>_<MM-SS.s>.png` is the current image.
  - `images/_history/<unit>_v<N>.png` holds every version ever made, and is never deleted.
- **Models:** all Pydantic models use `extra="forbid"`. `state.json` and every YAML file have `schema_version: 1`.
- **API calls:**
  - They go only through `stickman.cf.client.CloudflareClient` (`chat`, `generate_image`).
  - Every call goes through `stickman.meter.Meter.run`, which adds a ledger entry after it. Image calls are also budget-checked before they start.
  - Tests **never** touch the network. They use `FakeChat` and `FakeImages` from `tests/conftest.py`.
- **Writes:**
  - `state.json` is written only through `stickman.render.state.StateStore`, using `fsutil.safe_write` (temp file, fsync, replace), after **every** status change.
  - Images are written with `safe_write`.
  - M3 never writes `plan.yaml`.
- **CLI:**
  - Exit codes: `0` success; `1` user or validation error; `2` paused (daily limit, weekly budget, circuit breaker); `3` auth or config error.
  - Every command prints `Project: <folder name>` as its first line.
- **Tests:** `pytest`. Tests that call the real API are marked `live` and excluded by default.
- **Commits:** every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Pass it as a second `-m`.
- **Shell:**
  - Use Git Bash from the workspace root `C:\huSSNAIN PROJECTS\Tan Project`.
  - uv is not on PATH, so first run `export PATH="/c/Users/Operator PC/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"`.

## Decisions made while writing this plan

- **The user's decisions (2026-09-25):**
  - **No approval gates yet.** `generate` makes every `planned` or `failed` unit in time order. `--limit N` caps one run. The spec's approval steps (§10.1) arrive with the review page (M6) and the test-first flow (M7).
  - **Stale marking comes in M3** (§10.4). The strict `-p` rule stays in M7. `stickman status` isn't part of M3.
  - **Planning's LLM calls go in the ledger, but the weekly budget never stops them.** The budget check applies to image calls.
- **No QC in M3:**
  - A saved image makes the unit `generated`. M4 adds QC between saving the image and setting the final status.
  - A `refused` request makes the unit `failed` until M4 adds the softened retry.
- **Ledger billing:**
  - A 2xx response is `billed`, at its `cf-ai-neurons` header × $0.011/1000. Without the header, the cost comes from its token counts (LLM), else the estimate.
  - A timeout is `possibly_billed`, at the estimate.
  - Any other error is `not_billed`: it is recorded but not counted.
  - Each entry also records `neurons`.
- **Budget:** the week's spend is read from the ledger once, when a run starts. The run's own calls are added as they finish.
- **Circuit breaker:** it counts each API attempt that ends in a `transient` error, retries included. A success resets it.
- **Crash safety:**
  - Every history PNG carries its version record as JSON in a PNG text chunk.
  - When a run starts, `recover()` does the following:
    - it removes temp files;
    - it puts `generating` back to `planned`;
    - it adds a history image that `state.json` doesn't list (from that copy);
    - it recopies current images;
    - it marks stale units.
- **Seeds:** a null seed is drawn from 0 … 2³¹−1. That is valid whether the API reads it as signed or unsigned 32-bit.
- **What is sent:**
  - The prompt sent is the plan's `image_prompt`, as it stands.
  - The references sent are those that exist now (slot 0 is the anchor; §7.4). Today `library/` is empty, so nothing is sent.
  - See "After this plan" for what M5 must do when references first appear.
- **Carried over from the M2 final review:**
  - `load_settings` wraps read errors (Task 1).
  - The run log gets an entry per API attempt, with the cache key and the billing flag (Task 4).
  - The fallback model gets its own token limit (Task 4).
  - The console masks Cloudflare error text (Task 4).

## File Map

```
pyproject.toml, uv.lock           pillow moves to runtime dependencies              (modify, Task 1)
src/stickman/cf/client.py         ImageResult.neurons/.request_id, LLMResult.request_id (modify, Task 1)
src/stickman/settings.py          read_config_text; load_settings uses it           (modify, Task 1)
src/stickman/config_files.py      imports read_config_text; _load -> load_config_file (modify, Tasks 1-2)
src/stickman/pricing.py           PricingConfig, load_pricing, cost formulas, format_usd (new, Task 2)
src/stickman/ledger.py            LedgerEntry, Ledger, week_start, utc_day_start     (new, Task 3)
src/stickman/budget.py            Budget, BudgetExceeded                             (new, Task 3)
src/stickman/meter.py             Meter, Metered, billing_of, local_now              (new, Task 4)
src/stickman/runlog.py            mask(), RunLog.mask()                              (modify, Task 4)
src/stickman/plan/llm.py          metered calls, one log entry per API attempt, FALLBACK_MAX_TOKENS (modify, Task 4)
src/stickman/cli.py               new/replan metered + masked; generate, resume      (modify, Tasks 4, 11)
src/stickman/render/__init__.py                                                      (new, empty, Task 5)
src/stickman/render/images.py     sniff, decode_image, encode_png, read_metadata, names (new, Task 5)
src/stickman/render/fingerprint.py fingerprint (spec §10.4)                          (new, Task 5)
src/stickman/render/state.py      Version, UnitState, ProjectState, StateStore       (new, Task 6)
src/stickman/render/recovery.py   ExpectedUnit, recover                              (new, Task 6)
src/stickman/render/lock.py       ProjectLock, LockHeld, pid_alive                   (new, Task 7)
src/stickman/library.py           anchor_ref_path, character_ref_path                (modify, Task 8)
src/stickman/render/references.py RefImage, ReferenceFiles, reference_paths          (new, Task 8)
src/stickman/render/jobs.py       RenderContext, RenderJob, JobBuilder, random_seed  (new, Task 8)
src/stickman/render/renderer.py   Renderer, RunControl, CircuitBreaker, StopReason, RunResult (new, Task 9)
src/stickman/render/summary.py    summary_lines, format_duration                     (new, Task 11)
tests/conftest.py                 jpeg_bytes + jpeg fixture (Task 5), FakeImages + fake_images (Task 9)
tests/render/                     test_images, test_fingerprint, test_state, test_recovery, test_lock,
                                  test_references, test_jobs, test_renderer, test_resume, test_summary
tests/test_pricing.py, test_ledger.py, test_budget.py, test_meter.py, test_packaging.py,
tests/test_cli_generate.py, tests/test_live.py
spec.md                           [M3] notes in §5.2, §5.4, §9.2, §9.5, §9.7, §10.1, §10.3, §13, §16
docs/m3-generation-check.md       the live check (Task 12)
```

---

### Task 1: Cost and request id on API results, Pillow at runtime, settings read errors

**Files:**
- Modify: `src/stickman/cf/client.py`, `src/stickman/settings.py`, `src/stickman/config_files.py`, `pyproject.toml`, `uv.lock`
- Test: `tests/cf/test_client.py`, `tests/test_settings.py`, `tests/test_packaging.py` (new)

**Interfaces:**
- Produces:
  - `ImageResult(image_bytes: bytes, neurons: float | None = None, request_id: str | None = None)`.
  - `LLMResult` gains `request_id: str | None = None` (last field).
  - `request_id` is the `cf-ai-req-id` response header, else `cf-ray`. `ImageResult.neurons` is the `cf-ai-neurons` header.
  - `stickman.settings.read_config_text(path: Path) -> str` raises `ConfigError` for unreadable or non-UTF-8 files. `config_files` imports it from `settings` (it no longer defines it).

- [ ] **Step 1: Write the failing tests**

Append to `tests/cf/test_client.py`, and add `import json` and `from pathlib import Path` to its imports:

```python
FIXTURES = Path(__file__).parent.parent / "fixtures" / "cf"


def test_image_result_carries_its_neurons_and_request_id():
    def handler(request):
        request.read()
        return httpx.Response(
            200,
            headers={"cf-ai-neurons": "207.59", "cf-ai-req-id": "req-1"},
            json={"result": {"image": base64.b64encode(PNG).decode()}},
        )

    result = generate(handler)
    assert (result.neurons, result.request_id) == (207.59, "req-1")


def test_an_image_response_without_cost_headers_has_none():
    def handler(request):
        request.read()
        return httpx.Response(200, json={"result": {"image": base64.b64encode(PNG).decode()}})

    result = generate(handler)
    assert (result.neurons, result.request_id) == (None, None)


def test_chat_result_carries_the_request_id():
    def handler(request):
        return httpx.Response(200, headers={"cf-ai-req-id": "req-2"}, json={"choices": [{"message": {"content": "OK"}}]})

    assert chat(handler).request_id == "req-2"


def test_recorded_klein_4b_headers_give_the_cost_and_request_id():
    record = json.loads((FIXTURES / "image_klein4b_1920x1088.json").read_text(encoding="utf-8"))
    headers = {name: record["headers"][name] for name in ("cf-ai-neurons", "cf-ai-req-id")}

    def handler(request):
        request.read()
        # The M0 recorder shortened the recorded base64 image, so a small one stands in for it.
        return httpx.Response(record["status"], headers=headers, json={"result": {"image": base64.b64encode(PNG).decode()}})

    result = generate(handler, width=1920, height=1088)
    assert result.neurons == pytest.approx(207.59)
    assert result.request_id == record["headers"]["cf-ai-req-id"]
```

Append to `tests/test_settings.py`:

```python
def test_a_settings_file_that_is_not_utf8_is_a_config_error(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_bytes("account:\n  plan: free  # café\n".encode("cp1252"))
    with pytest.raises(ConfigError, match="not UTF-8"):
        load_settings(path)
```

Create `tests/test_packaging.py`:

```python
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_pillow_is_a_runtime_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(dep.lower().startswith("pillow") for dep in project["dependencies"])
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/cf/test_client.py tests/test_settings.py tests/test_packaging.py -q`
Expected: FAIL. The client tests fail with `AttributeError: 'ImageResult' object has no attribute 'neurons'` (and `request_id` for chat). The settings test fails with `UnicodeDecodeError`. The packaging test fails its assert.

- [ ] **Step 3: Implement**

In `src/stickman/cf/client.py`, replace the two result classes:

```python
@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    raw: dict[str, Any]
    neurons: float | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class ImageResult:
    image_bytes: bytes
    neurons: float | None = None
    request_id: str | None = None
```

In `chat()`, add `request_id=_request_id(response),` after `neurons=_neurons(response, usage),` in the `LLMResult(...)` call. At the end of `generate_image()`, replace the `return` with:

```python
        return ImageResult(
            image_bytes=_extract_image(response),
            neurons=_neurons(response, {}),
            request_id=_request_id(response),
        )
```

Add after `_neurons`:

```python
def _request_id(response: httpx.Response) -> str | None:
    """Cloudflare's id for the call, kept in the ledger (spec §5.4)."""
    return response.headers.get("cf-ai-req-id") or response.headers.get("cf-ray")
```

In `src/stickman/settings.py`, add this function above `load_settings`, and make `load_settings` read through it:

```python
def read_config_text(path: Path) -> str:
    """A hand-edited YAML file's text. A file that can't be read is a ConfigError naming it (exit 3)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not UTF-8 text ({exc}). Save it as UTF-8.") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: can't read it: {exc.strerror or exc}") from exc


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        data = YAML(typ="safe").load(read_config_text(path)) or {}
    except YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {_format_errors(exc)}") from exc
```

In `src/stickman/config_files.py`:
- Delete its `read_config_text` function.
- Change the settings import to `from stickman.settings import ConfigError, default_config_text, read_config_text`.
- `library.py` still imports `read_config_text` from `config_files`, which works through that import.

In `pyproject.toml`, add `"pillow>=10",` to `[project] dependencies` (after `"httpx>=0.27",`), and change the dev group to `dev = ["pytest>=8"]`. Then run `uv lock` and `uv sync`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/cf/test_client.py tests/test_settings.py tests/test_packaging.py tests/test_config_files.py tests/test_library.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/cf/client.py src/stickman/settings.py src/stickman/config_files.py pyproject.toml uv.lock tests/cf/test_client.py tests/test_settings.py tests/test_packaging.py
git commit -m "feat: API results carry their neuron cost and request id; pillow is a runtime dependency; unreadable settings are a config error" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Pricing (spec §9.6)

**Files:**
- Create: `src/stickman/pricing.py`
- Modify: `src/stickman/config_files.py` (rename `_load` to `load_config_file`)
- Test: `tests/test_pricing.py`

**Interfaces:**
- Consumes: `config_files.load_config_file(model, workspace, name)`, which is the renamed `_load`. It reads `config/<name>`, or the packaged default when the file is missing.
- Produces (`stickman.pricing`):
  - `USD_PER_NEURON = 0.011 / 1000`
  - `ImagePrice`, whose `.formula` is one of `"tiles"`, `"megapixels"` or `"tile_steps"`, and which has `.supports_steps` and `.supports_negative_prompt`
  - `LLMPrice`
  - `PricingConfig`, with `.image(model) -> ImagePrice` (raises `ConfigError` when the model has no image price), `.llm(model) -> LLMPrice | None`, `.llm_estimate(model, prompt_chars, max_tokens) -> float`, `.llm_cost(model, input_tokens, output_tokens) -> float | None` and `.free_daily_neurons`
  - `load_pricing(workspace) -> PricingConfig`
  - `image_cost_usd(price, size, refs=(), *, steps=1) -> float`
  - `llm_cost_usd(price, input_tokens, output_tokens) -> float`
  - `neurons_usd(n)` and `usd_neurons(usd)`
  - `format_usd(amount) -> str`

- [ ] **Step 1: Write the failing tests** (`tests/test_pricing.py`)

```python
import pytest
from pydantic import ValidationError

from stickman.pricing import (
    ImagePrice,
    LLMPrice,
    format_usd,
    image_cost_usd,
    llm_cost_usd,
    load_pricing,
    neurons_usd,
    usd_neurons,
)
from stickman.settings import ConfigError

KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
GPT_OSS = "@cf/openai/gpt-oss-120b"


def test_the_default_pricing_loads_when_there_is_no_config_file(tmp_path):
    pricing = load_pricing(tmp_path)
    assert pricing.free_daily_usd == 0.11
    assert pricing.free_daily_neurons == pytest.approx(10_000)
    assert pricing.image(KLEIN_4B).formula == "tiles"
    assert pricing.image(KLEIN_9B).formula == "megapixels"
    assert pricing.image("@cf/black-forest-labs/flux-2-dev").supports_steps is True
    assert pricing.llm(GPT_OSS) == LLMPrice(kind="llm", in_per_m=0.35, out_per_m=0.75)


def test_the_klein_4b_estimate_matches_the_measured_cost(tmp_path):
    usd = image_cost_usd(load_pricing(tmp_path).image(KLEIN_4B), (1920, 1088))
    assert usd_neurons(usd) == pytest.approx(207.59, rel=0.01)  # measured in M0


def test_the_klein_9b_estimate_matches_the_measured_cost(tmp_path):
    usd = image_cost_usd(load_pricing(tmp_path).image(KLEIN_9B), (1920, 1088))
    assert usd_neurons(usd) == pytest.approx(1541, rel=0.01)  # measured in M0


def test_klein_4b_adds_each_reference_images_tiles():
    price = ImagePrice(kind="image", out_tile=0.000287, in_tile=0.000059)
    usd = image_cost_usd(price, (1024, 1024), [(512, 512), (512, 384)])
    assert usd == pytest.approx(4 * 0.000287 + 1 * 0.000059 + 0.75 * 0.000059)


def test_klein_9b_rounds_megapixels_up():
    price = ImagePrice(kind="image", first_mp=0.015, extra_mp=0.002, in_mp=0.002)
    assert image_cost_usd(price, (1024, 1024)) == pytest.approx(0.015)
    assert image_cost_usd(price, (1025, 1024)) == pytest.approx(0.017)
    assert image_cost_usd(price, (1024, 1024), [(512, 512)]) == pytest.approx(0.017)


def test_dev_multiplies_by_the_steps():
    price = ImagePrice(kind="image", out_tile_step=0.00041, in_tile_step=0.00021, supports_steps=True)
    usd = image_cost_usd(price, (1024, 1024), [(512, 512)], steps=25)
    assert usd == pytest.approx(25 * (4 * 0.00041 + 1 * 0.00021))


@pytest.mark.parametrize(
    "fields",
    [
        {"out_tile": 0.1},
        {"out_tile": 0.1, "in_tile": 0.1, "first_mp": 0.1, "extra_mp": 0.1, "in_mp": 0.1},
        {},
    ],
)
def test_an_image_price_needs_exactly_one_formula(fields):
    with pytest.raises(ValidationError):
        ImagePrice(kind="image", **fields)


def test_llm_cost_and_estimate(tmp_path):
    price = LLMPrice(kind="llm", in_per_m=0.35, out_per_m=0.75)
    assert llm_cost_usd(price, 1_000_000, 2_000_000) == pytest.approx(1.85)
    pricing = load_pricing(tmp_path)
    assert pricing.llm_estimate(GPT_OSS, 4000, 1000) == pytest.approx((1000 * 0.35 + 1000 * 0.75) / 1e6)
    assert pricing.llm_cost(GPT_OSS, 1000, 1000) == pytest.approx(0.0011)
    assert pricing.llm_cost(GPT_OSS, None, 1000) is None
    assert pricing.llm_estimate("@cf/unknown/model", 4000, 1000) == 0.0


def test_neuron_prices():
    assert neurons_usd(1000) == pytest.approx(0.011)
    assert usd_neurons(0.011) == pytest.approx(1000)


def test_an_unpriced_image_model_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="no image price"):
        load_pricing(tmp_path).image("@cf/unknown/model")


def test_a_bad_pricing_file_names_the_file(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pricing.yaml").write_text(
        "schema_version: 1\nfree_daily_usd: 0.11\nmodels:\n  x: {kind: image, out_tile: 1}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="pricing.yaml"):
        load_pricing(tmp_path)


@pytest.mark.parametrize(("amount", "text"), [(0.74, "$0.74"), (0.0023, "$0.0023"), (0.0, "$0.00"), (15.0, "$15.00")])
def test_dollars_keep_small_amounts_readable(amount, text):
    assert format_usd(amount) == text
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_pricing.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.pricing'`.

- [ ] **Step 3: Implement**

In `src/stickman/config_files.py`, rename `_load` to `load_config_file`, and update its three callers (`load_style`, `load_mascot`, `load_visual_rules`). Give it the docstring `"""config/<name> validated as `model`; the packaged default when the file is missing."""`

Create `src/stickman/pricing.py`:

```python
"""Cost estimates from config/pricing.yaml (spec §9.6), and the neuron price (spec §9.7)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stickman.config_files import load_config_file
from stickman.settings import ConfigError

USD_PER_NEURON = 0.011 / 1000
TILE_PIXELS = 512 * 512
MP_PIXELS = 1024 * 1024

Size = tuple[int, int]

# Which prices each formula needs. Tiles and megapixels are area-based (M0, docs/m0-findings.md).
_FORMULAS: dict[str, tuple[str, ...]] = {
    "tiles": ("out_tile", "in_tile"),  # FLUX.2 [klein] 4B
    "megapixels": ("first_mp", "extra_mp", "in_mp"),  # FLUX.2 [klein] 9B
    "tile_steps": ("out_tile_step", "in_tile_step"),  # FLUX.2 [dev]
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImagePrice(_Model):
    kind: Literal["image"]
    out_tile: float | None = Field(None, ge=0)
    in_tile: float | None = Field(None, ge=0)
    first_mp: float | None = Field(None, ge=0)
    extra_mp: float | None = Field(None, ge=0)
    in_mp: float | None = Field(None, ge=0)
    out_tile_step: float | None = Field(None, ge=0)
    in_tile_step: float | None = Field(None, ge=0)
    supports_negative_prompt: bool = False
    supports_steps: bool = False

    @model_validator(mode="after")
    def _one_formula(self) -> ImagePrice:
        given = {name for names in _FORMULAS.values() for name in names if getattr(self, name) is not None}
        if not any(given == set(names) for names in _FORMULAS.values()):
            raise ValueError(
                "give the prices of exactly one formula: out_tile + in_tile, "
                "first_mp + extra_mp + in_mp, or out_tile_step + in_tile_step"
            )
        return self

    @property
    def formula(self) -> str:
        return next(name for name, names in _FORMULAS.items() if getattr(self, names[0]) is not None)


class LLMPrice(_Model):
    kind: Literal["llm"]
    in_per_m: float = Field(ge=0)
    out_per_m: float = Field(ge=0)


class PricingConfig(_Model):
    schema_version: Literal[1] = 1
    free_daily_usd: float = Field(ge=0)
    models: dict[str, Annotated[ImagePrice | LLMPrice, Field(discriminator="kind")]]

    def image(self, model: str) -> ImagePrice:
        price = self.models.get(model)
        if not isinstance(price, ImagePrice):
            raise ConfigError(f"config/pricing.yaml has no image price for {model}")
        return price

    def llm(self, model: str) -> LLMPrice | None:
        price = self.models.get(model)
        return price if isinstance(price, LLMPrice) else None

    def llm_estimate(self, model: str, prompt_chars: int, max_tokens: int) -> float:
        """The most a chat call can cost: its prompt (about 4 characters a token) plus max_tokens."""
        price = self.llm(model)
        return 0.0 if price is None else llm_cost_usd(price, prompt_chars // 4, max_tokens)

    def llm_cost(self, model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
        """A chat call's cost from its token counts, when the response had them."""
        price = self.llm(model)
        if price is None or input_tokens is None or output_tokens is None:
            return None
        return llm_cost_usd(price, input_tokens, output_tokens)

    @property
    def free_daily_neurons(self) -> float:
        return usd_neurons(self.free_daily_usd)


def _tiles(size: Size) -> float:
    return size[0] * size[1] / TILE_PIXELS


def _mp(size: Size) -> float:
    return size[0] * size[1] / MP_PIXELS


def image_cost_usd(price: ImagePrice, size: Size, refs: Sequence[Size] = (), *, steps: int = 1) -> float:
    """spec §9.6. `refs` are the reference images' sizes; `steps` matters only for FLUX.2 dev."""
    if price.formula == "tiles":
        return _tiles(size) * price.out_tile + sum(_tiles(ref) * price.in_tile for ref in refs)
    if price.formula == "megapixels":
        return (
            price.first_mp
            + max(0, math.ceil(_mp(size) - 1)) * price.extra_mp
            + sum(math.ceil(_mp(ref)) * price.in_mp for ref in refs)
        )
    return steps * (_tiles(size) * price.out_tile_step + sum(_tiles(ref) * price.in_tile_step for ref in refs))


def llm_cost_usd(price: LLMPrice, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * price.in_per_m + output_tokens * price.out_per_m) / 1_000_000


def neurons_usd(neurons: float) -> float:
    return neurons * USD_PER_NEURON


def usd_neurons(usd: float) -> float:
    return usd / USD_PER_NEURON


def format_usd(amount: float) -> str:
    """Dollars: 2 decimals, or 4 under 10 cents so that one image's cost doesn't read $0.00."""
    return f"${amount:.4f}" if 0 < abs(amount) < 0.1 else f"${amount:.2f}"


def load_pricing(workspace: Path) -> PricingConfig:
    return load_config_file(PricingConfig, workspace, "pricing.yaml")
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_pricing.py tests/test_config_files.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/pricing.py src/stickman/config_files.py tests/test_pricing.py
git commit -m "feat: image and LLM cost estimates from pricing.yaml" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The cost ledger and the weekly budget (spec §5.4, §9.7)

**Files:**
- Create: `src/stickman/ledger.py`, `src/stickman/budget.py`
- Modify: `spec.md` (§5.4 note)
- Test: `tests/test_ledger.py`, `tests/test_budget.py`

**Interfaces:**
- Consumes: `stickman.settings.BudgetSettings` and `stickman.pricing.format_usd`.
- Produces:
  - **`stickman.ledger`:**
    - `LEDGER_FILE = "ledger.jsonl"`, and the literal types `Kind` and `Billing`.
    - `LedgerEntry(ts: AwareDatetime, project, unit: str | None, kind, model, est_usd, billing, request_id: str | None, neurons: float | None)`.
    - `Ledger(path)`, with `.append(entry)`, `.entries() -> Iterator[LedgerEntry]`, `.spent_since(start) -> float` (billed + possibly_billed) and `.neurons_since(start) -> float`.
    - `week_start(now) -> datetime`: Monday 00:00 in `now`'s own time zone.
    - `utc_day_start(now) -> datetime`: 00:00 UTC.
  - **`stickman.budget`:**
    - `BudgetExceeded(Exception)`.
    - `Budget(settings, *, spent, force=False, warn=None)`, with `.reserve(estimate) -> int` (raises `BudgetExceeded`), `.release(token)`, `.add_spent(usd)`, `.spent` and `.in_flight`.
    - `Budget.from_ledger(ledger, settings, *, now, force=False, warn=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ledger.py`:

```python
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from stickman.ledger import Ledger, LedgerEntry, utc_day_start, week_start

PK = timezone(timedelta(hours=5))


def entry(ts, usd=0.01, billing="billed", neurons=None, unit="001"):
    return LedgerEntry(
        ts=ts, project="2026-09-25_demo", unit=unit, kind="image",
        model="@cf/black-forest-labs/flux-2-klein-4b", est_usd=usd, billing=billing, neurons=neurons,
    )


def test_entries_round_trip_one_json_object_per_line(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    first = entry(datetime(2026, 9, 22, 14, 3, 11, tzinfo=PK), neurons=207.59)
    ledger.append(first)
    ledger.append(entry(datetime(2026, 9, 22, 14, 4, 0, tzinfo=PK), unit=None))
    assert list(ledger.entries())[0] == first
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["ts"] == "2026-09-22T14:03:11+05:00"


def test_a_missing_ledger_is_empty(tmp_path):
    assert list(Ledger(tmp_path / "none.jsonl").entries()) == []


def test_a_torn_last_line_is_skipped_and_the_next_entry_stays_readable(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry(datetime(2026, 9, 22, 14, 0, tzinfo=PK)))
    with (tmp_path / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-09-2')  # a write that was killed halfway
    ledger.append(entry(datetime(2026, 9, 22, 15, 0, tzinfo=PK), unit="002a"))
    assert [e.unit for e in ledger.entries()] == ["001", "002a"]


def test_the_week_starts_on_monday_at_midnight():
    thursday = datetime(2026, 9, 24, 10, 0, tzinfo=PK)
    assert week_start(thursday) == datetime(2026, 9, 21, 0, 0, tzinfo=PK)
    assert week_start(datetime(2026, 9, 27, 23, 59, 59, tzinfo=PK)) == datetime(2026, 9, 21, tzinfo=PK)
    assert week_start(datetime(2026, 9, 28, 0, 0, tzinfo=PK)) == datetime(2026, 9, 28, tzinfo=PK)


def test_the_weeks_spend_counts_billed_and_possibly_billed(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry(datetime(2026, 9, 20, 23, 59, 59, tzinfo=PK), usd=5.0))  # Sunday: last week
    ledger.append(entry(datetime(2026, 9, 21, 0, 0, 0, tzinfo=PK), usd=1.0))  # Monday 00:00
    ledger.append(entry(datetime(2026, 9, 23, 12, 0, tzinfo=PK), usd=0.5, billing="possibly_billed"))
    ledger.append(entry(datetime(2026, 9, 23, 12, 0, tzinfo=PK), usd=0.25, billing="not_billed"))
    sunday_night = datetime(2026, 9, 27, 23, 59, 59, tzinfo=PK)
    assert ledger.spent_since(week_start(sunday_night)) == pytest.approx(1.5)


def test_todays_neurons_count_from_midnight_utc(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry(datetime(2026, 9, 24, 4, 59, tzinfo=PK), neurons=100.0))  # 23:59 UTC the day before
    ledger.append(entry(datetime(2026, 9, 24, 5, 0, tzinfo=PK), neurons=207.59))  # 00:00 UTC
    now = datetime(2026, 9, 24, 20, 0, tzinfo=PK)
    assert utc_day_start(now) == datetime(2026, 9, 24, 0, 0, tzinfo=UTC)
    assert ledger.neurons_since(utc_day_start(now)) == pytest.approx(207.59)
```

`tests/test_budget.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from stickman.budget import Budget, BudgetExceeded
from stickman.ledger import Ledger, LedgerEntry
from stickman.settings import BudgetSettings

PK = timezone(timedelta(hours=5))


def test_a_reservation_counts_until_it_is_released():
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=0.1)
    token = budget.reserve(0.2)
    assert budget.in_flight == pytest.approx(0.2)
    budget.release(token)
    assert budget.in_flight == 0


def test_in_flight_calls_count_toward_the_limit():
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=0.5)
    budget.reserve(0.3)
    with pytest.raises(BudgetExceeded, match="over the weekly budget"):
        budget.reserve(0.3)


def test_reaching_the_limit_exactly_is_allowed():
    Budget(BudgetSettings(weekly_usd=1.0), spent=0.7).reserve(0.3)


def test_spend_added_after_a_call_counts():
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=0.0)
    budget.add_spent(0.9)
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.2)


def test_the_warning_comes_once_at_the_warn_ratio():
    warnings = []
    budget = Budget(BudgetSettings(weekly_usd=1.0, warn_ratio=0.8), spent=0.75, warn=warnings.append)
    budget.reserve(0.01)
    assert warnings == []
    budget.reserve(0.04)
    budget.reserve(0.01)
    assert len(warnings) == 1
    assert "80%" in warnings[0]


def test_force_goes_past_the_limit_with_one_warning():
    warnings = []
    budget = Budget(BudgetSettings(weekly_usd=1.0), spent=1.0, force=True, warn=warnings.append)
    budget.reserve(0.1)
    budget.reserve(0.1)
    assert len(warnings) == 1
    assert "--force" in warnings[0]


def test_the_weeks_spend_comes_from_the_ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for day, usd in ((20, 5.0), (22, 1.25)):
        ledger.append(LedgerEntry(ts=datetime(2026, 9, day, 12, 0, tzinfo=PK), project="p", kind="image",
                                  model="m", est_usd=usd, billing="billed"))
    budget = Budget.from_ledger(ledger, BudgetSettings(), now=datetime(2026, 9, 25, 9, 0, tzinfo=PK))
    assert budget.spent == pytest.approx(1.25)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_ledger.py tests/test_budget.py -q`
Expected: FAIL with `ModuleNotFoundError` for `stickman.ledger` and `stickman.budget`.

- [ ] **Step 3: Implement**

`src/stickman/ledger.py`:

```python
"""The global cost ledger, ledger.jsonl (spec §5.4, §9.7): one JSON object per line, append-only."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

LEDGER_FILE = "ledger.jsonl"  # at the workspace root

Kind = Literal["image", "sheet", "anchor", "llm", "vision"]
Billing = Literal["billed", "possibly_billed", "not_billed"]
SPENT: frozenset[str] = frozenset({"billed", "possibly_billed"})


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: AwareDatetime
    project: str
    unit: str | None = None
    kind: Kind
    model: str
    est_usd: float = Field(ge=0)
    billing: Billing
    request_id: str | None = None
    neurons: float | None = None  # [M3] the cf-ai-neurons header of a successful call


def week_start(now: datetime) -> datetime:
    """Monday 00:00 of `now`'s week, in `now`'s own time zone; callers pass local time (§9.7)."""
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def utc_day_start(now: datetime) -> datetime:
    """00:00 UTC of `now`'s UTC day, when the free daily allocation resets."""
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: LedgerEntry) -> None:
        """Add one line. A torn last line (a killed write) is ended first, so this entry stays readable."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(entry.model_dump_json().encode("utf-8") + b"\n")

    def entries(self) -> Iterator[LedgerEntry]:
        """Every readable entry. A line that isn't a valid entry, such as a torn write, is skipped."""
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    yield LedgerEntry.model_validate_json(line)
                except ValidationError:
                    continue

    def spent_since(self, start: datetime) -> float:
        return sum(e.est_usd for e in self.entries() if e.billing in SPENT and e.ts >= start)

    def neurons_since(self, start: datetime) -> float:
        return sum(e.neurons or 0.0 for e in self.entries() if e.ts >= start)
```

`src/stickman/budget.py`:

```python
"""The weekly budget (spec §9.7), checked before every image call."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from stickman.ledger import Ledger, week_start
from stickman.pricing import format_usd
from stickman.settings import BudgetSettings


class BudgetExceeded(Exception):
    """Starting this call would take the week's spend over budget.weekly_usd."""


class Budget:
    """spent (billed + possibly billed this week) + in-flight reservations + this call's estimate."""

    def __init__(
        self,
        settings: BudgetSettings,
        *,
        spent: float,
        force: bool = False,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self.spent = spent
        self._force = force
        self._warn = warn or (lambda message: None)
        self._reserved: dict[int, float] = {}
        self._next_token = 0
        self._warned = False
        self._forced = False

    @classmethod
    def from_ledger(
        cls,
        ledger: Ledger,
        settings: BudgetSettings,
        *,
        now: datetime,
        force: bool = False,
        warn: Callable[[str], None] | None = None,
    ) -> Budget:
        return cls(settings, spent=ledger.spent_since(week_start(now)), force=force, warn=warn)

    @property
    def in_flight(self) -> float:
        return sum(self._reserved.values())

    def reserve(self, estimate: float) -> int:
        """Reserve a call's estimate. Raises BudgetExceeded above the limit, unless forced."""
        limit = self._settings.weekly_usd
        projected = self.spent + self.in_flight + estimate
        if projected > limit:
            if not self._force:
                raise BudgetExceeded(
                    f"{format_usd(projected)} would be over the weekly budget of {format_usd(limit)} "
                    f"({format_usd(self.spent)} spent this week)"
                )
            if not self._forced:
                self._forced = True
                self._warn(f"Over the weekly budget ({format_usd(projected)} of {format_usd(limit)}): continuing because of --force.")
        elif projected >= self._settings.warn_ratio * limit and not self._warned:
            self._warned = True
            self._warn(f"Weekly spend has reached {projected / limit:.0%} of the {format_usd(limit)} budget.")
        self._next_token += 1
        self._reserved[self._next_token] = estimate
        return self._next_token

    def release(self, token: int) -> None:
        self._reserved.pop(token, None)

    def add_spent(self, usd: float) -> None:
        self.spent += usd
```

In `spec.md` §5.4, add this bullet after the `billing` bullet:

```markdown
- **[M3]** Each entry also has `neurons`: the `cf-ai-neurons` header of a successful call, or null.
  - A `billed` entry's `est_usd` is that header's cost (§9.7), or the §9.6 estimate when the header is missing.
  - A timeout is `possibly_billed`, at its estimate.
  - Any other error response is `not_billed`.
  - When reading, a torn last line is skipped, and the next entry is still written on a line of its own.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_ledger.py tests/test_budget.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/ledger.py src/stickman/budget.py tests/test_ledger.py tests/test_budget.py spec.md
git commit -m "feat: append-only cost ledger and the weekly budget with in-flight reservations" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The meter, and planning calls through it (spec §9.7, §16)

**Files:**
- Create: `src/stickman/meter.py`
- Modify: `src/stickman/runlog.py`, `src/stickman/plan/llm.py`, `src/stickman/cli.py`, `spec.md` (§9.7 and §16 notes)
- Test: `tests/test_meter.py` (new), `tests/plan/test_llm.py`, `tests/test_cli_plan.py`, `tests/test_runlog.py`

**Interfaces:**
- Consumes: `Ledger`, `LedgerEntry`, `Budget`, `BudgetExceeded`, `PricingConfig`, `neurons_usd` and `load_pricing`.
- Produces:
  - **`stickman.meter`:**
    - `Meter(*, project, ledger=None, budget=None, pricing=None, now=local_now, clock=time.perf_counter)`.
    - `await meter.run(call, *, kind, model, estimate_usd, unit=None, cost_of=None) -> Metered[R]`. It raises `BudgetExceeded` before the call when there is a budget and it says no. `R` is anything with `.neurons` and `.request_id`.
    - `meter.run_usd` and `meter.possibly_billed` hold the run's totals.
    - `meter.llm_estimate(model, prompt_chars, max_tokens)` and `meter.llm_cost(model, input_tokens, output_tokens)`.
    - `Metered(result, usd, latency_s)`.
    - `billing_of(exc: CFError) -> Billing` and `local_now() -> datetime`.
  - **`stickman.runlog`:** `mask(text, secrets) -> str` and `RunLog.mask(text) -> str`.
  - **`stickman.plan.llm`:**
    - `FALLBACK_MAX_TOKENS = 8192`.
    - `StageRunner(..., meter: Meter | None = None)` and `StageRunner.max_tokens(model) -> int`.
    - One log entry per API attempt. Every entry has `kind`, `stage`, `model`, `attempt`, `call`, `cache_key`, `max_tokens`, `json_mode`, `prompt` and `billing`.

- [ ] **Step 1: Write the failing tests**

`tests/test_meter.py`:

```python
import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from stickman.budget import Budget, BudgetExceeded
from stickman.cf.client import ImageResult, LLMResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.ledger import Ledger
from stickman.meter import Meter, billing_of
from stickman.settings import BudgetSettings

PK = timezone(timedelta(hours=5))
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=PK)
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
IMAGE_USD = 207.59 * 0.011 / 1000


def metered(tmp_path, budget=None):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    meter = Meter(project="2026-09-25_demo", ledger=ledger, budget=budget, now=lambda: NOW,
                  clock=itertools.count().__next__)
    return meter, ledger


def returning(result):
    calls = []

    async def call():
        calls.append(1)
        return result

    return call, calls


def raising(exc):
    async def call():
        raise exc

    return call


def run(meter, call, **kwargs):
    kwargs = {"kind": "image", "model": KLEIN_4B, "estimate_usd": 0.0023, **kwargs}
    return asyncio.run(meter.run(call, **kwargs))


def test_a_successful_call_is_recorded_at_its_measured_cost(tmp_path):
    meter, ledger = metered(tmp_path)
    call, _ = returning(ImageResult(b"img", neurons=207.59, request_id="req-1"))
    result = run(meter, call, unit="006a")
    assert result.usd == pytest.approx(IMAGE_USD)
    assert result.latency_s == 1
    [entry] = list(ledger.entries())
    assert (entry.ts, entry.project, entry.unit, entry.kind, entry.model) == (NOW, "2026-09-25_demo", "006a", "image", KLEIN_4B)
    assert (entry.billing, entry.request_id, entry.neurons) == ("billed", "req-1", 207.59)
    assert entry.est_usd == pytest.approx(IMAGE_USD)
    assert meter.run_usd == pytest.approx(IMAGE_USD)


def test_without_a_neuron_header_the_token_cost_then_the_estimate_is_used(tmp_path):
    meter, _ = metered(tmp_path)
    reply = LLMResult(text="{}", input_tokens=1000, output_tokens=500, raw={})
    call, _ = returning(reply)
    assert run(meter, call, kind="llm", model="m", estimate_usd=0.5, cost_of=lambda r: 0.01).usd == 0.01
    assert run(meter, call, kind="llm", model="m", estimate_usd=0.5).usd == 0.5


def test_a_timeout_is_recorded_as_possibly_billed_at_its_estimate(tmp_path):
    meter, ledger = metered(tmp_path)
    with pytest.raises(CFError):
        run(meter, raising(CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True)))
    [entry] = list(ledger.entries())
    assert (entry.billing, entry.est_usd, entry.neurons) == ("possibly_billed", 0.0023, None)
    assert (meter.run_usd, meter.possibly_billed) == (0.0023, 1)


def test_an_error_response_is_recorded_as_not_billed(tmp_path):
    meter, ledger = metered(tmp_path)
    with pytest.raises(CFError):
        run(meter, raising(CFError(ErrorCategory.BAD_REQUEST, "invalid", status=400)))
    assert [e.billing for e in ledger.entries()] == ["not_billed"]
    assert meter.run_usd == 0.0


def test_the_budget_is_checked_before_the_call_and_charged_after(tmp_path):
    budget = Budget(BudgetSettings(weekly_usd=0.004), spent=0.0)
    meter, ledger = metered(tmp_path, budget)
    call, calls = returning(ImageResult(b"img", neurons=207.59))
    run(meter, call)
    assert budget.spent == pytest.approx(IMAGE_USD)
    assert budget.in_flight == 0
    with pytest.raises(BudgetExceeded):
        run(meter, call)  # 0.00228 spent + 0.0023 estimate is over 0.004
    assert len(calls) == 1
    assert len(list(ledger.entries())) == 1


def test_a_failed_call_releases_its_reservation(tmp_path):
    budget = Budget(BudgetSettings(), spent=0.0)
    meter, _ = metered(tmp_path, budget)
    with pytest.raises(CFError):
        run(meter, raising(CFError(ErrorCategory.BAD_REQUEST, "invalid", status=400)))
    assert budget.in_flight == 0


def test_billing_of_errors():
    assert billing_of(CFError(ErrorCategory.TRANSIENT, "t", possibly_billed=True)) == "possibly_billed"
    assert billing_of(CFError(ErrorCategory.RATE_LIMITED, "r", status=429)) == "not_billed"


def test_a_bare_meter_only_measures():
    meter = Meter(project="")
    call, _ = returning(ImageResult(b"img", neurons=10.0))
    assert run(meter, call).usd == pytest.approx(10 * 0.011 / 1000)
    assert meter.llm_estimate("m", 400, 100) == 0.0
    assert meter.llm_cost("m", 1, 1) is None
```

Append to `tests/test_runlog.py` (add `from stickman.runlog import RunLog, mask` to its imports if they aren't there):

```python
def test_mask_replaces_every_secret():
    assert mask("token tok-secret and tok-secret", ("tok-secret", "")) == "token *** and ***"
    assert RunLog(None, secrets=("tok-secret",)).mask("from tok-secret") == "from ***"
```

Append to `tests/plan/test_llm.py`. Extend the import to `from stickman.plan.llm import CUT_OFF_ERROR, FALLBACK_MAX_TOKENS, STAGE_MAX_TOKENS, PlanningError, StageRequest, StageRunner`, and add `from stickman.ledger import Ledger`, `from stickman.meter import Meter`, `from stickman.pricing import load_pricing`, `from stickman.runlog import RunLog` and `from stickman.settings import LLMSettings, RetrySettings`:

```python
def test_every_api_call_is_logged_including_retried_ones(fake_chat, stage_runner, tmp_path):
    log_path = tmp_path / "run.jsonl"
    chat = fake_chat([
        CFError(ErrorCategory.RATE_LIMITED, "slow down", status=429),
        CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True),
        GOOD,
    ])
    run(stage_runner(chat, log_path=log_path), request())
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["attempt"], e["call"], e["ok"], e.get("error"), e["billing"]) for e in entries] == [
        (1, 1, False, "rate_limited", "not_billed"),
        (1, 2, False, "transient", "possibly_billed"),
        (1, 3, True, None, "billed"),
    ]
    assert len({e["cache_key"] for e in entries}) == 1
    assert all(e["max_tokens"] == STAGE_MAX_TOKENS and e["json_mode"] is False for e in entries)


def test_the_fallback_model_has_its_own_token_limit(fake_chat, stage_runner):
    chat = fake_chat(["nope", "nope", GOOD])
    run(stage_runner(chat), request())
    assert [c["max_tokens"] for c in chat.calls] == [STAGE_MAX_TOKENS, STAGE_MAX_TOKENS, FALLBACK_MAX_TOKENS]
    assert chat.calls[2]["model"] == LLM.fallback_model


def test_planning_calls_go_in_the_ledger(fake_chat, tmp_path):
    async def no_sleep(seconds):
        return None

    ledger = Ledger(tmp_path / "ledger.jsonl")
    meter = Meter(project="2026-09-25_demo", ledger=ledger, pricing=load_pricing(tmp_path))
    runner = StageRunner(fake_chat([GOOD]), LLMSettings(), RetrySettings(), cache_dir=None, log=RunLog(None),
                         sleep=no_sleep, meter=meter)
    run(runner, request())
    [entry] = list(ledger.entries())
    assert (entry.kind, entry.model, entry.billing, entry.neurons, entry.project) == (
        "llm", LLM.planner_model, "billed", 1.5, "2026-09-25_demo")
    assert entry.est_usd == pytest.approx(1.5 * 0.011 / 1000)
```

Append to `tests/test_cli_plan.py`:

```python
def test_new_records_its_planning_calls_in_the_ledger(workspace, monkeypatch, sample_chat):
    use_chat(monkeypatch, sample_chat)
    assert new(workspace).exit_code == 0
    entries = [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 7  # analyse, cut and 5 describe batches
    assert {(e["kind"], e["billing"], e["project"]) for e in entries} == {("llm", "billed", folder(workspace).name)}


def test_cloudflare_errors_on_the_console_mask_the_token(workspace, monkeypatch, fake_chat):
    use_chat(monkeypatch, fake_chat([CFError(ErrorCategory.BAD_REQUEST, "rejected request from tok-secret")]))
    result = new(workspace)
    assert result.exit_code == 1
    assert "rejected request from ***" in result.output
    assert "tok-secret" not in result.output
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_meter.py tests/test_runlog.py tests/plan/test_llm.py tests/test_cli_plan.py -q`
Expected: FAIL:
- `ModuleNotFoundError: No module named 'stickman.meter'`.
- `ImportError` for `mask` and `FALLBACK_MAX_TOKENS`.
- The CLI tests fail because `ledger.jsonl` is missing and the output holds the unmasked token.

- [ ] **Step 3: Implement**

`src/stickman/meter.py`:

```python
"""A budget check before, and a ledger entry after, every API call (spec §9.7)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from stickman.budget import Budget
from stickman.cf.errors import CFError
from stickman.ledger import Billing, Kind, Ledger, LedgerEntry
from stickman.pricing import PricingConfig, neurons_usd


class Measured(Protocol):
    """A result that reports its own cost: LLMResult and ImageResult."""

    @property
    def neurons(self) -> float | None: ...

    @property
    def request_id(self) -> str | None: ...


R = TypeVar("R", bound=Measured)


def local_now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def billing_of(exc: CFError) -> Billing:
    """A timeout may have been billed; an error response wasn't (spec §5.4, §9.5)."""
    return "possibly_billed" if exc.possibly_billed else "not_billed"


@dataclass(frozen=True)
class Metered(Generic[R]):
    result: R
    usd: float  # what the ledger recorded for the call
    latency_s: float


class Meter:
    """One per command run. Without a ledger or budget it only measures, which tests use."""

    def __init__(
        self,
        *,
        project: str,
        ledger: Ledger | None = None,
        budget: Budget | None = None,
        pricing: PricingConfig | None = None,
        now: Callable[[], datetime] = local_now,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.project = project
        self.pricing = pricing
        self._ledger = ledger
        self._budget = budget
        self._now = now
        self._clock = clock
        self.run_usd = 0.0  # billed and possibly billed, through this meter
        self.possibly_billed = 0

    def llm_estimate(self, model: str, prompt_chars: int, max_tokens: int) -> float:
        return 0.0 if self.pricing is None else self.pricing.llm_estimate(model, prompt_chars, max_tokens)

    def llm_cost(self, model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
        return None if self.pricing is None else self.pricing.llm_cost(model, input_tokens, output_tokens)

    async def run(
        self,
        call: Callable[[], Awaitable[R]],
        *,
        kind: Kind,
        model: str,
        estimate_usd: float,
        unit: str | None = None,
        cost_of: Callable[[R], float | None] | None = None,
    ) -> Metered[R]:
        """Make one API call. With a budget, BudgetExceeded is raised before the call when it says no."""
        token = self._budget.reserve(estimate_usd) if self._budget is not None else None
        started = self._clock()
        try:
            result = await call()
        except CFError as exc:
            self._record(kind, model, unit, estimate_usd, billing_of(exc))
            raise
        finally:
            if token is not None and self._budget is not None:
                self._budget.release(token)
        latency = self._clock() - started
        usd = self._cost(result, estimate_usd, cost_of)
        self._record(kind, model, unit, usd, "billed", neurons=result.neurons, request_id=result.request_id)
        return Metered(result, usd, latency)

    @staticmethod
    def _cost(result: R, estimate: float, cost_of: Callable[[R], float | None] | None) -> float:
        """The cf-ai-neurons header, else the result's own cost (token counts), else the estimate."""
        if result.neurons is not None:
            return neurons_usd(result.neurons)
        actual = cost_of(result) if cost_of is not None else None
        return estimate if actual is None else actual

    def _record(
        self,
        kind: Kind,
        model: str,
        unit: str | None,
        usd: float,
        billing: Billing,
        *,
        neurons: float | None = None,
        request_id: str | None = None,
    ) -> None:
        if billing != "not_billed":
            self.run_usd += usd
            if self._budget is not None:
                self._budget.add_spent(usd)
        if billing == "possibly_billed":
            self.possibly_billed += 1
        if self._ledger is not None:
            self._ledger.append(
                LedgerEntry(
                    ts=self._now(), project=self.project, unit=unit, kind=kind, model=model,
                    est_usd=usd, billing=billing, request_id=request_id, neurons=neurons,
                )
            )
```

In `src/stickman/runlog.py`, add a module function above the class, and use it from `RunLog`:

```python
def mask(text: str, secrets: Sequence[str]) -> str:
    """`text` with every secret replaced by ***."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
```

Add this method to `RunLog`:

```python
    def mask(self, text: str) -> str:
        return mask(text, self._secrets)
```

In `RunLog.write`, replace the `for secret in self._secrets:` loop with `line = self.mask(json.dumps(record, ensure_ascii=False))`.

In `src/stickman/plan/llm.py`:
- Add `from stickman.meter import Meter, Metered, billing_of`.
- Replace the `STAGE_MAX_TOKENS` line with:

```python
STAGE_MAX_TOKENS = 16384  # the planner: gpt-oss spends part of this on reasoning; only used tokens are billed
FALLBACK_MAX_TOKENS = 8192  # llama-3.3-70b fp8-fast has a 24k context; 8192 answered in the first live run
```

Add `meter: Meter | None = None,` as the last keyword parameter of `StageRunner.__init__`, and set it in the body:

```python
        self._meter = meter if meter is not None else Meter(project="")
```

Add these two methods to `StageRunner`:

```python
    def max_tokens(self, model: str) -> int:
        return STAGE_MAX_TOKENS if model == self._llm.planner_model else FALLBACK_MAX_TOKENS

    def _entry(
        self,
        request: StageRequest[Any],
        model: str,
        response_format: dict[str, Any] | None,
        attempt: int,
        call: int,
        key: str,
    ) -> dict[str, Any]:
        """The fields every LLM log entry has (spec §16)."""
        return {
            "kind": "llm",
            "stage": request.stage,
            "model": model,
            "attempt": attempt,
            "call": call,
            "cache_key": key,
            "max_tokens": self.max_tokens(model),
            "json_mode": response_format is not None,
            "prompt": shorten(request.user),
        }
```

In `run()`, replace the body of the `for attempt in (1, 2):` loop up to `if result is not None:` with:

```python
                metered, call = await self._call(request, model, messages, response_format, attempt, key)
                reply = metered.result
                stop = finish_reason(reply)
                result, errors = self._parse(request, reply.text, stop=stop)
                self._log.write(
                    **self._entry(request, model, response_format, attempt, call, key),
                    ok=result is not None,
                    errors=errors,
                    finish_reason=stop,
                    reply_chars=len(reply.text),
                    latency_s=round(metered.latency_s, 2),
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                    neurons=reply.neurons,
                    usd=metered.usd,
                    billing="billed",
                    request_id=reply.request_id,
                )
```

Replace `_call` with:

```python
    async def _call(
        self,
        request: StageRequest[Any],
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None,
        attempt: int,
        key: str,
    ) -> tuple[Metered[LLMResult], int]:
        """One validation attempt's API call, retried on rate limits and temporary errors.

        Every call that fails is logged here; the one that answers is logged by run() (spec §16).
        """
        max_tokens = self.max_tokens(model)
        estimate = self._meter.llm_estimate(model, sum(len(str(m["content"])) for m in messages), max_tokens)
        calls = 0

        async def once() -> Metered[LLMResult]:
            nonlocal calls
            calls += 1
            started = time.perf_counter()
            try:
                return await self._meter.run(
                    lambda: self._client.chat(
                        model,
                        messages,
                        temperature=self._llm.temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                    ),
                    kind="llm",
                    model=model,
                    estimate_usd=estimate,
                    cost_of=lambda reply: self._meter.llm_cost(model, reply.input_tokens, reply.output_tokens),
                )
            except CFError as exc:
                self._log.write(
                    **self._entry(request, model, response_format, attempt, calls, key),
                    ok=False,
                    error=str(exc.category),
                    status=exc.status,
                    message=shorten(exc.message),
                    billing=billing_of(exc),
                    latency_s=round(time.perf_counter() - started, 2),
                )
                raise

        metered = await with_retries(once, self._retry, sleep=self._sleep)
        return metered, calls
```

In `src/stickman/cli.py`, add these imports: `from stickman.ledger import LEDGER_FILE, Ledger`, `from stickman.meter import Meter` and `from stickman.pricing import load_pricing`. In `_run_llm`:
- After `log = …`, add:

```python
    try:
        pricing = load_pricing(cfg.workspace)
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    # Every planning call goes in the ledger; the weekly budget never stops planning (spec §9.7 [M3]).
    meter = Meter(project=directory.name, ledger=Ledger(cfg.workspace / LEDGER_FILE), pricing=pricing)
```

- Pass `meter=meter` to `StageRunner(...)`.
- Wrap the two console messages in `log.mask(...)`:

```python
        _fail(log.mask(f"Planning failed: {exc}. Every attempt is logged in {log.path}.{again}"), EXIT_USER_ERROR)
```

```python
        _fail(log.mask(f"Cloudflare error: {exc}"), EXIT_USER_ERROR)
```

In `spec.md`:
- §9.7: add at the end of its bullet list:

```markdown
- **[M3]** Planning's LLM calls (`new`, `replan`) go in the ledger too. The weekly budget never stops them: a plan costs about $0.03, and `new` has no `--force`.
  - The budget check applies to image calls.
  - A run reads the week's spend from the ledger when it starts, and adds its own calls as they finish.
```

- §16: add after the **Logging** bullet:

```markdown
- **[M3] Log entries per attempt:** each API attempt gets its own entry, including attempts that are then retried.
  - LLM entries also carry `cache_key`, `call` (the try within one validation attempt), `max_tokens`, `json_mode`, `billing`, `usd` and `request_id`.
  - Image entries carry `unit`, `seed`, the size, `refs` (`path#sha256:…`), `billing`, `usd`, `neurons` and `request_id`.
  - The token is also masked in console messages built from Cloudflare errors, and in errors kept in `state.json`.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_meter.py tests/test_runlog.py tests/plan/test_llm.py tests/test_cli_plan.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`. Every existing planner and CLI test should pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/meter.py src/stickman/runlog.py src/stickman/plan/llm.py src/stickman/cli.py tests/test_meter.py tests/test_runlog.py tests/plan/test_llm.py tests/test_cli_plan.py spec.md
git commit -m "feat: every API call is metered; planning calls go in the ledger with one log entry per attempt" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Image files and fingerprints (spec §3, §9.2, §10.4)

**Files:**
- Create: `src/stickman/render/__init__.py` (empty), `src/stickman/render/images.py`, `src/stickman/render/fingerprint.py`
- Modify: `tests/conftest.py` (a small JPEG for tests)
- Test: `tests/render/test_images.py`, `tests/render/test_fingerprint.py`

**Interfaces:**
- Produces:
  - **`stickman.render.images`:**
    - The constants `HISTORY_DIR = "images/_history"` and `META_KEY = "stickman"`, and the exception `ImageDecodeError(ValueError)`.
    - `sniff(data) -> "jpeg" | "png" | "webp" | None`.
    - `decode_image(data) -> PIL.Image.Image`, which raises `ImageDecodeError`.
    - `encode_png(image, metadata: dict) -> bytes`, which puts the metadata as JSON in a compressed text chunk.
    - `read_metadata(path) -> dict | None`.
    - `image_stem(unit_id, start) -> str`, for example `"006a_00-21.0"`.
    - `history_name(unit_id, v) -> str`, for example `"006a_v3.png"`.
    - `history_files(project_dir) -> dict[str, dict[int, Path]]`.
  - **`stickman.render.fingerprint`:** `FINGERPRINT_FIELDS`, `canonical_json(data) -> str` and `fingerprint(unit, *, cast_descriptions, model, aspect, size, style_version, reference_hashes) -> "sha256:<hex>"`.
  - **`tests/conftest.py`:** `jpeg_bytes(width=64, height=36) -> bytes` and the fixture `jpeg`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/conftest.py` (with `import functools`, `import io` and `from PIL import Image` at the top):

```python
@functools.lru_cache
def jpeg_bytes(width=64, height=36):
    """A small white JPEG, like the base64 JPEG Klein returns (M0)."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def jpeg():
    return jpeg_bytes()
```

`tests/render/test_images.py`:

```python
import pytest
from PIL import Image

from stickman.render.images import (
    HISTORY_DIR,
    ImageDecodeError,
    decode_image,
    encode_png,
    history_files,
    history_name,
    image_stem,
    read_metadata,
    sniff,
)


def test_formats_are_told_apart_by_their_magic_bytes(jpeg):
    assert sniff(jpeg) == "jpeg"
    assert sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "png"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff(b"<html>") is None


def test_an_api_jpeg_is_saved_as_png_with_its_metadata(tmp_path, jpeg):
    metadata = {"v": 1, "prompt_sent": "Scene: a stickman — waving."}
    data = encode_png(decode_image(jpeg), metadata)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    path = tmp_path / "001_v1.png"
    path.write_bytes(data)
    assert read_metadata(path) == metadata
    with Image.open(path) as saved:
        assert saved.size == (64, 36)


@pytest.mark.parametrize("data", [b"<html>oops</html>", b"\xff\xd8\xff" + b"\x00" * 20])
def test_bytes_that_are_not_an_image_raise(data):
    with pytest.raises(ImageDecodeError):
        decode_image(data)


def test_a_truncated_jpeg_raises(jpeg):
    with pytest.raises(ImageDecodeError):
        decode_image(jpeg[: len(jpeg) // 2])


def test_a_file_without_metadata_has_none(tmp_path):
    plain = tmp_path / "plain.png"
    Image.new("RGB", (4, 4), "white").save(plain, format="PNG")
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"junk")
    assert read_metadata(plain) is None
    assert read_metadata(junk) is None
    assert read_metadata(tmp_path / "missing.png") is None


@pytest.mark.parametrize(
    ("start", "stem"),
    [(0.0, "001_00-00.0"), (21.0, "001_00-21.0"), (24.294, "001_00-24.3"), (59.96, "001_01-00.0"), (133.852, "001_02-13.9")],
)
def test_image_file_names_carry_the_start_time(start, stem):
    assert image_stem("001", start) == stem


def test_history_files_are_listed_by_unit_and_version(tmp_path):
    assert history_name("006a", 3) == "006a_v3.png"
    folder = tmp_path / HISTORY_DIR
    folder.mkdir(parents=True)
    for name in ("006a_v1.png", "006a_v3.png", "001_v2.png", "006a_v2.png.tmp", "notes.txt", "006a_v0.png"):
        (folder / name).write_bytes(b"x")
    found = history_files(tmp_path)
    assert {unit: sorted(files) for unit, files in found.items()} == {"006a": [1, 3], "001": [2]}
    assert found["006a"][3] == folder / "006a_v3.png"
    assert history_files(tmp_path / "no-project") == {}
```

`tests/render/test_fingerprint.py`:

```python
import pytest

from stickman.plan.models import CharacterRef, parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.render.fingerprint import fingerprint

KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
DESCRIPTIONS = {"mascot": "the main character", "caveman_group": "three cavemen"}


def fp(unit, **changes):
    args = dict(cast_descriptions=DESCRIPTIONS, model=KLEIN_4B, aspect="16:9", size=(1920, 1088),
                style_version=1, reference_hashes=[])
    return fingerprint(unit, **{**args, **changes})


def first_unit(plan_data):
    return parse_plan(plan_data).units()[0]  # 001 draws only the mascot


def test_the_fingerprint_is_a_stable_sha256(plan_data):
    unit = first_unit(plan_data)
    value = fp(unit)
    assert value.startswith("sha256:") and len(value) == len("sha256:") + 64
    assert fp(first_unit(plan_data)) == value


@pytest.mark.parametrize(
    "change",
    [
        {"props": ["a small campfire"]},
        {"shot": "close-up"},
        {"composition": "figures on the left"},
        {"image_prompt": "edited by hand"},
        {"seed": 42},
        {"characters": [CharacterRef(ref="mascot", action="sleeping", emotion="calm")]},
    ],
)
def test_a_visual_field_the_prompt_or_a_pinned_seed_changes_it(plan_data, change):
    unit = first_unit(plan_data)
    assert fp(unit.model_copy(update=change)) != fp(unit)


@pytest.mark.parametrize(
    "change",
    [
        {"model": "@cf/black-forest-labs/flux-2-klein-9b"},
        {"aspect": "9:16", "size": (1088, 1920)},
        {"style_version": 2},
        {"reference_hashes": ["sha256:" + "0" * 64]},
        {"cast_descriptions": {**DESCRIPTIONS, "mascot": "a different mascot"}},
    ],
)
def test_the_model_size_style_references_or_a_drawn_characters_description_change_it(plan_data, change):
    unit = first_unit(plan_data)
    assert fp(unit, **change) != fp(unit)


def test_text_timing_and_characters_not_drawn_never_change_it(plan_data):
    unit = first_unit(plan_data)
    same = unit.model_copy(update={"corrected_text": "Other words.", "start": 0.5, "softened": True})
    assert fp(same) == fp(unit)
    assert fp(unit, cast_descriptions={**DESCRIPTIONS, "caveman_group": "someone else"}) == fp(unit)


def test_comments_and_formatting_never_change_it(tmp_path, plan_data):
    path = tmp_path / "plan.yaml"
    write_plan(path, to_document(parse_plan(plan_data)), expected_hash=None)
    edited = tmp_path / "edited.yaml"
    text = path.read_text(encoding="utf-8")
    edited.write_text("# my notes\n" + text.replace("shot: wide", "shot: wide   # keep it wide", 1), encoding="utf-8")
    assert fp(load_plan(edited).plan.units()[0]) == fp(load_plan(path).plan.units()[0])
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/render -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.render'`.

- [ ] **Step 3: Implement**

Create an empty `src/stickman/render/__init__.py`.

`src/stickman/render/images.py`:

```python
"""Image files: format sniffing, PNG files with their version record inside, and names (spec §3, §9.2)."""

from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, PngImagePlugin

HISTORY_DIR = "images/_history"
META_KEY = "stickman"  # the PNG text chunk that holds the version record (spec §5.2 [M3])
_HISTORY_NAME = re.compile(r"^(?P<unit>\d{3}[ab]?)_v(?P<v>[1-9]\d*)\.png$")


class ImageDecodeError(ValueError):
    """The API's bytes aren't a JPEG, PNG or WebP image that can be read."""


def sniff(data: bytes) -> str | None:
    """The image format, from its magic bytes (spec §9.2)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def decode_image(data: bytes) -> Image.Image:
    kind = sniff(data)
    if kind is None:
        raise ImageDecodeError(f"not a JPEG, PNG or WebP image (it starts with {data[:12]!r})")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, SyntaxError, ValueError) as exc:
        raise ImageDecodeError(f"can't read the {kind} image: {exc}") from exc
    return image


def encode_png(image: Image.Image, metadata: dict[str, Any]) -> bytes:
    """PNG bytes with `metadata` as JSON in a compressed text chunk, so an image saved just before a
    run was killed can be added back to state.json (spec §5.2 [M3])."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    info = PngImagePlugin.PngInfo()
    info.add_text(META_KEY, json.dumps(metadata, sort_keys=True), zip=True)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def read_metadata(path: Path) -> dict[str, Any] | None:
    """The metadata encode_png put in the file, or None when it has none or can't be read."""
    try:
        with Image.open(path) as image:
            text = getattr(image, "text", {}).get(META_KEY)
    except (OSError, SyntaxError, ValueError):
        return None
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def image_stem(unit_id: str, start: float) -> str:
    """`<unit>_<MM-SS.s>`: the start rounded half up to 0.1 s, like 006a_00-21.0 (spec §3)."""
    tenths = math.floor(start * 10 + 0.5)
    minutes, rest = divmod(tenths, 600)
    return f"{unit_id}_{minutes:02d}-{rest // 10:02d}.{rest % 10}"


def history_name(unit_id: str, version: int) -> str:
    return f"{unit_id}_v{version}.png"


def history_files(project_dir: Path) -> dict[str, dict[int, Path]]:
    """The images/_history/<unit>_v<N>.png files, by unit and version number."""
    folder = project_dir / HISTORY_DIR
    found: dict[str, dict[int, Path]] = {}
    if folder.is_dir():
        for path in folder.iterdir():
            match = _HISTORY_NAME.match(path.name)
            if match:
                found.setdefault(match["unit"], {})[int(match["v"])] = path
    return found
```

`src/stickman/render/fingerprint.py`:

```python
"""A unit's fingerprint: what its image was made from (spec §10.4)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from stickman.plan.models import PlanUnit

FINGERPRINT_FIELDS: tuple[str, ...] = (
    "visual_idea",
    "visual_type",
    "shot",
    "time_of_day",
    "characters",
    "mood",
    "setting",
    "props",
    "composition",
    "energy_marks",
)


def canonical_json(data: object) -> str:
    """Sorted keys, UTF-8 and no whitespace (spec §10.4)."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(
    unit: PlanUnit,
    *,
    cast_descriptions: Mapping[str, str],
    model: str,
    aspect: str,
    size: tuple[int, int],
    style_version: int,
    reference_hashes: Sequence[str],
) -> str:
    """From the parsed plan, so comments and formatting never change it. `reference_hashes` are the
    sha256 of each reference file sent, in slot order."""
    drawn = sorted({character.ref for character in unit.characters})
    payload = {
        **unit.model_dump(mode="json", include=set(FINGERPRINT_FIELDS)),
        "cast": {ref: cast_descriptions[ref] for ref in drawn},
        "image_prompt": unit.image_prompt,
        "seed": unit.seed,
        "model": model,
        "aspect": aspect,
        "width": size[0],
        "height": size[1],
        "style_version": style_version,
        "references": list(reference_hashes),
    }
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/render -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/render/__init__.py src/stickman/render/images.py src/stickman/render/fingerprint.py tests/conftest.py tests/render/test_images.py tests/render/test_fingerprint.py
git commit -m "feat: PNG image files with their version record inside, file names, and unit fingerprints" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `state.json` and recovery after a crash (spec §5.2, §10.4, §16)

**Files:**
- Create: `src/stickman/render/state.py`, `src/stickman/render/recovery.py`
- Modify: `spec.md` (§5.2 note)
- Test: `tests/render/test_state.py`, `tests/render/test_recovery.py`

**Interfaces:**
- Consumes: `HISTORY_DIR`, `history_files`, `read_metadata` and `encode_png` (the last in tests) from `stickman.render.images`, and `stickman.fsutil.safe_write`.
- Produces:
  - **`stickman.render.state`:**
    - `STATE_FILE = "state.json"`, `UnitStatus` and `StateError(Exception)`.
    - `Version(v, file, seed, model, width, height, fingerprint, refs, prompt_sent, retry_of, retry_reason, qc, est_cost_usd, latency_s, created: AwareDatetime)`.
    - `UnitState(status, current_version, approved_version, versions, error)`, with `.version(v) -> Version | None`.
    - `ProjectState(schema_version, plan_approved, sheets_approved, test_units, tests_approved, units)`.
    - `StateStore.load(project_dir)`, with `.state`, `.project_dir`, `.save()`, `.unit(unit_id) -> UnitState` (created as `planned` when missing), `.set_status(unit_id, status, *, error=None)`, `.add_version(unit_id, version, *, status)` and `.next_version(unit_id) -> int`. Every change saves at once.
  - **`stickman.render.recovery`:**
    - `ExpectedUnit(stem: str, fingerprint: str)`.
    - `recover(store, expected: Mapping[str, ExpectedUnit]) -> list[str]` returns notes to print.
- **Import style (Task 10 relies on it):** `state.py` and `recovery.py` import `safe_write` as `from stickman.fsutil import safe_write`.

- [ ] **Step 1: Write the failing tests**

`tests/render/test_state.py`:

```python
import json
from datetime import datetime, timedelta, timezone

import pytest

from stickman.render.state import ProjectState, StateError, StateStore, Version

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def version(v=1, unit="006a", **changes):
    data = dict(v=v, file=f"images/_history/{unit}_v{v}.png", seed=48213377, model=KLEIN_4B, width=1920,
                height=1088, fingerprint="sha256:abc", refs=[], prompt_sent="a prompt", est_cost_usd=0.0023,
                latency_s=7.4, created=datetime(2026, 9, 22, 14, 3, 11, tzinfo=PK))
    return Version(**{**data, **changes})


def test_a_missing_state_file_is_an_empty_state(tmp_path):
    store = StateStore.load(tmp_path)
    assert store.state == ProjectState()
    assert store.unit("001").status == "planned"
    assert not (tmp_path / "state.json").exists()


def test_state_round_trips_through_state_json(tmp_path):
    StateStore.load(tmp_path).add_version("006a", version(), status="generated")
    unit = StateStore.load(tmp_path).state.units["006a"]
    assert (unit.status, unit.current_version, unit.versions) == ("generated", 1, [version()])
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["units"]["006a"]["versions"][0]["created"] == "2026-09-22T14:03:11+05:00"
    assert (data["plan_approved"], data["tests_approved"], data["test_units"]) == (False, False, [])
    assert not (tmp_path / "state.json.tmp").exists()


def test_every_status_change_is_saved_at_once(tmp_path):
    StateStore.load(tmp_path).set_status("001", "failed", error="bad_request: invalid")
    unit = StateStore.load(tmp_path).state.units["001"]
    assert (unit.status, unit.error) == ("failed", "bad_request: invalid")


def test_a_new_version_clears_the_last_error(tmp_path):
    store = StateStore.load(tmp_path)
    store.set_status("006a", "failed", error="transient: bad gateway")
    store.add_version("006a", version(), status="generated")
    assert store.unit("006a").error is None
    assert store.unit("006a").version(1) == version()
    assert store.unit("006a").version(2) is None


@pytest.mark.parametrize("text", ["{not json", '{"schema_version": 1, "surprise": true}'])
def test_an_unreadable_state_file_is_a_state_error(tmp_path, text):
    (tmp_path / "state.json").write_text(text, encoding="utf-8")
    with pytest.raises(StateError, match="state.json"):
        StateStore.load(tmp_path)


def test_version_numbers_skip_files_already_in_the_history(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("006a", version(1), status="generated")
    history = tmp_path / "images" / "_history"
    history.mkdir(parents=True)
    (history / "006a_v3.png").write_bytes(b"left by a killed run")
    (history / "006b_v7.png").write_bytes(b"another unit")
    assert store.next_version("006a") == 4
    assert store.next_version("001") == 1
```

`tests/render/test_recovery.py`:

```python
from datetime import datetime, timedelta, timezone

from PIL import Image

from stickman.render.images import encode_png
from stickman.render.recovery import ExpectedUnit, recover
from stickman.render.state import StateStore, Version

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
EXPECTED = {"001": ExpectedUnit("001_00-00.0", "sha256:abc"), "006a": ExpectedUnit("006a_00-21.0", "sha256:abc")}
CHANGED = {**EXPECTED, "006a": ExpectedUnit("006a_00-21.0", "sha256:new")}


def version(v=1, unit="006a", **changes):
    data = dict(v=v, file=f"images/_history/{unit}_v{v}.png", seed=1, model=KLEIN_4B, width=8, height=8,
                fingerprint="sha256:abc", refs=[], prompt_sent="a prompt", est_cost_usd=0.0023, latency_s=7.4,
                created=datetime(2026, 9, 22, 14, 3, 11, tzinfo=PK))
    return Version(**{**data, **changes})


def history_image(project, v=1, unit="006a", **changes):
    """A history PNG as the renderer writes it: the image, with its version record inside."""
    record = version(v, unit, **changes)
    path = project / record.file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(Image.new("RGB", (8, 8), "white"), record.model_dump(mode="json")))
    return path


def test_units_left_generating_go_back_to_planned(tmp_path):
    store = StateStore.load(tmp_path)
    store.set_status("001", "generating")
    notes = recover(store, EXPECTED)
    assert store.unit("001").status == "planned"
    assert StateStore.load(tmp_path).state.units["001"].status == "planned"
    assert any("generating" in note and "001" in note for note in notes)


def test_every_plan_unit_gets_a_state_entry(tmp_path):
    store = StateStore.load(tmp_path)
    recover(store, EXPECTED)
    assert set(StateStore.load(tmp_path).state.units) == {"001", "006a"}


def test_an_image_saved_just_before_a_kill_is_kept(tmp_path):
    store = StateStore.load(tmp_path)
    store.set_status("006a", "generating")
    path = history_image(tmp_path)
    notes = recover(store, EXPECTED)
    unit = store.unit("006a")
    assert (unit.status, unit.current_version, [v.v for v in unit.versions]) == ("generated", 1, [1])
    assert (tmp_path / "images" / "006a_00-21.0.png").read_bytes() == path.read_bytes()
    assert any("006a_v1.png" in note for note in notes)


def test_a_history_file_without_its_record_is_left_alone_and_its_number_skipped(tmp_path):
    store = StateStore.load(tmp_path)
    history = tmp_path / "images" / "_history"
    history.mkdir(parents=True)
    (history / "006a_v1.png").write_bytes(b"not a png")
    notes = recover(store, EXPECTED)
    assert store.unit("006a").versions == []
    assert store.next_version("006a") == 2
    assert any("006a_v1.png" in note for note in notes)


def test_a_missing_or_outdated_current_image_is_copied_again(tmp_path):
    store = StateStore.load(tmp_path)
    path = history_image(tmp_path)
    store.add_version("006a", version(), status="generated")
    current = tmp_path / "images" / "006a_00-21.0.png"
    recover(store, EXPECTED)
    assert current.read_bytes() == path.read_bytes()
    current.write_bytes(b"an older version")
    recover(store, EXPECTED)
    assert current.read_bytes() == path.read_bytes()


def test_temp_files_from_interrupted_writes_are_removed(tmp_path):
    history = tmp_path / "images" / "_history"
    history.mkdir(parents=True)
    for path in (tmp_path / "state.json.tmp", history / "006a_v2.png.tmp", tmp_path / "images" / "001_00-00.0.png.tmp"):
        path.write_bytes(b"half")
    recover(StateStore.load(tmp_path), EXPECTED)
    assert not list(tmp_path.rglob("*.tmp"))


def test_a_unit_whose_fingerprint_changed_is_stale_until_edited_back(tmp_path):
    store = StateStore.load(tmp_path)
    history_image(tmp_path)
    store.add_version("006a", version(), status="generated")
    notes = recover(store, CHANGED)
    assert store.unit("006a").status == "stale"
    assert any(note.startswith("Stale") and "006a" in note for note in notes)
    notes = recover(store, EXPECTED)
    assert store.unit("006a").status == "generated"
    assert "No longer stale: 006a" in notes


def test_an_approved_unit_is_compared_with_its_approved_version(tmp_path):
    store = StateStore.load(tmp_path)
    history_image(tmp_path, 1)
    history_image(tmp_path, 2, fingerprint="sha256:new")
    store.add_version("006a", version(1), status="generated")
    store.add_version("006a", version(2, fingerprint="sha256:new"), status="generated")
    unit = store.unit("006a")
    unit.approved_version, unit.status = 1, "approved"
    store.save()
    recover(store, EXPECTED)
    assert store.unit("006a").status == "approved"
    recover(store, CHANGED)
    assert store.unit("006a").status == "stale"
    recover(store, EXPECTED)
    assert store.unit("006a").status == "approved"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/render/test_state.py tests/render/test_recovery.py -q`
Expected: FAIL with `ModuleNotFoundError` for `stickman.render.state` and `stickman.render.recovery`.

- [ ] **Step 3: Implement**

`src/stickman/render/state.py`:

```python
"""state.json: the tool's data about a project's images (spec §5.2). Written only by the tool, safely."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from stickman.fsutil import safe_write
from stickman.render.images import history_files

STATE_FILE = "state.json"

UnitStatus = Literal["planned", "generating", "generated", "needs_review", "approved", "failed", "stale"]


class StateError(Exception):
    """state.json can't be read (CLI exit code 1). Nothing is written when this is raised."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Version(_Model):
    v: int = Field(ge=1)
    file: str  # relative to the project folder: images/_history/<unit>_v<N>.png
    seed: int
    model: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fingerprint: str
    refs: list[str] = Field(default_factory=list)  # "<path>#sha256:<hex>" per reference image, slot order
    prompt_sent: str
    retry_of: int | None = None
    retry_reason: str | None = None
    qc: dict[str, Any] | None = None  # M4
    est_cost_usd: float = Field(ge=0)
    latency_s: float = Field(ge=0)
    created: AwareDatetime


class UnitState(_Model):
    status: UnitStatus = "planned"
    current_version: int | None = None
    approved_version: int | None = None
    versions: list[Version] = Field(default_factory=list)
    error: str | None = None  # [M3] the last API error of a unit that ended failed

    def version(self, v: int) -> Version | None:
        return next((item for item in self.versions if item.v == v), None)


class ProjectState(_Model):
    schema_version: Literal[1] = 1
    plan_approved: bool = False
    sheets_approved: bool = False
    test_units: list[str] = Field(default_factory=list)
    tests_approved: bool = False
    units: dict[str, UnitState] = Field(default_factory=dict)


class StateStore:
    """state.json in memory. Every change is saved at once (spec §5.2)."""

    def __init__(self, project_dir: Path, state: ProjectState) -> None:
        self.project_dir = project_dir
        self.state = state

    @property
    def path(self) -> Path:
        return self.project_dir / STATE_FILE

    @classmethod
    def load(cls, project_dir: Path) -> StateStore:
        path = project_dir / STATE_FILE
        if not path.exists():
            return cls(project_dir, ProjectState())
        try:
            return cls(project_dir, ProjectState.model_validate_json(path.read_bytes()))
        except (ValidationError, ValueError, OSError) as exc:
            raise StateError(f"{path}: {exc}") from exc

    def save(self) -> None:
        safe_write(self.path, self.state.model_dump_json(indent=2).encode("utf-8"))

    def unit(self, unit_id: str) -> UnitState:
        return self.state.units.setdefault(unit_id, UnitState())

    def set_status(self, unit_id: str, status: UnitStatus, *, error: str | None = None) -> None:
        unit = self.unit(unit_id)
        unit.status = status
        unit.error = error
        self.save()

    def add_version(self, unit_id: str, version: Version, *, status: UnitStatus) -> None:
        unit = self.unit(unit_id)
        unit.versions = sorted([*unit.versions, version], key=lambda item: item.v)
        unit.current_version = version.v
        unit.status = status
        unit.error = None
        self.save()

    def next_version(self, unit_id: str) -> int:
        """One above every version in state.json and every file in images/_history, so an image
        that a killed run left behind is never overwritten."""
        used = {version.v for version in self.unit(unit_id).versions}
        used |= set(history_files(self.project_dir).get(unit_id, {}))
        return max(used, default=0) + 1
```

`src/stickman/render/recovery.py`:

```python
"""Putting a project in order before a run (spec §5.2, §10.4, §16)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from stickman.fsutil import safe_write
from stickman.render.images import HISTORY_DIR, history_files, read_metadata
from stickman.render.state import STATE_FILE, StateStore, Version

HAS_IMAGE = ("generated", "needs_review", "approved", "stale")


@dataclass(frozen=True)
class ExpectedUnit:
    """What a plan unit's files should be now: its current image's name, and the fingerprint an
    image made now would have (spec §10.4)."""

    stem: str
    fingerprint: str


def recover(store: StateStore, expected: Mapping[str, ExpectedUnit]) -> list[str]:
    """Undo what a crashed or killed run left behind, then mark stale units. Returns notes to print."""
    notes: list[str] = []
    _remove_temp_files(store.project_dir)
    for unit_id in expected:
        store.unit(unit_id)
    stuck = sorted(unit_id for unit_id, unit in store.state.units.items() if unit.status == "generating")
    for unit_id in stuck:
        store.state.units[unit_id].status = "planned"
    if stuck:
        notes.append(f"Reset {len(stuck)} unit(s) a stopped run left generating: {', '.join(stuck)}")
    adopted, unknown = _adopt_saved_images(store)
    if adopted:
        notes.append(f"Kept {len(adopted)} image(s) saved just before a run stopped: {', '.join(adopted)}")
    if unknown:
        notes.append(f"Left as they are (no version record inside): {', '.join(unknown)}")
    missing = _restore_current_images(store, expected)
    if missing:
        notes.append(f"Missing image files: {', '.join(missing)}")
    stale, fresh = _mark_stale(store, expected)
    if stale:
        notes.append(f"Stale, the plan changed after the image was made (not regenerated automatically): {', '.join(stale)}")
    if fresh:
        notes.append(f"No longer stale: {', '.join(fresh)}")
    store.save()
    return notes


def _remove_temp_files(project: Path) -> None:
    """Leftovers of writes a kill interrupted. Only the generating process writes these (the .lock)."""
    (project / f"{STATE_FILE}.tmp").unlink(missing_ok=True)
    for folder in (project / "images", project / HISTORY_DIR):
        if folder.is_dir():
            for path in folder.glob("*.tmp"):
                path.unlink(missing_ok=True)


def _adopt_saved_images(store: StateStore) -> tuple[list[str], list[str]]:
    """History images that state.json doesn't list: a kill landed between saving the image and
    saving the state. Each is added from the version record inside it."""
    adopted: list[str] = []
    unknown: list[str] = []
    for unit_id, files in sorted(history_files(store.project_dir).items()):
        unit = store.unit(unit_id)
        known = {version.v for version in unit.versions}
        found: list[Version] = []
        for v, path in sorted(files.items()):
            if v in known:
                continue
            version = _saved_version(path, v)
            if version is None:
                unknown.append(path.name)
            else:
                found.append(version)
                adopted.append(path.name)
        if not found:
            continue
        unit.versions = sorted([*unit.versions, *found], key=lambda item: item.v)
        if unit.status in ("planned", "failed"):
            unit.current_version = found[-1].v
            unit.status = "generated"  # M4: its QC result decides
            unit.error = None
    return adopted, unknown


def _saved_version(path: Path, v: int) -> Version | None:
    metadata = read_metadata(path)
    if metadata is None:
        return None
    try:
        version = Version.model_validate(metadata)
    except ValidationError:
        return None
    if version.v != v or version.file != f"{HISTORY_DIR}/{path.name}":
        return None
    return version


def _restore_current_images(store: StateStore, expected: Mapping[str, ExpectedUnit]) -> list[str]:
    """images/<stem>.png is a copy of the unit's current version (spec §3)."""
    missing: list[str] = []
    for unit_id, want in expected.items():
        unit = store.state.units[unit_id]
        version = unit.version(unit.current_version) if unit.current_version is not None else None
        if version is None:
            continue
        source = store.project_dir / version.file
        if not source.is_file():
            missing.append(version.file)
            continue
        data = source.read_bytes()
        target = store.project_dir / "images" / f"{want.stem}.png"
        if not target.is_file() or target.read_bytes() != data:
            safe_write(target, data)
    return missing


def _mark_stale(store: StateStore, expected: Mapping[str, ExpectedUnit]) -> tuple[list[str], list[str]]:
    """spec §10.4: compared with the approved version when there is one, else the current one."""
    stale: list[str] = []
    fresh: list[str] = []
    for unit_id, want in expected.items():
        unit = store.state.units[unit_id]
        if unit.status not in HAS_IMAGE:
            continue
        compared = unit.approved_version or unit.current_version
        version = unit.version(compared) if compared is not None else None
        if version is None:
            continue
        if version.fingerprint != want.fingerprint:
            if unit.status != "stale":
                unit.status = "stale"
                stale.append(unit_id)
        elif unit.status == "stale":
            # M4: needs_review when the current version failed QC
            unit.status = "approved" if unit.approved_version is not None else "generated"
            fresh.append(unit_id)
    return stale, fresh
```

In `spec.md` §5.2, add after the **Safe writes** paragraph:

```markdown
**[M3] Errors, version records and recovery:**
- Each unit also has `error`: the last API error (`<category>: <message>`, token masked) of a unit that ended `failed`.
- Each history PNG carries a copy of its version record, as JSON in a `stickman` text chunk.
- When a run starts, it does these steps in order:
  1. Temp files are removed.
  2. `generating` goes back to `planned`.
  3. A history image that `state.json` doesn't list is added from the copy inside it, and becomes current. This happens when a kill landed between saving the image and saving the state.
  4. Each current image `images/<unit>_<MM-SS.s>.png` is copied again from its version if it's missing or different.
  5. Stale units are marked (§10.4).
- A stale unit whose fingerprint matches again goes back to `approved` if it has an approved version, else to `generated`. From M4 it goes to `needs_review` when its current version failed QC.
- `qc` stays null until M4.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/render/test_state.py tests/render/test_recovery.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/render/state.py src/stickman/render/recovery.py tests/render/test_state.py tests/render/test_recovery.py spec.md
git commit -m "feat: state.json store and recovery after a crash, with stale marking" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The project lock (spec §3)

**Files:**
- Create: `src/stickman/render/lock.py`
- Test: `tests/render/test_lock.py`

**Interfaces:**
- Produces (`stickman.render.lock`):
  - `LOCK_FILE = ".lock"`.
  - `LockHeld(Exception)`, with `.pid: int | None`.
  - `pid_alive(pid) -> bool`. It never signals the process: on Windows, `os.kill` would end it.
  - `ProjectLock(project_dir, *, pid=None, alive=None)`, with `.acquire()` (raises `LockHeld`), `.release()`, `.removed_stale: int | None` and context-manager use. `alive` defaults to `pid_alive`, looked up when the lock is created, so tests can monkeypatch `stickman.render.lock.pid_alive`.

- [ ] **Step 1: Write the failing tests** (`tests/render/test_lock.py`)

```python
import os
import subprocess
import sys
import time

import pytest

from stickman.render.lock import LockHeld, ProjectLock, pid_alive


def test_acquire_writes_the_pid_and_release_removes_it(tmp_path):
    lock = ProjectLock(tmp_path, pid=4242)
    lock.acquire()
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "4242"
    lock.release()
    assert not (tmp_path / ".lock").exists()


def test_a_lock_held_by_a_running_process_is_refused(tmp_path):
    ProjectLock(tmp_path, pid=4242, alive=lambda pid: True).acquire()
    with pytest.raises(LockHeld, match="PID 4242") as info:
        ProjectLock(tmp_path, pid=5151, alive=lambda pid: True).acquire()
    assert info.value.pid == 4242
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "4242"


def test_a_lock_left_by_a_process_that_is_gone_is_removed(tmp_path):
    (tmp_path / ".lock").write_text("4242", encoding="ascii")
    lock = ProjectLock(tmp_path, pid=5151, alive=lambda pid: False)
    lock.acquire()
    assert lock.removed_stale == 4242
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "5151"


def test_a_lock_with_this_processs_own_pid_is_a_leftover(tmp_path):
    (tmp_path / ".lock").write_text("5151", encoding="ascii")
    lock = ProjectLock(tmp_path, pid=5151, alive=lambda pid: True)
    lock.acquire()
    assert lock.removed_stale == 5151


def test_an_empty_lock_is_another_process_starting_until_it_is_old(tmp_path):
    (tmp_path / ".lock").write_text("", encoding="ascii")
    with pytest.raises(LockHeld):
        ProjectLock(tmp_path, pid=5151, alive=lambda pid: True).acquire()
    old = time.time() - 60
    os.utime(tmp_path / ".lock", (old, old))
    lock = ProjectLock(tmp_path, pid=5151, alive=lambda pid: True)
    lock.acquire()
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "5151"


def test_release_never_removes_another_processs_lock(tmp_path):
    (tmp_path / ".lock").write_text("4242", encoding="ascii")
    ProjectLock(tmp_path, pid=5151).release()
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "4242"


def test_the_lock_is_released_when_the_block_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with ProjectLock(tmp_path, pid=4242):
            raise RuntimeError("boom")
    assert not (tmp_path / ".lock").exists()


def test_pid_alive_tells_a_running_process_from_a_finished_one():
    assert pid_alive(os.getpid()) is True
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    assert pid_alive(process.pid) is False
    assert pid_alive(0) is False
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/render/test_lock.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.render.lock'`.

- [ ] **Step 3: Implement** (`src/stickman/render/lock.py`)

```python
"""The project lock: one generating process per project (spec §3)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

LOCK_FILE = ".lock"
STARTING_GRACE_S = 5.0  # an empty lock this new belongs to a process that is still writing its PID

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


class LockHeld(Exception):
    def __init__(self, pid: int | None) -> None:
        self.pid = pid
        who = f"(PID {pid})" if pid is not None else "(starting up)"
        super().__init__(f"another stickman process {who} is generating this project")


def pid_alive(pid: int) -> bool:
    """Whether a process with this PID is running. It never signals the process: on Windows,
    os.kill(pid, 0) would end it."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # it exists, but we may not look at it
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class ProjectLock:
    """`.lock` holds the generating process's PID. A lock whose process is gone is removed, and
    `removed_stale` says whose it was, so the CLI can warn."""

    def __init__(self, project_dir: Path, *, pid: int | None = None, alive: Callable[[int], bool] | None = None) -> None:
        self.path = project_dir / LOCK_FILE
        self.pid = os.getpid() if pid is None else pid
        self._alive = alive or pid_alive
        self.removed_stale: int | None = None

    def acquire(self) -> None:
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                other = self._read_pid()
                if other is None and self._age() < STARTING_GRACE_S:
                    raise LockHeld(None) from None
                if other is not None and other != self.pid and self._alive(other):
                    raise LockHeld(other) from None
                self.removed_stale = other
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(str(self.pid))
            return
        raise LockHeld(self._read_pid())

    def release(self) -> None:
        if self._read_pid() == self.pid:
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def _read_pid(self) -> int | None:
        try:
            return int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    def _age(self) -> float:
        try:
            return time.time() - self.path.stat().st_mtime
        except OSError:
            return STARTING_GRACE_S
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/render/test_lock.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/render/lock.py tests/render/test_lock.py
git commit -m "feat: project lock with a Windows-safe check for a running process" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Reference images and each unit's request (spec §7.4, §8.3, §9.2)

**Files:**
- Create: `src/stickman/render/references.py`, `src/stickman/render/jobs.py`
- Modify: `src/stickman/library.py` (the two reference paths), `spec.md` (§9.2 note)
- Test: `tests/render/test_references.py`, `tests/render/test_jobs.py`

**Interfaces:**
- Consumes:
  - From the library: `find_references` and `LibraryCharacter`.
  - From config: `MascotConfig`, with `.ref` and `.description`.
  - From planning: `cast_infos`, and `Plan` / `PlanUnit`.
  - From pricing: `PricingConfig.image` and `image_cost_usd`.
  - From Tasks 5–6: `fingerprint`, `image_stem` and `ExpectedUnit`.
- Produces:
  - **`stickman.library`:** `anchor_ref_path(workspace, style_version) -> Path` and `character_ref_path(workspace, entry) -> Path`. `find_references` uses them.
  - **`stickman.render.references`:**
    - `RefImage(path: str, sha256: str, data: bytes, size)`, whose `.label` is `"<path>#sha256:<hex>"`.
    - `ReferenceFiles(workspace, *, ref_max_side)`, with `.load(path) -> RefImage`. It raises `ConfigError`, and it refuses anything inside `style_refs/`.
    - `reference_paths(workspace, slots, *, style_version, mascot, cast, library) -> list[Path]`.
  - **`stickman.render.jobs`:**
    - `JobError(ValueError)` and `random_seed() -> int`.
    - `RenderContext(workspace, settings, mascot, library, pricing)`, with `.load(workspace, settings)` and `.library_ids`.
    - `RenderJob(unit_id, stem, prompt, model, width, height, seed, steps, references, fingerprint, estimate_usd)`.
    - `JobBuilder(ctx, plan, *, seeds=random_seed)`, with `.size`, `.references(unit)`, `.fingerprint(unit)`, `.expected() -> dict[str, ExpectedUnit]` and `.jobs(units) -> list[RenderJob]`. `jobs()` raises `JobError` for units with an empty `image_prompt`. Creating the builder raises `ConfigError` when the plan's image model has no price.

- [ ] **Step 1: Write the failing tests**

`tests/render/test_references.py`:

```python
import hashlib
from datetime import date

import pytest
from PIL import Image

from stickman.config_files import MascotConfig
from stickman.library import LibraryCharacter
from stickman.plan.models import CastMember, MascotEntry
from stickman.render.references import ReferenceFiles, reference_paths
from stickman.settings import ConfigError

MASCOT = MascotConfig(name="Everyman", identity="a stickman", sheet="library/mascot/sheet_v1.png",
                      ref="library/mascot/ref_v1.png", seed=7, style_version=1)
CAVEMEN = LibraryCharacter(id="cavemen_v1", name="Caveman group", figures=3, description="three cavemen",
                           style_version=1, model="@cf/black-forest-labs/flux-2-klein-9b", sheet="sheet.png",
                           ref="ref.png", approved=date(2026, 9, 22))
CAST = [MascotEntry(id="mascot"), CastMember(id="caveman_group", name="Caveman group", figures=3,
                                              description="three cavemen", library_ref="cavemen_v1")]


def png(path, size=(512, 384), kind="PNG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format=kind)
    return path


def test_slot_0_is_the_anchor_then_one_file_per_slot(tmp_path):
    paths = reference_paths(tmp_path, ["mascot", "caveman_group"], style_version=1, mascot=MASCOT, cast=CAST,
                            library=[CAVEMEN])
    assert paths == [
        tmp_path / "library" / "style" / "anchor_v1_ref.png",
        tmp_path / "library" / "mascot" / "ref_v1.png",
        tmp_path / "library" / "characters" / "cavemen_v1" / "ref.png",
    ]


def test_without_an_anchor_nothing_is_sent(tmp_path):
    assert reference_paths(tmp_path, None, style_version=1, mascot=MASCOT, cast=CAST, library=[CAVEMEN]) == []


def test_a_reference_is_read_once_and_named_by_path_and_hash(tmp_path):
    path = png(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    files = ReferenceFiles(tmp_path, ref_max_side=512)
    ref = files.load(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert (ref.path, ref.sha256, ref.size, ref.data) == ("library/style/anchor_v1_ref.png", f"sha256:{digest}",
                                                          (512, 384), path.read_bytes())
    assert ref.label == f"library/style/anchor_v1_ref.png#sha256:{digest}"
    assert files.load(path) is ref


def test_the_stock_images_are_never_sent(tmp_path):
    path = png(tmp_path / "style_refs" / "stock1.png")
    with pytest.raises(ConfigError, match="never sent"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(path)


@pytest.mark.parametrize(
    ("size", "kind", "message"),
    [((600, 400), "PNG", "larger than image.ref_max_side"), ((512, 384), "JPEG", "must be PNG")],
)
def test_a_reference_copy_must_be_a_small_png(tmp_path, size, kind, message):
    path = png(tmp_path / "library" / "style" / "anchor_v1_ref.png", size, kind)
    with pytest.raises(ConfigError, match=message):
        ReferenceFiles(tmp_path, ref_max_side=512).load(path)


def test_a_missing_reference_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="can't read"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(tmp_path / "library" / "style" / "anchor_v1_ref.png")
```

`tests/render/test_jobs.py`:

```python
import itertools
from datetime import date

import pytest
from PIL import Image

from stickman.config_files import MascotConfig, load_mascot
from stickman.library import LibraryCharacter
from stickman.plan.models import parse_plan
from stickman.pricing import image_cost_usd, load_pricing
from stickman.render.jobs import JobBuilder, JobError, RenderContext, random_seed
from stickman.render.recovery import ExpectedUnit
from stickman.settings import ConfigError, Settings

KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
DEV = "@cf/black-forest-labs/flux-2-dev"


def context(workspace, mascot=None, library=()):
    return RenderContext(workspace, Settings(), mascot or load_mascot(workspace), tuple(library), load_pricing(workspace))


def builder(workspace, plan_data, **kwargs):
    return JobBuilder(context(workspace, **kwargs), parse_plan(plan_data), seeds=itertools.count(1000).__next__)


def png(path, size=(512, 384)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format="PNG")


def test_a_job_is_the_units_prompt_at_the_projects_size_and_model(tmp_path, plan_data):
    jobs = builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units())
    first = jobs[0]
    assert (first.unit_id, first.stem, first.prompt, first.model) == ("001", "001_00-00.0", "prompt for 001", KLEIN_4B)
    assert (first.width, first.height, first.steps, first.references) == (1920, 1088, None, ())
    assert [job.seed for job in jobs] == [1000, 1001, 1002]
    assert first.estimate_usd == pytest.approx(image_cost_usd(load_pricing(tmp_path).image(KLEIN_4B), (1920, 1088)))


def test_a_vertical_project_uses_the_vertical_size(tmp_path, plan_data):
    plan_data["aspect"] = "9:16"
    assert builder(tmp_path, plan_data).size == (1088, 1920)


def test_a_pinned_seed_is_used(tmp_path, plan_data):
    plan_data["scenes"][0]["units"][0]["seed"] = 42
    assert builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units()[:1])[0].seed == 42


def test_steps_are_sent_only_to_models_that_accept_them(tmp_path, plan_data):
    plan_data["image_model"] = DEV
    assert builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units()[:1])[0].steps == 25


def test_an_unpriced_model_is_a_config_error(tmp_path, plan_data):
    plan_data["image_model"] = "@cf/unknown/model"
    with pytest.raises(ConfigError, match="no image price"):
        builder(tmp_path, plan_data)


def test_units_without_a_prompt_are_refused(tmp_path, plan_data):
    plan_data["scenes"][1]["units"][0]["image_prompt"] = "  "
    with pytest.raises(JobError, match="002a"):
        builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units())


def test_references_come_in_slot_order_and_are_priced(tmp_path, plan_data):
    png(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    png(tmp_path / "library" / "mascot" / "ref_v1.png", (384, 512))
    png(tmp_path / "library" / "characters" / "cavemen_v1" / "ref.png")
    mascot = MascotConfig(name="Everyman", identity="a stickman", sheet="library/mascot/sheet_v1.png",
                          ref="library/mascot/ref_v1.png", seed=7, style_version=1)
    cavemen = LibraryCharacter(id="cavemen_v1", name="Caveman group", figures=3, description="three cavemen",
                               style_version=1, model=KLEIN_4B, sheet="sheet.png", ref="ref.png",
                               approved=date(2026, 9, 22))
    plan_data["cast"][1]["library_ref"] = "cavemen_v1"
    plan_data["scenes"][0]["units"][0]["characters"].append({"ref": "caveman_group", "action": "sitting", "emotion": "calm"})
    [job] = builder(tmp_path, plan_data, mascot=mascot, library=[cavemen]).jobs(parse_plan(plan_data).units()[:1])
    assert [ref.path for ref in job.references] == [
        "library/style/anchor_v1_ref.png", "library/mascot/ref_v1.png", "library/characters/cavemen_v1/ref.png"]
    price = load_pricing(tmp_path).image(KLEIN_4B)
    assert job.estimate_usd == pytest.approx(image_cost_usd(price, (1920, 1088), [(512, 384), (384, 512), (512, 384)]))


def test_expected_gives_each_units_image_name_and_fingerprint(tmp_path, plan_data):
    build = builder(tmp_path, plan_data)
    plan = parse_plan(plan_data)
    expected = build.expected()
    assert list(expected) == ["001", "002a", "002b"]
    assert expected["002b"] == ExpectedUnit("002b_00-07.0", build.fingerprint(plan.units()[2]))


def test_a_changed_reference_file_changes_the_fingerprint(tmp_path, plan_data):
    anchor = tmp_path / "library" / "style" / "anchor_v1_ref.png"
    png(anchor)
    unit = parse_plan(plan_data).units()[0]
    before = builder(tmp_path, plan_data).fingerprint(unit)
    png(anchor, (500, 384))
    assert builder(tmp_path, plan_data).fingerprint(unit) != before


def test_random_seeds_fit_a_signed_32_bit_integer():
    assert all(0 <= random_seed() < 2**31 for _ in range(200))
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/render/test_references.py tests/render/test_jobs.py -q`
Expected: FAIL with `ModuleNotFoundError` for `stickman.render.references` and `stickman.render.jobs`.

- [ ] **Step 3: Implement**

In `src/stickman/library.py`, add these functions above `find_references`, and use them inside it for the anchor path and each character's reference path:

```python
def anchor_ref_path(workspace: Path, style_version: int) -> Path:
    """The style anchor's reference copy (spec §2.2, §8.1)."""
    return workspace / "library" / "style" / f"anchor_v{style_version}_ref.png"


def character_ref_path(workspace: Path, entry: LibraryCharacter) -> Path:
    return workspace / "library" / "characters" / entry.id / entry.ref
```

`src/stickman/render/references.py`:

```python
"""The reference images sent with a unit's request (spec §7.4, §8.3; acceptance §18 #7 and #9)."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from stickman.config_files import MascotConfig
from stickman.library import LibraryCharacter, anchor_ref_path, character_ref_path
from stickman.plan.models import MASCOT, CastMember, MascotEntry
from stickman.settings import ConfigError

FORBIDDEN_DIR = "style_refs"  # the stock images: for humans only, never sent to any model (spec §2.2)


@dataclass(frozen=True)
class RefImage:
    path: str  # relative to the workspace, with forward slashes
    sha256: str  # "sha256:<hex>" of the file's bytes
    data: bytes
    size: tuple[int, int]

    @property
    def label(self) -> str:
        """How state.json and the run log name it (spec §5.2 `refs`)."""
        return f"{self.path}#{self.sha256}"


def reference_paths(
    workspace: Path,
    slots: Sequence[str] | None,
    *,
    style_version: int,
    mascot: MascotConfig,
    cast: Sequence[MascotEntry | CastMember],
    library: Sequence[LibraryCharacter],
) -> list[Path]:
    """Slot 0 is the style anchor, then one reference per cast id in `slots` (spec §7.4). `slots`
    is None when there is no anchor, and then nothing is sent."""
    if slots is None:
        return []
    members = {member.id: member for member in cast if isinstance(member, CastMember)}
    entries = {entry.id: entry for entry in library}
    paths = [anchor_ref_path(workspace, style_version)]
    for ref in slots:
        if ref == MASCOT:
            paths.append(workspace / mascot.ref)
        else:
            paths.append(character_ref_path(workspace, entries[str(members[ref].library_ref)]))
    return paths


class ReferenceFiles:
    """Reads each reference copy once per run, and refuses one that must never be sent."""

    def __init__(self, workspace: Path, *, ref_max_side: int) -> None:
        self._workspace = workspace.resolve()
        self._max_side = ref_max_side
        self._loaded: dict[Path, RefImage] = {}

    def load(self, path: Path) -> RefImage:
        resolved = path.resolve()
        if resolved not in self._loaded:
            self._loaded[resolved] = self._read(path, resolved)
        return self._loaded[resolved]

    def _read(self, path: Path, resolved: Path) -> RefImage:
        if resolved.is_relative_to(self._workspace / FORBIDDEN_DIR):
            raise ConfigError(f"{path}: images in {FORBIDDEN_DIR}/ are never sent to any API")
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            raise ConfigError(f"{path}: can't read the reference image: {exc.strerror or exc}") from exc
        try:
            with Image.open(io.BytesIO(data)) as image:
                size, kind = image.size, image.format
        except (OSError, SyntaxError, ValueError) as exc:
            raise ConfigError(f"{path}: not an image ({exc})") from exc
        if kind != "PNG":
            raise ConfigError(f"{path}: reference copies must be PNG, not {kind}")
        if max(size) > self._max_side:
            raise ConfigError(
                f"{path}: {size[0]}x{size[1]} is larger than image.ref_max_side ({self._max_side} px); "
                "reference copies are made by the tool"
            )
        inside = resolved.is_relative_to(self._workspace)
        shown = resolved.relative_to(self._workspace).as_posix() if inside else resolved.as_posix()
        return RefImage(shown, "sha256:" + hashlib.sha256(data).hexdigest(), data, size)
```

`src/stickman/render/jobs.py`:

```python
"""What each unit's image request is made of (spec §7.4, §9.2, §10.4)."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from stickman.config_files import MascotConfig, load_mascot
from stickman.library import LibraryCharacter, find_references, load_library
from stickman.plan.cast import cast_infos
from stickman.plan.models import Plan, PlanUnit
from stickman.pricing import PricingConfig, image_cost_usd, load_pricing
from stickman.render.fingerprint import fingerprint
from stickman.render.images import image_stem
from stickman.render.recovery import ExpectedUnit
from stickman.render.references import RefImage, ReferenceFiles, reference_paths
from stickman.settings import Settings


class JobError(ValueError):
    """Some units can't be sent as they are (CLI exit code 1)."""


def random_seed() -> int:
    """From 0 to 2**31 − 1, valid whether the API reads it as signed or unsigned 32-bit (spec §9.2).
    It is kept with the version as a record, not as a way to recreate the image."""
    return secrets.randbelow(2**31)


@dataclass(frozen=True)
class RenderContext:
    """What rendering reads besides the plan: settings, the mascot, the library and the prices."""

    workspace: Path
    settings: Settings
    mascot: MascotConfig
    library: tuple[LibraryCharacter, ...]
    pricing: PricingConfig

    @classmethod
    def load(cls, workspace: Path, settings: Settings) -> RenderContext:
        return cls(workspace, settings, load_mascot(workspace), tuple(load_library(workspace)), load_pricing(workspace))

    @property
    def library_ids(self) -> set[str]:
        return {entry.id for entry in self.library}


@dataclass(frozen=True)
class RenderJob:
    unit_id: str
    stem: str  # images/<stem>.png is the unit's current image
    prompt: str
    model: str
    width: int
    height: int
    seed: int
    steps: int | None  # only for models that accept it (FLUX.2 dev)
    references: tuple[RefImage, ...]  # slot order
    fingerprint: str
    estimate_usd: float


class JobBuilder:
    """RenderJobs and fingerprints for one plan. The plan's `image_prompt` is sent as it stands;
    the references are the ones that exist now (spec §7.4)."""

    def __init__(self, ctx: RenderContext, plan: Plan, *, seeds: Callable[[], int] = random_seed) -> None:
        self._ctx = ctx
        self._plan = plan
        self._seeds = seeds
        self._files = ReferenceFiles(ctx.workspace, ref_max_side=ctx.settings.image.ref_max_side)
        self._availability = find_references(
            ctx.workspace,
            use_references=ctx.settings.image.use_references,
            style_version=plan.style_version,
            mascot=ctx.mascot,
            cast=plan.cast,
            library=ctx.library,
        )
        self._descriptions = {ref: info.description for ref, info in cast_infos(plan.cast, ctx.mascot).items()}
        self._price = ctx.pricing.image(plan.image_model)
        width, height = ctx.settings.image.sizes[plan.aspect]
        self.size = (width, height)

    def references(self, unit: PlanUnit) -> tuple[RefImage, ...]:
        paths = reference_paths(
            self._ctx.workspace,
            self._availability.for_unit(unit.characters),
            style_version=self._plan.style_version,
            mascot=self._ctx.mascot,
            cast=self._plan.cast,
            library=self._ctx.library,
        )
        return tuple(self._files.load(path) for path in paths)

    def fingerprint(self, unit: PlanUnit) -> str:
        return fingerprint(
            unit,
            cast_descriptions=self._descriptions,
            model=self._plan.image_model,
            aspect=self._plan.aspect,
            size=self.size,
            style_version=self._plan.style_version,
            reference_hashes=[ref.sha256 for ref in self.references(unit)],
        )

    def expected(self) -> dict[str, ExpectedUnit]:
        return {unit.id: ExpectedUnit(image_stem(unit.id, unit.start), self.fingerprint(unit)) for unit in self._plan.units()}

    def jobs(self, units: Sequence[PlanUnit]) -> list[RenderJob]:
        empty = [unit.id for unit in units if not unit.image_prompt.strip()]
        if empty:
            raise JobError(f"no image_prompt for {', '.join(empty)}: run `stickman replan <unit>` for each")
        return [self._job(unit) for unit in units]

    def _job(self, unit: PlanUnit) -> RenderJob:
        refs = self.references(unit)
        steps = self._ctx.settings.image.steps if self._price.supports_steps else None
        return RenderJob(
            unit_id=unit.id,
            stem=image_stem(unit.id, unit.start),
            prompt=unit.image_prompt,
            model=self._plan.image_model,
            width=self.size[0],
            height=self.size[1],
            seed=unit.seed if unit.seed is not None else self._seeds(),
            steps=steps,
            references=refs,
            fingerprint=self.fingerprint(unit),
            estimate_usd=image_cost_usd(self._price, self.size, [ref.size for ref in refs], steps=steps or 1),
        )
```

In `spec.md` §9.2, add this bullet after the **Seeds** bullet:

```markdown
- **[M3]** A null seed is drawn from 0 … 2³¹−1, valid whether the API reads it as signed or unsigned 32-bit.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/render/test_references.py tests/render/test_jobs.py tests/test_library.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/library.py src/stickman/render/references.py src/stickman/render/jobs.py tests/render/test_references.py tests/render/test_jobs.py spec.md
git commit -m "feat: reference-slot files with the style_refs guard, and each unit's render job" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The renderer (spec §9.5, §10.3)

**Files:**
- Create: `src/stickman/render/renderer.py`
- Modify: `tests/conftest.py` (`FakeImages`), `spec.md` (§9.5 and §10.3 notes)
- Test: `tests/render/test_renderer.py`

**Interfaces:**
- Consumes:
  - From earlier tasks: `Meter` / `Metered` / `billing_of` / `local_now` (Task 4), `BudgetExceeded` (Task 3), `RenderJob` (Task 8), `StateStore` / `Version` (Task 6), and `decode_image` / `encode_png` / `history_name` / `HISTORY_DIR` / `ImageDecodeError` (Task 5).
  - From M2: `with_retries` and `RunLog.mask`.
- Produces (`stickman.render.renderer`):
  - `ImageClient` (Protocol).
  - `StopReason` (`daily_limit`, `budget`, `circuit_breaker`, `auth`).
  - `RunStopped`, `RunControl`, `CircuitBreaker` and `RunResult(stop, detail, elapsed_s)`.
  - `Renderer(client, store, meter, *, retry, concurrency, log, sleep=asyncio.sleep, now=local_now, on_done=None)`, with `await renderer.run(jobs) -> RunResult` and `renderer.control`.
  - **Final statuses:**
    - `generated`: the image was saved.
    - `failed`, with `error` set: `bad_request`, `refused`, a rate limit or temporary error after its retries, or `bad_image`.
    - `planned`: the unit was stopped by the daily limit, the budget, auth or the circuit breaker, or never started.
  - **Import style (Task 10 relies on it):** `from stickman.fsutil import safe_write`.
- **`tests/conftest.py`:** `FakeImages(outcomes=None, *, neurons=207.59)` and the fixture `fake_images` (the class).

- [ ] **Step 1: Write the failing tests**

Add to `tests/conftest.py` (with `import asyncio`, `import inspect` and `from stickman.cf.client import ImageResult, LLMResult`):

```python
class FakeImages:
    """Stands in for CloudflareClient.generate_image (and `async with`).

    `outcomes` is None (every call gets a small JPEG), a list (one outcome per call, in order), or a
    function `(call) -> outcome`. An outcome is image bytes, an exception to raise, or an awaitable
    that gives image bytes (a slow request).
    """

    def __init__(self, outcomes=None, *, neurons=207.59):
        self._outcomes = list(outcomes) if isinstance(outcomes, (list, tuple)) else outcomes
        self._neurons = neurons
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def generate_image(self, model, *, prompt, width, height, seed, steps=None, guidance=None, input_images=()):
        call = {"model": model, "prompt": prompt, "width": width, "height": height, "seed": seed,
                "steps": steps, "input_images": list(input_images)}
        self.calls.append(call)
        number = len(self.calls)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)  # like a network call: other requests can start meanwhile
            outcome = self._next(call)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        finally:
            self.active -= 1
        if isinstance(outcome, BaseException):
            raise outcome
        return ImageResult(image_bytes=outcome, neurons=self._neurons, request_id=f"req-{number}")

    def _next(self, call):
        if self._outcomes is None:
            return jpeg_bytes()
        if callable(self._outcomes):
            return self._outcomes(call)
        if not self._outcomes:
            raise AssertionError("FakeImages: more calls than scripted outcomes")
        return self._outcomes.pop(0)


@pytest.fixture
def fake_images():
    return FakeImages
```

`tests/render/test_renderer.py`:

```python
import asyncio
import itertools
import json

import pytest
from PIL import Image

from stickman.budget import Budget
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import MascotConfig, load_mascot
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.plan.models import parse_plan
from stickman.pricing import load_pricing
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.renderer import Renderer, StopReason
from stickman.render.state import StateStore
from stickman.runlog import RunLog
from stickman.settings import BudgetSettings, RetrySettings, Settings

FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
IMAGE_USD = 207.59 * 0.011 / 1000
TRANSIENT = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)


async def no_sleep(seconds):
    return None


async def slow(data, seconds=0.01):
    await asyncio.sleep(seconds)
    return data


class Run:
    """A renderer over plan_data's three units: 001, 002a and 002b."""

    def __init__(self, workspace, plan_data, client, *, mascot=None, budget=None, concurrency=4, retry=None):
        self.project = workspace / "projects" / FOLDER
        self.project.mkdir(parents=True, exist_ok=True)
        ctx = RenderContext(workspace, Settings(), mascot or load_mascot(workspace), (), load_pricing(workspace))
        plan = parse_plan(plan_data)
        self.jobs = JobBuilder(ctx, plan, seeds=itertools.count(1000).__next__).jobs(plan.units())
        self.store = StateStore.load(self.project)
        self.ledger = Ledger(workspace / "ledger.jsonl")
        self.meter = Meter(project=FOLDER, ledger=self.ledger, budget=budget, pricing=ctx.pricing)
        self.log_path = self.project / "logs" / "run.jsonl"
        self.client = client
        self.renderer = Renderer(client, self.store, self.meter, retry=retry or RetrySettings(), concurrency=concurrency,
                                 log=RunLog(self.log_path, secrets=("tok-secret",)), sleep=no_sleep)

    def go(self, jobs=None):
        return asyncio.run(self.renderer.run(self.jobs if jobs is None else jobs))

    def statuses(self):
        return {job.unit_id: self.store.unit(job.unit_id).status for job in self.jobs}

    def log(self):
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]


def test_every_unit_gets_one_version_its_history_file_and_its_current_image(tmp_path, plan_data, fake_images):
    run = Run(tmp_path, plan_data, fake_images())
    assert run.go().stop is None
    assert run.statuses() == {"001": "generated", "002a": "generated", "002b": "generated"}
    unit = run.store.unit("002a")
    [version] = unit.versions
    assert (unit.current_version, version.v, version.file) == (1, 1, "images/_history/002a_v1.png")
    assert (version.seed, version.model, version.width, version.height) == (1001, KLEIN_4B, 64, 36)
    assert (version.prompt_sent, version.refs, version.fingerprint) == ("prompt for 002a", [], run.jobs[1].fingerprint)
    assert version.est_cost_usd == pytest.approx(IMAGE_USD)
    history = (run.project / version.file).read_bytes()
    assert history.startswith(b"\x89PNG")
    assert (run.project / "images" / "002a_00-04.0.png").read_bytes() == history
    assert StateStore.load(run.project).state.units["002a"].versions == unit.versions
    entries = list(run.ledger.entries())
    assert sorted(e.unit for e in entries) == ["001", "002a", "002b"]
    assert {(e.kind, e.billing, e.neurons) for e in entries} == {("image", "billed", 207.59)}


def test_requests_carry_the_job_and_at_most_concurrency_run_at_once(tmp_path, plan_data, fake_images, jpeg):
    client = fake_images(lambda call: slow(jpeg))
    Run(tmp_path, plan_data, client, concurrency=2).go()
    assert client.max_active == 2
    first = next(call for call in client.calls if call["prompt"] == "prompt for 001")
    assert (first["model"], first["width"], first["height"], first["seed"], first["steps"], first["input_images"]) == (
        KLEIN_4B, 1920, 1088, 1000, None, [])


def test_generating_is_saved_before_the_request(tmp_path, plan_data, fake_images, jpeg):
    seen = []

    def outcome(call):
        state = json.loads((tmp_path / "projects" / FOLDER / "state.json").read_text(encoding="utf-8"))
        seen.append(state["units"]["001"]["status"])
        return jpeg

    run = Run(tmp_path, plan_data, fake_images(outcome))
    run.go(run.jobs[:1])
    assert seen == ["generating"]


def test_each_api_call_is_logged(tmp_path, plan_data, fake_images, jpeg):
    run = Run(tmp_path, plan_data, fake_images([TRANSIENT, jpeg]))
    run.go(run.jobs[:1])
    first, second = run.log()
    assert (first["ok"], first["error"], first["status"], first["billing"]) == (False, "transient", 502, "not_billed")
    assert (second["ok"], second["billing"], second["neurons"], second["request_id"]) == (True, "billed", 207.59, "req-2")
    assert all(e["kind"] == "image" and e["unit"] == "001" and e["seed"] == 1000 and e["prompt"] == "prompt for 001"
               for e in (first, second))
    assert run.statuses()["001"] == "generated"


def test_a_daily_limit_stops_new_requests_and_lets_running_ones_finish(tmp_path, plan_data, fake_images, jpeg):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    client = fake_images(lambda call: daily if call["prompt"] == "prompt for 001" else slow(jpeg))
    run = Run(tmp_path, plan_data, client, concurrency=2)
    assert run.go().stop is StopReason.DAILY_LIMIT
    assert run.statuses() == {"001": "planned", "002a": "generated", "002b": "planned"}
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "prompt for 002a"]


def test_a_rejected_token_stops_the_run(tmp_path, plan_data, fake_images):
    client = fake_images([CFError(ErrorCategory.AUTH, "Authentication error", status=401)])
    run = Run(tmp_path, plan_data, client, concurrency=1)
    assert run.go().stop is StopReason.AUTH
    assert set(run.statuses().values()) == {"planned"}
    assert len(client.calls) == 1


@pytest.mark.parametrize("category", [ErrorCategory.BAD_REQUEST, ErrorCategory.REFUSED])
def test_a_rejected_request_fails_only_its_unit(tmp_path, plan_data, fake_images, jpeg, category):
    rejected = CFError(category, "no: tok-secret", status=400)
    run = Run(tmp_path, plan_data, fake_images(lambda call: rejected if call["prompt"] == "prompt for 001" else jpeg))
    assert run.go().stop is None
    assert run.statuses() == {"001": "failed", "002a": "generated", "002b": "generated"}
    assert run.store.unit("001").error == f"{category}: no: ***"


def test_five_temporary_errors_in_a_row_pause_the_run(tmp_path, plan_data, fake_images):
    client = fake_images(lambda call: TRANSIENT)
    run = Run(tmp_path, plan_data, client, concurrency=1, retry=RetrySettings(transient_max=3, circuit_breaker=5))
    assert run.go().stop is StopReason.CIRCUIT_BREAKER
    assert run.statuses() == {"001": "failed", "002a": "planned", "002b": "planned"}
    assert len(client.calls) == 5  # 001: 1 try + 3 retries; 002a: the 5th error in a row
    assert run.store.unit("001").error == "transient: bad gateway"


def test_refusals_and_bad_requests_never_trip_the_breaker(tmp_path, plan_data, fake_images):
    errors = itertools.cycle([CFError(ErrorCategory.BAD_REQUEST, "invalid", status=400),
                              CFError(ErrorCategory.REFUSED, "flagged", status=400)])
    run = Run(tmp_path, plan_data, fake_images(lambda call: next(errors)), concurrency=1,
              retry=RetrySettings(circuit_breaker=1))
    assert run.go().stop is None
    assert set(run.statuses().values()) == {"failed"}


def test_a_success_resets_the_breaker(tmp_path, plan_data, fake_images, jpeg):
    client = fake_images([TRANSIENT, TRANSIENT, jpeg, TRANSIENT, TRANSIENT, jpeg, jpeg])
    run = Run(tmp_path, plan_data, client, concurrency=1, retry=RetrySettings(transient_max=3, circuit_breaker=3))
    assert run.go().stop is None
    assert set(run.statuses().values()) == {"generated"}


def test_the_weekly_budget_stops_new_requests(tmp_path, plan_data, fake_images):
    client = fake_images()
    run = Run(tmp_path, plan_data, client, concurrency=1, budget=Budget(BudgetSettings(weekly_usd=0.003), spent=0.0))
    result = run.go()
    assert result.stop is StopReason.BUDGET
    assert "over the weekly budget" in result.detail
    assert run.statuses() == {"001": "generated", "002a": "planned", "002b": "planned"}
    assert len(client.calls) == 1


def test_force_goes_past_the_budget_with_one_warning(tmp_path, plan_data, fake_images):
    warnings = []
    budget = Budget(BudgetSettings(weekly_usd=0.003), spent=0.0, force=True, warn=warnings.append)
    run = Run(tmp_path, plan_data, fake_images(), concurrency=1, budget=budget)
    assert run.go().stop is None
    assert set(run.statuses().values()) == {"generated"}
    assert len(warnings) == 1 and "--force" in warnings[0]


def test_timeouts_are_recorded_as_possibly_billed(tmp_path, plan_data, fake_images):
    timeout = CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True)
    run = Run(tmp_path, plan_data, fake_images(lambda call: timeout), concurrency=1,
              retry=RetrySettings(transient_max=1, circuit_breaker=10))
    run.go(run.jobs[:1])
    assert [e.billing for e in run.ledger.entries()] == ["possibly_billed", "possibly_billed"]
    assert run.meter.possibly_billed == 2
    assert run.meter.run_usd == pytest.approx(2 * run.jobs[0].estimate_usd)
    assert run.statuses()["001"] == "failed"


def test_bytes_that_are_not_an_image_fail_the_unit(tmp_path, plan_data, fake_images, jpeg):
    run = Run(tmp_path, plan_data, fake_images(lambda call: b"<html>oops</html>" if call["prompt"] == "prompt for 001" else jpeg))
    run.go()
    assert run.statuses() == {"001": "failed", "002a": "generated", "002b": "generated"}
    assert run.store.unit("001").error.startswith("bad_image: ")


def test_reference_images_are_sent_in_slot_order_and_recorded(tmp_path, plan_data, fake_images):
    for relative, size in (("library/style/anchor_v1_ref.png", (512, 384)), ("library/mascot/ref_v1.png", (384, 512))):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, "white").save(path, format="PNG")
    mascot = MascotConfig(name="Everyman", identity="a stickman with three hair strokes", sheet="library/mascot/sheet_v1.png",
                          ref="library/mascot/ref_v1.png", seed=7, style_version=1)
    client = fake_images()
    run = Run(tmp_path, plan_data, client, mascot=mascot)
    run.go(run.jobs[:1])
    anchor = (tmp_path / "library/style/anchor_v1_ref.png").read_bytes()
    sheet = (tmp_path / "library/mascot/ref_v1.png").read_bytes()
    assert client.calls[0]["input_images"] == [anchor, sheet]
    [version] = run.store.unit("001").versions
    assert [ref.split("#")[0] for ref in version.refs] == ["library/style/anchor_v1_ref.png", "library/mascot/ref_v1.png"]
    assert all(ref.split("#")[1].startswith("sha256:") for ref in version.refs)
    assert run.log()[0]["refs"] == version.refs
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/render/test_renderer.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'stickman.render.renderer'`.

- [ ] **Step 3: Implement** (`src/stickman/render/renderer.py`)

```python
"""Generating units' images, several at once (spec §9.5, §10.3)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from stickman.budget import BudgetExceeded
from stickman.cf.client import ImageResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.cf.retry import with_retries
from stickman.fsutil import safe_write
from stickman.meter import Meter, Metered, billing_of, local_now
from stickman.render.images import HISTORY_DIR, ImageDecodeError, decode_image, encode_png, history_name
from stickman.render.jobs import RenderJob
from stickman.render.state import StateStore, Version
from stickman.runlog import RunLog, shorten
from stickman.settings import RetrySettings

ERROR_CHARS = 200  # how much of an API error message a failed unit keeps in state.json


class ImageClient(Protocol):
    async def generate_image(
        self,
        model: str,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        steps: int | None = ...,
        guidance: float | None = ...,
        input_images: Sequence[bytes] = ...,
    ) -> ImageResult: ...


class StopReason(StrEnum):
    DAILY_LIMIT = "daily_limit"
    BUDGET = "budget"
    CIRCUIT_BREAKER = "circuit_breaker"
    AUTH = "auth"


class RunStopped(Exception):
    """The run is stopping, so this unit's request isn't started."""


class RunControl:
    """Whether the run is stopping, and why. The first reason is the one reported."""

    def __init__(self) -> None:
        self.reason: StopReason | None = None
        self.detail = ""

    def stop(self, reason: StopReason, detail: str = "") -> None:
        if self.reason is None:
            self.reason, self.detail = reason, detail

    def check(self) -> None:
        if self.reason is not None:
            raise RunStopped(str(self.reason))


class CircuitBreaker:
    """Counts temporary errors in a row; a success resets the count (spec §9.5)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def failure(self) -> bool:
        """Count one. True when the limit is reached."""
        self.count += 1
        return self.count >= self.limit

    def success(self) -> None:
        self.count = 0


@dataclass(frozen=True)
class RunResult:
    stop: StopReason | None
    detail: str
    elapsed_s: float


class Renderer:
    def __init__(
        self,
        client: ImageClient,
        store: StateStore,
        meter: Meter,
        *,
        retry: RetrySettings,
        concurrency: int,
        log: RunLog,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = local_now,
        on_done: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._meter = meter
        self._retry = retry
        self._concurrency = concurrency
        self._log = log
        self._sleep = sleep
        self._now = now
        self._on_done = on_done
        self.control = RunControl()
        self._breaker = CircuitBreaker(retry.circuit_breaker)

    async def run(self, jobs: Sequence[RenderJob]) -> RunResult:
        """Render each job's unit, at most `concurrency` at once. Once the run is stopping, no new
        request starts and the requests already running finish (spec §9.5)."""
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(job: RenderJob) -> None:
            async with semaphore:
                if self.control.reason is not None:
                    return
                await self._render(job)
                if self._on_done is not None:
                    self._on_done(job.unit_id)

        await asyncio.gather(*(one(job) for job in jobs))
        return RunResult(self.control.reason, self.control.detail, time.perf_counter() - started)

    async def _render(self, job: RenderJob) -> None:
        """spec §10.3: `generating` is saved before the request, the final status after the image."""
        self._store.set_status(job.unit_id, "generating")
        try:
            metered = await with_retries(lambda: self._attempt(job), self._retry, sleep=self._sleep)
        except RunStopped:
            self._store.set_status(job.unit_id, "planned")
            return
        except BudgetExceeded as exc:
            self.control.stop(StopReason.BUDGET, str(exc))
            self._store.set_status(job.unit_id, "planned")
            return
        except CFError as exc:
            self._failed(job, exc)
            return
        try:
            self._save(job, metered)
        except ImageDecodeError as exc:
            self._store.set_status(job.unit_id, "failed", error=f"bad_image: {exc}")

    async def _attempt(self, job: RenderJob) -> Metered[ImageResult]:
        """One API call: budget-checked, ledgered and logged. The breaker counts temporary errors."""
        self.control.check()
        started = time.perf_counter()
        try:
            metered = await self._meter.run(
                lambda: self._client.generate_image(
                    job.model,
                    prompt=job.prompt,
                    width=job.width,
                    height=job.height,
                    seed=job.seed,
                    steps=job.steps,
                    input_images=[ref.data for ref in job.references],
                ),
                kind="image",
                model=job.model,
                estimate_usd=job.estimate_usd,
                unit=job.unit_id,
            )
        except CFError as exc:
            self._log_call(
                job,
                latency_s=time.perf_counter() - started,
                ok=False,
                error=str(exc.category),
                status=exc.status,
                message=shorten(exc.message),
                billing=billing_of(exc),
                usd=job.estimate_usd,
            )
            if exc.category is ErrorCategory.TRANSIENT and self._breaker.failure():
                self.control.stop(StopReason.CIRCUIT_BREAKER, f"{self._breaker.count} temporary errors in a row")
            raise
        self._breaker.success()
        self._log_call(
            job,
            latency_s=metered.latency_s,
            ok=True,
            billing="billed",
            usd=metered.usd,
            neurons=metered.result.neurons,
            request_id=metered.result.request_id,
        )
        return metered

    def _log_call(self, job: RenderJob, *, latency_s: float, **fields: Any) -> None:
        self._log.write(
            kind="image",
            unit=job.unit_id,
            model=job.model,
            seed=job.seed,
            width=job.width,
            height=job.height,
            steps=job.steps,
            refs=[ref.label for ref in job.references],
            prompt=shorten(job.prompt),
            latency_s=round(latency_s, 2),
            **fields,
        )

    def _failed(self, job: RenderJob, exc: CFError) -> None:
        if exc.category is ErrorCategory.DAILY_LIMIT:
            self.control.stop(StopReason.DAILY_LIMIT, exc.message)
            self._store.set_status(job.unit_id, "planned")
        elif exc.category is ErrorCategory.AUTH:
            self.control.stop(StopReason.AUTH, exc.message)
            self._store.set_status(job.unit_id, "planned")
        else:
            # bad_request and refused (M4 turns a refusal into a softened retry, spec §7.5), or a
            # rate limit or temporary error after its retries.
            message = self._log.mask(shorten(exc.message, ERROR_CHARS))
            self._store.set_status(job.unit_id, "failed", error=f"{exc.category}: {message}")

    def _save(self, job: RenderJob, metered: Metered[ImageResult]) -> None:
        """The history file first, then state.json, then the current copy. A kill between any two
        is put right by recover() when the next run starts (spec §5.2 [M3])."""
        image = decode_image(metered.result.image_bytes)
        v = self._store.next_version(job.unit_id)
        version = Version(
            v=v,
            file=f"{HISTORY_DIR}/{history_name(job.unit_id, v)}",
            seed=job.seed,
            model=job.model,
            width=image.width,
            height=image.height,
            fingerprint=job.fingerprint,
            refs=[ref.label for ref in job.references],
            prompt_sent=job.prompt,
            est_cost_usd=metered.usd,
            latency_s=round(metered.latency_s, 2),
            created=self._now(),
        )
        data = encode_png(image, version.model_dump(mode="json"))
        project = self._store.project_dir
        (project / HISTORY_DIR).mkdir(parents=True, exist_ok=True)
        safe_write(project / version.file, data)
        self._store.add_version(job.unit_id, version, status="generated")  # M4: QC decides
        safe_write(project / "images" / f"{job.stem}.png", data)
```

In `spec.md`:
- §9.5: add after the **Retry counts [M2]** paragraph:

```markdown
**[M3] What the breaker counts:** each API attempt that ends in a `transient` error, including attempts that are then retried. So with `transient_max: 3`, two units' failures can trip it. A `refused` request ends `failed` until M4 adds the softened retry (§7.5).
```

- §10.3: add after its list:

```markdown
- **[M3]** There's no QC step until M4: a saved image makes the unit `generated`. The history file is written first, then `state.json`, then the current copy. A kill between any two is put right when the next run starts (§5.2).
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/render/test_renderer.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/render/renderer.py tests/conftest.py tests/render/test_renderer.py spec.md
git commit -m "feat: parallel renderer with error categories, budget and daily-limit stops, and the circuit breaker" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Killed at random points, resume finishes (spec §17 "Resume", acceptance §18 #11)

**Files:**
- Test: `tests/render/test_resume.py` (new)
- Modify: only if this test finds a bug. Fix it in the module where it lives (`render/state.py`, `render/recovery.py` or `render/renderer.py`), with a regression test in that module's test file.

**Interfaces:**
- Consumes: everything in `stickman.render`, plus `FakeImages` and `jpeg`.
- The test relies on `safe_write` being imported with `from stickman.fsutil import safe_write` in `render/state.py`, `render/recovery.py` and `render/renderer.py`, so it can replace each module's `safe_write`.

**The idea:**
- A synthetic plan of 90 units lasts 4.5 minutes, which is the design limit.
- A `Killer` raises `Kill`, a `BaseException` standing in for the process being killed, at one numbered step. The steps are every file write (before it, and after its temp file) and every request.
- After the kill, every further step raises too, so nothing more reaches the disk, just as with a real kill.
- Each seed kills a run up to 4 times, then lets a run finish.
- At the end, every unit must be `generated` with exactly one version and one history file, its current image must equal that file, and no temp files may remain.

- [ ] **Step 1: Write the test** (`tests/render/test_resume.py`)

```python
import asyncio
import os
import random

import pytest

from stickman.config_files import load_mascot
from stickman.meter import Meter
from stickman.plan.models import parse_plan
from stickman.pricing import load_pricing
from stickman.render import recovery, renderer, state
from stickman.render.images import history_files, image_stem
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.recovery import recover
from stickman.render.renderer import Renderer
from stickman.render.state import StateStore
from stickman.runlog import RunLog
from stickman.settings import RetrySettings, Settings

UNITS = 90
KILLS = 4


class Kill(BaseException):
    """The process being killed: nothing after it reaches the disk."""


class Killer:
    def __init__(self):
        self.count = 0
        self.at = None
        self.dead = False

    def arm(self, steps_from_now):
        self.dead = False
        self.at = None if steps_from_now is None else self.count + steps_from_now

    def tick(self):
        if self.dead:
            raise Kill()
        self.count += 1
        if self.at is not None and self.count >= self.at:
            self.dead = True
            raise Kill()


def killable_write(killer):
    def write(path, data):
        killer.tick()  # killed before this write
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)  # no fsync: this test is about the order of writes, not durability
        killer.tick()  # killed after the temp file, before the replace
        os.replace(tmp, path)

    return write


def synthetic_plan(units=UNITS):
    """About a 5-minute script: 3-second units, every fourth scene split in two."""
    scenes, start, made, number = [], 0.0, 0, 0
    while made < units:
        number += 1
        scene_id = f"{number:03d}"
        text = f"Line {number} of the synthetic script."
        split = number % 4 == 0 and units - made >= 2
        parts = [(f"{scene_id}a", "1 of 2"), (f"{scene_id}b", "2 of 2")] if split else [(scene_id, None)]
        unit_list = []
        for index, (unit_id, part) in enumerate(parts):
            unit_start = start + 3.0 * index
            unit_list.append({
                "id": unit_id, "part": part, "start": unit_start, "end": unit_start + 3.0,
                "source_text": text, "corrected_text": text, "visual_idea": f"Idea {unit_id}",
                "visual_type": "literal", "shot": "wide", "time_of_day": "day",
                "characters": [{"ref": "mascot", "action": "waving", "emotion": "happy"}],
                "setting": [], "props": [], "composition": "centred", "energy_marks": [],
                "image_prompt": f"prompt for {unit_id}",
            })
        end = start + 3.0 * len(parts)
        scenes.append({
            "id": scene_id, "lines": [number], "start": start, "end": end, "source_text": text,
            "corrected_text": text, "units": unit_list,
            "split": {"status": "split", "cut_after_word": 3, "candidates": [3]} if split else {"status": "none"},
        })
        made += len(parts)
        start = end
    return parse_plan({
        "schema_version": 1, "project": "synthetic", "aspect": "16:9", "style_version": 1,
        "image_model": "@cf/black-forest-labs/flux-2-klein-4b", "duration_end": start, "pace_wps": 2.5,
        "cast": [{"id": "mascot"}], "corrections": [], "merge_check": [], "scenes": scenes,
    })


async def no_sleep(seconds):
    return None


def run_once(workspace, project, plan, client):
    """What `stickman resume` does: recover, then render every planned or failed unit."""
    ctx = RenderContext(workspace, Settings(), load_mascot(workspace), (), load_pricing(workspace))
    builder = JobBuilder(ctx, plan)
    store = StateStore.load(project)
    recover(store, builder.expected())
    todo = [unit for unit in plan.units() if store.unit(unit.id).status in ("planned", "failed")]
    render = Renderer(client, store, Meter(project=project.name), retry=RetrySettings(), concurrency=4,
                      log=RunLog(None), sleep=no_sleep)
    asyncio.run(render.run(builder.jobs(todo)))


def test_the_synthetic_plan_is_about_five_minutes():
    plan = synthetic_plan()
    assert len(plan.units()) == UNITS
    assert 240 <= plan.duration_end <= 300


@pytest.mark.parametrize("seed", range(6))
def test_resume_after_kills_finishes_with_no_lost_or_duplicate_images(tmp_path, monkeypatch, fake_images, jpeg, seed):
    rng = random.Random(seed)
    killer = Killer()
    for module in (state, recovery, renderer):
        monkeypatch.setattr(module, "safe_write", killable_write(killer))
    project = tmp_path / "projects" / "2026-09-25_synthetic"
    project.mkdir(parents=True)
    plan = synthetic_plan()
    client = fake_images(lambda call: killer.tick() or jpeg)  # killed while the request is out
    kills = 0
    while True:
        killer.arm(rng.randint(1, 400) if kills < KILLS else None)
        try:
            run_once(tmp_path, project, plan, client)
            break
        except Kill:
            kills += 1
    assert kills >= 1

    saved = StateStore.load(project).state
    history = history_files(project)
    for unit in plan.units():
        entry = saved.units[unit.id]
        assert entry.status == "generated", (unit.id, entry.status)
        assert [version.v for version in entry.versions] == [1], unit.id
        assert entry.current_version == 1
        assert sorted(history[unit.id]) == [1], unit.id
        current = project / "images" / f"{image_stem(unit.id, unit.start)}.png"
        assert current.read_bytes() == history[unit.id][1].read_bytes(), unit.id
    assert set(history) == {unit.id for unit in plan.units()}
    assert len(list((project / "images").glob("*.png"))) == UNITS
    assert not list(project.rglob("*.tmp"))
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/render/test_resume.py -q`
Expected: PASS if Tasks 5–9 are correct. Asyncio may log "exception during shutdown" messages for tasks that the kill cut short; that is expected. If a seed fails:
- Read its assertion: which unit, and in what state.
- Find the ordering bug in `state.py`, `recovery.py` or `renderer.py`, and fix it there.
- Add a focused regression test for that bug in the module's own test file.
- Record the bug, the fix and the regression test in your report. Don't weaken this test.

- [ ] **Step 3: Check that the test can fail**

- Temporarily comment out the `_adopt_saved_images(store)` call in `recover()`, so it assigns `adopted, unknown = [], []` instead.
- Run the test and confirm that at least one seed fails, because a unit ends with 2 versions or with a history file that `state.json` doesn't list.
- Restore the line, run again and see it pass.
- Record both runs in your report.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/render/test_resume.py
git commit -m "test: a run killed at random points resumes with no lost or duplicate images" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

If the test found a bug, commit its fix and regression test first, as `fix: …`, then this commit.

---

### Task 11: `stickman generate` and `stickman resume`, and the run summary (spec §10.1, §10.5, §13)

**Files:**
- Create: `src/stickman/render/summary.py`
- Modify: `src/stickman/cli.py`, `spec.md` (§10.1 and §13 notes)
- Test: `tests/render/test_summary.py`, `tests/test_cli_generate.py` (new)

**Interfaces:**
- Consumes: everything in `stickman.render`, plus `Budget`, `Ledger` / `LEDGER_FILE` / `utc_day_start`, `Meter`, `format_usd` / `usd_neurons`, `resolve_project`, `load_plan` and `RunLog`.
- Produces:
  - **`stickman.render.summary`:** `format_duration(seconds) -> str` and `summary_lines(state, unit_ids, *, result, run_usd, possibly_billed, week_usd, weekly_usd) -> list[str]`.
  - **CLI:**
    - `stickman generate [-p PROJECT] [--force] [--limit N] [-w WORKSPACE]` and `stickman resume` (the same options) share `_generate`.
    - `cli._wait(seconds)` is the retry wait, and tests replace it.
    - `OUTAGE_MESSAGE` holds the spec's exact outage text.
    - Exit codes: 0 finished (even with failed units); 1 no project, invalid plan, unreadable `state.json`, a held lock, or units without a prompt; 2 daily limit, budget or circuit breaker; 3 config error or rejected token; 130 Ctrl+C.

- [ ] **Step 1: Write the failing tests**

`tests/render/test_summary.py`:

```python
from stickman.render.renderer import RunResult, StopReason
from stickman.render.state import ProjectState, UnitState
from stickman.render.summary import format_duration, summary_lines


def state(**statuses):
    return ProjectState(units={unit: UnitState(status=status) for unit, status in statuses.items()})


def test_durations_read_like_the_spec():
    assert [format_duration(s) for s in (45.2, 372, 3720)] == ["45s", "6m12s", "1h02m"]


def test_a_finished_run():
    project = state(**{"001": "generated", "002a": "approved", "002b": "failed", "003": "stale", "004": "planned"})
    project.units["002b"].error = "bad_request: invalid prompt"
    lines = summary_lines(project, ["001", "002a", "002b", "003", "004"], result=RunResult(None, "", 372.0),
                          run_usd=0.74, possibly_billed=2, week_usd=6.12, weekly_usd=15.0)
    assert lines == [
        "Run finished · 5 units · done 2 · needs_review 0 · failed 1 · stale 1 · skipped 1",
        "Cost this run ≈ $0.74 (≈ 67,273 neurons, 2 possibly billed) · week ≈ $6.12 / $15.00 · time 6m12s",
        "Failed: 002b (bad_request: invalid prompt)",
        "Stale (not regenerated automatically): 003",
    ]


def test_a_paused_run_names_its_reason_and_small_costs_stay_readable():
    lines = summary_lines(state(**{"001": "generated", "002a": "planned"}), ["001", "002a"],
                          result=RunResult(StopReason.DAILY_LIMIT, "daily free allocation", 45.0),
                          run_usd=0.00228, possibly_billed=0, week_usd=0.00228, weekly_usd=15.0)
    assert lines == [
        "Run paused (daily limit) · 2 units · done 1 · needs_review 0 · failed 0 · stale 0 · skipped 1",
        "Cost this run ≈ $0.0023 (≈ 207 neurons) · week ≈ $0.0023 / $15.00 · time 45s",
    ]
```

`tests/test_cli_generate.py`:

```python
import json

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, update_unit, write_plan
from stickman.render.state import StateStore

runner = CliRunner()
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


@pytest.fixture
def workspace(tmp_path, monkeypatch, plan_data):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(cli, "_wait", no_wait)
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    folder = tmp_path / "projects" / FOLDER
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    return tmp_path


def use_images(monkeypatch, client):
    monkeypatch.setattr(cli, "build_client", lambda cfg: client)
    return client


def generate(workspace, *args, command="generate"):
    return runner.invoke(cli.app, [command, "-w", str(workspace), *args])


def project(workspace):
    return workspace / "projects" / FOLDER


def statuses(workspace):
    data = json.loads((project(workspace) / "state.json").read_text(encoding="utf-8"))
    return {unit: entry["status"] for unit, entry in data["units"].items()}


def only_001(outcome, jpeg):
    return lambda call: outcome if call["prompt"] == "prompt for 001" else jpeg


def test_generate_makes_every_units_image_and_prints_the_summary(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"Project: {FOLDER}"
    assert f"Generating 3 unit(s) on {KLEIN_4B}" in result.output
    assert "so about 48 more image(s) fit" in result.output
    assert "Run finished · 3 units · done 3 · needs_review 0 · failed 0 · stale 0 · skipped 0" in result.output
    assert statuses(workspace) == {"001": "generated", "002a": "generated", "002b": "generated"}
    assert sorted(p.name for p in (project(workspace) / "images").glob("*.png")) == [
        "001_00-00.0.png", "002a_00-04.0.png", "002b_00-07.0.png"]
    entries = [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["kind"], e["billing"], e["project"]) for e in entries] == [("image", "billed", FOLDER)] * 3
    assert list((project(workspace) / "logs").glob("run-*.jsonl"))
    assert not (project(workspace) / ".lock").exists()


def test_a_daily_limit_pauses_and_resume_continues(workspace, monkeypatch, fake_images, jpeg):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    use_images(monkeypatch, fake_images(lambda call: jpeg if call["prompt"] == "prompt for 001" else daily))
    result = generate(workspace)
    assert result.exit_code == 2, result.output
    assert "Run paused (daily limit) · 3 units · done 1" in result.output
    assert "run `stickman resume` after the daily reset (00:00 UTC" in result.output
    resumed = use_images(monkeypatch, fake_images())
    result = generate(workspace, command="resume")
    assert result.exit_code == 0, result.output
    assert "done 3" in result.output
    assert sorted(call["prompt"] for call in resumed.calls) == ["prompt for 002a", "prompt for 002b"]


def test_limit_caps_one_run(workspace, monkeypatch, fake_images):
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace, "--limit", "1")
    assert result.exit_code == 0, result.output
    assert "done 1 · needs_review 0 · failed 0 · stale 0 · skipped 2" in result.output
    assert [call["prompt"] for call in client.calls] == ["prompt for 001"]


def test_the_weekly_budget_stops_the_run_and_force_goes_on(workspace, monkeypatch, fake_images):
    (workspace / "config").mkdir()
    (workspace / "config" / "settings.yaml").write_text("budget:\n  weekly_usd: 0.001\n", encoding="utf-8")
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 2, result.output
    assert "Weekly budget reached" in result.output
    assert "stickman resume --force" in result.output
    assert client.calls == []
    result = generate(workspace, "--force", command="resume")
    assert result.exit_code == 0, result.output
    assert "continuing because of --force" in result.output
    assert "done 3" in result.output


def test_five_temporary_errors_in_a_row_pause_the_run(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images(lambda call: CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)))
    result = generate(workspace)
    assert result.exit_code == 2, result.output
    assert cli.OUTAGE_MESSAGE in result.output


def test_a_rejected_token_exits_3(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images(lambda call: CFError(ErrorCategory.AUTH, "Authentication error", status=401)))
    result = generate(workspace)
    assert result.exit_code == 3, result.output
    assert "Cloudflare rejected the token" in result.output


def test_a_second_process_is_refused(workspace, monkeypatch, fake_images):
    monkeypatch.setattr("stickman.render.lock.pid_alive", lambda pid: True)
    (project(workspace) / ".lock").write_text("4242", encoding="ascii")
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 1, result.output
    assert "another stickman process (PID 4242)" in result.output
    assert client.calls == []
    assert (project(workspace) / ".lock").read_text(encoding="ascii") == "4242"


def test_a_unit_left_generating_by_a_killed_run_is_generated_again(workspace, monkeypatch, fake_images):
    StateStore.load(project(workspace)).set_status("001", "generating")
    use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Reset 1 unit(s) a stopped run left generating: 001" in result.output
    assert statuses(workspace)["001"] == "generated"


def test_an_edited_unit_becomes_stale_and_is_not_regenerated(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    assert generate(workspace).exit_code == 0
    path = project(workspace) / "plan.yaml"
    loaded = load_plan(path)
    update_unit(loaded.doc, "001", {"props": ["a small campfire"]})
    write_plan(path, loaded.doc, expected_hash=loaded.hash)
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Stale, the plan changed after the image was made (not regenerated automatically): 001" in result.output
    assert "Nothing to generate" in result.output
    assert client.calls == []
    assert statuses(workspace)["001"] == "stale"


def test_errors_that_echo_the_token_are_masked(workspace, monkeypatch, fake_images, jpeg):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "invalid token tok-secret", status=400)
    use_images(monkeypatch, fake_images(only_001(rejected, jpeg)))
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Failed: 001 (bad_request: invalid token ***)" in result.output
    assert "tok-secret" not in result.output
    assert "tok-secret" not in (project(workspace) / "state.json").read_text(encoding="utf-8")


def test_generate_without_a_planned_project_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=300))
    result = generate(tmp_path)
    assert result.exit_code == 1
    assert result.output.splitlines()[0] == "Project: (none found)"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/render/test_summary.py tests/test_cli_generate.py -q`
Expected: FAIL. `stickman.render.summary` doesn't exist yet, and `generate` / `resume` are unknown commands (exit code 2 from typer, "No such command").

- [ ] **Step 3: Implement**

`src/stickman/render/summary.py`:

```python
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
```

In `src/stickman/cli.py`, add these imports:
- `import math` and `from datetime import date, datetime, timedelta`, which replaces `from datetime import date`.
- `from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn`.
- From metering: `from stickman.budget import Budget`, `from stickman.ledger import LEDGER_FILE, Ledger, utc_day_start` (extend the Task 4 import), and `from stickman.pricing import format_usd, load_pricing, usd_neurons` (extend the Task 4 import).
- From rendering:
  - `from stickman.render.jobs import JobBuilder, JobError, RenderContext, RenderJob`
  - `from stickman.render.lock import LockHeld, ProjectLock`
  - `from stickman.render.recovery import recover`
  - `from stickman.render.renderer import Renderer, RunResult, StopReason`
  - `from stickman.render.state import StateError, StateStore`
  - `from stickman.render.summary import summary_lines`
- `from stickman.plan.models import CastMember, PlanValidationError`, and also `Plan`.

Then add the following code after `replan`:

```python
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
```

In `spec.md`:
- §10.1: add after the `--no-review` list:

```markdown
**[M3] Until M6/M7:** there's no review page (M6) and no test-first flow (M7) yet, so `generate` has no approval steps.
- It generates every `planned` or `failed` unit in time order.
- `--limit N` generates at most N of them in one run.
- On the free plan it first prints how many images fit in today's allocation.
- A run that reaches the daily limit pauses with exit code 2. `resume` continues after 00:00 UTC.
```

- §13: add after the **[M2] Continuing on a later date** paragraph:

```markdown
**[M3] `generate` and `resume`:**
- They take `-p`, or else use the most recently modified planned project. The strict `-p` rule comes in M7.
- Their other options are `--force` (go past the weekly budget) and `--limit N`.
- Both hold the project's `.lock` while they run.
- Ctrl+C exits with code 130; finished images are kept.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/render/test_summary.py tests/test_cli_generate.py -q`
Expected: PASS. Then run the full suite: `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/stickman/render/summary.py src/stickman/cli.py tests/render/test_summary.py tests/test_cli_generate.py spec.md
git commit -m "feat: stickman generate and resume with progress, pauses, recovery notes and the run summary" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: The live check (spec §17 live smoke test, plan.md M3 exit criteria)

**Files:**
- Create: `tests/test_live.py`, `docs/m3-generation-check.md` (after the live runs)

Steps 1–2 are for the implementer. They add an opt-in test that the default run skips, and make no API calls. Steps 3–6 call the real API, so the **controller runs them after the user's go-ahead**. They cost about 8 Klein 4B images, roughly 1,700 neurons, inside the free daily allocation.

- [ ] **Step 1: Write the live smoke test** (`tests/test_live.py`)

```python
"""Opt-in checks against the real Cloudflare API: uv run pytest -m live. They cost a little."""

import asyncio
from pathlib import Path

import pytest

from stickman.cf.client import CloudflareClient
from stickman.render.images import decode_image
from stickman.settings import load_config

pytestmark = pytest.mark.live
ROOT = Path(__file__).parent.parent
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def test_one_klein_4b_image():
    """About 208 neurons: one 1920x1088 image with no reference images."""
    cfg = load_config(ROOT)

    async def go():
        async with CloudflareClient(
            cfg.secrets.cf_account_id,
            cfg.secrets.cf_api_token.get_secret_value(),
            plan=cfg.settings.account.plan,
            timeout_s=cfg.settings.render.timeout_s,
        ) as client:
            return await client.generate_image(
                KLEIN_4B,
                prompt="Clean black ink line art of one stickman waving, on a plain white background. No text.",
                width=1920,
                height=1088,
                seed=12345,
            )

    result = asyncio.run(go())
    assert decode_image(result.image_bytes).size == (1920, 1088)
    assert result.neurons is not None and 150 < result.neurons < 260
    assert result.request_id
```

- [ ] **Step 2: Check the default run skips it, then commit**

Run: `uv run pytest -q`. The default run must deselect the live test and pass. Run `uv run pytest -m live --collect-only -q` to confirm it collects 1 test.

```bash
git add tests/test_live.py
git commit -m "test: opt-in live smoke test for one Klein 4B image" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3 (controller, user go-ahead): the smoke test**

Run: `uv run pytest -m live -q`. Expected: 1 passed. Record the neurons from the new `ledger.jsonl`, or from the test run.

- [ ] **Step 4 (controller): a real project from the cached sample plan**

To plan with no API calls, create today's folder and copy M2's cache into it. `new` then finds every stage in the cache:

```bash
D="projects/$(date +%F)_first-sleep"; mkdir -p "$D/.cache" && cp -r m2_out/.cache/llm "$D/.cache/"
uv run stickman new tests/fixtures/scripts/first-sleep.txt --aspect 16:9
uv run stickman generate --limit 3
```

Expected:
- `new` makes no API calls, so no new `llm` entries appear in `ledger.jsonl`.
- `generate` makes 3 images and exits 0.
- Check that `images/`, `images/_history/`, `state.json`, the run log and `ledger.jsonl` (3 `image` entries with neurons) all look as specified.
- Open one image and check that it is black line art on white.

- [ ] **Step 5 (controller): kill mid-batch, then resume** (plan.md exit criterion)

Start `uv run stickman generate --limit 4` in the background. When the first new history file appears, kill the process tree (`taskkill //F //T //PID <pid>` from Git Bash). Then:

```bash
uv run stickman resume --limit 4
```

Expected:
- `resume` prints the recovery notes, such as reset units or a kept image.
- It finishes the 4 units, and each unit has exactly one version.
- `images/_history` has no file that `state.json` doesn't list, and there are no `.tmp` files.
- Check this with a short Python snippet that loads `state.json` and lists `images/_history`.

- [ ] **Step 6 (controller): the report**

Write `docs/m3-generation-check.md`:
- the smoke test result;
- the neurons per image as measured, against the estimate;
- the kill-and-resume result, including the notes `resume` printed and the state and history check;
- anything that surprised us.

Commit it with the message `docs: M3 live generation check`. Say nothing about the images' content quality: that is M4 and M5 territory.

---

## After this plan

- **M4 (QC):**
  - Insert pixel and vision QC between saving the image and setting the final status. Vision calls go through `Meter.run(kind="vision")`.
  - Replace `Version.qc: dict | None` with a model.
  - `refused` becomes `safety_filtered` with the softened retry. Add `needs_review`.
  - A stale unit that matches again returns to `needs_review` when its current version failed QC.
  - The summary's "Needs review:" line.
- **M5 (bootstrap):** bootstrap writes the anchor and mascot reference copies, but M3 sends every reference that exists. Prompts built before those files existed have no reference paragraph (§7.4). So before generating with references, M5 must rebuild the `image_prompt` of unlocked units, and prompt-lock detection (M7) must not mistake that rebuild for a hand edit. Also re-check `pricing.yaml` against the dashboard.
- **M6/M7:**
  - approval gates, test units and `--no-review`;
  - the strict `-p` rule, `regen`, `rebuild-prompt` and prompt-lock detection;
  - library reuse.
- **M8:** export centre-crops 1920×1088 to 1920×1080, and the small-size generation option adds an upscale.
- **Not scheduled:** `stickman status` (spec §13). The user left it out of M3.
- **Still deferred from M2 (the final review can defer them all):**
  - `safe_write`'s fixed temp name (the `.lock` now covers one process per project);
  - the `inline_schema` cycle guard;
  - `MascotConfig.model` being optional;
  - the untested `cut_after_word` rule;
  - store flow-style tests;
  - the `_load_document` wording;
  - the >3 cut candidates test;
  - `_article('')`;
  - `replan_unit`'s bare ValueError;
  - `check_plan` checking hand-edited corrections only as a substring;
  - the `Part` literal defined twice;
  - `library.py` importing from `prompt.builder`;
  - `replan_unit`'s hard-coded 2/3;
  - the M7 prompt tuning ("text" in describe).
