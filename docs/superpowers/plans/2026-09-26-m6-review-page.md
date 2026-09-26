# Stickman M6 (Review Page) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `stickman review` opens a local page (127.0.0.1 only) where you can:
- read the plan, with live reload and validation errors, and approve it;
- approve bootstrap candidates;
- approve the test units;
- work through a gallery with flagged units first: approve, regenerate, edit the prompt, pick from history, compare side by side, replan with a hint, and approve everything remaining;
- do all of the above with keyboard shortcuts (spec §12).

**Architecture:**
- **New `stickman.review` package:**

  | Module | Contents |
  |---|---|
  | `view.py` | builds the page's JSON from the plan, `state.json`, the bootstrap store and the ledger (pure, and tested without a server) |
  | `actions.py` | the page's state and plan changes, each raising `ActionError(status, message)` |
  | `access.py` | shares one `StateStore` between page actions and a running job, and takes the project lock, so a CLI run's `state.json` is never overwritten |
  | `jobs.py` | regenerate, replan and more-candidates, run in the server with the normal `Renderer`/`StageRunner`/`CandidateMaker` |
  | `events.py` | server-sent events |
  | `watch.py` | turns changes to `plan.yaml`/`state.json` into events, ignoring the server's own writes |
  | `app.py` | the FastAPI routes, a Host check and a same-origin header check |
  | `static/` | `index.html`, `app.css`, `app.js`: plain HTML/JS, no build step |

- **Render support:**
  - `Renderer.run(…, fresh=…)` starts a new chain for a regenerated unit.
  - `UnitState.compare_with` records the side-by-side pair.
  - `StateStore` gains `approve`, `select_version` and `begin_regeneration`.
  - `recovery.stale_status` is the pure stale rule, so the page can show `stale` without writing `state.json`.
- **`runtime.py`:** `build_client`, `secrets_of` and `check_usd` move out of `cli.py`, which keeps its names, so the server can use them.
- **`cli.py`:** a `stickman review` command.

Approval gates on `generate`, test-unit picking, extras' sheets and library reuse stay in M7.

**Tech Stack:** Python 3.12 (uv), typer, rich, pydantic v2, ruamel.yaml, httpx, Pillow, numpy, and **fastapi, uvicorn, watchfiles** (added in Task 1, as spec §2.1 lists them), pytest. The front end is plain HTML, CSS and JavaScript.

**Source documents:** `spec.md` (§2.1, §3, §5.2, §9.7, §10.4, §12, §13, §17 "Review API", §18 #18–19), `plan.md` (M6 row). Section numbers like "§12.4" refer to `spec.md`.

## Global Constraints

- **Python and packaging:** Python **3.12**, package `stickman` in `src/stickman/`, managed with **uv**. Run everything with `uv run …`.
- **Runtime dependencies:** `typer`, `rich`, `pydantic>=2`, `pydantic-settings`, `ruamel.yaml`, `httpx`, `pillow`, `numpy`, and from Task 1 `fastapi`, `uvicorn` and `watchfiles`. Nothing else. Dev dependency: `pytest`. The page loads **no external resources**: no CDN, no web fonts, no analytics.
- **Secrets:**
  - They live **only** in `.env` as `CF_ACCOUNT_ID` and `CF_API_TOKEN`. Never write them to config, logs, `state.json`, the ledger, API responses, events or test fixtures.
  - Errors shown on the page are masked (the token and the account id become `***`) before any cut.
- **`style_refs/`:** the stock images are **never** sent to any API, and the server never serves them. `/files/` refuses any path inside `style_refs/`.
- **The review server:**
  - It binds to **127.0.0.1 only** (`review.host`, which is already validated).
  - It refuses requests whose `Host` isn't `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding).
  - Every `POST`/`PUT` needs the header `X-Stickman: 1`, which a cross-site page can't send without CORS, and CORS is never enabled.
- **Models:** all Pydantic models use `extra="forbid"` (`VisionReport` keeps its M5 exception). `state.json` keeps `schema_version: 1`; the new field has a default, so older files still load.
- **API calls:**
  - They go only through `CloudflareClient`. Every call goes through `Meter.run` (ledgered, and budget-checked for image and vision calls).
  - Page jobs use the same settings, budget, lock and retry rules as the CLI.
  - Tests **never** touch the network. They use `FakeChat`/`FakeImages` and FastAPI's `TestClient`.
- **Writes:**
  - `state.json` is written only through `StateStore`.
  - `plan.yaml` is written only with `write_plan(expected_hash=…)`, so a file changed on disk gives **409** and nothing is overwritten.
  - `bootstrap.json` is written only through `BootstrapStore`, and files with `safe_write`.
  - The server writes `state.json` only while it holds the project `.lock`, or while its own job holds it.
- **CLI:**
  - Exit codes: `0` success; `1` user or validation error; `2` pause; `3` auth or config error.
  - `stickman review` prints `Project: <folder name>` as its first line.
- **Tests:** `pytest`. Tests that call the real API are marked `live` and excluded by default. The full suite takes about 3 minutes.
- **Paid calls:** nothing in Tasks 1–9 calls the real API. Task 10 (the browser check) never clicks Regenerate, Replan or "Make more", so it spends nothing.
- **Commits:**
  - Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Pass it as a second `-m`.
  - Never commit `config/style.yaml`, `config/visual_rules.yaml`, `config/mascot.yaml`, `config/pricing.yaml`, `.env.example`, `COMPACT-SUMMARY.md` or `.superpowers/`.
- **Shell:**
  - Use Git Bash from the workspace root `C:\huSSNAIN PROJECTS\Tan Project`.
  - uv is not on PATH, so first run `export PATH="/c/Users/Operator PC/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"`.
  - Python code goes in a file that you then run with `uv run python <file>`. A heredoc holding Python code fails in this shell.

## Decisions made while writing this plan

- **The user's decisions (2026-09-26):**
  - M6 branches from `feat/m5-bootstrap` (stacked; one PR each, M5 merges first).
  - It runs as SDD **without per-task reviews**, then one whole-branch review, one fix wave and a scoped re-review.
- **What the page shows from each file:**
  - **The plan:** read on every `GET /api/project`. An invalid plan shows its errors (YAML path and message), and the page keeps showing the last valid plan until it's fixed.
  - **`state.json`:** read fresh on every request unless the server's own job is running (then the job's store in memory is the truth).
  - **Stale:** computed for display with `recovery.stale_status`. The server doesn't write it; `generate`'s `recover()` does. This way a page open during a CLI run never touches `state.json`.
- **Live reload:**
  - `watchfiles` watches the project folder (not recursively) with a 300 ms debounce. A changed `plan.yaml` sends a `plan` event and a changed `state.json` sends a `state` event, and the page refetches `GET /api/project`, well within the 2 s of §18 #18.
  - After the server writes `plan.yaml`, it records the new hash, and a change with that hash is ignored (§12.4).
- **Prompt edits (§12.4):**
  - The editor remembers `plan_hash` **when it was opened**. Saving sends it.
  - A file changed on disk since then gives 409 and "plan.yaml changed on disk since this page loaded — reload before saving". Nothing is written.
  - A successful save writes the prompt with `prompt_locked: true`.
- **Regenerate** (from the page, and later M7's `regen`):
  - It starts a **new chain** for the unit. The old current version stays, and becomes `compare_with`, the left image of the side-by-side view.
  - The unit's approval is cleared, because the approved image is no longer the one shown.
  - Choosing a version (`select-version`, or `1`/`2` in the side-by-side view) makes it current and clears `compare_with`. The approval stays only if the chosen version is the approved one; otherwise the status comes from its QC (passed → `generated`, failed → `needs_review`, unchecked → `generated`, checked by the next run).
  - A regeneration uses the full QC chain with retries, and the plan rewriter for soften/redesign, exactly like `generate`. It also rebuilds the unit's tool-built prompt for the current references (Task 3's refresh), writing `plan.yaml` hash-checked.
- **Jobs:**
  - One page job at a time (regenerate, replan, more candidates). A second one gives 409 "the page is already regenerating 006a".
  - A job holds the project `.lock` for its whole length, and more candidates holds the bootstrap lock. If a CLI run holds it, the page gets 409 "another stickman process (PID n) is generating this project; try again when it finishes".
  - Page actions that change `state.json` take the lock just for the write, or use the job's store while this server's job runs.
  - A job reports through events: `job` with `started` / `progress` / `finished` / `paused` / `failed` and a message.
- **The Sheets view:**
  - It shows the style anchor and mascot candidates (§12.2 "bootstrap only"), with Approve and "Make 2 more".
  - Mascot candidates made with a previous anchor are marked, and can't be approved (M5).
  - Extras have no sheets until M7, so the view lists the cast with "drawn from its description until extras' sheets arrive (M7)". `POST /api/sheets/<extra>/…` gives 400 with that message.
  - `sheets_approved` is left for M7.
- **The Tests view** shows `state.test_units`. That list stays empty until M7 picks the units, so the view says so, and `POST /api/tests/approve` gives 409 until there are test units.
- **Approve plan** sets `plan_approved`. It's refused (409) while the plan has errors. Nothing enforces the gates until M7.
- **The estimate on the Plan view (§12.2):**
  - Images: every unit with a prompt, at its job's estimate, × (1 + `expected_retry_rate`). Checks: the typical vision cost for the same count.
  - Sheets: 2 candidates per extra with no `library_ref`, at `sheet_size` (or 1024×768 for a group) on the plan's model.
  - Time: images × (1 + retry rate) × `render.est_seconds_per_image` ÷ `render.concurrency`.
  - The free-allocation note (§9.7).
  - The LLM line is 0: planning is done.
- **Security:**
  - The Host check and the `X-Stickman` header check (Global Constraints).
  - `/files/<path>` serves only PNGs under the project's `images/` folder, `library/_bootstrap/`, `library/style/` and `library/mascot/`, resolved and checked with `is_relative_to`, never inside `style_refs/`.
- **The page's look**, from the design pass:
  - A cool light-table workspace (`#E6E9EC`), with the drawings on pure white "paper" mats, since they're black ink on white.
  - One accent, non-photo blue `#2F6FDB` (the pencil comic artists sketch in), for focus and selection.
  - Status colours: approved green `#2E7D4F`, needs review amber `#B7791F`, failed red `#B42318`, stale violet `#7A4FB5`, generating blue-grey `#5B6B7F`.
  - The system UI font with tabular figures for times, and no web fonts.
  - **The one memorable element is the timeline strip:** the video's units as segments sized by their duration and coloured by status, so a glance shows where the flagged units are in the video. Clicking a segment focuses that unit.
  - A dark theme follows the system setting, and the drawings keep their white paper.
  - Keyboard focus is always visible, reduced motion is respected, and the layout stacks on a narrow window.
- **`stickman review`:**
  - It opens the browser unless `--no-browser` is given.
  - It checks that the port is free first (exit 1 with a clear message if not).
  - It works without `.env`: approving and editing still work, and regenerate and replan fail with a message saying they need `CF_ACCOUNT_ID` and `CF_API_TOKEN`.
  - Ctrl+C stops it with exit 0.

## File Map

```
pyproject.toml, uv.lock               fastapi, uvicorn, watchfiles                               (modify, Task 1)
src/stickman/runtime.py               secrets_of, build_client, check_usd                         (new, Task 1)
src/stickman/cli.py                   uses runtime; `review` command                               (modify, Tasks 1, 9)
src/stickman/render/state.py          UnitState.compare_with; StateStore.approve/select_version/begin_regeneration (modify, Task 2)
src/stickman/render/recovery.py       stale_status (pure); _mark_stale uses it                      (modify, Task 2)
src/stickman/render/renderer.py       Renderer.run(…, fresh=…)                                     (modify, Task 2)
src/stickman/plan/refresh.py          refresh_plan_file (shared by generate and the page)           (modify, Task 5)
src/stickman/review/__init__.py                                                                     (new, empty, Task 3)
src/stickman/review/view.py           file_url, unit_view, gallery_order, estimate, project_view    (new, Task 3)
src/stickman/review/actions.py        ActionError, approve_unit, approve_remaining, select_version,
                                      approve_plan, approve_tests, edit_prompt, approve_sheet       (new, Task 4)
src/stickman/review/access.py         Busy, JobInfo, ProjectAccess                                  (new, Task 5)
src/stickman/review/jobs.py           Jobs (regenerate, replan, more_candidates)                    (new, Task 5)
src/stickman/review/events.py         EventHub, sse, event_stream                                    (new, Task 6)
src/stickman/review/watch.py          PlanWatcher                                                    (new, Task 6)
src/stickman/review/app.py            PlanSource, create_app                                         (new, Task 7)
src/stickman/review/static/index.html, app.css, app.js                                              (new, Task 8)
tests/review/test_view.py, test_actions.py, test_jobs.py, test_events.py, test_app.py, test_static.py (new, Tasks 3-8)
tests/test_cli_review.py                                                                             (new, Task 9)
tests/render/test_state.py, test_recovery.py, test_renderer.py, tests/test_packaging.py              (modify, Tasks 1-2)
spec.md, plan.md                      [M6] notes                                                      (modify, Task 9)
```

---

### Task 1: The web dependencies and `runtime.py`

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (with `uv add`)
- Create: `src/stickman/runtime.py`
- Modify: `src/stickman/cli.py`
- Test: `tests/test_packaging.py`, `tests/test_runtime.py` (new)

**Interfaces:**
- Produces (`stickman.runtime`):
  - `secrets_of(cfg: AppConfig) -> tuple[str, ...]`: the token and the account id, or `()` without secrets.
  - `build_client(cfg: AppConfig) -> CloudflareClient`: raises `ConfigError` without secrets.
  - `check_usd(settings: Settings, pricing: PricingConfig) -> float`: a typical vision check's cost, or 0 with `qc.vision` off.
- `cli.py` keeps `build_client` (imported by name, so `monkeypatch.setattr(cli, "build_client", …)` still works), `_secrets(cfg)` and `_check_usd(cfg, pricing)` as thin wrappers.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_packaging.py`, following the pattern of its numpy test (read the file first and copy how it reads `pyproject.toml`):

```python
@pytest.mark.parametrize("name", ["fastapi", "uvicorn", "watchfiles"])
def test_the_review_server_dependencies_are_runtime_dependencies(name):
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(dep.split(">")[0].split("=")[0].strip() == name for dep in data["project"]["dependencies"])
```

(Add `import pytest` at the top if the file doesn't have it.)

Create `tests/test_runtime.py`:

```python
import pytest

from stickman.pricing import load_pricing
from stickman.runtime import build_client, check_usd, secrets_of
from stickman.settings import AppConfig, ConfigError, QCSettings, Secrets, Settings


def test_secrets_are_the_token_and_the_account_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acc123")
    monkeypatch.setenv("CF_API_TOKEN", "tok-secret")
    cfg = AppConfig(workspace=tmp_path, settings=Settings(), secrets=Secrets())
    assert secrets_of(cfg) == ("tok-secret", "acc123")
    assert secrets_of(AppConfig(workspace=tmp_path, settings=Settings(), secrets=None)) == ()


def test_a_client_needs_the_credentials(tmp_path):
    with pytest.raises(ConfigError):
        build_client(AppConfig(workspace=tmp_path, settings=Settings(), secrets=None))


def test_a_typical_check_costs_something_unless_the_vision_check_is_off(tmp_path):
    pricing = load_pricing(tmp_path)
    assert check_usd(Settings(), pricing) > 0
    assert check_usd(Settings(qc=QCSettings(vision=False)), pricing) == 0.0
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_packaging.py tests/test_runtime.py -q`
Expected: FAIL (the dependencies are missing, and `stickman.runtime` doesn't exist).

- [ ] **Step 3: Add the dependencies**

Run: `uv add "fastapi>=0.115" "uvicorn>=0.30" "watchfiles>=0.24"`
Expected: `pyproject.toml` lists all three under `[project] dependencies`, and `uv.lock` is updated.

- [ ] **Step 4: Create `src/stickman/runtime.py`**

```python
"""What the CLI and the review server share for talking to Cloudflare: the client, the secrets that
logs and messages mask, and the typical cost of a vision check."""

from __future__ import annotations

from stickman.cf.client import CloudflareClient
from stickman.pricing import PricingConfig, llm_cost_usd
from stickman.qc.vision import TYPICAL_TOKENS
from stickman.settings import AppConfig, ConfigError, Settings


def secrets_of(cfg: AppConfig) -> tuple[str, ...]:
    """What run logs, state.json and messages built from Cloudflare errors mask: the token and the account
    id (Cloudflare's routing errors echo the request path, which holds the account id)."""
    if cfg.secrets is None:
        return ()
    return (cfg.secrets.cf_api_token.get_secret_value(), cfg.secrets.cf_account_id)


def build_client(cfg: AppConfig) -> CloudflareClient:
    if cfg.secrets is None:
        raise ConfigError("Cloudflare credentials are not loaded (CF_ACCOUNT_ID and CF_API_TOKEN in .env)")
    return CloudflareClient(
        cfg.secrets.cf_account_id,
        cfg.secrets.cf_api_token.get_secret_value(),
        plan=cfg.settings.account.plan,
        timeout_s=cfg.settings.render.timeout_s,
    )


def check_usd(settings: Settings, pricing: PricingConfig) -> float:
    """What a typical vision check costs (qwen's tokens, measured in M4)."""
    if not settings.qc.vision:
        return 0.0
    price = pricing.llm(settings.llm.vision_model)
    return 0.0 if price is None else llm_cost_usd(price, *TYPICAL_TOKENS)
```

- [ ] **Step 5: Make `cli.py` use it**

In `src/stickman/cli.py`:
- Delete the bodies of `_secrets` and `build_client`.
- Add `from stickman.runtime import build_client, check_usd, secrets_of`, which binds `build_client` in `cli`'s namespace.
- Keep the wrappers:

```python
def _secrets(cfg: AppConfig) -> tuple[str, ...]:
    return secrets_of(cfg)


def _check_usd(cfg: AppConfig, pricing: PricingConfig) -> float:
    return check_usd(cfg.settings, pricing)
```

Delete the old `build_client` function definition. The imported name replaces it. Remove imports that are now unused in `cli.py` (`CloudflareClient`, and `TYPICAL_TOKENS`/`llm_cost_usd` if nothing else uses them).

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/test_packaging.py tests/test_runtime.py tests/test_cli_generate.py tests/test_cli_bootstrap.py tests/test_cli_compare.py tests/test_cli_init.py -q`
Expected: PASS (the CLI tests still patch `cli.build_client`).

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add pyproject.toml uv.lock src/stickman/runtime.py src/stickman/cli.py tests/test_packaging.py tests/test_runtime.py
git commit -m "feat: fastapi, uvicorn and watchfiles for the review page; the client, secrets and check cost move to runtime.py" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Regenerating a unit, choosing a version, approving, and the stale rule

**Files:**
- Modify: `src/stickman/render/state.py`, `src/stickman/render/recovery.py`, `src/stickman/render/renderer.py`
- Test: `tests/render/test_state.py`, `tests/render/test_recovery.py`, `tests/render/test_renderer.py`

**Interfaces:**
- Produces:
  - `UnitState.compare_with: int | None = None`: the version shown beside the current one after a regeneration, until one is chosen (§12.2).
  - `StateStore.approve(unit_id) -> None`: raises `ValueError` without a current version. It sets `approved_version = current_version`, `status = "approved"` and `compare_with = None`, then saves.
  - `StateStore.select_version(unit_id, v) -> None`: raises `KeyError` for an unknown version. The rules are in the Decisions (current = v; `compare_with` cleared; the approval kept only if v is the approved version; otherwise the status comes from its QC). It saves.
  - `StateStore.begin_regeneration(unit_id) -> None`: `compare_with = current_version` (which may be None) and `approved_version = None`, then saves.
  - `recovery.stale_status(unit: UnitState, fingerprint: str) -> UnitStatus`: the status the unit has with this fingerprint, changing nothing.
  - `Renderer.run(jobs, *, fresh: Collection[str] = ()) -> RunResult`: units in `fresh` start a new chain; their earlier versions stay as a record.

- [ ] **Step 1: Write the failing tests**

Add to `tests/render/test_state.py` (it already imports `pytest`, `decide`, `PixelResult`, `StateStore` and has a `version(v, unit, **changes)` helper):

```python
def checked(passed):
    """A QC result that passes, or fails for `empty`."""
    pixel = PixelResult(reason=None if passed else "empty", lum_std=30.0, lum_mean=250.0, ink_fraction=0.02,
                        lap_var=900.0, white_fraction=0.97, colour_fraction=0.0, black_fraction=0.01)
    return decide(pixel, expected_figures=1, min_idea_score=3)


def test_approving_makes_the_current_version_the_approved_one(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version(1, "001"), status="generated")
    store.approve("001")
    unit = StateStore.load(tmp_path).unit("001")
    assert (unit.status, unit.approved_version, unit.compare_with) == ("approved", 1, None)


def test_a_unit_without_an_image_cannot_be_approved(tmp_path):
    with pytest.raises(ValueError):
        StateStore.load(tmp_path).approve("001")


def test_a_regeneration_keeps_the_old_current_version_to_compare_and_clears_the_approval(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version(1, "001"), status="generated")
    store.approve("001")
    store.begin_regeneration("001")
    unit = StateStore.load(tmp_path).unit("001")
    assert (unit.compare_with, unit.approved_version, unit.current_version) == (1, None, 1)


def test_choosing_a_version_makes_it_current_and_ends_the_comparison(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version(1, "001", qc=checked(True)), status="generated")
    store.begin_regeneration("001")
    store.add_version("001", version(2, "001", qc=checked(False)), status="needs_review")
    store.select_version("001", 1)
    unit = StateStore.load(tmp_path).unit("001")
    assert (unit.current_version, unit.compare_with, unit.status) == (1, None, "generated")
    store.select_version("001", 2)
    assert StateStore.load(tmp_path).unit("001").status == "needs_review"


def test_choosing_the_approved_version_keeps_the_approval_and_another_clears_it(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version(1, "001", qc=checked(True)), status="generated")
    store.add_version("001", version(2, "001", qc=checked(True)), status="generated")
    store.select_version("001", 1)
    store.approve("001")
    store.select_version("001", 1)
    assert store.unit("001").status == "approved" and store.unit("001").approved_version == 1
    store.select_version("001", 2)
    assert store.unit("001").approved_version is None and store.unit("001").status == "generated"


def test_choosing_an_unknown_version_is_refused(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version(1, "001"), status="generated")
    with pytest.raises(KeyError):
        store.select_version("001", 7)


def test_an_older_state_json_without_compare_with_still_loads(tmp_path):
    (tmp_path / "state.json").write_text('{"schema_version": 1, "units": {"001": {"status": "planned"}}}', encoding="utf-8")
    assert StateStore.load(tmp_path).unit("001").compare_with is None
```

Add to `tests/render/test_recovery.py` (it has the `version` and `EXPECTED` helpers):

```python
from stickman.render.recovery import stale_status


def test_stale_status_changes_nothing_and_matches_the_stale_rule(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("006a", version(1), status="generated")
    unit = store.unit("006a")
    assert stale_status(unit, "sha256:abc") == "generated"
    assert stale_status(unit, "sha256:new") == "stale"
    assert unit.status == "generated"  # unchanged
    unit.status = "stale"
    assert stale_status(unit, "sha256:abc") == "generated"  # it matches again
    assert stale_status(store.unit("001"), "sha256:new") == "planned"  # no image: never stale
```

Add to `tests/render/test_renderer.py`:

```python
def test_a_fresh_unit_gets_a_new_chain_and_keeps_its_old_versions(tmp_path, plan_data, fake_images):
    run = Run(tmp_path, plan_data, fake_images())
    run.go(run.jobs[:1])
    run.store.begin_regeneration("001")
    later = fake_images()
    asyncio.run(run.make_renderer(later).run(run.jobs[:1], fresh={"001"}))
    unit = run.store.unit("001")
    assert len(later.calls) == 1
    assert [v.v for v in unit.versions] == [1, 2]
    assert (unit.current_version, unit.compare_with, unit.status) == (2, 1, "generated")
    assert unit.versions[1].retry_of is None  # a new chain, not a retry of v1
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/render/test_state.py tests/render/test_recovery.py tests/render/test_renderer.py -q`
Expected: FAIL (the missing methods and field, `stale_status` and `fresh`).

- [ ] **Step 3: Implement in `src/stickman/render/state.py`**

Add the field to `UnitState`:

```python
    compare_with: int | None = None  # [M6] shown beside the current version after a regeneration, until one is chosen
```

Add to `StateStore`:

```python
    def approve(self, unit_id: str) -> None:
        unit = self.unit(unit_id)
        if unit.current_version is None:
            raise ValueError(f"{unit_id} has no image to approve")
        unit.approved_version = unit.current_version
        unit.status = "approved"
        unit.compare_with = None
        unit.error = None
        self.save()

    def select_version(self, unit_id: str, v: int) -> None:
        """Make version v current (a history pick, or 1/2 in the side-by-side view, spec §12.2). The
        approval stays only if v is the approved version; otherwise the status comes from v's QC."""
        unit = self.unit(unit_id)
        version = unit.version(v)
        if version is None:
            raise KeyError(f"{unit_id} has no version {v}")
        unit.current_version = v
        unit.compare_with = None
        unit.error = None
        if unit.approved_version is not None and unit.approved_version != v:
            unit.approved_version = None
        if unit.approved_version == v:
            unit.status = "approved"
        elif version.qc is not None and not version.qc.passed:
            unit.status = "needs_review"
        else:
            unit.status = "generated"  # an unchecked version is checked by the next run
        self.save()

    def begin_regeneration(self, unit_id: str) -> None:
        """Before a regeneration: the current version is kept to compare with the new one, and the
        approval is cleared, since the approved image won't be the one shown."""
        unit = self.unit(unit_id)
        unit.compare_with = unit.current_version
        unit.approved_version = None
        self.save()
```

- [ ] **Step 4: Implement `stale_status` in `src/stickman/render/recovery.py`**

```python
def stale_status(unit: UnitState, fingerprint: str) -> UnitStatus:
    """The status a unit has once compared with `fingerprint` (spec §10.4), without changing it: stale
    when the approved (else current) version was made from other fields, back from stale when it matches
    again, otherwise unchanged. Units without an image are never stale."""
    if unit.status not in HAS_IMAGE:
        return unit.status
    compared = unit.approved_version or unit.current_version
    version = unit.version(compared) if compared is not None else None
    if version is None:
        return unit.status
    if version.fingerprint != fingerprint:
        return "stale"
    return _status_again(unit) if unit.status == "stale" else unit.status
```

Rewrite `_mark_stale` to use it:

```python
def _mark_stale(store: StateStore, expected: Mapping[str, ExpectedUnit]) -> tuple[list[str], list[str]]:
    """spec §10.4: compared with the approved version when there is one, else the current one."""
    stale: list[str] = []
    fresh: list[str] = []
    for unit_id, want in expected.items():
        unit = store.state.units[unit_id]
        status = stale_status(unit, want.fingerprint)
        if status == unit.status:
            continue
        if status == "stale":
            stale.append(unit_id)
        else:
            fresh.append(unit_id)
        unit.status = status
    return stale, fresh
```

- [ ] **Step 5: Add `fresh` to `Renderer.run` in `src/stickman/render/renderer.py`**

Change the signature to `async def run(self, jobs: Sequence[RenderJob], *, fresh: Collection[str] = ()) -> RunResult:`, import `Collection` from `collections.abc`, and at its start set `self._fresh = frozenset(fresh)`. Also initialise `self._fresh: frozenset[str] = frozenset()` in `__init__`. In `_unit`, replace the line that sets `chain`:

```python
        # A regenerated unit starts a new chain; its earlier versions stay as a record (spec §12.2 [M6]).
        chain = [] if unit_id in self._fresh else chain_of(self._store.unit(unit_id), job.fingerprint)
```

Update the docstring of `run` to mention `fresh`.

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/render -q`
Expected: PASS, including `test_resume`.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/render/state.py src/stickman/render/recovery.py src/stickman/render/renderer.py tests/render/test_state.py tests/render/test_recovery.py tests/render/test_renderer.py
git commit -m "feat: a regenerated unit starts a new chain beside its old image; choosing, approving, and the stale rule without writing" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The page's data, `review/view.py`

**Files:**
- Create: `src/stickman/review/__init__.py` (empty), `src/stickman/review/view.py`
- Test: `tests/review/test_view.py` (new)

**Interfaces:**
- Consumes:
  - `stale_status` (Task 2), `check_usd` (Task 1);
  - `JobBuilder`, `RenderContext`, `review_reason` (`render/summary.py`), `image_stem`;
  - `BootstrapStore`, `ranked`, `made_with`, `anchor_done`, `mascot_done` (M5);
  - `find_references`, `anchor_ref_path`, `ReferenceFiles`, `cast_infos`, `Budget`, `Ledger`, `image_cost_usd`, `format_usd`, `usd_neurons`.
- Produces (`stickman.review.view`):
  - `file_url(workspace: Path, path: Path) -> str`: `"/files/<path relative to the workspace, forward slashes>"`.
  - `qc_view(qc) -> dict | None`: `{passed, reason, score, notes, text_seen}`.
  - `unit_view(workspace, project_dir, plan, unit, state, fingerprint) -> dict`. Its keys are listed in Step 3.
  - `gallery_order(units: Sequence[Mapping]) -> list[str]`: `needs_review`, then `stale`, then `failed`, then the rest, each in time order.
  - `estimate_view(plan, builder, ctx) -> dict`.
  - `budget_view(workspace, settings, now) -> dict`.
  - `bootstrap_view(workspace, ctx) -> dict`, which raises `BootstrapError`.
  - `cast_view(workspace, plan, ctx) -> list[dict]`.
  - `empty_view(project_dir, problems) -> dict`: the page's data when config can't be read.
  - `project_view(*, workspace, project_dir, ctx, plan, plan_hash, errors, state, now, job=None) -> dict`, with the keys:

    | Key | Holds |
    |---|---|
    | `project` | the project folder's name |
    | `plan_hash`, `errors` | the plan file's hash, and its validation errors |
    | `problems` | config, bootstrap or reference errors |
    | `job` | the running job |
    | `approvals` | `{plan, sheets, tests}` |
    | `test_units` | the test unit ids |
    | `budget` | the week's spend |
    | `aspect`, `duration_end` | from the plan |
    | `units`, `order` | the unit entries, and the gallery order |
    | `estimate` | the cost and time estimate |
    | `corrections`, `merge_check`, `cast` | from the plan |
    | `bootstrap` | the candidates and approvals |

- [ ] **Step 1: Write the failing tests**

Create `tests/review/test_view.py`:

```python
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.store import BootstrapStore, Candidate
from stickman.ledger import Ledger, LedgerEntry
from stickman.library import anchor_ref_path
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan
from stickman.pricing import image_cost_usd
from stickman.qc.decide import decide
from stickman.qc.pixel import PixelResult
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.references import ReferenceFiles
from stickman.render.state import ProjectState, StateStore, Version
from stickman.review.view import file_url, gallery_order, project_view
from stickman.runtime import check_usd
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=PK)
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


def qc(passed=True, reason=None):
    pixel = PixelResult(reason=None if passed else reason, lum_std=30.0, lum_mean=250.0, ink_fraction=0.02,
                        lap_var=900.0, white_fraction=0.97, colour_fraction=0.0, black_fraction=0.01)
    return decide(pixel, expected_figures=1, min_idea_score=3)


def version(unit, v=1, fingerprint="sha256:x", **changes):
    data = dict(v=v, file=f"images/_history/{unit}_v{v}.png", seed=40 + v, model=KLEIN_4B, width=1920, height=1088,
                fingerprint=fingerprint, prompt_sent="p", qc=qc(), est_cost_usd=0.0023, latency_s=12.0,
                created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    return Version(**{**data, **changes})


class Setup:
    def __init__(self, tmp_path, plan_data):
        self.workspace = tmp_path
        self.project = tmp_path / "projects" / FOLDER
        self.project.mkdir(parents=True)
        write_plan(self.project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
        self.plan = parse_plan(plan_data)
        self.ctx = RenderContext.load(tmp_path, Settings())
        self.builder = JobBuilder(self.ctx, self.plan)
        self.store = StateStore.load(self.project)

    def fingerprint(self, unit_id):
        return self.builder.fingerprint(next(u for u in self.plan.units() if u.id == unit_id))

    def view(self, errors=(), plan="same", state=None, job=None):
        return project_view(workspace=self.workspace, project_dir=self.project, ctx=self.ctx,
                            plan=self.plan if plan == "same" else plan, plan_hash="sha256:h", errors=list(errors),
                            state=state or self.store.state, now=NOW, job=job)


def units_by_id(view):
    return {unit["id"]: unit for unit in view["units"]}


def test_a_file_url_is_relative_to_the_workspace(tmp_path):
    assert file_url(tmp_path, tmp_path / "projects" / "p" / "images" / "a.png") == "/files/projects/p/images/a.png"


def test_units_carry_their_status_versions_and_image_urls(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    run.store.add_version("001", version("001", fingerprint=run.fingerprint("001")), status="generated")
    unit = units_by_id(run.view())["001"]
    assert (unit["status"], unit["current_version"], unit["stem"]) == ("generated", 1, "001_00-00.0")
    assert unit["versions"][0]["url"] == f"/files/projects/{FOLDER}/images/_history/001_v1.png"
    assert unit["versions"][0]["qc"] == {"passed": True, "reason": None, "score": None, "notes": "", "text_seen": ""}
    assert unit["visual_idea"] == "Idea 001" and unit["split"] == "none"
    assert units_by_id(run.view())["002a"]["split"] == "split"


def test_a_unit_whose_fields_changed_shows_stale_and_nothing_is_written(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    run.store.add_version("001", version("001", fingerprint="sha256:old"), status="generated")
    before = (run.project / "state.json").read_bytes()
    assert units_by_id(run.view())["001"]["status"] == "stale"
    assert (run.project / "state.json").read_bytes() == before


def test_the_side_by_side_pair_is_shown_only_when_it_differs_from_the_current_version(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    fp = run.fingerprint("001")
    run.store.add_version("001", version("001", fingerprint=fp), status="generated")
    run.store.begin_regeneration("001")
    assert units_by_id(run.view())["001"]["compare_with"] is None  # the new image hasn't come yet
    run.store.add_version("001", version("001", v=2, fingerprint=fp), status="generated")
    assert units_by_id(run.view())["001"]["compare_with"] == 1


def test_a_needs_review_unit_says_why(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    run.store.add_version("001", version("001", fingerprint=run.fingerprint("001"), qc=qc(False, "empty")),
                          status="needs_review")
    unit = units_by_id(run.view())["001"]
    assert (unit["status"], unit["review_reason"]) == ("needs_review", "empty")


def test_the_gallery_shows_flagged_units_first_then_time_order():
    units = [{"id": "001", "status": "generated"}, {"id": "002", "status": "failed"}, {"id": "003", "status": "stale"},
             {"id": "004", "status": "needs_review"}, {"id": "005", "status": "planned"}, {"id": "006", "status": "needs_review"}]
    assert gallery_order(units) == ["004", "006", "003", "002", "001", "005"]


def test_the_estimate_counts_images_checks_and_extras_sheets(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    estimate = run.view()["estimate"]
    settings, pricing = run.ctx.settings, run.ctx.pricing
    per_image = image_cost_usd(pricing.image(KLEIN_4B), (1920, 1088))
    sheets = 2 * image_cost_usd(pricing.image(KLEIN_4B), (1024, 768))  # the caveman group has 3 figures
    assert estimate["units"] == 3
    assert estimate["images_usd"] == pytest.approx(3 * per_image * 1.25)
    assert estimate["checks_usd"] == pytest.approx(3 * check_usd(settings, pricing) * 1.25)
    assert estimate["sheets_usd"] == pytest.approx(sheets)
    assert estimate["llm_usd"] == 0.0
    assert estimate["minutes"] == pytest.approx(round(3 * 1.25 * 10 / 4 / 60, 1))
    assert estimate["free_note"] == "Up to $0.11 of today's usage may be covered by the free daily allocation."


def test_an_invalid_plan_keeps_showing_the_last_one_with_its_errors(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    view = run.view(errors=["scenes[0].units[0].shot: Input should be 'wide', 'medium' or 'close-up'"])
    assert view["errors"] and len(view["units"]) == 3
    empty = run.view(plan=None, errors=["invalid YAML"])
    assert empty["units"] == [] and empty["estimate"] is None


def test_corrections_merge_checks_and_cast_are_listed(tmp_path, plan_data):
    view = Setup(tmp_path, plan_data).view()
    assert view["corrections"] == [{"scene": "001", "from": "90 at night", "to": "9 at night", "reason": "clock time"}]
    assert [member["id"] for member in view["cast"]] == ["mascot", "caveman_group"]
    assert view["cast"][1]["figures"] == 3 and view["cast"][1]["sheet"] is False


def test_the_budget_shows_the_weeks_spend(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    Ledger(tmp_path / "ledger.jsonl").append(LedgerEntry(ts=NOW, project=FOLDER, kind="image", model=KLEIN_4B,
                                                        est_usd=13.0, billing="billed"))
    budget = run.view()["budget"]
    assert budget["week_usd"] == pytest.approx(13.0) and budget["weekly_usd"] == 15.0 and budget["warn"] is True


def test_bootstrap_candidates_are_listed_best_first_and_old_anchor_ones_are_marked(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    ref = anchor_ref_path(tmp_path, 1)
    ref.parent.mkdir(parents=True)
    Image.new("RGB", (512, 384), "white").save(ref, format="PNG")
    store = BootstrapStore.load(tmp_path, 1)
    for n, refs in ((1, ["library/style/anchor_v1_ref.png#sha256:old"]), (2, None)):
        store.add("mascot", Candidate(n=n, file=f"library/_bootstrap/v1/mascot/c{n}.png", seed=n, model=KLEIN_9B,
                                      width=768, height=1024, prompt_sent="p", refs=refs or [], est_cost_usd=0.017,
                                      latency_s=4.0, created=NOW, qc=qc()))
    label = ReferenceFiles(tmp_path, ref_max_side=512).load(ref).label
    store.state.mascot.candidates[1].refs = [label]
    store.save()
    boot = run.view()["bootstrap"]
    assert boot["style_version"] == 1 and boot["anchor_done"] is False
    marks = {c["n"]: c["previous_anchor"] for c in boot["mascot"]["candidates"]}
    assert marks == {1: True, 2: False}
    assert boot["mascot"]["candidates"][0]["url"].startswith("/files/library/_bootstrap/v1/mascot/c")


def test_the_job_and_approvals_are_passed_through(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    state = ProjectState(plan_approved=True, test_units=["001"])
    view = run.view(state=state, job={"kind": "regenerate", "unit": "001", "message": ""})
    assert view["approvals"] == {"plan": True, "sheets": False, "tests": False}
    assert view["test_units"] == ["001"] and view["job"]["unit"] == "001"
    json.dumps(view)  # everything is JSON-ready
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/review/test_view.py -q`
Expected: FAIL with `ModuleNotFoundError: stickman.review`.

- [ ] **Step 3: Create `src/stickman/review/__init__.py` (empty) and `src/stickman/review/view.py`**

```python
"""The review page's data (spec §12.2): one JSON-ready dict built from the plan, state.json, the bootstrap
store and the ledger. It only reads, so a page open during a CLI run never touches state.json; stale is
worked out for display (recovery.stale_status) and written by the next generate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from stickman.bootstrap.store import BootstrapError, BootstrapStore, anchor_done, made_with, mascot_done, ranked
from stickman.budget import Budget
from stickman.ledger import LEDGER_FILE, Ledger
from stickman.library import anchor_ref_path, find_references
from stickman.plan.cast import cast_infos
from stickman.plan.models import CastMember, Plan, PlanUnit
from stickman.pricing import format_usd, image_cost_usd, usd_neurons
from stickman.qc.decide import QCResult
from stickman.render.images import image_stem
from stickman.render.jobs import JobBuilder, JobError, RenderContext
from stickman.render.recovery import stale_status
from stickman.render.references import ReferenceFiles
from stickman.render.state import ProjectState, UnitState, Version
from stickman.render.summary import review_reason
from stickman.runtime import check_usd
from stickman.settings import ConfigError, Settings

FLAGGED = ("needs_review", "stale", "failed")  # the gallery's order (spec §12.2)
GROUP_SHEET = (1024, 768)  # a cast entry with more than one figure (spec §8.2)


def file_url(workspace: Path, path: Path) -> str:
    return "/files/" + path.resolve().relative_to(workspace.resolve()).as_posix()


def qc_view(qc: QCResult | None) -> dict[str, Any] | None:
    if qc is None:
        return None
    vision = qc.vision
    return {
        "passed": qc.passed,
        "reason": qc.reason,
        "score": qc.score if vision is not None else None,
        "notes": (vision.notes or "") if vision is not None else (qc.vision_error or ""),
        "text_seen": (vision.text_seen or "") if vision is not None else "",
    }


def _version_view(workspace: Path, project_dir: Path, version: Version) -> dict[str, Any]:
    return {
        "v": version.v, "url": file_url(workspace, project_dir / version.file), "seed": version.seed,
        "retry_of": version.retry_of, "retry_reason": version.retry_reason, "latency_s": version.latency_s,
        "created": version.created.isoformat(), "qc": qc_view(version.qc),
    }


def unit_view(
    workspace: Path, project_dir: Path, plan: Plan, unit: PlanUnit, state: UnitState, fingerprint: str | None
) -> dict[str, Any]:
    """`fingerprint` None: it couldn't be worked out (a reference image can't be read), so the stored status shows."""
    status = stale_status(state, fingerprint) if fingerprint is not None else state.status
    return {
        "id": unit.id, "part": unit.part, "start": unit.start, "end": unit.end, "stem": image_stem(unit.id, unit.start),
        "source_text": unit.source_text, "corrected_text": unit.corrected_text, "visual_idea": unit.visual_idea,
        "visual_type": unit.visual_type, "shot": unit.shot, "time_of_day": unit.time_of_day,
        "characters": [character.model_dump() for character in unit.characters],
        "setting": list(unit.setting), "props": list(unit.props), "image_prompt": unit.image_prompt,
        "prompt_locked": unit.prompt_locked, "softened": unit.softened, "split": plan.scene_of(unit.id).split.status,
        "status": status, "error": state.error,
        "review_reason": review_reason(state) if status == "needs_review" else None,
        "current_version": state.current_version, "approved_version": state.approved_version,
        # The side-by-side pair (spec §12.2): only once the new image differs from the kept one.
        "compare_with": state.compare_with if state.compare_with not in (None, state.current_version) else None,
        "versions": [_version_view(workspace, project_dir, version) for version in state.versions],
    }


def gallery_order(units: Sequence[Mapping[str, Any]]) -> list[str]:
    rank = {status: index for index, status in enumerate(FLAGGED)}
    ordered = sorted(enumerate(units), key=lambda pair: (rank.get(pair[1]["status"], len(FLAGGED)), pair[0]))
    return [unit["id"] for _, unit in ordered]


def estimate_view(plan: Plan, builder: JobBuilder, ctx: RenderContext) -> dict[str, Any]:
    """spec §12.2: images × (1 + expected retry rate), their checks, extras' sheets; time from the
    measured seconds per image and the concurrency. Planning is done, so the LLM costs nothing more here."""
    settings, pricing = ctx.settings, ctx.pricing
    per_image: list[float] = []
    for unit in plan.units():
        if not unit.image_prompt.strip():
            continue
        try:
            per_image.append(builder.job(unit).estimate_usd)
        except JobError:
            continue
    factor = 1 + settings.budget.expected_retry_rate
    images = sum(per_image) * factor
    checks = check_usd(settings, pricing) * len(per_image) * factor
    price = pricing.image(plan.image_model)
    extras = [m for m in plan.cast if isinstance(m, CastMember) and m.library_ref is None]
    sheets = sum(
        2 * image_cost_usd(price, GROUP_SHEET if member.figures > 1 else tuple(settings.image.sheet_size))
        for member in extras
    )
    total = images + checks + sheets
    minutes = len(per_image) * factor * settings.render.est_seconds_per_image / settings.render.concurrency / 60
    return {
        "units": len(per_image), "images_usd": images, "checks_usd": checks, "sheets_usd": sheets, "llm_usd": 0.0,
        "total_usd": total, "total_text": format_usd(total), "neurons": round(usd_neurons(total)),
        "minutes": round(minutes, 1),
        "free_note": f"Up to {format_usd(pricing.free_daily_usd)} of today's usage may be covered by the free daily allocation.",
    }


def budget_view(workspace: Path, settings: Settings, now: datetime) -> dict[str, Any]:
    spent = Budget.from_ledger(Ledger(workspace / LEDGER_FILE), settings.budget, now=now).spent
    weekly = settings.budget.weekly_usd
    return {
        "week_usd": spent, "weekly_usd": weekly, "warn": spent >= settings.budget.warn_ratio * weekly,
        "text": f"{format_usd(spent)} of {format_usd(weekly)} this week",
    }


def bootstrap_view(workspace: Path, ctx: RenderContext) -> dict[str, Any]:
    """The anchor and mascot candidates for the current style version (spec §12.2 Sheets, "bootstrap only").
    Mascot candidates made with a previous anchor are marked; they can't be approved (M5)."""
    version = ctx.style.style_version
    store = BootstrapStore.load(workspace, version)
    label: str | None = None
    ref = anchor_ref_path(workspace, version)
    if ref.is_file():
        try:
            label = ReferenceFiles(workspace, ref_max_side=ctx.settings.image.ref_max_side).load(ref).label
        except ConfigError:
            label = None

    def step(name: str) -> dict[str, Any]:
        state = store.step(name)  # type: ignore[arg-type]
        return {
            "approved": state.approved,
            "candidates": [
                {
                    "n": c.n, "url": file_url(workspace, workspace / c.file), "seed": c.seed, "qc": qc_view(c.qc),
                    "approved": state.approved == c.n,
                    "previous_anchor": name == "mascot" and label is not None and not made_with(c, label),
                }
                for c in ranked(state.candidates)
            ],
        }

    return {
        "style_version": version, "anchor_done": anchor_done(workspace, version),
        "mascot_done": mascot_done(workspace, ctx.mascot, version), "anchor": step("anchor"), "mascot": step("mascot"),
    }


def cast_view(workspace: Path, plan: Plan, ctx: RenderContext) -> list[dict[str, Any]]:
    available = find_references(
        workspace, use_references=True, style_version=plan.style_version, mascot=ctx.mascot, cast=plan.cast,
        library=ctx.library,
    )
    table = cast_infos(plan.cast, ctx.mascot)
    return [
        {
            "id": member.id, "name": table[member.id].name, "figures": table[member.id].figures,
            "description": table[member.id].description, "library_ref": getattr(member, "library_ref", None),
            "sheet": member.id in available.sheets,
        }
        for member in plan.cast
    ]


def empty_view(project_dir: Path, problems: Sequence[str]) -> dict[str, Any]:
    """What the page gets when the config can't be read: the problems, and nothing to act on."""
    return {
        "project": project_dir.name, "plan_hash": None, "errors": [], "problems": list(problems), "job": None,
        "approvals": {"plan": False, "sheets": False, "tests": False}, "test_units": [], "budget": None,
        "aspect": None, "duration_end": None, "units": [], "order": [], "estimate": None, "corrections": [],
        "merge_check": [], "cast": [], "bootstrap": None,
    }


def project_view(
    *,
    workspace: Path,
    project_dir: Path,
    ctx: RenderContext,
    plan: Plan | None,
    plan_hash: str | None,
    errors: Sequence[str],
    state: ProjectState,
    now: datetime,
    job: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`plan` is the last valid plan (None before there is one); `errors` are the current file's validation errors."""
    view = empty_view(project_dir, [])
    view.update(
        plan_hash=plan_hash, errors=list(errors), job=dict(job) if job else None,
        approvals={"plan": state.plan_approved, "sheets": state.sheets_approved, "tests": state.tests_approved},
        test_units=list(state.test_units), budget=budget_view(workspace, ctx.settings, now),
    )
    problems: list[str] = []
    try:
        view["bootstrap"] = bootstrap_view(workspace, ctx)
    except (BootstrapError, OSError) as exc:  # an unreadable bootstrap.json is shown, never fatal to the page
        problems.append(str(exc))
    if plan is not None:
        builder: JobBuilder | None = None
        fingerprints: dict[str, str] = {}
        try:
            builder = JobBuilder(ctx, plan)
            fingerprints = {unit.id: builder.fingerprint(unit) for unit in plan.units()}
        except ConfigError as exc:
            problems.append(str(exc))
        units = [
            unit_view(workspace, project_dir, plan, unit, state.units.get(unit.id, UnitState()), fingerprints.get(unit.id))
            for unit in plan.units()
        ]
        view.update(
            aspect=plan.aspect, duration_end=plan.duration_end, units=units, order=gallery_order(units),
            corrections=[{"scene": c.scene, "from": c.from_, "to": c.to, "reason": c.reason} for c in plan.corrections],
            merge_check=[check.model_dump() for check in plan.merge_check],
            cast=cast_view(workspace, plan, ctx),
        )
        if builder is not None:
            try:
                view["estimate"] = estimate_view(plan, builder, ctx)
            except ConfigError as exc:
                problems.append(str(exc))
    view["problems"] = problems
    return view
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/review/test_view.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/review tests/review/test_view.py
git commit -m "feat: the review page's data: units with status and versions, the gallery order, the estimate, the budget and the bootstrap candidates" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The page's actions, `review/actions.py`

**Files:**
- Create: `src/stickman/review/actions.py`
- Test: `tests/review/test_actions.py` (new)

**Interfaces:**
- Consumes: `StateStore.approve/select_version` (Task 2); `load_plan`, `update_unit`, `write_plan`, `PlanChangedError`; `BootstrapStore`, `bootstrap_folder`, `approve_anchor`, `approve_mascot`, `ApprovalError` (M5); `ProjectLock`, `LockHeld`.
- Produces (`stickman.review.actions`):
  - `ActionError(status: int, message: str)`, with `.status` and `.message`.
  - The messages: `EXTRAS_LATER`, `TESTS_LATER`, `PLAN_CHANGED` (the exact §12.4 text), `PLAN_HAS_ERRORS`.
  - `approve_unit(store, unit_id, *, unit_ids, busy=None) -> None`.
  - `approve_remaining(store, statuses: Mapping[str, str], *, busy=None) -> list[str]`.
  - `select_version(store, unit_id, v, *, unit_ids, busy=None) -> None`.
  - `approve_plan(store, errors: Sequence[str]) -> None` and `approve_tests(store) -> None`.
  - `edit_prompt(plan_path, unit_id, prompt, plan_hash, *, library_ids) -> str`: the new file hash.
  - `approve_sheet(workspace, char_id, n, *, settings, style, mascot) -> list[str]`: the written paths, relative to the workspace.

`busy` is the unit id that this server's running job is regenerating. Actions on that unit give 409.

- [ ] **Step 1: Write the failing tests**

Create `tests/review/test_actions.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.store import BootstrapStore, Candidate
from stickman.config_files import load_mascot, load_style
from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.render.images import encode_png
from stickman.render.state import StateStore, Version
from stickman.review.actions import (
    PLAN_CHANGED,
    ActionError,
    approve_plan,
    approve_remaining,
    approve_sheet,
    approve_tests,
    approve_unit,
    edit_prompt,
    select_version,
)
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))
IDS = {"001", "002a", "002b"}
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def version(unit, v=1):
    return Version(v=v, file=f"images/_history/{unit}_v{v}.png", seed=v, model=KLEIN_4B, width=8, height=8,
                   fingerprint="sha256:x", prompt_sent="p", est_cost_usd=0.0, latency_s=1.0,
                   created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))


def status_of(error):
    return error.value.status


def test_approving_a_unit(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version("001"), status="generated")
    approve_unit(store, "001", unit_ids=IDS)
    assert store.unit("001").status == "approved"


@pytest.mark.parametrize(("unit_id", "busy", "code"), [("009", None, 404), ("002a", None, 409), ("001", "001", 409)])
def test_approving_an_unknown_imageless_or_busy_unit_is_refused(tmp_path, unit_id, busy, code):
    store = StateStore.load(tmp_path)
    store.add_version("001", version("001"), status="generated")
    with pytest.raises(ActionError) as error:
        approve_unit(store, unit_id, unit_ids=IDS, busy=busy)
    assert status_of(error) == code


def test_approve_remaining_takes_only_generated_units(tmp_path):
    store = StateStore.load(tmp_path)
    for unit in ("001", "002a", "002b"):
        store.add_version(unit, version(unit), status="generated")
    done = approve_remaining(store, {"001": "generated", "002a": "stale", "002b": "needs_review"})
    assert done == ["001"]
    assert [store.unit(u).status for u in ("001", "002a", "002b")] == ["approved", "generated", "generated"]


def test_selecting_a_version(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version("001", 1), status="generated")
    store.add_version("001", version("001", 2), status="generated")
    select_version(store, "001", 1, unit_ids=IDS)
    assert store.unit("001").current_version == 1
    with pytest.raises(ActionError) as error:
        select_version(store, "001", 9, unit_ids=IDS)
    assert status_of(error) == 404


def test_the_plan_is_approved_only_without_errors(tmp_path):
    store = StateStore.load(tmp_path)
    with pytest.raises(ActionError) as error:
        approve_plan(store, ["scenes[0].shot: bad"])
    assert status_of(error) == 409 and not store.state.plan_approved
    approve_plan(store, [])
    assert StateStore.load(tmp_path).state.plan_approved


def test_tests_are_approved_only_once_there_are_test_units(tmp_path):
    store = StateStore.load(tmp_path)
    with pytest.raises(ActionError) as error:
        approve_tests(store)
    assert status_of(error) == 409 and "M7" in error.value.message
    store.state.test_units = ["001"]
    approve_tests(store)
    assert StateStore.load(tmp_path).state.tests_approved


def planned(tmp_path, plan_data):
    path = tmp_path / "plan.yaml"
    write_plan(path, to_document(parse_plan(plan_data)), expected_hash=None)
    path.write_text("# my notes\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    return path, load_plan(path).hash


def test_a_prompt_edit_is_written_and_locks_the_prompt(tmp_path, plan_data):
    path, loaded_hash = planned(tmp_path, plan_data)
    new_hash = edit_prompt(path, "002a", "A hand-written prompt.\r\nSecond line.", loaded_hash, library_ids=set())
    loaded = load_plan(path)
    unit = next(u for u in loaded.plan.units() if u.id == "002a")
    assert (unit.image_prompt, unit.prompt_locked) == ("A hand-written prompt.\nSecond line.", True)
    assert loaded.hash == new_hash and path.read_text(encoding="utf-8").startswith("# my notes\n")


def test_a_prompt_edit_after_the_file_changed_on_disk_gives_409_and_writes_nothing(tmp_path, plan_data):
    path, loaded_hash = planned(tmp_path, plan_data)
    path.write_text(path.read_text(encoding="utf-8") + "# edited elsewhere\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ActionError) as error:
        edit_prompt(path, "002a", "new", loaded_hash, library_ids=set())
    assert (status_of(error), error.value.message) == (409, PLAN_CHANGED)
    assert path.read_bytes() == before


@pytest.mark.parametrize(("unit_id", "prompt", "code"), [("009", "x", 404), ("001", "  ", 422)])
def test_a_prompt_edit_of_an_unknown_unit_or_an_empty_prompt_is_refused(tmp_path, plan_data, unit_id, prompt, code):
    path, loaded_hash = planned(tmp_path, plan_data)
    with pytest.raises(ActionError) as error:
        edit_prompt(path, unit_id, prompt, loaded_hash, library_ids=set())
    assert status_of(error) == code


def test_approving_an_anchor_candidate_from_the_page(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    record = Candidate(n=1, file="library/_bootstrap/v1/anchor/c1.png", seed=5,
                       model="@cf/black-forest-labs/flux-2-klein-9b", width=1024, height=768, prompt_sent="p",
                       est_cost_usd=0.015, latency_s=3.0, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    path = tmp_path / record.file
    path.parent.mkdir(parents=True)
    path.write_bytes(encode_png(Image.new("RGB", (1024, 768), "white"), record.model_dump(mode="json")))
    store.add("anchor", record)
    written = approve_sheet(tmp_path, "anchor", 1, settings=Settings(), style=load_style(tmp_path), mascot=load_mascot(tmp_path))
    assert written == ["library/style/anchor_v1.png", "library/style/anchor_v1_ref.png"]
    assert not (tmp_path / "library" / "_bootstrap" / "v1" / ".lock").exists()


def test_extras_sheets_come_later_and_unknown_candidates_are_refused(tmp_path):
    kwargs = dict(settings=Settings(), style=load_style(tmp_path), mascot=load_mascot(tmp_path))
    with pytest.raises(ActionError) as error:
        approve_sheet(tmp_path, "caveman_group", 1, **kwargs)
    assert status_of(error) == 400 and "M7" in error.value.message
    with pytest.raises(ActionError) as error:
        approve_sheet(tmp_path, "anchor", 3, **kwargs)
    assert status_of(error) == 409 and "no anchor candidate 3" in error.value.message
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/review/test_actions.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/stickman/review/actions.py`**

```python
"""What the review page changes (spec §12.2, §12.4, §12.5). Each action raises ActionError(status, message),
which the server sends as that HTTP status with the message; nothing is changed when one is raised."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from stickman.bootstrap.approve import ApprovalError, approve_anchor, approve_mascot
from stickman.bootstrap.store import BootstrapError, BootstrapStore, bootstrap_folder
from stickman.config_files import MascotConfig, StyleConfig
from stickman.plan.models import PlanValidationError
from stickman.plan.store import PlanChangedError, load_plan, update_unit, write_plan
from stickman.render.lock import LockHeld, ProjectLock
from stickman.render.state import StateStore
from stickman.settings import ConfigError, Settings

PLAN_CHANGED = "plan.yaml changed on disk since this page loaded — reload before saving"
PLAN_HAS_ERRORS = "plan.yaml has validation errors; fix them first."
EXTRAS_LATER = "Sheets for extras come in M7; until then they're drawn from their description."
TESTS_LATER = "There are no test units yet: M7 picks them."


class ActionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _unit(unit_id: str, unit_ids: Collection[str], busy: str | None) -> None:
    if unit_id not in unit_ids:
        raise ActionError(404, f"No unit {unit_id} in this plan.")
    if busy == unit_id:
        raise ActionError(409, f"{unit_id} is being regenerated; wait for it to finish.")


def approve_unit(store: StateStore, unit_id: str, *, unit_ids: Collection[str], busy: str | None = None) -> None:
    _unit(unit_id, unit_ids, busy)
    if store.unit(unit_id).current_version is None:
        raise ActionError(409, f"{unit_id} has no image to approve.")
    store.approve(unit_id)


def approve_remaining(store: StateStore, statuses: Mapping[str, str], *, busy: str | None = None) -> list[str]:
    """spec §12.2: every unit showing `generated`; needs_review, stale and failed units are skipped."""
    approved: list[str] = []
    for unit_id, status in statuses.items():
        if status == "generated" and unit_id != busy and store.unit(unit_id).current_version is not None:
            store.approve(unit_id)
            approved.append(unit_id)
    return approved


def select_version(
    store: StateStore, unit_id: str, v: int, *, unit_ids: Collection[str], busy: str | None = None
) -> None:
    _unit(unit_id, unit_ids, busy)
    try:
        store.select_version(unit_id, v)
    except KeyError:
        raise ActionError(404, f"{unit_id} has no version {v}.") from None


def approve_plan(store: StateStore, errors: Sequence[str]) -> None:
    if errors:
        raise ActionError(409, PLAN_HAS_ERRORS)
    store.state.plan_approved = True
    store.save()


def approve_tests(store: StateStore) -> None:
    if not store.state.test_units:
        raise ActionError(409, TESTS_LATER)
    store.state.tests_approved = True
    store.save()


def edit_prompt(plan_path: Path, unit_id: str, prompt: str, plan_hash: str, *, library_ids: Collection[str]) -> str:
    """spec §12.4: refused (409) when plan.yaml changed on disk since `plan_hash` was read; otherwise the
    prompt is written with ruamel (comments kept) and prompt_locked: true. Returns the new file hash."""
    text = prompt.replace("\r\n", "\n")
    if not text.strip():
        raise ActionError(422, "The prompt can't be empty.")
    try:
        loaded = load_plan(plan_path, library_ids=library_ids)
    except PlanValidationError:
        raise ActionError(409, PLAN_HAS_ERRORS) from None
    if loaded.hash != plan_hash:
        raise ActionError(409, PLAN_CHANGED)
    if unit_id not in {unit.id for unit in loaded.plan.units()}:
        raise ActionError(404, f"No unit {unit_id} in this plan.")
    update_unit(loaded.doc, unit_id, {"image_prompt": text, "prompt_locked": True})
    try:
        return write_plan(plan_path, loaded.doc, expected_hash=loaded.hash)
    except PlanChangedError:
        raise ActionError(409, PLAN_CHANGED) from None
    except PlanValidationError as exc:
        raise ActionError(422, "; ".join(exc.errors[:3])) from None


def approve_sheet(
    workspace: Path, char_id: str, n: int, *, settings: Settings, style: StyleConfig, mascot: MascotConfig
) -> list[str]:
    """The anchor or the mascot sheet (spec §8.1), under bootstrap's lock. Extras' sheets come in M7."""
    if char_id not in ("anchor", "mascot"):
        raise ActionError(400, EXTRAS_LATER)
    folder = bootstrap_folder(workspace, style.style_version)
    folder.mkdir(parents=True, exist_ok=True)
    lock = ProjectLock(folder)
    try:
        lock.acquire()
    except LockHeld as exc:
        raise ActionError(409, f"{exc}; try again when it finishes.") from None
    try:
        store = BootstrapStore.load(workspace, style.style_version)
        max_side = settings.image.ref_max_side
        if char_id == "anchor":
            written = approve_anchor(store, n, ref_max_side=max_side)
        else:
            written = approve_mascot(store, n, mascot=mascot, ref_max_side=max_side)
    except (ApprovalError, BootstrapError, ConfigError) as exc:
        raise ActionError(409, str(exc)) from None
    finally:
        lock.release()
    return [store.relative(path) for path in written]
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/review/test_actions.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/review/actions.py tests/review/test_actions.py
git commit -m "feat: the review page's actions: approve, approve remaining, pick a version, approve the plan and tests, hash-checked prompt edits, bootstrap approval" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Server-sent events and the file watcher

**Files:**
- Create: `src/stickman/review/events.py`, `src/stickman/review/watch.py`
- Test: `tests/review/test_events.py` (new)

**Interfaces:**
- Produces:
  - `stickman.review.events`:
    - `EventHub`, with `subscribe() -> asyncio.Queue`, `unsubscribe(queue)`, `publish(kind: str, data: dict | None = None)`;
    - `sse(kind, data) -> str`;
    - `async event_stream(hub, is_disconnected: Callable[[], Awaitable[bool]], *, keepalive_s=15.0) -> AsyncIterator[str]`.
  - `stickman.review.watch`:
    - `PlanWatcher(project_dir, hub)`, with `wrote(file_hash)`, `changed(paths: Iterable[Path | str]) -> list[str]` (the event kinds it published), and `async run(stop: asyncio.Event)`;
    - `DEBOUNCE_MS = 300`.
- Event kinds: `plan` (`plan.yaml` changed on disk, not by this server), `state` (`state.json` changed, or the server changed something), `job` (a page job's progress), `budget` (the 80% warning).

- [ ] **Step 1: Write the failing tests**

Create `tests/review/test_events.py`:

```python
import asyncio
import json

from stickman.plan.store import file_hash
from stickman.review.events import EventHub, event_stream, sse
from stickman.review.watch import PlanWatcher


def test_an_event_is_written_the_way_server_sent_events_are():
    assert sse("plan", {"hash": "sha256:1"}) == 'event: plan\ndata: {"hash": "sha256:1"}\n\n'


def test_every_subscriber_gets_each_event_until_it_unsubscribes():
    async def go():
        hub = EventHub()
        first, second = hub.subscribe(), hub.subscribe()
        hub.publish("state")
        hub.unsubscribe(second)
        hub.publish("job", {"state": "started"})
        return [first.get_nowait(), first.get_nowait()], second.qsize()

    got, left = asyncio.run(go())
    assert got == [("state", {}), ("job", {"state": "started"})] and left == 1


def test_a_full_queue_drops_events_instead_of_blocking():
    async def go():
        hub = EventHub()
        queue = hub.subscribe()
        for _ in range(500):
            hub.publish("state")
        return queue.qsize()

    assert asyncio.run(go()) == 100


def test_the_stream_sends_events_and_ends_when_the_page_goes_away():
    async def go():
        hub = EventHub()
        gone = False

        async def disconnected():
            return gone

        stream = event_stream(hub, disconnected, keepalive_s=0.01)
        chunks = [await anext(stream)]
        hub.publish("plan", {"hash": "h"})
        chunks.append(await anext(stream))
        chunks.append(await anext(stream))  # nothing happened: a keepalive comment
        gone = True
        rest = [chunk async for chunk in stream]
        return chunks, rest, hub

    chunks, rest, hub = asyncio.run(go())
    assert chunks[0].startswith("retry: ")
    assert chunks[1] == 'event: plan\ndata: {"hash": "h"}\n\n'
    assert chunks[2] == ": keepalive\n\n"
    assert rest == [] and hub._queues == set()


def test_a_plan_change_by_someone_else_sends_a_plan_event(tmp_path):
    hub = EventHub()
    queue = asyncio.run(_subscribe(hub))
    (tmp_path / "plan.yaml").write_text("a: 1\n", encoding="utf-8")
    watcher = PlanWatcher(tmp_path, hub)
    (tmp_path / "plan.yaml").write_text("a: 2\n", encoding="utf-8")
    assert watcher.changed([tmp_path / "plan.yaml"]) == ["plan"]
    assert queue.get_nowait() == ("plan", {"hash": file_hash(b"a: 2\n")})


def test_the_servers_own_plan_write_is_ignored(tmp_path):
    hub = EventHub()
    (tmp_path / "plan.yaml").write_text("a: 1\n", encoding="utf-8")
    watcher = PlanWatcher(tmp_path, hub)
    (tmp_path / "plan.yaml").write_text("a: 2\n", encoding="utf-8")
    watcher.wrote(file_hash(b"a: 2\n"))
    assert watcher.changed([tmp_path / "plan.yaml"]) == []
    assert watcher.changed([tmp_path / "plan.yaml"]) == []  # the same content again: still nothing new


def test_a_state_change_sends_a_state_event_and_other_files_nothing(tmp_path):
    watcher = PlanWatcher(tmp_path, EventHub())
    assert watcher.changed([tmp_path / "state.json", tmp_path / "state.json.tmp", tmp_path / ".lock"]) == ["state"]


async def _subscribe(hub):
    return hub.subscribe()
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/review/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/stickman/review/events.py`**

```python
"""Server-sent events for the review page (spec §12.1): plan reloads, state changes, job progress and budget
warnings. The page refetches GET /api/project on each; an event only says that something changed."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

QUEUE_SIZE = 100  # a page that stops reading loses events, not the server's memory; it refetches anyway
RETRY_MS = 2000  # how soon the browser reconnects after the server restarts


class EventHub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = set()

    def subscribe(self) -> asyncio.Queue[tuple[str, dict[str, Any]]]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[tuple[str, dict[str, Any]]]) -> None:
        self._queues.discard(queue)

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> None:
        for queue in list(self._queues):
            try:
                queue.put_nowait((kind, data or {}))
            except asyncio.QueueFull:
                pass


def sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def event_stream(
    hub: EventHub, is_disconnected: Callable[[], Awaitable[bool]], *, keepalive_s: float = 15.0
) -> AsyncIterator[str]:
    queue = hub.subscribe()
    try:
        yield f"retry: {RETRY_MS}\n\n"
        while not await is_disconnected():
            try:
                kind, data = await asyncio.wait_for(queue.get(), keepalive_s)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield sse(kind, data)
    finally:
        hub.unsubscribe(queue)
```

- [ ] **Step 4: Create `src/stickman/review/watch.py`**

```python
"""Watching the project folder for the review page (spec §12.4): a saved plan.yaml sends a `plan` event and
a changed state.json a `state` event, within DEBOUNCE_MS. After the server writes plan.yaml it records the
new hash, and a change with that hash is ignored."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path

from watchfiles import awatch

from stickman.plan.store import file_hash
from stickman.review.events import EventHub

DEBOUNCE_MS = 300


class PlanWatcher:
    def __init__(self, project_dir: Path, hub: EventHub) -> None:
        self._project = project_dir
        self._hub = hub
        self._own: set[str] = set()
        self._last = self._plan_hash()

    def _plan_hash(self) -> str | None:
        try:
            return file_hash((self._project / "plan.yaml").read_bytes())
        except OSError:
            return None

    def wrote(self, written_hash: str) -> None:
        """This server wrote plan.yaml with this content: its change isn't news to the page."""
        self._own.add(written_hash)

    def changed(self, paths: Iterable[Path | str]) -> list[str]:
        names = {Path(path).name for path in paths}
        kinds: list[str] = []
        if "plan.yaml" in names:
            current = self._plan_hash()
            if current != self._last:
                self._last = current
                if current not in self._own:
                    self._hub.publish("plan", {"hash": current})
                    kinds.append("plan")
        if "state.json" in names:
            self._hub.publish("state", {})
            kinds.append("state")
        return kinds

    async def run(self, stop: asyncio.Event) -> None:
        async for changes in awatch(self._project, recursive=False, debounce=DEBOUNCE_MS, stop_event=stop):
            self.changed(path for _, path in changes)
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `uv run pytest tests/review/test_events.py -q`
Expected: PASS. If `test_the_stream_sends_events_and_ends_when_the_page_goes_away` hangs, check that the generator re-checks `is_disconnected()` after each keepalive, which is how the loop above ends.

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/review/events.py src/stickman/review/watch.py tests/review/test_events.py
git commit -m "feat: server-sent events for the review page, and a watcher that ignores the server's own plan.yaml writes" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Sharing the project with a running job, and the page's jobs

**Files:**
- Create: `src/stickman/review/access.py`, `src/stickman/review/jobs.py`
- Modify: `src/stickman/plan/refresh.py` (`refresh_plan_file`), `src/stickman/cli.py` (`_refresh_prompts` uses it; `BOOTSTRAP_PROJECT` moves), `src/stickman/bootstrap/generate.py` (`BOOTSTRAP_PROJECT`)
- Test: `tests/review/test_jobs.py` (new), `tests/plan/test_refresh.py`

**Interfaces:**
- Consumes:
  - `StateStore.begin_regeneration`, `Renderer.run(fresh=…)` (Task 2); `EventHub` (Task 5);
  - `Calls`, `RunControl`, `GuardedChat`, `Renderer`, `PlanRewriter`, `StageRunner`, `replan_unit`, `REPLAN_KEYS`, `load_planning_context`, `PlanningContext`;
  - `CandidateMaker`, `step_plan`, `pending_step`, `mascot_version_mismatch`, `bootstrap_folder`, `BootstrapStore`;
  - `recover`, `JobBuilder`, `RenderContext`, `find_references`, `Budget`, `Ledger`, `Meter`, `unit_scope`, `RunLog`, `PAUSE_NAMES` (`render/summary.py`), `ProjectLock`, `LockHeld`.
- Produces:
  - `stickman.plan.refresh.refresh_plan_file(path, loaded, *, style, mascot, references) -> tuple[Plan, PromptRefresh, str | None]`: the plan as it now is, the refresh, and the new file hash (None when nothing was written). It raises `PlanChangedError`.
  - `stickman.bootstrap.generate.BOOTSTRAP_PROJECT = "bootstrap"`, moved from `cli.py`, which imports it.
  - `stickman.review.access`:
    - `Busy(Exception)`;
    - `JobInfo(kind, unit, message="")`, with `describe()` and `as_dict()`;
    - `ProjectAccess(project_dir)`, with:
      - `.job: JobInfo | None` and `.store: StateStore | None`;
      - `claim(kind, unit, *, project=True) -> JobInfo`, which raises `Busy`, and `release()`;
      - `state()`, a context manager yielding the `StateStore` to change;
      - `read() -> ProjectState`;
      - `busy_unit -> str | None`, the unit a regeneration is working on.
  - `stickman.review.jobs.Jobs(workspace, project_dir, settings, access, hub, *, client_factory, secrets=(), on_plan_written=lambda h: None, sleep=asyncio.sleep)`, with:
    - `regenerate(unit_id) -> JobInfo`, `replan(unit_id, hint) -> JobInfo` and `more_candidates(step) -> JobInfo`. Each claims synchronously (raising `Busy`), then runs in the background.
    - `async wait()`: for tests.

- [ ] **Step 1: Write the failing tests**

Add to `tests/plan/test_refresh.py`:

```python
def test_refresh_plan_file_writes_the_rebuilt_prompts_hash_checked(tmp_path, plan_data, built_prompts):
    from stickman.plan.refresh import refresh_plan_file
    from stickman.plan.store import PlanChangedError, load_plan, to_document, write_plan

    path = tmp_path / "plan.yaml"
    write_plan(path, to_document(parse_plan(built_prompts(plan_data))), expected_hash=None)
    loaded = load_plan(path)
    plan, result, new_hash = refresh_plan_file(path, loaded, style=load_style(tmp_path), mascot=load_mascot(tmp_path),
                                               references=ANCHOR_AND_MASCOT)
    assert list(result.rebuilt) == ["001", "002a", "002b"] and new_hash == load_plan(path).hash
    assert all("Reference images:" in unit.image_prompt for unit in plan.units())
    again = load_plan(path)
    assert refresh_plan_file(path, again, style=load_style(tmp_path), mascot=load_mascot(tmp_path),
                             references=ANCHOR_AND_MASCOT)[2] is None  # nothing to rebuild, nothing written
    stale = load_plan(path)
    path.write_text(path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(PlanChangedError):
        refresh_plan_file(path, stale, style=load_style(tmp_path), mascot=load_mascot(tmp_path), references=NONE)
```

(Add `import pytest` to that file if it's missing.)

Create `tests/review/test_jobs.py`:

```python
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from stickman.bootstrap.store import BootstrapStore
from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.render.images import encode_png
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.state import StateStore, Version
from stickman.review.access import Busy, ProjectAccess
from stickman.review.events import EventHub
from stickman.review.jobs import Jobs
from stickman.settings import ConfigError, QCSettings, Settings

PK = timezone(timedelta(hours=5))
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


async def no_sleep(seconds):
    return None


class Page:
    """A project with an approved image for every unit, and the page's jobs over it."""

    def __init__(self, tmp_path, plan_data, drawings, client, *, factory=None):
        self.workspace = tmp_path
        self.project = tmp_path / "projects" / FOLDER
        self.project.mkdir(parents=True)
        write_plan(self.project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
        plan = parse_plan(plan_data)
        builder = JobBuilder(RenderContext.load(tmp_path, Settings()), plan)
        store = StateStore.load(self.project)
        image = drawings.clean()
        good = decide(pixel_check(image, QCSettings()), expected_figures=1, min_idea_score=3)
        for unit in plan.units():
            record = Version(v=1, file=f"images/_history/{unit.id}_v1.png", seed=1, model=KLEIN_4B, width=960,
                             height=544, fingerprint=builder.fingerprint(unit), prompt_sent=unit.image_prompt,
                             qc=good, est_cost_usd=0.002, latency_s=1.0, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
            (self.project / "images" / "_history").mkdir(parents=True, exist_ok=True)
            (self.project / record.file).write_bytes(encode_png(image, record.model_dump(mode="json")))
            store.add_version(unit.id, record, status="generated")
            store.approve(unit.id)
        self.client = client
        self.hub = EventHub()
        self.access = ProjectAccess(self.project)
        self.written = []
        self.jobs = Jobs(tmp_path, self.project, Settings(), self.access, self.hub,
                         client_factory=factory or (lambda: client), secrets=("tok-secret",),
                         on_plan_written=self.written.append, sleep=no_sleep)

    def run(self, start):
        """Start a job inside a running loop and wait for it, collecting the events it published."""
        async def go():
            queue = self.hub.subscribe()
            info = start()
            await self.jobs.wait()
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
            return info, events

        return asyncio.run(go())

    def unit(self, unit_id):
        return StateStore.load(self.project).unit(unit_id)
```

Continue the file with:

```python
def job_events(events):
    return [(data["kind"], data["state"]) for kind, data in events if kind == "job"]


def test_regenerating_makes_a_new_image_beside_the_old_one(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    info, events = page.run(lambda: page.jobs.regenerate("001"))
    unit = page.unit("001")
    assert (info.kind, info.unit) == ("regenerate", "001")
    assert [v.v for v in unit.versions] == [1, 2]
    assert (unit.current_version, unit.compare_with, unit.approved_version, unit.status) == (2, 1, None, "generated")
    assert job_events(events) == [("regenerate", "started"), ("regenerate", "finished")]
    assert page.access.job is None and not (page.project / ".lock").exists()
    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["kind"] for e in entries} == {"image", "vision"} and {e["unit"] for e in entries} == {"001"}


def test_one_page_job_at_a_time(tmp_path, plan_data, drawings, fake_images, jpeg):
    gate = asyncio.Event()

    async def slow(call):
        await gate.wait()
        return jpeg

    page = Page(tmp_path, plan_data, drawings, fake_images(lambda call: slow(call)))

    async def go():
        page.jobs.regenerate("001")
        with pytest.raises(Busy, match="already regenerating 001"):
            page.jobs.regenerate("002a")
        gate.set()
        await page.jobs.wait()

    asyncio.run(go())
    assert page.unit("002a").versions[-1].v == 1


def test_a_cli_run_holding_the_lock_makes_the_page_wait(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    (page.project / ".lock").write_text(str(os.getppid()), encoding="ascii")  # a live process that isn't us
    with pytest.raises(Busy, match="another stickman process"):
        page.jobs.regenerate("001")
    assert page.unit("001").status == "approved"


def test_without_credentials_a_regeneration_fails_before_changing_anything(tmp_path, plan_data, drawings, fake_images):
    def no_credentials():
        raise ConfigError("Cloudflare credentials are not loaded (CF_ACCOUNT_ID and CF_API_TOKEN in .env)")

    page = Page(tmp_path, plan_data, drawings, fake_images(), factory=no_credentials)
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "failed"]
    assert "CF_API_TOKEN" in data["message"]
    unit = page.unit("001")
    assert (unit.status, unit.compare_with, unit.approved_version) == ("approved", None, 1)


def test_a_daily_limit_pauses_the_job_with_a_masked_message(tmp_path, plan_data, drawings, fake_images):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation for tok-secret", status=429)
    page = Page(tmp_path, plan_data, drawings, fake_images([daily]))
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "paused"]
    assert "daily limit" in data["message"] and "tok-secret" not in data["message"]
    assert page.unit("001").status == "planned"


def test_replanning_with_a_hint_writes_plan_yaml_and_records_the_hash(tmp_path, plan_data, drawings, fake_images, sample_reply):
    client = fake_images(chat=sample_reply)  # the planner's describe replies (see conftest)
    page = Page(tmp_path, plan_data, drawings, client)
    _, events = page.run(lambda: page.jobs.replan("001", "show the moon"))
    loaded = load_plan(page.project / "plan.yaml")
    unit = next(u for u in loaded.plan.units() if u.id == "001")
    assert unit.visual_idea == "Idea for 001"
    assert page.written == [loaded.hash]
    assert job_events(events)[-1] == ("replan", "finished")
    [call] = client.chat_calls
    assert "show the moon" in call["messages"][1]["content"]


def test_more_anchor_candidates_are_made_under_the_bootstrap_lock(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    _, events = page.run(lambda: page.jobs.more_candidates("anchor"))
    store = BootstrapStore.load(tmp_path, 1)
    assert [c.n for c in store.state.anchor.candidates] == [1, 2]
    assert job_events(events)[0] == ("candidates", "started") and job_events(events)[-1] == ("candidates", "finished")
    assert not (tmp_path / "library" / "_bootstrap" / "v1" / ".lock").exists()
    assert page.access.job is None


def test_more_candidates_for_a_step_bootstrap_isnt_on_fails(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    _, events = page.run(lambda: page.jobs.more_candidates("mascot"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "failed"]
    assert "anchor" in data["message"] and page.client.calls == []


def test_page_actions_use_the_jobs_store_while_it_runs(tmp_path, plan_data, drawings, fake_images, jpeg):
    gate = asyncio.Event()

    async def slow(call):
        await gate.wait()
        return jpeg

    page = Page(tmp_path, plan_data, drawings, fake_images(lambda call: slow(call)))

    async def go():
        page.jobs.regenerate("001")
        await asyncio.sleep(0)
        assert page.access.busy_unit == "001"
        with page.access.state() as store:
            assert store is page.access.store
            store.state.plan_approved = True
            store.save()
        gate.set()
        await page.jobs.wait()

    asyncio.run(go())
    assert StateStore.load(page.project).state.plan_approved  # not lost when the job saved after it
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/review/test_jobs.py tests/plan/test_refresh.py -q`
Expected: FAIL (missing modules and `refresh_plan_file`).

- [ ] **Step 3: Add `refresh_plan_file` to `src/stickman/plan/refresh.py`, and use it in `cli.py`**

```python
def refresh_plan_file(
    path: Path, loaded: LoadedPlan, *, style: StyleConfig, mascot: MascotConfig, references: ReferenceAvailability
) -> tuple[Plan, PromptRefresh, str | None]:
    """refresh_prompts, with the rebuilt prompts written to plan.yaml hash-checked (comments kept). Returns the
    plan as it now is, the refresh, and the new file hash, None when nothing was written. PlanChangedError when
    plan.yaml changed on disk since `loaded` was read; nothing is written then."""
    refresh = refresh_prompts(loaded.plan, style=style, mascot=mascot, references=references)
    if not refresh.rebuilt:
        return loaded.plan, refresh, None
    for unit_id, prompt in refresh.rebuilt.items():
        update_unit(loaded.doc, unit_id, {"image_prompt": prompt})
    new_hash = write_plan(path, loaded.doc, expected_hash=loaded.hash)
    return with_prompts(loaded.plan, refresh.rebuilt), refresh, new_hash
```

(Add the imports: `from pathlib import Path`, and `from stickman.plan.store import LoadedPlan, update_unit, write_plan`.)

In `cli._refresh_prompts`, replace the rebuild-and-write part with a call to `refresh_plan_file`. Keep:
- the hand-edited warning;
- the "Rebuilt the image prompts …" line;
- the exact failure messages: catch `PlanChangedError`, `PlanValidationError` and `OSError` around the call, and `_fail` with the same texts `_write_plan` uses for `during="the prompts were being rebuilt"`.

`tests/test_cli_generate.py` must pass unchanged.

Move `BOOTSTRAP_PROJECT = "bootstrap"` to `src/stickman/bootstrap/generate.py`, with its comment, and import it in `cli.py`.

- [ ] **Step 4: Create `src/stickman/review/access.py`**

```python
"""How the review server shares a project (spec §3, §12.1). One page job runs at a time and holds the
project's .lock for its whole length. While it runs, page actions change the job's own StateStore, so neither
overwrites the other. Otherwise an action reads state.json fresh and takes the lock just for the write, so a
CLI run is never overwritten: a lock another process holds is Busy (HTTP 409)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stickman.render.lock import LockHeld, ProjectLock
from stickman.render.state import ProjectState, StateStore

VERBS = {"regenerate": "regenerating", "replan": "replanning", "candidates": "making candidates for"}


class Busy(Exception):
    """Another page job is running, or another process holds the lock (HTTP 409)."""


@dataclass
class JobInfo:
    kind: str  # regenerate | replan | candidates
    unit: str | None  # the unit, or the bootstrap step for candidates
    message: str = ""

    def describe(self) -> str:
        return f"{VERBS[self.kind]} {self.unit}" if self.unit else VERBS[self.kind]

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "unit": self.unit, "message": self.message}


class ProjectAccess:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.job: JobInfo | None = None
        self.store: StateStore | None = None  # the running job's store, which page actions share
        self._held: ProjectLock | None = None

    @property
    def busy_unit(self) -> str | None:
        return self.job.unit if self.job is not None and self.job.kind == "regenerate" else None

    def _lock(self) -> ProjectLock:
        lock = ProjectLock(self.project_dir)
        try:
            lock.acquire()
        except LockHeld as exc:
            raise Busy(f"{exc}; try again when it finishes") from None
        return lock

    def claim(self, kind: str, unit: str | None, *, project: bool = True) -> JobInfo:
        """Start a job now, or raise Busy. `project`: hold the project's lock (and share its store) until release()."""
        if self.job is not None:
            raise Busy(f"the page is already {self.job.describe()}; wait for it to finish")
        if project:
            lock = self._lock()
            try:
                self.store = StateStore.load(self.project_dir)
            except Exception:
                lock.release()
                raise
            self._held = lock
        self.job = JobInfo(kind, unit)
        return self.job

    def release(self) -> None:
        self.job = None
        self.store = None
        if self._held is not None:
            self._held.release()
            self._held = None

    @contextmanager
    def state(self) -> Iterator[StateStore]:
        """The StateStore a page action changes. Actions are synchronous, so nothing else runs meanwhile."""
        if self.store is not None:
            yield self.store
            return
        lock = self._lock()
        try:
            yield StateStore.load(self.project_dir)
        finally:
            lock.release()

    def read(self) -> ProjectState:
        return self.store.state if self.store is not None else StateStore.load(self.project_dir).state
```

- [ ] **Step 5: Create `src/stickman/review/jobs.py`**

```python
"""The review page's jobs (spec §12.1): regenerate a unit, replan one with a hint, make more bootstrap
candidates. They run in the server process with the renderer, planner, budget, ledger and lock the CLI uses,
one at a time (ProjectAccess), and report through `job` events: started, progress, then finished, paused or
failed with a message. Messages are masked."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from stickman.bootstrap.approve import mascot_version_mismatch
from stickman.bootstrap.generate import BOOTSTRAP_PROJECT, CandidateMaker, step_plan
from stickman.bootstrap.store import BootstrapStore, Step, bootstrap_folder, pending_step
from stickman.budget import Budget
from stickman.cf.errors import CFError
from stickman.config_files import load_mascot, load_style
from stickman.ledger import LEDGER_FILE, Ledger
from stickman.library import find_references
from stickman.meter import Meter, unit_scope
from stickman.plan.llm import PlanningError, StageRunner
from stickman.plan.models import PlanValidationError
from stickman.plan.planner import REPLAN_KEYS, PlanningContext, load_planning_context, replan_unit
from stickman.plan.refresh import refresh_plan_file
from stickman.plan.store import PlanChangedError, load_plan, update_unit, write_plan
from stickman.pricing import load_pricing
from stickman.render.calls import Calls
from stickman.render.jobs import JobBuilder, JobError, RenderContext
from stickman.render.lock import LockHeld, ProjectLock
from stickman.render.recovery import recover
from stickman.render.references import ReferenceFiles
from stickman.render.renderer import GuardedChat, Renderer, RunControl
from stickman.render.rewrite import PlanRewriter
from stickman.render.summary import PAUSE_NAMES
from stickman.review.access import JobInfo, ProjectAccess
from stickman.review.events import EventHub
from stickman.runlog import RunLog, mask
from stickman.settings import ConfigError, Settings

Outcome = tuple[str, str]  # (message, state): finished | paused | failed


class Jobs:
    def __init__(
        self,
        workspace: Path,
        project_dir: Path,
        settings: Settings,
        access: ProjectAccess,
        hub: EventHub,
        *,
        client_factory: Callable[[], Any],
        secrets: Sequence[str] = (),
        on_plan_written: Callable[[str], None] = lambda written_hash: None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._workspace = workspace
        self._project = project_dir
        self._plan_path = project_dir / "plan.yaml"
        self._settings = settings
        self._access = access
        self._hub = hub
        self._client_factory = client_factory
        self._secrets = tuple(secrets)
        self._on_plan_written = on_plan_written
        self._sleep = sleep
        self._tasks: set[asyncio.Task[None]] = set()

    # --- starting jobs: the claim is made now (Busy -> HTTP 409), the work runs in the background ---

    def regenerate(self, unit_id: str) -> JobInfo:
        info = self._access.claim("regenerate", unit_id)
        self._spawn(info, self._regenerate(unit_id))
        return info

    def replan(self, unit_id: str, hint: str) -> JobInfo:
        info = self._access.claim("replan", unit_id)
        self._spawn(info, self._replan(unit_id, hint))
        return info

    def more_candidates(self, step: Step) -> JobInfo:
        info = self._access.claim("candidates", step, project=False)
        self._spawn(info, self._more_candidates(step))
        return info

    async def wait(self) -> None:
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _spawn(self, info: JobInfo, work: Coroutine[Any, Any, Outcome]) -> None:
        async def run() -> None:
            self._publish(info, "started")
            try:
                message, state = await work
            except Exception as exc:  # an unexpected error must not leave the job slot claimed
                message, state = f"{type(exc).__name__}: {exc}", "failed"
            finally:
                self._access.release()
            info.message = mask(message, self._secrets)
            self._publish(info, state)
            self._hub.publish("state", {})

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _publish(self, info: JobInfo, state: str) -> None:
        self._hub.publish("job", {**info.as_dict(), "state": state})

    def _meter(self, pricing: Any, *, budget: bool, project: str) -> Meter:
        ledger = Ledger(self._workspace / LEDGER_FILE)
        spend = None
        if budget:
            spend = Budget.from_ledger(
                ledger, self._settings.budget, now=datetime.now().astimezone(),
                warn=lambda message: self._hub.publish("budget", {"message": message}),
            )
        return Meter(project=project, ledger=ledger, budget=spend, pricing=pricing)

    # --- the jobs ---

    async def _regenerate(self, unit_id: str) -> Outcome:
        """A new chain for the unit (spec §12.2 [M6]); its old current version is kept to compare with."""
        store = self._access.store
        assert store is not None
        settings = self._settings
        try:
            ctx = RenderContext.load(self._workspace, settings)
            loaded = load_plan(self._plan_path, library_ids=ctx.library_ids)
            client = self._client_factory()  # before any change: no credentials means nothing happens
        except (ConfigError, PlanValidationError, OSError) as exc:
            return f"Can't regenerate {unit_id}: {exc}", "failed"
        if unit_id not in {unit.id for unit in loaded.plan.units()}:
            return f"No unit {unit_id} in this plan.", "failed"
        references = find_references(
            self._workspace, use_references=settings.image.use_references, style_version=loaded.plan.style_version,
            mascot=ctx.mascot, cast=loaded.plan.cast, library=ctx.library,
        )
        try:
            plan, _, written = refresh_plan_file(self._plan_path, loaded, style=ctx.style, mascot=ctx.mascot, references=references)
        except PlanChangedError:
            return "plan.yaml changed on disk while the prompts were being rebuilt; try again.", "failed"
        if written is not None:
            self._on_plan_written(written)
        try:
            builder = JobBuilder(ctx, plan)
            expected = builder.expected()
            job = builder.job(next(unit for unit in plan.units() if unit.id == unit_id))
        except (ConfigError, JobError) as exc:
            return f"Can't regenerate {unit_id}: {exc}", "failed"
        recover(store, expected)
        store.begin_regeneration(unit_id)
        meter = self._meter(ctx.pricing, budget=True, project=self._project.name)
        log = RunLog.for_project(self._project, secrets=self._secrets)
        async with client:
            control = RunControl(settings.retry.circuit_breaker)
            runner = StageRunner(
                GuardedChat(client, control), settings.llm, settings.retry, cache_dir=self._project / ".cache" / "llm",
                log=log, meter=meter, sleep=self._sleep,
            )
            planning = PlanningContext(ctx.workspace, ctx.settings, ctx.style, ctx.mascot, ctx.rules, ctx.library)
            renderer = Renderer(
                client, store, meter, builder, qc=settings.qc, vision_model=settings.llm.vision_model,
                retry=settings.retry, concurrency=1, log=log, rewriter=PlanRewriter(runner, planning, self._plan_path),
                sleep=self._sleep, control=control,
            )
            result = await renderer.run([job], fresh={unit_id})
        if result.stop is not None:
            detail = f": {result.detail}" if result.detail else ""
            return f"Stopped ({PAUSE_NAMES[result.stop]}){detail}. Finished images are kept.", "paused"
        unit = store.unit(unit_id)
        return f"{unit_id} is {unit.status.replace('_', ' ')}" + (f": {unit.error}" if unit.error else "."), "finished"

    async def _replan(self, unit_id: str, hint: str) -> Outcome:
        """Stage 3 again for one unit with the hint (spec §6.5), written to plan.yaml hash-checked. Planning is
        ledgered but never stopped by the budget (spec §9.7 [M3])."""
        settings = self._settings
        try:
            planning = load_planning_context(self._workspace, settings)
            loaded = load_plan(self._plan_path, library_ids=planning.library_ids)
            pricing = load_pricing(self._workspace)
            client = self._client_factory()
        except (ConfigError, PlanValidationError, OSError) as exc:
            return f"Can't replan {unit_id}: {exc}", "failed"
        if unit_id not in {unit.id for unit in loaded.plan.units()}:
            return f"No unit {unit_id} in this plan.", "failed"
        meter = self._meter(pricing, budget=False, project=self._project.name)
        log = RunLog.for_project(self._project, secrets=self._secrets)
        try:
            async with client:
                runner = StageRunner(
                    client, settings.llm, settings.retry, cache_dir=self._project / ".cache" / "llm", log=log,
                    meter=meter, sleep=self._sleep,
                )
                with unit_scope(unit_id):
                    updated = await replan_unit(runner, planning, loaded.plan, unit_id, hint=hint.strip() or None)
        except PlanningError as exc:
            return f"Replanning {unit_id} failed: {exc}", "failed"
        except CFError as exc:
            return f"Replanning {unit_id} failed: {exc.category}: {exc.message}", "failed"
        fields = {key: value for key, value in updated.model_dump(mode="json").items() if key in REPLAN_KEYS}
        update_unit(loaded.doc, unit_id, fields)
        try:
            written = write_plan(self._plan_path, loaded.doc, expected_hash=loaded.hash)
        except PlanChangedError:
            return "plan.yaml changed on disk while the LLM was working; nothing was written.", "failed"
        self._on_plan_written(written)
        return f"Replanned {unit_id}: {updated.visual_idea}", "finished"

    async def _more_candidates(self, step: Step) -> Outcome:
        """Two more candidates for bootstrap's current step (spec §8.1), under bootstrap's lock."""
        settings = self._settings
        try:
            style, mascot, pricing = load_style(self._workspace), load_mascot(self._workspace), load_pricing(self._workspace)
            client = self._client_factory()
        except ConfigError as exc:
            return f"Can't make candidates: {exc}", "failed"
        pending = pending_step(self._workspace, mascot, style.style_version)
        if pending != step:
            where = "complete" if pending is None else f"on the {pending} step"
            return f"Bootstrap is {where}, so no {step} candidates are made.", "failed"
        if step == "mascot" and (mismatch := mascot_version_mismatch(mascot, style.style_version)) is not None:
            return mismatch, "failed"
        folder = bootstrap_folder(self._workspace, style.style_version)
        folder.mkdir(parents=True, exist_ok=True)
        lock = ProjectLock(folder)
        try:
            lock.acquire()
        except LockHeld as exc:
            return f"{exc}; try again when it finishes.", "failed"
        try:
            store = BootstrapStore.load(self._workspace, style.style_version)
            store.recover()
            plan = step_plan(step, workspace=self._workspace, settings=settings, style=style, mascot=mascot,
                             pricing=pricing, files=ReferenceFiles(self._workspace, ref_max_side=settings.image.ref_max_side))
            jobs = plan.jobs(store, 2)
            meter = self._meter(pricing, budget=True, project=BOOTSTRAP_PROJECT)
            log = RunLog.for_project(folder, secrets=self._secrets)
            info = self._access.job
            async with client:
                calls = Calls(client, meter, log, RunControl(settings.retry.circuit_breaker), retry=settings.retry,
                              qc=settings.qc, vision_model=settings.llm.vision_model, sleep=self._sleep)
                maker = CandidateMaker(calls, store, concurrency=settings.render.concurrency,
                                       on_done=lambda: self._publish(info, "progress") if info else None)
                result = await maker.run(plan, jobs)
        finally:
            lock.release()
        if result.stop is not None:
            return f"Stopped ({PAUSE_NAMES[result.stop]}). Finished candidates are kept.", "paused"
        errors = "; ".join(f"{label}: {error}" for label, error in maker.errors.items())
        made = len(jobs) - sum(1 for job in jobs if job.label in maker.errors)
        return f"Made {made} {step} candidate(s)." + (f" {errors}" if errors else ""), "finished"
```

Check each name against the real modules before you run anything:
- `PAUSE_NAMES` in `render/summary.py`;
- `mascot_version_mismatch` in `bootstrap/approve.py`;
- `StepPlan.jobs`, and `CandidateJob.label` in `bootstrap/generate.py`;
- `bootstrap_folder` and `pending_step` in `bootstrap/store.py`.

If a signature differs (the M5 fix wave changed a few), adapt the call and keep the behaviour.

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/review/test_jobs.py tests/plan/test_refresh.py tests/test_cli_generate.py tests/test_cli_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/review/access.py src/stickman/review/jobs.py src/stickman/plan/refresh.py src/stickman/cli.py src/stickman/bootstrap/generate.py tests/review/test_jobs.py tests/plan/test_refresh.py
git commit -m "feat: the review page's jobs (regenerate, replan with a hint, more bootstrap candidates), one at a time, sharing state with page actions" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The review server, `review/app.py`

**Files:**
- Create: `src/stickman/review/app.py`, and a placeholder `src/stickman/review/static/index.html` that Task 8 replaces
- Test: `tests/review/test_app.py` (new)

**Interfaces:**
- Consumes: Tasks 3–6.
- Produces (`stickman.review.app`):
  - `STATIC: Path`, the `static/` folder.
  - `PlanSource(path)`, with `load(library_ids) -> tuple[Plan | None, str | None, list[str]]`: the last valid plan, the current file's hash, and its errors.
  - `create_app(workspace, project_dir, settings, *, client_factory, secrets=(), watch=True, allowed_hosts=None) -> FastAPI`, with the objects on `app.state`: `hub`, `access`, `jobs`, `watcher`.
- **Routes** (spec §12.5). Every `POST`/`PUT` returns the fresh `GET /api/project` data, except the three job routes, which return 202 `{"job": {...}}`.

  | Route | What it does |
  |---|---|
  | `GET /` | the page |
  | `GET /static/…` | its files |
  | `GET /api/project` | the page's data |
  | `GET /api/events` | server-sent events |
  | `GET /files/<path>` | images |
  | `POST /api/plan/approve` | approve the plan |
  | `POST /api/tests/approve` | approve the tests |
  | `POST /api/sheets/{char_id}/approve` | body `{"candidate": N}` |
  | `POST /api/sheets/{char_id}/regenerate` | 202, a job |
  | `POST /api/units/approve-remaining` | approve every generated unit |
  | `POST /api/units/{id}/approve` | approve one unit |
  | `POST /api/units/{id}/regenerate` | 202, a job |
  | `POST /api/units/{id}/select-version` | body `{"v": N}` |
  | `PUT /api/units/{id}/prompt` | body `{"prompt": "…", "plan_hash": "sha256:…"}`; 409 on a hash mismatch |
  | `POST /api/units/{id}/replan` | body `{"hint": "…"}`; 202, a job |

- **Errors:** `{"error": "<message>"}` with the `ActionError`'s status. `Busy` → 409. A bad `Host` → 400. A `POST`/`PUT` without `X-Stickman: 1` → 403.

- [ ] **Step 1: Write the failing tests**

Create `src/stickman/review/static/index.html` with just `<!doctype html><title>stickman review</title>` for now. Task 8 writes the real page.

Create `tests/review/test_app.py`:

```python
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.render.images import encode_png
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.state import StateStore, Version
from stickman.review.actions import PLAN_CHANGED
from stickman.review.app import create_app
from stickman.settings import QCSettings, Settings

PK = timezone(timedelta(hours=5))
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
WRITE = {"X-Stickman": "1"}


@pytest.fixture
def site(tmp_path, plan_data, drawings, fake_images):
    """A project with an image for 001 and 002a (002b has none), and a TestClient over its review app."""
    project = tmp_path / "projects" / FOLDER
    project.mkdir(parents=True)
    write_plan(project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    plan = parse_plan(plan_data)
    builder = JobBuilder(RenderContext.load(tmp_path, Settings()), plan)
    store = StateStore.load(project)
    image = drawings.clean()
    good = decide(pixel_check(image, QCSettings()), expected_figures=1, min_idea_score=3)
    for unit in plan.units()[:2]:
        record = Version(v=1, file=f"images/_history/{unit.id}_v1.png", seed=1, model=KLEIN_4B, width=960,
                         height=544, fingerprint=builder.fingerprint(unit), prompt_sent=unit.image_prompt, qc=good,
                         est_cost_usd=0.002, latency_s=1.0, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
        (project / "images" / "_history").mkdir(parents=True, exist_ok=True)
        (project / record.file).write_bytes(encode_png(image, record.model_dump(mode="json")))
        store.add_version(unit.id, record, status="generated")
    images = fake_images()
    app = create_app(tmp_path, project, Settings(), client_factory=lambda: images, secrets=("tok-secret",),
                     watch=False, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        client.project = project
        client.images = images
        yield client


def state(site):
    return StateStore.load(site.project).state


def wait_for_job(site, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        view = site.get("/api/project").json()
        if view["job"] is None:
            return view
        time.sleep(0.02)
    raise AssertionError("the job didn't finish")


def test_the_page_and_its_data_are_served(site):
    assert site.get("/").status_code == 200
    view = site.get("/api/project").json()
    assert view["project"] == FOLDER and [u["id"] for u in view["units"]] == ["001", "002a", "002b"]
    assert view["plan_hash"].startswith("sha256:") and view["errors"] == []


def test_only_127_0_0_1_hosts_are_answered(tmp_path, plan_data, fake_images):
    project = tmp_path / "p"
    project.mkdir()
    write_plan(project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    app = create_app(tmp_path, project, Settings(), client_factory=lambda: fake_images(), watch=False)
    with TestClient(app) as client:
        assert client.get("/api/project").status_code == 400  # Host: testserver
        assert client.get("/api/project", headers={"Host": "127.0.0.1:8765"}).status_code == 200
        assert client.get("/api/project", headers={"Host": "localhost:8765"}).status_code == 200
        assert client.get("/api/project", headers={"Host": "evil.example:8765"}).status_code == 400


def test_a_change_without_the_same_origin_header_is_refused(site):
    response = site.post("/api/units/001/approve")
    assert response.status_code == 403
    assert state(site).units["001"].status == "generated"


def test_approving_and_approving_the_rest(site):
    view = site.post("/api/units/001/approve", headers=WRITE).json()
    assert next(u for u in view["units"] if u["id"] == "001")["status"] == "approved"
    response = site.post("/api/units/approve-remaining", headers=WRITE)
    assert response.json()["approved"] == ["002a"]
    assert state(site).units["002b"].status == "planned"


def test_approving_a_unit_without_an_image_is_409_and_an_unknown_unit_404(site):
    assert site.post("/api/units/002b/approve", headers=WRITE).status_code == 409
    response = site.post("/api/units/999/approve", headers=WRITE)
    assert response.status_code == 404 and response.json() == {"error": "No unit 999 in this plan."}


def test_the_plan_and_tests_approvals(site):
    assert site.post("/api/plan/approve", headers=WRITE).json()["approvals"]["plan"] is True
    response = site.post("/api/tests/approve", headers=WRITE)
    assert response.status_code == 409 and "M7" in response.json()["error"]


def test_a_prompt_edit_locks_the_prompt_and_is_ignored_by_the_watcher(site):
    view = site.get("/api/project").json()
    response = site.put("/api/units/002a/prompt", headers=WRITE,
                        json={"prompt": "My own prompt.", "plan_hash": view["plan_hash"]})
    assert response.status_code == 200
    unit = next(u for u in response.json()["units"] if u["id"] == "002a")
    assert (unit["image_prompt"], unit["prompt_locked"]) == ("My own prompt.", True)
    assert site.app.state.watcher.changed([site.project / "plan.yaml"]) == []  # the server's own write


def test_a_prompt_edit_after_the_file_changed_gives_409_and_changes_nothing(site):
    old_hash = site.get("/api/project").json()["plan_hash"]
    path = site.project / "plan.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# edited in the editor\n", encoding="utf-8")
    before = path.read_bytes()
    response = site.put("/api/units/002a/prompt", headers=WRITE, json={"prompt": "x", "plan_hash": old_hash})
    assert (response.status_code, response.json()) == (409, {"error": PLAN_CHANGED})
    assert path.read_bytes() == before


def test_an_invalid_plan_shows_its_errors_and_keeps_the_last_units(site):
    site.get("/api/project")
    path = site.project / "plan.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("shot: wide", "shot: sideways", 1), encoding="utf-8")
    view = site.get("/api/project").json()
    assert view["errors"] and "shot" in view["errors"][0]
    assert len(view["units"]) == 3
    assert site.post("/api/plan/approve", headers=WRITE).status_code == 409


def test_regenerating_runs_in_the_background_and_shows_both_images(site):
    response = site.post("/api/units/001/regenerate", headers=WRITE)
    assert response.status_code == 202 and response.json()["job"]["unit"] == "001"
    view = wait_for_job(site)
    unit = next(u for u in view["units"] if u["id"] == "001")
    assert (unit["current_version"], unit["compare_with"]) == (2, 1)
    chosen = site.post("/api/units/001/select-version", headers=WRITE, json={"v": 1}).json()
    unit = next(u for u in chosen["units"] if u["id"] == "001")
    assert (unit["current_version"], unit["compare_with"]) == (1, None)


def test_a_cli_run_holding_the_lock_makes_changes_wait(site):
    (site.project / ".lock").write_text(str(os.getppid()), encoding="ascii")
    response = site.post("/api/units/001/approve", headers=WRITE)
    assert response.status_code == 409 and "another stickman process" in response.json()["error"]
    assert site.post("/api/units/001/regenerate", headers=WRITE).status_code == 409


def test_extras_sheets_are_for_m7(site):
    response = site.post("/api/sheets/caveman_group/approve", headers=WRITE, json={"candidate": 1})
    assert response.status_code == 400 and "M7" in response.json()["error"]


def test_files_serves_project_images_and_nothing_else(site, tmp_path):
    assert site.get(f"/files/projects/{FOLDER}/images/_history/001_v1.png").headers["content-type"] == "image/png"
    stock = tmp_path / "style_refs" / "a.png"
    stock.parent.mkdir()
    Image.new("RGB", (8, 8)).save(stock, format="PNG")
    (tmp_path / ".env").write_text("CF_API_TOKEN=tok-secret\n", encoding="utf-8")
    for path in ("style_refs/a.png", ".env", f"projects/{FOLDER}/plan.yaml", f"projects/{FOLDER}/state.json",
                 "../outside.png", f"projects/{FOLDER}/images/../../../style_refs/a.png"):
        assert site.get(f"/files/{path}").status_code == 404, path


def test_no_secret_reaches_the_page(site):
    site.post("/api/units/001/approve", headers=WRITE)
    assert "tok-secret" not in site.get("/api/project").text
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/review/test_app.py -q`
Expected: FAIL with `ModuleNotFoundError: stickman.review.app`.

- [ ] **Step 3: Create `src/stickman/review/app.py`**

```python
"""The review server (spec §12): FastAPI bound to 127.0.0.1, serving the page, its API, server-sent events and
the images it shows. Requests must name 127.0.0.1 (or localhost) as their Host, which defeats DNS rebinding,
and every change must carry X-Stickman: 1, which another site's page can't send, since CORS is never enabled."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from stickman.bootstrap.store import BOOTSTRAP_DIR
from stickman.config_files import load_mascot, load_style
from stickman.plan.models import Plan, PlanValidationError
from stickman.plan.store import file_hash, load_plan
from stickman.render.jobs import RenderContext
from stickman.render.references import FORBIDDEN_DIR
from stickman.render.state import StateError
from stickman.review.access import Busy, ProjectAccess
from stickman.review.actions import (
    EXTRAS_LATER,
    ActionError,
    approve_plan,
    approve_remaining,
    approve_sheet,
    approve_tests,
    approve_unit,
    edit_prompt,
    select_version,
)
from stickman.review.events import EventHub, event_stream
from stickman.review.jobs import Jobs
from stickman.review.view import empty_view, project_view
from stickman.review.watch import PlanWatcher
from stickman.runlog import mask
from stickman.settings import ConfigError, Settings

STATIC = Path(__file__).parent / "static"
CHANGES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
NO_CACHE = {"Cache-Control": "no-cache"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateBody(_Body):
    candidate: int = Field(ge=1)


class VersionBody(_Body):
    v: int = Field(ge=1)


class PromptBody(_Body):
    prompt: str
    plan_hash: str


class HintBody(_Body):
    hint: str = ""


class PlanSource:
    """plan.yaml as the page sees it: while the file is invalid, the last valid plan stays on the page, with
    the file's errors beside it (spec §12.4)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._plan: Plan | None = None

    def load(self, library_ids: Iterable[str]) -> tuple[Plan | None, str | None, list[str]]:
        try:
            loaded = load_plan(self.path, library_ids=set(library_ids))
        except PlanValidationError as exc:
            try:
                current = file_hash(self.path.read_bytes())
            except OSError:
                current = None
            return self._plan, current, list(exc.errors)
        except OSError as exc:
            return self._plan, None, [f"can't read plan.yaml: {exc.strerror or exc}"]
        self._plan = loaded.plan
        return loaded.plan, loaded.hash, []


def create_app(
    workspace: Path,
    project_dir: Path,
    settings: Settings,
    *,
    client_factory: Callable[[], Any],
    secrets: Sequence[str] = (),
    watch: bool = True,
    allowed_hosts: Iterable[str] | None = None,
) -> FastAPI:
    port = settings.review.port
    hosts = set(allowed_hosts) if allowed_hosts is not None else {f"127.0.0.1:{port}", f"localhost:{port}"}
    hub = EventHub()
    access = ProjectAccess(project_dir)
    watcher = PlanWatcher(project_dir, hub)
    jobs = Jobs(workspace, project_dir, settings, access, hub, client_factory=client_factory, secrets=secrets,
                on_plan_written=watcher.wrote)
    source = PlanSource(project_dir / "plan.yaml")
    plan_path = project_dir / "plan.yaml"
    roots = [project_dir / "images", workspace / BOOTSTRAP_DIR, workspace / "library" / "style", workspace / "library" / "mascot"]
    forbidden = (workspace / FORBIDDEN_DIR).resolve()

    def masked(text: str) -> str:
        return mask(text, secrets)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task = asyncio.create_task(watcher.run(stop)) if watch else None
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await jobs.wait()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub, app.state.access, app.state.jobs, app.state.watcher = hub, access, jobs, watcher

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Any:
        if request.headers.get("host") not in hosts:
            return JSONResponse({"error": "The review page answers only on 127.0.0.1."}, status_code=400)
        if request.method in CHANGES and request.headers.get("x-stickman") != "1":
            return JSONResponse({"error": "Changes need the X-Stickman: 1 header."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ActionError)
    async def action_error(request: Request, exc: ActionError) -> JSONResponse:
        return JSONResponse({"error": masked(exc.message)}, status_code=exc.status)

    @app.exception_handler(Busy)
    async def busy(request: Request, exc: Busy) -> JSONResponse:
        return JSONResponse({"error": masked(str(exc))}, status_code=409)

    def view() -> dict[str, Any]:
        try:
            ctx = RenderContext.load(workspace, settings)
        except ConfigError as exc:
            return empty_view(project_dir, [masked(str(exc))])
        plan, plan_hash, errors = source.load(ctx.library_ids)
        try:
            state = access.read()
        except StateError as exc:
            return empty_view(project_dir, [masked(str(exc))])
        data = project_view(
            workspace=workspace, project_dir=project_dir, ctx=ctx, plan=plan, plan_hash=plan_hash, errors=errors,
            state=state, now=datetime.now().astimezone(), job=access.job.as_dict() if access.job else None,
        )
        data["problems"] = [masked(problem) for problem in data["problems"]]
        for unit in data["units"]:
            if unit["error"]:
                unit["error"] = masked(unit["error"])
        return data

    def unit_ids() -> set[str]:
        return {unit["id"] for unit in view()["units"]}

    def changed() -> dict[str, Any]:
        hub.publish("state", {})
        return view()

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers=NO_CACHE)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/project")
    async def get_project() -> dict[str, Any]:
        return view()

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(event_stream(hub, request.is_disconnected), media_type="text/event-stream",
                                 headers=NO_CACHE)

    @app.post("/api/plan/approve")
    async def plan_approve() -> dict[str, Any]:
        errors = view()["errors"]
        with access.state() as store:
            approve_plan(store, errors)
        return changed()

    @app.post("/api/tests/approve")
    async def tests_approve() -> dict[str, Any]:
        with access.state() as store:
            approve_tests(store)
        return changed()

    @app.post("/api/sheets/{char_id}/approve")
    async def sheet_approve(char_id: str, body: CandidateBody) -> dict[str, Any]:
        try:
            style, mascot = load_style(workspace), load_mascot(workspace)
        except ConfigError as exc:
            raise ActionError(409, str(exc)) from None
        approve_sheet(workspace, char_id, body.candidate, settings=settings, style=style, mascot=mascot)
        return changed()

    @app.post("/api/sheets/{char_id}/regenerate", status_code=202)
    async def sheet_more(char_id: str) -> dict[str, Any]:
        if char_id not in ("anchor", "mascot"):
            raise ActionError(400, EXTRAS_LATER)
        return {"job": jobs.more_candidates(char_id).as_dict()}  # type: ignore[arg-type]

    @app.post("/api/units/approve-remaining")
    async def units_approve_remaining() -> dict[str, Any]:
        statuses = {unit["id"]: unit["status"] for unit in view()["units"]}
        with access.state() as store:
            approved = approve_remaining(store, statuses, busy=access.busy_unit)
        return {**changed(), "approved": approved}

    @app.post("/api/units/{unit_id}/approve")
    async def unit_approve(unit_id: str) -> dict[str, Any]:
        ids = unit_ids()
        with access.state() as store:
            approve_unit(store, unit_id, unit_ids=ids, busy=access.busy_unit)
        return changed()

    @app.post("/api/units/{unit_id}/select-version")
    async def unit_select(unit_id: str, body: VersionBody) -> dict[str, Any]:
        ids = unit_ids()
        with access.state() as store:
            select_version(store, unit_id, body.v, unit_ids=ids, busy=access.busy_unit)
        return changed()

    @app.post("/api/units/{unit_id}/regenerate", status_code=202)
    async def unit_regenerate(unit_id: str) -> dict[str, Any]:
        if unit_id not in unit_ids():
            raise ActionError(404, f"No unit {unit_id} in this plan.")
        return {"job": jobs.regenerate(unit_id).as_dict()}

    @app.put("/api/units/{unit_id}/prompt")
    async def unit_prompt(unit_id: str, body: PromptBody) -> dict[str, Any]:
        try:
            library_ids = RenderContext.load(workspace, settings).library_ids
        except ConfigError as exc:
            raise ActionError(409, str(exc)) from None
        written = edit_prompt(plan_path, unit_id, body.prompt, body.plan_hash, library_ids=library_ids)
        watcher.wrote(written)
        hub.publish("plan", {"hash": written})
        return view()

    @app.post("/api/units/{unit_id}/replan", status_code=202)
    async def unit_replan(unit_id: str, body: HintBody) -> dict[str, Any]:
        if unit_id not in unit_ids():
            raise ActionError(404, f"No unit {unit_id} in this plan.")
        return {"job": jobs.replan(unit_id, body.hint).as_dict()}

    @app.get("/files/{path:path}")
    async def files(path: str) -> FileResponse:
        """Only PNGs under the project's images, bootstrap's candidates and the approved library images; never
        style_refs/ (spec §2.2), and never anything outside those folders."""
        target = (workspace / path).resolve()
        allowed = (
            target.suffix.lower() == ".png"
            and target.is_file()
            and not target.is_relative_to(forbidden)
            and any(target.is_relative_to(root.resolve()) for root in roots)
        )
        if not allowed:
            raise ActionError(404, "Not found.")
        return FileResponse(target, media_type="image/png", headers=NO_CACHE)

    return app
```

Check that `BOOTSTRAP_DIR` is exported from `bootstrap/store.py` (it is, as `"library/_bootstrap"`) and that `FORBIDDEN_DIR` is in `render/references.py`.

`test_regenerating_runs_in_the_background_and_shows_both_images` depends on background tasks: inside `with TestClient(app)` the app's event loop keeps running between requests, so the job finishes while the test polls.

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/review/test_app.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/review/app.py src/stickman/review/static/index.html tests/review/test_app.py
git commit -m "feat: the review server: the page's API with a Host check and a same-origin header, events, jobs and safe image serving" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The page, `review/static/`

**Files:**
- Create (replacing Task 7's placeholder): `src/stickman/review/static/index.html`, `app.css`, `app.js`
- Test: `tests/review/test_static.py` (new)

**Interfaces:**
- Consumes: the API from Task 7, and `GET /api/project`'s keys from Task 3.
- Produces: the page (spec §12.2–12.4).
  - **Views** (the URL hash `#plan` etc. remembers the view): Plan, Sheets, Tests, Gallery.
  - **Keyboard shortcuts** in Gallery and Tests: `A` approve · `R` regenerate · `E` edit prompt · `H` history · `J`/`→` next · `K`/`←` previous · `1`/`2` pick left/right in the side-by-side view · `Esc` close. In the prompt editor, `Ctrl`+`Enter` saves.
  - `app.js` exports `formatTime`, `money` and `describeJob` for Node (`module.exports`), and only starts the page when `document` exists. That's how the tests check its logic.

The look (from the design pass in the plan's Decisions):
- a cool light-table workspace;
- drawings on white paper mats;
- one accent, non-photo blue `#2F6FDB`;
- status colours;
- the system font with tabular figures;
- the timeline strip as the one memorable element;
- a dark theme, visible focus, reduced motion, and stacking below 900 px.

Copy is plain and in sentence case, with no all-caps labels. The buttons say what they do ("Approve plan", "Make 2 more").

- [ ] **Step 1: Write the failing tests**

Create `tests/review/test_static.py`:

```python
import json
import re
import shutil
import subprocess

import pytest

from stickman.review.app import STATIC

PAGE = (STATIC / "index.html").read_text(encoding="utf-8")
SCRIPT = (STATIC / "app.js").read_text(encoding="utf-8")
STYLE = (STATIC / "app.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def test_the_page_loads_its_own_script_and_style_and_nothing_external():
    assert '<script src="/static/app.js"></script>' in PAGE
    assert '<link rel="stylesheet" href="/static/app.css">' in PAGE
    for text in (PAGE, SCRIPT, STYLE):
        assert not re.search(r"https?://", text)


def test_every_view_has_a_tab():
    for view in ("plan", "sheets", "tests", "gallery"):
        assert f'data-view="{view}"' in PAGE


def test_every_keyboard_shortcut_is_handled():
    handlers = SCRIPT[SCRIPT.index("const shortcuts = {"):]
    for key in ("a:", "r:", "e:", "h:", "j:", "k:", "ArrowRight:", "ArrowLeft:", '"1":', '"2":'):
        assert key in handlers, key
    assert '"Escape"' in SCRIPT


def test_changes_carry_the_same_origin_header_and_events_are_followed():
    assert '"X-Stickman"' in SCRIPT
    assert 'new EventSource("/api/events")' in SCRIPT
    for event in ("plan", "state", "job", "budget"):
        assert f'addEventListener("{event}"' in SCRIPT


def test_the_style_covers_dark_mode_reduced_motion_focus_and_narrow_windows():
    assert "prefers-color-scheme: dark" in STYLE
    assert "prefers-reduced-motion" in STYLE
    assert ":focus-visible" in STYLE
    assert "@media (max-width: 900px)" in STYLE


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_script_is_valid_javascript():
    result = subprocess.run([NODE, "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_times_money_and_job_names_are_written_like_the_cli_writes_them():
    code = (
        f"const m = require({json.dumps(str(STATIC / 'app.js'))});"
        "console.log(JSON.stringify([m.formatTime(0), m.formatTime(21), m.formatTime(129.44), m.money(0.00228),"
        " m.money(0.5), m.money(12.3), m.describeJob({kind: 'regenerate', unit: '006a'}),"
        " m.describeJob({kind: 'candidates', unit: 'anchor'}), m.describeJob({kind: 'replan', unit: '001'})]));"
    )
    result = subprocess.run([NODE, "-e", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "0:00.0", "0:21.0", "2:09.4", "$0.0023", "$0.50", "$12.30",
        "regenerating 006a", "making anchor candidates", "replanning 001",
    ]
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/review/test_static.py -q`
Expected: FAIL, because the placeholder `index.html` has no script and `app.js` doesn't exist. The module-level reads fail, so collection errors.

- [ ] **Step 3: Create `src/stickman/review/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stickman review</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<header class="bar">
  <h1 class="project" id="project">Stickman review</h1>
  <nav class="tabs" aria-label="Views">
    <button type="button" data-view="plan">Plan</button>
    <button type="button" data-view="sheets">Sheets</button>
    <button type="button" data-view="tests">Tests</button>
    <button type="button" data-view="gallery">Gallery</button>
  </nav>
  <p class="budget" id="budget"></p>
</header>
<p id="notice" class="notice" role="status" aria-live="polite" hidden></p>
<main id="main"><p class="empty">Loading the project…</p></main>
<div id="overlay" class="overlay" hidden></div>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create `src/stickman/review/static/app.css`**

```css
/* The stickman review page. The drawings are black ink on white, so they sit on white paper mats over a cool
   light-table workspace; one accent, non-photo blue, marks focus and selection. */
:root {
  --desk: #e6e9ec;
  --surface: #f6f7f9;
  --paper: #ffffff;
  --ink: #1b1f24;
  --pencil: #5d6772;
  --rule: #c9cfd6;
  --blue: #2f6fdb;
  --blue-soft: #dce7fa;
  --approved: #2e7d4f;
  --review: #b7791f;
  --failed: #b42318;
  --stale: #7a4fb5;
  --working: #5b6b7f;
  --planned: #aab3bd;
  --radius-mat: 3px;
  --radius-control: 6px;
  --space: 16px;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --desk: #1d2126;
    --surface: #272c33;
    --ink: #e8ebef;
    --pencil: #a3adb8;
    --rule: #3b424b;
    --blue: #6f9ef0;
    --blue-soft: #243552;
    --planned: #56606b;
    color-scheme: dark;
  }
}

* { box-sizing: border-box; }
html { background: var(--desk); }
body {
  margin: 0;
  color: var(--ink);
  font: 15px/1.5 system-ui, "Segoe UI Variable Text", "Segoe UI", sans-serif;
  font-variant-numeric: tabular-nums;
}
button, input, textarea { font: inherit; color: inherit; }
button {
  border: 1px solid var(--rule);
  background: var(--surface);
  border-radius: var(--radius-control);
  padding: 6px 12px;
  cursor: pointer;
}
button:hover:not(:disabled) { border-color: var(--blue); }
button:disabled { opacity: 0.5; cursor: default; }
button.primary { background: var(--blue); border-color: var(--blue); color: #fff; }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
code, kbd { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 0.9em; }
kbd { border: 1px solid var(--rule); border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; background: var(--surface); }

.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 24px;
  padding: 12px var(--space);
  border-bottom: 1px solid var(--rule);
  background: var(--surface);
}
.project { font-size: 17px; font-weight: 650; margin: 0; }
.tabs { display: flex; gap: 4px; }
.tabs button { border-color: transparent; background: none; }
.tabs button[aria-current="page"] { background: var(--blue-soft); border-color: var(--blue); font-weight: 600; }
.budget { margin: 0 0 0 auto; color: var(--pencil); }
.budget.warn { color: var(--review); font-weight: 600; }

.notice { margin: 0; padding: 8px var(--space); background: var(--blue-soft); }
.notice[data-kind="error"] { background: color-mix(in srgb, var(--failed) 18%, var(--surface)); }
.notice[data-kind="warn"] { background: color-mix(in srgb, var(--review) 22%, var(--surface)); }
.notice[data-kind="ok"] { background: color-mix(in srgb, var(--approved) 18%, var(--surface)); }

main { padding: var(--space); max-width: 1500px; margin: 0 auto; }
.panel { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius-control); padding: var(--space); margin-bottom: var(--space); }
.panel h2 { font-size: 16px; margin: 0 0 8px; }
.errors { border-color: var(--failed); }
.errors li { margin: 4px 0; }
.muted, .empty { color: var(--pencil); }
.empty { margin: 24px 0; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: var(--space); }
.shortcuts { color: var(--pencil); font-size: 13px; }

/* The timeline strip: the video's units along its length, coloured by status. */
.timeline {
  display: flex;
  gap: 2px;
  height: 34px;
  margin-bottom: var(--space);
  padding: 3px;
  background: var(--paper);
  border: 1px solid var(--rule);
  border-radius: var(--radius-mat);
}
.segment { flex: 1 1 0; min-width: 3px; padding: 0; border: 0; border-radius: 2px; background: var(--planned); }
.segment[data-status="generated"] { background: var(--ink); opacity: 0.55; }
.segment[data-status="approved"] { background: var(--approved); }
.segment[data-status="needs_review"] { background: var(--review); }
.segment[data-status="failed"] { background: var(--failed); }
.segment[data-status="stale"] { background: var(--stale); }
.segment[data-status="generating"] { background: var(--working); }
.segment.focused { box-shadow: 0 0 0 2px var(--paper), 0 0 0 4px var(--blue); }

.badge { display: inline-block; font-size: 12px; font-weight: 600; padding: 1px 7px; border-radius: 10px; border: 1px solid currentColor; }
.badge[data-status="approved"] { color: var(--approved); }
.badge[data-status="needs_review"] { color: var(--review); }
.badge[data-status="failed"] { color: var(--failed); }
.badge[data-status="stale"] { color: var(--stale); }
.badge[data-status="generating"], .badge[data-status="planned"], .badge[data-status="generated"] { color: var(--pencil); }
.tag { font-size: 12px; color: var(--pencil); border: 1px dashed var(--rule); border-radius: 10px; padding: 1px 7px; }
.tag.ok { color: var(--approved); border-color: var(--approved); border-style: solid; }
.pass { color: var(--approved); }
.fail { color: var(--failed); }

/* Paper mats: the drawings are white, whatever the theme. */
.paper { background: var(--paper); border: 1px solid var(--rule); border-radius: var(--radius-mat); padding: 8px; }
.paper img { display: block; width: 100%; height: auto; }
.empty-image { display: grid; place-items: center; aspect-ratio: 16 / 9; color: #5d6772; text-align: center; padding: 24px; }

.gallery { display: grid; grid-template-columns: minmax(220px, 300px) 1fr; gap: var(--space); align-items: start; }
.queue { list-style: none; margin: 0; padding: 0; max-height: calc(100vh - 220px); overflow: auto; display: grid; gap: 6px; }
.queue-item { display: grid; grid-template-columns: 96px 1fr; gap: 10px; align-items: center; width: 100%; text-align: left; padding: 6px; }
.queue-item img { width: 96px; height: 54px; object-fit: contain; background: var(--paper); border-radius: 2px; }
.queue-item .no-image { width: 96px; height: 54px; display: grid; place-items: center; background: var(--paper); color: #5d6772; font-size: 12px; border-radius: 2px; }
.queue-item.focused { border-color: var(--blue); background: var(--blue-soft); }
.queue-text { display: grid; gap: 2px; }
.queue-id { font-weight: 650; }
.queue-time { color: var(--pencil); font-size: 13px; }

.detail { display: grid; gap: var(--space); }
.detail-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; }
.detail-head h2 { margin: 0; font-size: 20px; }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space); }
.pick { margin: 0; display: grid; gap: 8px; }
.pick figcaption { color: var(--pencil); }
.words { max-width: 72ch; }
.words p { margin: 4px 0; }
.words .idea { font-size: 17px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.replan { display: flex; gap: 8px; flex: 1 1 320px; }
.replan input { flex: 1; min-width: 0; padding: 6px 10px; border: 1px solid var(--rule); border-radius: var(--radius-control); background: var(--paper); color: #1b1f24; }

.candidates { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: var(--space); margin: 12px 0; }
.candidate { margin: 0; display: grid; gap: 8px; align-content: start; }
.candidate.approved .paper { border-color: var(--approved); box-shadow: 0 0 0 2px var(--approved); }
.note { color: var(--review); }

table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--rule); }
th { font-weight: 600; color: var(--pencil); }
.units td.text { max-width: 36ch; }
.table-scroll { overflow-x: auto; }
dl.costs { display: grid; grid-template-columns: max-content max-content; gap: 2px 16px; margin: 8px 0; }
dl.costs dt { color: var(--pencil); }
dl.costs dd { margin: 0; text-align: right; }
.total { font-size: 22px; font-weight: 650; margin: 0; }

.overlay { position: fixed; inset: 0; background: rgb(15 18 22 / 0.55); display: grid; place-items: center; padding: var(--space); z-index: 10; }
.dialog { background: var(--surface); border-radius: var(--radius-control); padding: var(--space); width: min(900px, 100%); max-height: calc(100vh - 32px); overflow: auto; display: grid; gap: 12px; }
.dialog h2 { margin: 0; font-size: 17px; }
.dialog textarea { width: 100%; min-height: 50vh; padding: 10px; border: 1px solid var(--rule); border-radius: var(--radius-control); background: var(--paper); color: #1b1f24; line-height: 1.45; }
.history { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: var(--space); }
.history figure { margin: 0; display: grid; gap: 6px; }

@media (max-width: 900px) {
  .gallery { grid-template-columns: 1fr; }
  .queue { max-height: none; grid-auto-flow: column; grid-auto-columns: 220px; overflow-x: auto; }
  .compare { grid-template-columns: 1fr; }
  .budget { margin-left: 0; }
}
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; transition: none !important; animation: none !important; }
}
```

- [ ] **Step 5: Create `src/stickman/review/static/app.js`**

```javascript
"use strict";
// The stickman review page (spec §12): plain JavaScript, no build step. It shows GET /api/project and fetches it
// again on every server-sent event; every change goes through the API with the X-Stickman: 1 header.

const VIEWS = ["plan", "sheets", "tests", "gallery"];
const STATUS_TEXT = {
  planned: "Planned", generating: "Generating", generated: "Generated", needs_review: "Needs review",
  approved: "Approved", failed: "Failed", stale: "Stale",
};
const JOB_VERBS = { regenerate: "regenerating", replan: "replanning" };
const ui = { data: null, view: "gallery", focus: null, overlay: null, editHash: null };

// --- small helpers (exported for the tests) ---

function formatTime(seconds) {
  const tenths = Math.floor(seconds * 10 + 0.5);
  const minutes = Math.floor(tenths / 600);
  const rest = tenths - minutes * 600;
  return `${minutes}:${String(Math.floor(rest / 10)).padStart(2, "0")}.${rest % 10}`;
}

function money(usd) {
  return usd > 0 && usd < 0.1 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

function describeJob(job) {
  if (job.kind === "candidates") return `making ${job.unit} candidates`;
  return `${JOB_VERBS[job.kind] || job.kind} ${job.unit}`;
}

function capital(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function h(tag, attrs, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (key === "class") element.className = value;
    else if (value === true) element.setAttribute(key, "");
    else element.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child instanceof Node ? child : String(child));
  }
  return element;
}

// --- talking to the server ---

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-Stickman"] = "1";
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function say(text, kind) {
  const notice = document.getElementById("notice");
  notice.textContent = text || "";
  notice.hidden = !text;
  notice.dataset.kind = kind || "info";
}

async function act(method, path, body, done) {
  try {
    const data = await api(method, path, body);
    if (data.units) ui.data = data;
    if (data.job) say(`${capital(describeJob(data.job))}…`, "info");
    else if (done) say(done, "ok");
    render();
    return data;
  } catch (error) {
    say(error.message, "error");
    return null;
  }
}

async function refresh() {
  try {
    ui.data = await api("GET", "/api/project");
  } catch (error) {
    say(`Can't reach the review server (${error.message}). Is \`stickman review\` still running?`, "error");
    return;
  }
  render();
}

function listen() {
  const events = new EventSource("/api/events");
  events.addEventListener("plan", () => refresh());
  events.addEventListener("state", () => refresh());
  events.addEventListener("job", (event) => {
    const job = JSON.parse(event.data);
    if (job.state === "started") say(`${capital(describeJob(job))}…`, "info");
    else if (job.state === "finished") say(job.message || "Done.", "ok");
    else if (job.state === "paused") say(job.message, "warn");
    else if (job.state === "failed") say(job.message, "error");
    refresh();
  });
  events.addEventListener("budget", (event) => say(JSON.parse(event.data).message, "warn"));
}

// --- what's shown ---

function unitsById() {
  return Object.fromEntries((ui.data ? ui.data.units : []).map((unit) => [unit.id, unit]));
}

function viewIds() {
  if (!ui.data) return [];
  return ui.view === "tests" ? ui.data.test_units : ui.data.order;
}

function focusedUnit() {
  return unitsById()[ui.focus] || null;
}

function currentVersion(unit) {
  return unit.versions.find((version) => version.v === unit.current_version) || null;
}

function badge(status) {
  return h("span", { class: "badge", "data-status": status }, STATUS_TEXT[status] || status);
}

function verdict(qc) {
  if (!qc) return h("span", { class: "muted" }, "not checked yet");
  const idea = qc.score ? `, idea ${qc.score}/5` : "";
  return qc.passed
    ? h("span", { class: "pass" }, `passed QC${idea}`)
    : h("span", { class: "fail" }, `failed QC: ${qc.reason.replace(/_/g, " ")}${idea}`);
}

function render() {
  const data = ui.data;
  if (!data) return;
  document.getElementById("project").textContent = data.project;
  document.title = `${data.project}: stickman review`;
  const budget = document.getElementById("budget");
  budget.textContent = data.budget ? data.budget.text : "";
  budget.classList.toggle("warn", Boolean(data.budget && data.budget.warn));
  for (const tab of document.querySelectorAll(".tabs button")) {
    tab.setAttribute("aria-current", tab.dataset.view === ui.view ? "page" : "false");
  }
  const ids = viewIds();
  if (!ids.includes(ui.focus)) ui.focus = ids[0] || null;
  const body = { plan: planView, sheets: sheetsView, tests: testsView, gallery: galleryView }[ui.view](data);
  document.getElementById("main").replaceChildren(problemsPanel(data), body);
  if (ui.overlay !== "prompt") renderOverlay(); // never redraw the editor over what's being typed
  const focused = document.querySelector(".queue-item.focused");
  if (focused) focused.scrollIntoView({ block: "nearest" });
}

function problemsPanel(data) {
  if (!data.problems || !data.problems.length) return "";
  return h("section", { class: "panel errors" }, h("h2", {}, "Something needs fixing before this page is complete"),
    h("ul", {}, data.problems.map((problem) => h("li", {}, problem))));
}

// --- Plan ---

function planView(data) {
  const approved = data.approvals.plan;
  return h("div", { class: "plan" },
    data.errors.length
      ? h("section", { class: "panel errors" }, h("h2", {}, "plan.yaml has errors"),
          h("p", { class: "muted" }, "Fix them in plan.yaml; this page reloads when you save. It shows the last valid plan meanwhile."),
          h("ul", {}, data.errors.map((error) => h("li", {}, h("code", {}, error)))))
      : "",
    h("div", { class: "toolbar" },
      h("button", {
        type: "button", class: "primary", disabled: approved || data.errors.length > 0,
        onclick: () => act("POST", "/api/plan/approve", undefined, "Plan approved."),
      }, approved ? "Plan approved" : "Approve plan"),
      h("span", { class: "muted" }, `${data.units.length} units, ${formatTime(data.duration_end || 0)} long`)),
    estimatePanel(data.estimate),
    correctionsPanel(data),
    castPanel(data.cast),
    h("section", { class: "panel" }, h("h2", {}, "Units"), h("div", { class: "table-scroll" }, unitsTable(data.units))));
}

function estimatePanel(estimate) {
  if (!estimate) return "";
  const row = (label, value) => [h("dt", {}, label), h("dd", {}, value)];
  return h("section", { class: "panel" }, h("h2", {}, "Cost and time for the whole video"),
    h("p", { class: "total" }, `${money(estimate.total_usd)}, about ${estimate.minutes} min`),
    h("dl", { class: "costs" },
      row(`${estimate.units} images`, money(estimate.images_usd)), row("Their checks", money(estimate.checks_usd)),
      row("Extras' sheets", money(estimate.sheets_usd)), row("Planning (done)", money(estimate.llm_usd))),
    h("p", { class: "muted" }, `About ${estimate.neurons.toLocaleString()} neurons, retries included. ${estimate.free_note}`));
}

function correctionsPanel(data) {
  const corrections = data.corrections.length
    ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Scene"), h("th", {}, "Heard"), h("th", {}, "Corrected to"), h("th", {}, "Why"))),
        h("tbody", {}, data.corrections.map((c) => h("tr", {}, h("td", {}, c.scene), h("td", {}, c.from), h("td", {}, c.to), h("td", {}, c.reason)))))
    : h("p", { class: "muted" }, "No corrections.");
  const merges = data.merge_check.length
    ? h("ul", {}, data.merge_check.map((m) => h("li", {}, `Line ${m.line}: the rules say ${m.rules}, the LLM says ${m.llm}.`)))
    : h("p", { class: "muted" }, "The LLM and the fragment rules agree on every merge.");
  return h("section", { class: "panel" }, h("h2", {}, "Corrections"), corrections, h("h2", {}, "Check merges"), merges);
}

function castPanel(cast) {
  return h("section", { class: "panel" }, h("h2", {}, "Cast"),
    h("ul", {}, cast.map((member) => h("li", {}, h("strong", {}, member.name),
      ` (${member.figures} figure${member.figures === 1 ? "" : "s"}${member.sheet ? ", has a sheet" : ""}): ${member.description}`))));
}

function unitsTable(units) {
  return h("table", { class: "units" },
    h("thead", {}, h("tr", {}, ["Time", "Part", "Text", "Corrected", "Visual idea", "Shot", "Characters", "Notes"].map((t) => h("th", {}, t)))),
    h("tbody", {}, units.map((unit) => h("tr", {},
      h("td", {}, formatTime(unit.start)), h("td", {}, unit.part || ""), h("td", { class: "text" }, unit.source_text),
      h("td", { class: "text" }, unit.corrected_text === unit.source_text ? "" : unit.corrected_text),
      h("td", { class: "text" }, unit.visual_idea), h("td", {}, unit.shot),
      h("td", {}, unit.characters.map((c) => c.ref).join(", ")),
      h("td", {}, unitTags(unit))))));
}

function unitTags(unit) {
  return [
    unit.prompt_locked ? h("span", { class: "tag" }, "locked") : "",
    unit.softened ? h("span", { class: "tag" }, "softened") : "",
    unit.split === "split" ? h("span", { class: "tag" }, "split") : "",
    unit.split === "no_valid_cut" ? h("span", { class: "tag" }, "no valid cut") : "",
  ];
}

// --- Sheets ---

function sheetsView(data) {
  const boot = data.bootstrap;
  if (!boot) return h("p", { class: "empty" }, "Bootstrap's candidates can't be read; see the problem above.");
  return h("div", { class: "sheets" },
    stepSection("anchor", "Style anchor", boot.anchor, boot.anchor_done,
      "Every image takes its line weight and look from the anchor. Approve one, then make the mascot sheet."),
    stepSection("mascot", "Mascot sheet", boot.mascot, boot.mascot_done,
      "The main character's head and hair, sent with every scene he's in."),
    h("section", { class: "panel" }, h("h2", {}, "Extras"),
      h("p", { class: "muted" }, "Extras are drawn from their description until their sheets arrive (M7)."),
      h("ul", {}, data.cast.filter((m) => m.id !== "mascot").map((m) => h("li", {}, h("strong", {}, m.name), `: ${m.description}`)))));
}

function stepSection(step, title, state, done, blurb) {
  const cards = state.candidates.map((c) => h("figure", { class: `candidate${c.approved ? " approved" : ""}` },
    h("div", { class: "paper" }, h("img", { src: c.url, alt: `${title} candidate ${c.n}`, loading: "lazy" })),
    h("figcaption", {}, h("strong", {}, `c${c.n} `), verdict(c.qc),
      c.previous_anchor ? h("span", { class: "note" }, ". Made with a previous anchor") : ""),
    c.approved
      ? h("span", { class: "tag ok" }, "Approved")
      : h("button", {
          type: "button", disabled: c.previous_anchor,
          onclick: () => act("POST", `/api/sheets/${step}/approve`, { candidate: c.n }, `Approved ${title.toLowerCase()} c${c.n}.`),
        }, "Approve")));
  return h("section", { class: "panel" },
    h("h2", {}, title, done ? " " : "", done ? h("span", { class: "tag ok" }, "done") : ""),
    h("p", { class: "muted" }, blurb),
    cards.length ? h("div", { class: "candidates" }, cards) : h("p", { class: "empty" }, "No candidates yet."),
    h("button", {
      type: "button", title: "Spends neurons: two images and their checks",
      onclick: () => act("POST", `/api/sheets/${step}/regenerate`),
    }, "Make 2 more"));
}

// --- Tests and Gallery ---

function testsView(data) {
  if (!data.test_units.length) {
    return h("section", { class: "panel" }, h("h2", {}, "Test images"),
      h("p", { class: "empty" }, "No test units yet. From M7, three units are made first and wait here for approval before the full batch."));
  }
  return h("div", {},
    h("div", { class: "toolbar" }, h("button", {
      type: "button", class: "primary", disabled: data.approvals.tests,
      onclick: () => act("POST", "/api/tests/approve", undefined, "Tests approved."),
    }, data.approvals.tests ? "Tests approved" : "Approve tests and start the batch"), shortcutsHint()),
    galleryBody(data, data.test_units));
}

function galleryView(data) {
  const remaining = data.units.filter((unit) => unit.status === "generated").length;
  return h("div", {},
    timeline(data),
    h("div", { class: "toolbar" },
      h("button", {
        type: "button", disabled: remaining === 0,
        onclick: () => act("POST", "/api/units/approve-remaining", undefined, `Approved ${remaining} unit(s).`),
      }, remaining ? `Approve all remaining (${remaining})` : "Nothing left to approve"),
      shortcutsHint()),
    galleryBody(data, data.order));
}

function shortcutsHint() {
  return h("span", { class: "shortcuts" }, h("kbd", {}, "A"), " approve  ", h("kbd", {}, "R"), " regenerate  ",
    h("kbd", {}, "E"), " edit prompt  ", h("kbd", {}, "H"), " history  ", h("kbd", {}, "J"), h("kbd", {}, "K"), " next and previous  ",
    h("kbd", {}, "1"), h("kbd", {}, "2"), " keep left or right");
}

function timeline(data) {
  return h("div", { class: "timeline", role: "list", "aria-label": "Units along the video" },
    data.units.map((unit) => h("button", {
      type: "button", role: "listitem", class: `segment${unit.id === ui.focus ? " focused" : ""}`,
      "data-status": unit.status, style: `flex-grow: ${Math.max(unit.end - unit.start, 0.1)}`,
      title: `${unit.id} at ${formatTime(unit.start)}: ${STATUS_TEXT[unit.status]}`,
      "aria-label": `${unit.id} at ${formatTime(unit.start)}, ${STATUS_TEXT[unit.status]}`,
      onclick: () => focusUnit(unit.id),
    })));
}

function galleryBody(data, ids) {
  const units = unitsById();
  const list = ids.map((id) => units[id]).filter(Boolean);
  if (!list.length) return h("p", { class: "empty" }, "No units to show.");
  return h("div", { class: "gallery" },
    h("ol", { class: "queue", "aria-label": "Units, flagged first" }, list.map(queueItem)),
    detail(units[ui.focus] || list[0]));
}

function queueItem(unit) {
  const current = currentVersion(unit);
  const focused = unit.id === ui.focus;
  return h("li", {}, h("button", {
    type: "button", class: `queue-item${focused ? " focused" : ""}`, "aria-current": focused ? "true" : null,
    onclick: () => focusUnit(unit.id),
  },
    current ? h("img", { src: current.url, alt: "", loading: "lazy" }) : h("span", { class: "no-image" }, "no image"),
    h("span", { class: "queue-text" }, h("span", { class: "queue-id" }, unit.id),
      h("span", { class: "queue-time" }, formatTime(unit.start)), badge(unit.status))));
}

function detail(unit) {
  const current = currentVersion(unit);
  const earlier = unit.compare_with ? unit.versions.find((version) => version.v === unit.compare_with) : null;
  let stage;
  if (earlier && current) {
    stage = h("div", { class: "compare" }, pick(unit, earlier, 1, "Previous"), pick(unit, current, 2, "New"));
  } else if (current) {
    stage = h("div", { class: "paper" }, h("img", { src: current.url, alt: `${unit.id}: ${unit.visual_idea}` }));
  } else {
    const why = unit.status === "failed" ? `No image: ${unit.error || "the request failed"}.` : "No image yet: `stickman generate` makes it.";
    stage = h("div", { class: "paper empty-image" }, h("p", {}, why));
  }
  const qc = current ? current.qc : null;
  return h("article", { class: "detail", "aria-label": `Unit ${unit.id}` },
    h("div", { class: "detail-head" },
      h("h2", {}, unit.id), h("span", { class: "muted" }, `${formatTime(unit.start)} to ${formatTime(unit.end)}`),
      unit.part ? h("span", { class: "muted" }, `part ${unit.part}`) : "", badge(unit.status), unitTags(unit)),
    stage,
    h("div", { class: "words" },
      h("p", { class: "idea" }, unit.visual_idea),
      h("p", {}, unit.corrected_text),
      unit.corrected_text !== unit.source_text ? h("p", { class: "muted" }, `Heard as: ${unit.source_text}`) : "",
      current ? h("p", {}, verdict(qc), qc && qc.notes ? `. ${qc.notes}` : "") : "",
      unit.status === "needs_review" && unit.review_reason ? h("p", { class: "fail" }, `Needs review: ${unit.review_reason.replace(/_/g, " ")}`) : "",
      unit.error && unit.status !== "failed" ? h("p", { class: "muted" }, unit.error) : ""),
    h("div", { class: "actions" },
      h("button", { type: "button", class: "primary", disabled: !current || unit.status === "approved", onclick: () => approve(unit) },
        unit.status === "approved" ? "Approved" : "Approve"),
      h("button", { type: "button", title: "Spends neurons: a new image, its check and any retries", onclick: () => regenerate(unit) }, "Regenerate"),
      h("button", { type: "button", onclick: () => openOverlay("prompt") }, "Edit prompt"),
      h("button", { type: "button", disabled: !unit.versions.length, onclick: () => openOverlay("history") }, `History (${unit.versions.length})`),
      replanForm(unit)));
}

function pick(unit, version, key, label) {
  return h("figure", { class: "pick" },
    h("div", { class: "paper" }, h("img", { src: version.url, alt: `${label} image of ${unit.id}, version ${version.v}` })),
    h("figcaption", {}, `${key}: ${label}, v${version.v}, `, verdict(version.qc)),
    h("button", { type: "button", onclick: () => choose(unit, version.v) }, `Keep this one (${key})`));
}

function replanForm(unit) {
  const input = h("input", { type: "text", name: "hint", placeholder: "Replan with a hint, for example: show it at night", "aria-label": `Hint for replanning ${unit.id}` });
  return h("form", {
    class: "replan",
    onsubmit: (event) => { event.preventDefault(); replan(unit, input.value); },
  }, input, h("button", { type: "submit" }, "Replan"));
}

function focusUnit(id) {
  ui.focus = id;
  render();
}

function step(delta) {
  const ids = viewIds();
  if (!ids.length) return;
  const index = Math.max(0, ids.indexOf(ui.focus));
  focusUnit(ids[(index + delta + ids.length) % ids.length]);
}

// --- changes ---

function approve(unit) {
  return act("POST", `/api/units/${unit.id}/approve`, undefined, `Approved ${unit.id}.`);
}

function regenerate(unit) {
  return act("POST", `/api/units/${unit.id}/regenerate`);
}

function choose(unit, v) {
  return act("POST", `/api/units/${unit.id}/select-version`, { v }, `${unit.id} now shows version ${v}.`);
}

function replan(unit, hint) {
  return act("POST", `/api/units/${unit.id}/replan`, { hint });
}

// --- the history and the prompt editor ---

function openOverlay(kind) {
  const unit = focusedUnit();
  if (!unit || (kind === "history" && !unit.versions.length)) return;
  ui.overlay = kind;
  if (kind === "prompt") ui.editHash = ui.data.plan_hash; // the plan as it was when the editor opened (spec §12.4)
  renderOverlay();
}

function closeOverlay() {
  ui.overlay = null;
  renderOverlay();
}

function renderOverlay() {
  const root = document.getElementById("overlay");
  const unit = focusedUnit();
  if (!ui.overlay || !unit) {
    root.hidden = true;
    root.replaceChildren();
    return;
  }
  root.hidden = false;
  root.replaceChildren(ui.overlay === "history" ? historyDialog(unit) : promptDialog(unit));
  const first = root.querySelector("[data-autofocus]");
  if (first) first.focus();
}

function historyDialog(unit) {
  const versions = [...unit.versions].reverse();
  return h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": `History of ${unit.id}` },
    h("h2", {}, `Every version of ${unit.id}`),
    h("div", { class: "history" }, versions.map((version, index) => h("figure", {},
      h("div", { class: "paper" }, h("img", { src: version.url, alt: `${unit.id} version ${version.v}`, loading: "lazy" })),
      h("figcaption", {}, h("strong", {}, `v${version.v} `), verdict(version.qc),
        version.v === unit.approved_version ? h("span", { class: "tag ok" }, " approved") : ""),
      version.v === unit.current_version
        ? h("span", { class: "tag" }, "Shown now")
        : h("button", {
            type: "button", "data-autofocus": index === 0 ? true : null,
            onclick: async () => { closeOverlay(); await choose(unit, version.v); },
          }, "Show this one")))),
    h("div", { class: "actions" }, h("button", { type: "button", onclick: closeOverlay, "data-autofocus": true }, "Close")));
}

function promptDialog(unit) {
  const area = h("textarea", { id: "prompt-text", "aria-label": `Image prompt of ${unit.id}`, "data-autofocus": true });
  area.value = unit.image_prompt;
  return h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": `Edit the prompt of ${unit.id}` },
    h("h2", {}, `Image prompt of ${unit.id}`),
    h("p", { class: "muted" }, unit.prompt_locked
      ? "This prompt is locked: field changes in plan.yaml don't rebuild it."
      : "Saving locks the prompt, so field changes in plan.yaml won't rebuild it. `stickman rebuild-prompt` unlocks it."),
    area,
    h("div", { class: "actions" },
      h("button", { type: "button", class: "primary", onclick: savePrompt }, "Save prompt"),
      h("button", { type: "button", onclick: closeOverlay }, "Cancel"),
      h("span", { class: "shortcuts" }, h("kbd", {}, "Ctrl"), "+", h("kbd", {}, "Enter"), " saves")));
}

async function savePrompt() {
  const unit = focusedUnit();
  const area = document.getElementById("prompt-text");
  if (!unit || !area) return;
  const saved = await act("PUT", `/api/units/${unit.id}/prompt`, { prompt: area.value, plan_hash: ui.editHash }, `Saved and locked the prompt of ${unit.id}.`);
  if (saved) closeOverlay();
}

// --- keyboard (spec §12.3) ---

function onKey(event) {
  if (event.key === "Escape") {
    if (ui.overlay) {
      event.preventDefault();
      closeOverlay();
    }
    return;
  }
  const typing = event.target.closest && event.target.closest("input, textarea, select");
  if (typing) {
    if (ui.overlay === "prompt" && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      savePrompt();
    }
    return;
  }
  if (ui.overlay || event.ctrlKey || event.metaKey || event.altKey) return;
  if (ui.view !== "gallery" && ui.view !== "tests") return;
  const unit = focusedUnit();
  const shortcuts = {
    a: () => unit && approve(unit),
    r: () => unit && regenerate(unit),
    e: () => openOverlay("prompt"),
    h: () => openOverlay("history"),
    j: () => step(1),
    ArrowRight: () => step(1),
    k: () => step(-1),
    ArrowLeft: () => step(-1),
    "1": () => unit && unit.compare_with && choose(unit, unit.compare_with),
    "2": () => unit && unit.compare_with && choose(unit, unit.current_version),
  };
  const handler = shortcuts[event.key.length === 1 ? event.key.toLowerCase() : event.key];
  if (handler) {
    event.preventDefault();
    handler();
  }
}

function start() {
  const wanted = location.hash.slice(1);
  if (VIEWS.includes(wanted)) ui.view = wanted;
  for (const tab of document.querySelectorAll(".tabs button")) {
    tab.addEventListener("click", () => {
      ui.view = tab.dataset.view;
      history.replaceState(null, "", `#${ui.view}`);
      render();
    });
  }
  document.addEventListener("keydown", onKey);
  document.getElementById("overlay").addEventListener("click", (event) => {
    if (event.target.id === "overlay") closeOverlay();
  });
  refresh();
  listen();
}

if (typeof document !== "undefined") start();
if (typeof module !== "undefined") module.exports = { formatTime, money, describeJob };
```

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/review/test_static.py tests/review/test_app.py -q`
Expected: PASS. If `node` isn't found, the two Node tests are skipped. Say so in your report.

- [ ] **Step 7: See the page once in a browser (no API calls)**

There's no browser automation in this task. Just check that the page loads against a throwaway copy of the test data:
- write a small script under `$CLAUDE_JOB_DIR/tmp` (or `%TEMP%`) that builds a `tmp` workspace with `plan_data`-like content (copy the `plan_data` fixture's dict into the script), and serves `create_app(..., client_factory=<a function raising ConfigError>, allowed_hosts=None)` with uvicorn on `127.0.0.1:8799`;
- then run `curl -s -H "Host: 127.0.0.1:8799" http://127.0.0.1:8799/api/project` and check the JSON comes back;
- stop the server.

Don't spend time on visual polish here: the controller checks the page in a browser in Task 10.

- [ ] **Step 8: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/review/static tests/review/test_static.py
git commit -m "feat: the review page: plan, sheets, tests and a gallery with the timeline strip, side-by-side choice, history, prompt editor and keyboard shortcuts" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `stickman review`, and the spec notes

**Files:**
- Modify: `src/stickman/cli.py`
- Modify: `spec.md`, `plan.md`
- Test: `tests/test_cli_review.py` (new)

**Interfaces:**
- Consumes: `create_app` (Task 7), `build_client`/`_secrets` (Task 1).
- Produces:
  - the `stickman review [-p <project>] [--no-browser] [-w <workspace>]` command;
  - `cli._port_free(host, port) -> bool`, `cli._open_browser(url)` and `cli._serve(app, host, port)`, which tests replace.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_review.py`:

```python
import socket

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan

runner = CliRunner()
FOLDER = "2026-09-25_demo"


@pytest.fixture
def workspace(tmp_path, monkeypatch, plan_data):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    folder = tmp_path / "projects" / FOLDER
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    return tmp_path


@pytest.fixture
def served(monkeypatch):
    calls = {"serve": [], "open": []}
    monkeypatch.setattr(cli, "_serve", lambda app, host, port: calls["serve"].append((app, host, port)))
    monkeypatch.setattr(cli, "_open_browser", lambda url: calls["open"].append(url))
    monkeypatch.setattr(cli, "_port_free", lambda host, port: True)
    return calls


def review(workspace, *args):
    return runner.invoke(cli.app, ["review", "-w", str(workspace), *args])


def test_review_serves_the_project_on_127_0_0_1_and_opens_the_browser(workspace, served):
    result = review(workspace)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"Project: {FOLDER}"
    assert "Review page: http://127.0.0.1:8765/ (Ctrl+C stops it)" in result.output
    [(app, host, port)] = served["serve"]
    assert (host, port) == ("127.0.0.1", 8765)
    assert served["open"] == ["http://127.0.0.1:8765/"]
    assert app.state.access.project_dir.name == FOLDER


def test_no_browser_opens_nothing(workspace, served):
    assert review(workspace, "--no-browser").exit_code == 0
    assert served["open"] == [] and len(served["serve"]) == 1


def test_without_credentials_the_page_still_opens_with_a_note(workspace, served):
    (workspace / ".env").unlink()
    result = review(workspace, "--no-browser")
    assert result.exit_code == 0, result.output
    assert "regenerating, replanning and making candidates need CF_ACCOUNT_ID and CF_API_TOKEN" in result.output


def test_a_port_in_use_exits_1_with_how_to_fix_it(workspace, monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        (workspace / "config").mkdir()
        (workspace / "config" / "settings.yaml").write_text(f"review:\n  port: {port}\n", encoding="utf-8")
        monkeypatch.setattr(cli, "_serve", lambda app, host, p: pytest.fail("must not serve"))
        result = review(workspace, "--no-browser")
    assert result.exit_code == 1
    assert f"Port {port} on 127.0.0.1 is in use" in result.output


def test_no_project_exits_1(tmp_path, served, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=300))
    result = runner.invoke(cli.app, ["review", "-w", str(tmp_path)])
    assert result.exit_code == 1 and result.output.splitlines()[0] == "Project: (none found)"
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_cli_review.py -q`
Expected: FAIL ("No such command 'review'").

- [ ] **Step 3: Add the command to `src/stickman/cli.py`**

Add `import socket` and `import webbrowser` at the top, then add these after the `compare` command's helpers:

```python
def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _open_browser(url: str) -> None:
    webbrowser.open(url)


def _serve(site: object, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(site, host=host, port=port, log_level="warning")


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
    if not no_browser:
        _open_browser(url)
    try:
        _serve(site, host, port)
    except KeyboardInterrupt:
        pass
    console.print("Review page stopped.")
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/test_cli_review.py -q`
Expected: PASS.

- [ ] **Step 5: Add the spec and plan notes**

In `spec.md`, add these `[M6]` notes. Keep the existing text:

1. **§12.1:** at the end, add:

```
**[M6] As built:**
- `stickman review [-p <project>] [--no-browser]` checks the port is free (exit 1 if not), serves on `review.host`:`review.port` (127.0.0.1 only) and opens the browser. It works without `.env`: approving and editing work; regenerating, replanning and making candidates need the credentials. Ctrl+C stops it.
- Requests whose `Host` isn't `127.0.0.1:<port>` or `localhost:<port>` get 400 (DNS rebinding), and every change needs the header `X-Stickman: 1` (403 without it), which another site's page can't send; CORS is never enabled.
- `/files/<path>` serves only PNGs under the project's `images/`, `library/_bootstrap/`, `library/style/` and `library/mascot/`, never `style_refs/`.
- One page job at a time (regenerate, replan, more bootstrap candidates); another gets 409 naming the running one. A job holds the project `.lock` for its whole length (candidates: bootstrap's lock); a lock another process holds gives 409. While a page job runs, page actions change its own state in memory; otherwise they read `state.json` fresh and take the lock just for the write.
- Events at `/api/events`: `plan`, `state`, `job` (started, progress, finished, paused, failed, with a masked message) and `budget`. The page fetches `GET /api/project` again on each.
```

2. **§12.2:** at the end, add:

```
**[M6] As built:**
- Stale is worked out for display from the fingerprints (§10.4); the page never writes it (`generate`'s recovery does).
- **Regenerate** starts a new chain for the unit (full QC, retries, soften/redesign, like `generate`) after rebuilding its tool-built prompt for the current references (§7.4 [M5]). The unit's approval is cleared, and its old current version becomes `compare_with`: the side-by-side view shows it (left, `1`) beside the new one (right, `2`) until one is chosen. Choosing a version makes it current and ends the comparison; the approval stays only if the chosen version is the approved one, otherwise its QC gives the status.
- **Sheets** shows bootstrap's anchor and mascot candidates with Approve and "Make 2 more"; mascot candidates made with a previous anchor are marked and can't be approved. Extras' sheets come in M7.
- **Tests** lists `state.json`'s `test_units`, which M7 picks; until then it says so, and approving tests gives 409.
- **Approve plan** sets `plan_approved`, refused while `plan.yaml` has errors. Nothing enforces the approvals until M7.
- The page also shows a timeline strip: the video's units as segments sized by duration and coloured by status; clicking one focuses it.
```

3. **§12.4:** at the end, add:

```
**[M6]** The watcher is `watchfiles` on the project folder (not recursive), debounced 300 ms; `plan.yaml` sends `plan` and `state.json` sends `state`. The prompt editor keeps the plan hash from when it opened, so a change on disk while editing gives the 409.
```

4. **§5.2:** after the `[M4]` list, add:

```
**[M6]** Each unit also has `compare_with`: the version shown beside the current one after a regeneration from the review page, until one is chosen (null otherwise).
```

5. **§13:** after the `[M5]` list, add:

```
**[M6] `review`:** `stickman review [-p] [--no-browser]` (§12.1 [M6]). The project rule for `-p` is the same as `replan`'s until M7.
```

In `plan.md`, in the M6 row's "Delivers" cell, append: " **[M6 as built]** Approvals are recorded; M7 enforces them. Test units and extras' sheets appear once M7 makes them."

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/cli.py tests/test_cli_review.py spec.md plan.md
git commit -m "feat: stickman review opens the review page on 127.0.0.1; spec and plan notes for M6" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The browser check (the controller runs this; nothing is paid)

This task is **not** for a subagent. The controller runs it after the whole-branch review and its fix wave, with the Playwright browser tools. It spends nothing: it never clicks Regenerate, Replan or "Make 2 more".

- [ ] **Step 1: Make a throwaway workspace copy** in `$CLAUDE_JOB_DIR/tmp/review-check/`:
  - `config/`;
  - `projects/2026-09-25_first-sleep/` (its `plan.yaml`, `state.json`, `images/`);
  - `library/` if it exists.

  Don't copy `.env`, so the page runs without credentials and can't spend anything. Don't copy `style_refs/`.

- [ ] **Step 2: Start the server:** run `uv run stickman review -w "$CLAUDE_JOB_DIR/tmp/review-check" -p 2026-09-25_first-sleep --no-browser` in the background.

- [ ] **Step 3: Check these in the browser (Playwright), with a screenshot of each view:**
  - The Plan view: units, corrections, the cast, the estimate, and "Approve plan". Approve it.
  - The Gallery: flagged units first, and the timeline strip coloured by status. `J`/`K` move the focus. `A` approves. "Approve all remaining" works. History picks a version.
  - Edit a prompt and save it: `plan.yaml` in the copy gets it with `prompt_locked: true`.
  - Edit `plan.yaml` in the copy while the editor is open, then save: the 409 message shows, and nothing is overwritten.
  - Break `plan.yaml` (an invalid `shot`): within 2 s the errors show. Fix it: they go.
  - The Sheets view without bootstrap data shows its empty state. The Tests view shows its M7 message.
  - Dark mode (emulate `prefers-color-scheme: dark`) and a 390 px wide window both read well.

- [ ] **Step 4: Design critique:** look at the screenshots against the design plan (the light table, paper mats, one accent, the timeline strip as the memorable element). If something reads poorly (spacing, contrast, hierarchy), write the findings down, and dispatch **one** small fix subagent for `app.css`/`app.js`, with a scoped re-review. Don't fix it in the controller.

- [ ] **Step 5: Stop the server**, and delete the throwaway copy.

---

## Self-review notes (written with the plan)

- **Spec coverage:**
  - §12.1 (server, 127.0.0.1, jobs in-process with the lock and budget, SSE): Tasks 5–7 and 9.
  - §12.2 (Plan, Sheets, Tests, Gallery, ordering, cards, actions, side by side, approve remaining): Tasks 3, 4 and 8.
  - §12.3 (shortcuts): Task 8.
  - §12.4 (watchfiles reload, ignoring own writes, 409 prompt edits): Tasks 4, 5 and 7.
  - §12.5 (API): Task 7.
  - §17 "Review API" (approve, regenerate, select-version, 409 prompt edit, the watcher ignoring own writes): Task 7's tests.
  - §18 #18 (127.0.0.1 only, reload within 2 s, 409 with no overwrite): Tasks 5, 7 and 10.
  - §18 #19 (flagged first, side by side, history, approve all remaining, every shortcut): Tasks 3, 8 and 10.
- **Deferred to M7:** enforcing the approval gates in `generate`; picking test units; extras' sheets, "swap for a new sheet" and library reuse; `regen` and `rebuild-prompt` on the CLI (the page's Regenerate already starts the new chain M7's `regen` will reuse); prompt-lock detection on load.
- **Types used across tasks:**
  - `StateStore.approve/select_version/begin_regeneration` and `stale_status`: Task 2, used in Tasks 3, 4 and 6.
  - `project_view`/`empty_view`: Task 3, used in Task 7.
  - `ActionError` and the actions: Task 4, used in Task 7.
  - `EventHub`/`event_stream`/`PlanWatcher`: Task 5, used in Tasks 6 and 7.
  - `ProjectAccess`/`Busy`/`JobInfo`/`Jobs`: Task 6, used in Task 7.
  - `create_app`: Task 7, used in Task 9.
  - `refresh_plan_file`: Task 6.
