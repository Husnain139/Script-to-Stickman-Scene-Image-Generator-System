# Stickman M5 (Bootstrap and Model Comparison) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two one-time setup commands, then use them to settle the defaults.
- `stickman bootstrap` makes the style anchor and the mascot sheet (spec §8.1).
- `stickman compare` runs the model comparison and writes its report (spec §14.4).
- Generation starts sending reference images: prompts built before the references existed are rebuilt.
- M5 is a **decision point**. Once the live runs are done, the user picks the default model, the QC thresholds, the timeout and the budget, and they're written to config.

**Architecture:**
- **New `render/calls.py`:** a run's image and vision calls, taken out of `Renderer`. Each call is metered, logged and retried, counted by the circuit breaker, and QC-checked. The renderer, bootstrap and compare all make their calls through it.
- **New `stickman.bootstrap` package:**

  | Module | Contents |
  |---|---|
  | `prompts.py` | the §8.1 anchor and mascot-sheet prompts, and what QC expects them to show |
  | `store.py` | candidates and approvals in `library/_bootstrap/v<style_version>/bootstrap.json`; each candidate PNG also carries its own record |
  | `generate.py` | makes and checks one step's candidates, several at once |
  | `approve.py` | copies an approved candidate into the library, makes its reference copy, and writes the mascot's `seed` and `model` to `config/mascot.yaml` |

- **New `plan/refresh.py`:** rebuilds tool-built `image_prompt`s for the reference images that exist now. Hand-edited and locked prompts are never touched.
- **New `plan/picking.py`:** unit complexity, night/fire units, and the §14.4 comparison picks. M7's test-unit picking goes here later.
- **New `stickman.compare` package:**

  | Module | Contents |
  |---|---|
  | `setup.py` | `compare.json`, the frozen copy of the plan, the run list |
  | `runs.py` | one run folder per model/size/reference setting, rendered with the normal `Renderer` (so a daily-limit pause resumes the next day) |
  | `report.py` | the numbers and `export/compare.html` |

- **`cli.py`:** new `bootstrap` and `compare` commands. `generate` rebuilds prompts before rendering. Pause messages name the command that continues the work.

The review page (M6), approval gates, test units and library reuse (M7) stay in later plans.

**Tech Stack:** Python 3.12 (uv), typer, rich, pydantic v2, ruamel.yaml, httpx, Pillow, numpy, pytest. No new dependencies.

**Source documents:** `spec.md` (§2.3, §5.2, §5.4, §7.4, §8, §9.4–9.7, §10.2, §13, §14.4, §17, §18 #7, #9, #23), `plan.md` (M5 row; the small-size candidate), `docs/m0-findings.md`, `docs/m4-qc-check.md`. Section numbers like "§8.1" refer to `spec.md`.

## Global Constraints

- **Python and packaging:** Python **3.12**, package `stickman` in `src/stickman/`, managed with **uv**. Run everything with `uv run …`.
- **Runtime dependencies:** `typer`, `rich`, `pydantic>=2`, `pydantic-settings`, `ruamel.yaml`, `httpx`, `pillow`, `numpy`. Nothing new. Dev dependency: `pytest`.
- **Secrets:**
  - They live **only** in `.env` as `CF_ACCOUNT_ID` and `CF_API_TOKEN`. Never write them to config, logs, caches, `state.json`, `bootstrap.json`, `compare.json`, the ledger, reports or test fixtures.
  - Run logs, stored errors and console messages built from Cloudflare errors all mask the token and the account id as `***`. Mask before any cut (`shorten`), because a cut can split a secret.
- **`style_refs/`:** the stock images there are **never** sent to any API. Reference images are only ever read through `stickman.render.references.ReferenceFiles`, which refuses them. That includes the anchor sent with the mascot-sheet candidates.
- **Models:** all Pydantic models use `extra="forbid"`. The one exception is `VisionReport`, which is parsed from an LLM reply (Task 2). `state.json`, `bootstrap.json`, `compare.json` and every YAML file have `schema_version: 1`.
- **API calls:**
  - They go only through `stickman.cf.client.CloudflareClient` (`chat`, `generate_image`).
  - Every call goes through `stickman.meter.Meter.run`, which adds a ledger entry after it. Image and vision calls are budget-checked before they start.
  - Ledger kinds: anchor candidates use `anchor`, mascot-sheet candidates `sheet`, unit images `image`, checks `vision`, and planner calls `llm`.
  - Tests **never** touch the network. They use `FakeChat` and `FakeImages` from `tests/conftest.py`.
- **Writes:**
  - Files are written with `stickman.fsutil.safe_write`.
  - `state.json` is written only through `StateStore`, and `bootstrap.json` only through `BootstrapStore`, after **every** change.
  - `plan.yaml` is written with `stickman.plan.store.write_plan(expected_hash=…)`, and `config/mascot.yaml` with ruamel round-trip, so comments survive.
- **CLI:**
  - Exit codes: `0` success; `1` user or validation error; `2` a pause, which includes waiting for an approval; `3` an auth or config error.
  - `generate`, `resume` and `compare` print `Project: <folder name>` as their first line. `bootstrap` has no project, so its first line is `Bootstrap: style v<N>`.
- **Tests:** `pytest`. Tests that call the real API are marked `live` and excluded by default. `test_resume` takes about 90 s; run it with the full suite at the end of each task.
- **Paid calls:** nothing in Tasks 1–12 calls the real API. Task 13 spends neurons and needs the user at two points, so it **needs the user's go-ahead first**.
- **Commits:**
  - Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Pass it as a second `-m`.
  - Never commit `config/style.yaml`, `config/visual_rules.yaml`, `config/mascot.yaml`, `config/pricing.yaml`, `.env.example`, `COMPACT-SUMMARY.md` or `.superpowers/`. `config/settings.yaml` is committed, but no task here changes it.
- **Shell:**
  - Use Git Bash from the workspace root `C:\huSSNAIN PROJECTS\Tan Project`.
  - uv is not on PATH, so first run `export PATH="/c/Users/Operator PC/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"`.
  - Python code goes in a file that you then run with `uv run python <file>`. A heredoc holding Python code fails in this shell.

## Decisions made while writing this plan

- **The user's decisions (2026-09-26):**
  - M5 branches from `main` after M4 merged (PR #5). M6 will branch from M5 (stacked branches, one PR each).
  - Both run as SDD **without per-task reviews**, then one whole-branch review, one fix wave and a scoped re-review.
  - **Bootstrap approval is on the command line in M5**: `stickman bootstrap --approve-anchor N` and `--approve-mascot N`. M6 adds the same actions to the review page's Sheets view.
  - **`compare` runs Klein 4B only**, in three runs:
    - `klein-4b-refs`: the project's size, with references;
    - `klein-4b-no-refs`: the project's size, no references;
    - `klein-4b-small-refs`: 1280×720 (720×1280 for 9:16), with references. This tests `plan.md`'s small-size candidate against native 1920×1088.
  - **Klein 9B is left out of `compare`** (about 22k neurons, and commercial use of its output is still unconfirmed). `bootstrap.model` stays Klein 9B, as the user decided on 2026-09-23.
- **Where the candidates live:**
  - Candidates go in `library/_bootstrap/v<style_version>/anchor/c<N>.png` and `…/mascot/c<N>.png`, with `bootstrap.json` beside them. `library/` is git-ignored.
  - Each candidate PNG carries its record (seed, model, prompt, cost) in the `stickman` text chunk, like a history image. A candidate saved just before a kill is added back from it.
  - `load_library` only reads `library/characters/`, so the folder doesn't disturb it.
- **Bootstrap steps:**
  - **Step order:** anchor, then mascot. `stickman bootstrap` works on the first step not yet done.
  - **Candidate count:** it makes candidates until the step has `--candidates` of them (default `bootstrap.anchor_candidates` 4, or the new `bootstrap.mascot_candidates` 3). A higher number adds more.
  - **After a run:** it checks every candidate, prints them best first with their QC result, and exits 2 (waiting for approval). A candidate whose check couldn't reach the vision model is checked again next run, with no new image, like a unit (spec §9.4).
  - **Approving:**
    - Approving the anchor writes `library/style/anchor_v<N>.png`, plus `anchor_v<N>_ref.png` resized to fit `image.ref_max_side`.
    - Approving the mascot writes `mascot.sheet` and `mascot.ref`, then `seed` and `model` into `config/mascot.yaml`. That file is created from the packaged default when it's missing.
    - A candidate that failed QC can still be approved, with a warning: your judgement is the final gate.
  - **Re-approving the anchor** is allowed. Units whose images used the old one become stale (their reference hashes change).
- **Bootstrap prompts:**
  - They follow §8.1 word for word. The mascot-sheet prompt also carries the unit prompts' "image 0 shows the drawing style only" sentence, because it is sent with the anchor in slot 0.
  - The anchor's scene is the new setting `bootstrap.anchor_scene`: `two_figures` (the §8.1 default) or `one_figure` (the fallback if the anchor leaks, §8.1 [M0]).
  - QC expects 2 figures (or 1) for the anchor and 1 for the mascot sheet. There's no mascot reference for the vision check yet, so `mascot_matches_sheet` doesn't count.
- **The anchor-leak check (§8.1 [M0], §15 #12)** isn't a separate probe. In the report, `klein-4b-refs` against `klein-4b-no-refs` shows the `character_count` failures with and without the two-figure anchor. If the refs run shows extra figures, the user switches `bootstrap.anchor_scene` and re-bootstraps.
- **Prompts are rebuilt when references appear:**
  - Before bootstrap, every prompt was built with no reference paragraph. Once the anchor exists, `generate` sends reference images, and the prompt must say what each one is.
  - So before rendering, `generate` rebuilds the `image_prompt` of every unlocked unit whose prompt the tool built. That's a prompt which, with its "Reference images:" paragraph removed, equals the builder's output with no references. The result goes to `plan.yaml`, hash-checked, with a notice.
  - A prompt that isn't tool-built was hand-edited. It's left as it is (M7 locks it), with a warning when its images would be sent with references it doesn't describe. Locked prompts are never touched.
  - Because the tool writes the rebuilt prompt, M7's prompt-lock detection (text ≠ builder output) won't mistake it for a hand edit.
- **`compare` takes an already planned project** (`-p`, default the most recent), not `--script`. Plan the sample with `stickman new` first. The live project `projects/2026-09-25_first-sleep/` is the sample script, already planned.
- **How `compare` runs:**
  - It needs bootstrap to be complete. Extras have no sheets until M7, so they appear in the text only (§7.4).
  - Its folder is `projects/<date>_compare_<source slug>/`. It holds `compare.json`, `source_plan.yaml` (a frozen copy of the plan), `runs/<run id>/` (each with `state.json`, `images/` and `logs/`) and `export/compare.html`. There's no `plan.yaml` at its top, so `resolve_project` never picks it as a project.
  - Running `compare` again continues the newest comparison of the same source project. `--new` starts another.
  - **No QC retries** (`retry.qc_max: 0`): the report measures how often the first image passes. Each image still gets the full QC.
  - Each run's prompts are rebuilt in memory for that run's references. The frozen plan is never written.
- **The report (`export/compare.html` plus the same numbers on the console):** per run it shows:
  - images made, the QC pass rate, the no-text failure rate (images where the vision model saw text), `character_count` failures;
  - the median and 90th-percentile seconds per image, and the average cost per image in USD and neurons.
  - It also suggests `render.timeout_s` (3 × the 90th percentile, rounded up to 10 s) and `render.est_seconds_per_image` (the median). Times are measured at `render.concurrency`, so they include waiting in Cloudflare's queue.
- **M4 items fixed here (Task 2); the others stay parked:**
  - **N1:** the run-start line now counts a unit whose next step is a check (any unchecked current image) as a check, not a new image.
  - **N2:** `recover()` removes `images/<stem>.png` when a unit has no current version.
  - **Rewrite ledger entries** now name their unit (`meter.unit_scope`, Task 1).
  - **An unreadable image** clears the unit's current version, so the next run makes a new one instead of failing again.
  - **`VisionReport` ignores extra keys** in the reply.
  - **Still parked:** 8a (a stop between a soften's plan write and its image restarts the chain), the progress bar counting units that ended planned, and `decide()`'s pixel-before-vision order.
- **Prices:** `pricing.yaml` is re-checked against the dashboard by the user in Task 13. Task 13's live numbers show whether the estimates still hold.

## File Map

```
src/stickman/render/calls.py        RenderClient, StopReason, RunStopped, RunControl, STOPS, UNREACHABLE, ERROR_CHARS,
                                    ImageSpec, error_text, Calls (image, check)                    (new, Task 1)
src/stickman/render/renderer.py     uses Calls; re-exports the names that moved; unit_scope around rewrites;
                                    an unreadable image clears the current version                 (modify, Tasks 1-2)
src/stickman/meter.py               unit_scope, the ledger's unit inside it                         (modify, Task 1)
src/stickman/render/recovery.py     removes the current copy of a unit with no current version     (modify, Task 2)
src/stickman/qc/vision.py           VisionReport ignores extra keys                                 (modify, Task 2)
src/stickman/plan/refresh.py        without_references, PromptRefresh, refresh_prompts, with_prompts (new, Task 3)
src/stickman/settings.py            bootstrap.mascot_candidates, bootstrap.anchor_scene             (modify, Task 4)
src/stickman/defaults/settings.yaml the two new keys                                                (modify, Task 4)
src/stickman/library.py             anchor_path, reference_copy                                     (modify, Task 4)
src/stickman/bootstrap/__init__.py                                                                  (new, empty, Task 4)
src/stickman/bootstrap/prompts.py   ANCHOR_SCENES, anchor_prompt, mascot_prompt, anchor_expected, mascot_expected (new, Task 4)
src/stickman/bootstrap/store.py     Candidate, StepState, BootstrapState, BootstrapStore, ranked,
                                    anchor_done, mascot_done, pending_step                         (new, Task 5)
src/stickman/bootstrap/generate.py  CandidateJob, candidate_jobs, CandidateMaker                    (new, Task 6)
src/stickman/bootstrap/approve.py   ApprovalError, approve_anchor, approve_mascot, set_mascot_approval (new, Task 7)
src/stickman/plan/picking.py        complexity, night_or_fire, Pick, COMPARE_CATEGORIES, pick_compare_units (new, Task 9)
src/stickman/compare/__init__.py                                                                    (new, empty, Task 10)
src/stickman/compare/setup.py       CompareRun, ComparePick, CompareSetup, default_runs, compare_dir,
                                    find_compare, create_compare, load_compare                     (new, Task 10)
src/stickman/compare/runs.py        run_settings, PreparedRun, prepare_run, render_runs             (new, Task 10)
src/stickman/compare/report.py      percentile, RunStats, Cell, collect, report_lines, write_report (new, Task 11)
src/stickman/cli.py                 generate: prompt refresh, N1; _free_plan_line; _exit_for(again=…);
                                    bootstrap (Task 8); compare (Task 12)                          (modify, Tasks 2, 3, 8, 12)
tests/conftest.py                   built_prompts fixture                                           (modify, Task 3)
tests/render/test_calls.py                                                                           (new, Task 1)
tests/plan/test_refresh.py, tests/plan/test_picking.py                                             (new, Tasks 3, 9)
tests/bootstrap/test_prompts.py, test_store.py, test_generate.py, test_approve.py                   (new, Tasks 4-7)
tests/compare/test_setup.py, test_runs.py, test_report.py                                            (new, Tasks 10-11)
tests/test_cli_bootstrap.py, tests/test_cli_compare.py                                              (new, Tasks 8, 12)
tests/test_meter.py, test_library.py, test_settings.py, test_cli_generate.py,
tests/render/test_renderer.py, test_recovery.py, tests/qc/test_vision.py                            (modify)
spec.md, plan.md                    [M5] notes                                                      (modify, Task 12)
docs/m5-bootstrap-compare.md        the live check and the user's decisions                         (new, Task 13)
```

---

### Task 1: `render/calls.py` — a run's image and vision calls, shared by every caller

`Renderer` makes its image and vision calls through private methods tied to `RenderJob`. Bootstrap (Task 6) needs the same calls for images that aren't plan units. This task moves them into a `Calls` class, with no change in behaviour. It also makes a rewrite's planner calls name their unit in the ledger (an M4 parked item).

**Files:**
- Create: `src/stickman/render/calls.py`
- Modify: `src/stickman/render/renderer.py`
- Modify: `src/stickman/meter.py`
- Test: `tests/render/test_calls.py` (new), `tests/test_meter.py`, `tests/render/test_renderer.py`

**Interfaces:**
- Consumes: `Meter.run`, `with_retries`, `pixel_check`, `decide`, the `qc.vision` helpers, `RefImage`, `RunLog`, `QCSettings`, `RetrySettings`. They're all unchanged.
- Produces:
  - `stickman.render.calls`:
    - `RenderClient` (Protocol), `StopReason`, `RunStopped`, `RunControl`, `STOPS`, `UNREACHABLE`, `ERROR_CHARS`. They moved from `renderer.py`, which re-exports them, so `from stickman.render.renderer import RunControl, StopReason` keeps working.
    - `ImageSpec` (Protocol): `model: str`, `prompt: str`, `width: int`, `height: int`, `seed: int`, `steps: int | None`, `references: tuple[RefImage, ...]`, `estimate_usd: float`. `RenderJob` already satisfies it.
    - `error_text(exc: CFError, log: RunLog) -> str`: `"<category>: <masked, shortened message>"`.
    - `Calls(client, meter, log, control, *, retry: RetrySettings, qc: QCSettings, vision_model: str, sleep=asyncio.sleep)`, with:
      - `async image(spec: ImageSpec, *, unit: str | None, kind: Kind = "image") -> Metered[ImageResult]`: retried, ledgered under `kind`, and counted by the breaker as `image`.
      - `async check(image: Image.Image, *, expected: ExpectedPicture, reference: RefImage | None, unit: str | None) -> QCResult`: the pixel checks, then the vision check only if they pass. It raises `CFError` when the checker can't be reached or the error stops the run.
      - `control: RunControl`, `log: RunLog`.
  - `stickman.meter.unit_scope(unit: str)`: a context manager. Inside it, a `Meter.run` call given no `unit` records `unit` in the ledger.

- [ ] **Step 1: Write the failing tests**

Create `tests/render/test_calls.py`:

```python
import asyncio
import json
from dataclasses import dataclass

import pytest

from stickman.cf.errors import CFError, ErrorCategory
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.qc.vision import ExpectedPicture
from stickman.render.calls import Calls, RunControl, StopReason
from stickman.runlog import RunLog
from stickman.settings import QCSettings, RetrySettings

KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
VISION = "@cf/qwen/qwen3.8-27b"
TRANSIENT = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)
TWO = ExpectedPicture("two stickmen talking", 2, "stickmen: 2")


@dataclass(frozen=True)
class Spec:
    """An image request that isn't a plan unit, like a bootstrap candidate."""

    model: str = KLEIN_9B
    prompt: str = "two stickmen talking"
    width: int = 1024
    height: int = 768
    seed: int = 7
    steps: int | None = None
    references: tuple = ()
    estimate_usd: float = 0.015


async def no_sleep(seconds):
    return None


def make_calls(tmp_path, client, *, retry=None, qc=None):
    meter = Meter(project="bootstrap", ledger=Ledger(tmp_path / "ledger.jsonl"))
    log = RunLog(tmp_path / "run.jsonl", secrets=("tok-secret",))
    retry = retry or RetrySettings()
    return Calls(client, meter, log, RunControl(retry.circuit_breaker), retry=retry, qc=qc or QCSettings(),
                 vision_model=VISION, sleep=no_sleep)


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_an_image_call_is_ledgered_under_its_kind_and_logged(tmp_path, fake_images):
    calls = make_calls(tmp_path, fake_images())
    metered = asyncio.run(calls.image(Spec(), unit="anchor-c1", kind="anchor"))
    assert metered.result.neurons == 207.59
    [entry] = lines(tmp_path / "ledger.jsonl")
    assert (entry["kind"], entry["unit"], entry["model"], entry["billing"]) == ("anchor", "anchor-c1", KLEIN_9B, "billed")
    [logged] = lines(tmp_path / "run.jsonl")
    assert (logged["kind"], logged["unit"], logged["ok"], logged["seed"], logged["width"]) == ("anchor", "anchor-c1", True, 7, 1024)


def test_temporary_errors_of_an_anchor_call_count_as_image_calls(tmp_path, fake_images):
    calls = make_calls(tmp_path, fake_images(lambda call: TRANSIENT), retry=RetrySettings(transient_max=1, circuit_breaker=2))
    with pytest.raises(CFError):
        asyncio.run(calls.image(Spec(), unit="anchor-c1", kind="anchor"))
    assert calls.control.reason is StopReason.CIRCUIT_BREAKER
    assert "(image calls)" in calls.control.detail


def test_no_call_starts_once_the_run_is_stopping(tmp_path, fake_images):
    client = fake_images()
    calls = make_calls(tmp_path, client)
    calls.control.stop(StopReason.BUDGET, "over")
    with pytest.raises(Exception, match="budget"):
        asyncio.run(calls.image(Spec(), unit="anchor-c1", kind="anchor"))
    assert client.calls == []


def test_a_pixel_failure_skips_the_vision_check(tmp_path, fake_images, drawings):
    client = fake_images()
    qc = asyncio.run(make_calls(tmp_path, client).check(drawings.all_black(), expected=TWO, reference=None, unit="anchor-c1"))
    assert (qc.passed, qc.reason, qc.vision) == (False, "safety_filtered", None)
    assert client.chat_calls == []


def test_a_clean_image_is_checked_by_the_vision_model(tmp_path, fake_images, drawings):
    client = fake_images()
    qc = asyncio.run(make_calls(tmp_path, client).check(drawings.clean(), expected=TWO, reference=None, unit="anchor-c1"))
    assert qc.passed and qc.expected_figures == 2 and qc.reference is False
    [call] = client.chat_calls
    assert "Expected: two stickmen talking.\nExpected figures: 2 (stickmen: 2).\n" in call["messages"][0]["content"][0]["text"]
    assert [entry["kind"] for entry in lines(tmp_path / "ledger.jsonl")] == ["vision"]
    assert [entry["unit"] for entry in lines(tmp_path / "ledger.jsonl")] == ["anchor-c1"]


def test_a_checker_that_cannot_be_reached_raises(tmp_path, fake_images, drawings):
    calls = make_calls(tmp_path, fake_images(chat=lambda model, messages: TRANSIENT), retry=RetrySettings(transient_max=0))
    with pytest.raises(CFError) as caught:
        asyncio.run(calls.check(drawings.clean(), expected=TWO, reference=None, unit="anchor-c1"))
    assert caught.value.category is ErrorCategory.TRANSIENT


def test_a_checker_that_answers_unusably_is_a_vision_error(tmp_path, fake_images, drawings):
    calls = make_calls(tmp_path, fake_images(chat=lambda model, messages: "no json here"))
    qc = asyncio.run(calls.check(drawings.clean(), expected=TWO, reference=None, unit="anchor-c1"))
    assert (qc.passed, qc.reason) == (False, "vision_error")
    assert qc.vision_error.startswith("invalid reply: ")
```

Add to `tests/test_meter.py` (use its existing imports; add `unit_scope` to the `stickman.meter` import, and `LLMResult` from `stickman.cf.client` if it isn't imported yet):

```python
def test_calls_inside_a_unit_scope_are_ledgered_under_that_unit(tmp_path):
    path = tmp_path / "ledger.jsonl"
    meter = Meter(project="demo", ledger=Ledger(path))

    async def reply():
        return LLMResult(text="ok", input_tokens=1, output_tokens=1, raw={}, neurons=1.0)

    async def go():
        with unit_scope("006a"):
            await meter.run(reply, kind="llm", model="m", estimate_usd=0.0)
            await meter.run(reply, kind="llm", model="m", estimate_usd=0.0, unit="007")  # its own unit wins
        await meter.run(reply, kind="llm", model="m", estimate_usd=0.0)

    asyncio.run(go())
    units = [json.loads(line)["unit"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert units == ["006a", "007", None]
```

Add to `tests/render/test_renderer.py`:

```python
def test_a_rewrites_llm_calls_are_ledgered_under_its_unit(tmp_path, plan_data, fake_images, drawings, jpeg):
    from stickman.cf.client import LLMResult

    async def reply():
        return LLMResult(text="{}", input_tokens=1, output_tokens=1, raw={}, neurons=2.0)

    class MeteredRewriter(FakeRewriter):
        async def soften(self, unit_id, notes, check=None):
            await run.meter.run(reply, kind="llm", model="@cf/openai/gpt-oss-120b", estimate_usd=0.0)  # no unit given
            return await super().soften(unit_id, notes, check)

    run = Run(tmp_path, plan_data, fake_images([jpeg_of(drawings.all_black()), jpeg]), rewriter=MeteredRewriter(plan_data))
    run.go(run.jobs[:1])
    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["kind"], e["unit"]) for e in entries if e["kind"] == "llm"] == [("llm", "001")]
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/render/test_calls.py tests/test_meter.py tests/render/test_renderer.py -q`
Expected: FAIL. `stickman.render.calls` and `unit_scope` don't exist, and the rewrite's ledger entry has `unit: null`.

- [ ] **Step 3: Add `unit_scope` to `src/stickman/meter.py`**

Add the imports `from collections.abc import Iterator`, `from contextlib import contextmanager` and `from contextvars import ContextVar`, then, after `billing_of`:

```python
_UNIT: ContextVar[str | None] = ContextVar("stickman_unit", default=None)


@contextmanager
def unit_scope(unit: str) -> Iterator[None]:
    """Ledger entries of calls made inside name `unit` when the call names none itself: a soften's or
    redesign's planner calls, which go through StageRunner (spec §5.4). Each asyncio task has its own
    scope, so units rendered at the same time don't mix."""
    token = _UNIT.set(unit)
    try:
        yield
    finally:
        _UNIT.reset(token)
```

At the start of `Meter.run`'s body, add:

```python
        if unit is None:
            unit = _UNIT.get()
```

- [ ] **Step 4: Create `src/stickman/render/calls.py`**

The code is the renderer's `_call`, `_image_fields`, `_image`, `_check` (without reading the file) and `_vision`, made independent of `RenderJob`. `RenderClient`, `StopReason`, `RunStopped`, `RunControl`, `STOPS`, `UNREACHABLE` and `ERROR_CHARS` move here unchanged.

```python
"""A run's image and vision calls (spec §9.4, §9.5, §9.7): budget-checked and ledgered by the meter,
logged, retried, and counted by the circuit breaker. The renderer, bootstrap and compare all make their
calls through a Calls object, so they all stop, pause and record the same way."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any, Protocol

from PIL import Image

from stickman.cf.client import ImageResult, LLMResult
from stickman.cf.errors import CFError, ErrorCategory
from stickman.cf.retry import with_retries
from stickman.ledger import Kind
from stickman.meter import Meter, Metered, billing_of
from stickman.plan.llm import finish_reason
from stickman.qc.decide import QCResult, decide
from stickman.qc.pixel import pixel_check
from stickman.qc.vision import (
    IMAGE_TOKENS,
    VISION_ATTEMPTS,
    VISION_MAX_TOKENS,
    ExpectedPicture,
    VisionReport,
    parse_vision,
    retry_messages,
    vision_messages,
    vision_png,
    vision_prompt,
)
from stickman.render.references import RefImage
from stickman.runlog import RunLog, shorten
from stickman.settings import QCSettings, RetrySettings

ERROR_CHARS = 200  # how much of an API error message a unit keeps in state.json


class RenderClient(Protocol):
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

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = ...,
        max_tokens: int = ...,
        response_format: dict[str, Any] | None = ...,
    ) -> LLMResult: ...


class StopReason(StrEnum):
    DAILY_LIMIT = "daily_limit"
    BUDGET = "budget"
    CIRCUIT_BREAKER = "circuit_breaker"
    AUTH = "auth"


class RunStopped(Exception):
    """The run is stopping, so this request isn't started."""


class RunControl:
    # Move the class body from renderer.py unchanged (docstring, __init__, stop, check, transient, success).
    ...


# Errors that stop the whole run, from any call (spec §9.5).
STOPS: dict[ErrorCategory, StopReason] = {
    ErrorCategory.DAILY_LIMIT: StopReason.DAILY_LIMIT,
    ErrorCategory.AUTH: StopReason.AUTH,
}
# A vision call that ends in one of these (after its retries) never reached the checker (spec §9.4 [M4]).
UNREACHABLE = (ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMITED)


class ImageSpec(Protocol):
    """What an image request is made of. RenderJob (a unit) and CandidateJob (bootstrap) have it."""

    @property
    def model(self) -> str: ...
    @property
    def prompt(self) -> str: ...
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def seed(self) -> int: ...
    @property
    def steps(self) -> int | None: ...
    @property
    def references(self) -> tuple[RefImage, ...]: ...
    @property
    def estimate_usd(self) -> float: ...


def error_text(exc: CFError, log: RunLog) -> str:
    """How an API error is kept and shown. Masked before the cut, which could split a secret."""
    return f"{exc.category}: {shorten(log.mask(exc.message), ERROR_CHARS)}"


class Calls:
    """One run's image and vision calls. `control` is the run's stop flag and circuit breaker; pass
    the one a GuardedChat uses too, so a rewrite's errors reach the same breaker."""

    def __init__(
        self,
        client: RenderClient,
        meter: Meter,
        log: RunLog,
        control: RunControl,
        *,
        retry: RetrySettings,
        qc: QCSettings,
        vision_model: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._meter = meter
        self.log = log
        self.control = control
        self._retry = retry
        self._qc = qc
        self._vision_model = vision_model
        self._sleep = sleep

    async def image(self, spec: ImageSpec, *, unit: str | None, kind: Kind = "image") -> Metered[ImageResult]:
        """One image, with rate limits and temporary errors retried (spec §9.5). The ledger records it
        under `kind` (image, anchor or sheet); the breaker counts it as an `image` call either way."""
        return await with_retries(lambda: self._image_once(spec, unit, kind), self._retry, sleep=self._sleep)

    async def _image_once(self, spec: ImageSpec, unit: str | None, kind: Kind) -> Metered[ImageResult]:
        fields = {
            "seed": spec.seed, "width": spec.width, "height": spec.height, "steps": spec.steps,
            "refs": [ref.label for ref in spec.references], "prompt": shorten(spec.prompt),
        }
        metered = await self._call(
            unit, kind, "image",
            lambda: self._client.generate_image(
                spec.model, prompt=spec.prompt, width=spec.width, height=spec.height, seed=spec.seed,
                steps=spec.steps, input_images=[ref.data for ref in spec.references],
            ),
            model=spec.model, estimate=spec.estimate_usd, fields=fields,
        )
        self.log.write(
            kind=kind, unit=unit, model=spec.model, **fields, latency_s=round(metered.latency_s, 2),
            ok=True, billing="billed", usd=metered.usd, neurons=metered.result.neurons,
            request_id=metered.result.request_id,
        )
        return metered

    async def check(
        self, image: Image.Image, *, expected: ExpectedPicture, reference: RefImage | None, unit: str | None
    ) -> QCResult:
        """Pixel checks, then the vision check only if they pass (spec §11.2)."""
        pixel = await asyncio.to_thread(pixel_check, image, self._qc)
        common = {
            "expected_figures": expected.figures,
            "min_figures": expected.fewest,
            "reference": reference is not None,
            "min_idea_score": self._qc.min_idea_score,
        }
        if pixel.reason is not None or not self._qc.vision:
            return decide(pixel, **common)
        report, error = await self._vision(image, expected, reference, unit)
        return decide(pixel, report, vision_error=error, **common)

    async def _vision(
        self, image: Image.Image, expected: ExpectedPicture, reference: RefImage | None, unit: str | None
    ) -> tuple[VisionReport | None, str | None]:
        # Move the body of Renderer._vision here unchanged, with these substitutions:
        #   job.vision_reference -> reference;   job.expected -> expected;   job.unit_id -> unit
        #   self._call(job, "vision", …)        -> self._call(unit, "vision", "vision", …)
        #   self._error_text(exc)               -> error_text(exc, self.log)
        #   self._log                           -> self.log
        ...

    async def _call(
        self,
        unit: str | None,
        kind: Kind,
        breaker: str,
        request: Callable[[], Awaitable[Any]],
        *,
        model: str,
        estimate: float,
        fields: dict[str, Any],
        cost_of: Callable[[Any], float | None] | None = None,
    ) -> Metered[Any]:
        """One call, metered and logged when it fails. `breaker` is the kind the circuit breaker counts
        its temporary errors under (spec §9.5 [M4]): image, vision or llm."""
        self.control.check()
        started = time.perf_counter()
        try:
            metered = await self._meter.run(
                request, kind=kind, model=model, estimate_usd=estimate, unit=unit, cost_of=cost_of
            )
        except CFError as exc:
            self.log.write(
                kind=kind, unit=unit, model=model, **fields,
                latency_s=round(time.perf_counter() - started, 2), ok=False, error=str(exc.category),
                status=exc.status, message=shorten(self.log.mask(exc.message)),  # masked before the cut
                billing=billing_of(exc), usd=estimate,
            )
            if exc.category is ErrorCategory.TRANSIENT:
                self.control.transient(breaker)
            raise
        self.control.success(breaker)
        return metered
```

Fill in both `...` bodies by **moving** the code (`RunControl`'s whole class, and `_vision`'s loop) from `renderer.py`. Don't rewrite it. The old `_call` logged failures with `kind=kind` and counted the breaker under `kind`; the new one keeps the log's `kind` and counts under `breaker`. The only difference is that anchor and sheet calls count as `image`.

- [ ] **Step 5: Make `Renderer` use `Calls`**

In `src/stickman/render/renderer.py`:

1. Delete the class bodies and constants that moved (`RenderClient`, `StopReason`, `RunStopped`, `RunControl`, `STOPS`, `UNREACHABLE`, `ERROR_CHARS`). Import them instead, so existing imports keep working:

```python
from stickman.render.calls import (  # noqa: F401  (re-exported: the CLI and tests import them from here)
    ERROR_CHARS,
    STOPS,
    UNREACHABLE,
    Calls,
    RenderClient,
    RunControl,
    RunStopped,
    StopReason,
    error_text,
)
```

2. At the end of `Renderer.__init__` (after `self.control` is set), add:

```python
        self._calls = Calls(client, meter, log, self.control, retry=retry, qc=qc, vision_model=vision_model, sleep=sleep)
```

3. Delete `_call`, `_image_fields`, `_image` and `_vision`. Replace `_generate` and `_check` with:

```python
    async def _generate(self, job: RenderJob, step: Render) -> tuple[Version, Image.Image]:
        metered = await self._calls.image(job, unit=job.unit_id)
        reason = step.reason if step.retry_of is not None else None
        return self._save(job, metered, retry_of=step.retry_of, reason=reason)

    async def _check(self, job: RenderJob, version: Version, image: Image.Image | None) -> QCResult:
        if image is None:  # made by an earlier run
            image = decode_image((self._store.project_dir / version.file).read_bytes())
        return await self._calls.check(image, expected=job.expected, reference=job.vision_reference, unit=job.unit_id)
```

4. Replace `_error_text`'s body with `return error_text(exc, self._log)`.

5. In `_rewrite`, wrap the rewrite call so its planner calls name the unit:

```python
        try:
            with unit_scope(job.unit_id):
                unit = await rewrite(job.unit_id, step.notes, check)
        except CFError as exc:
```

   Add `unit_scope` to the `from stickman.meter import …` line.

6. Remove the imports that are now unused (`with_retries`, `finish_reason`, `pixel_check`, `decide`, the `qc.vision` names, `billing_of`, `Metered` if unused, `StrEnum`, `Protocol`, `Sequence` if unused). Run `uv run python -m pyflakes` if it's available; otherwise read the import list against the file.

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/render tests/test_meter.py tests/test_cli_generate.py -q`
Expected: PASS, including every existing renderer and resume test.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS (about 770 tests).

```bash
git add src/stickman/render/calls.py src/stickman/render/renderer.py src/stickman/meter.py tests/render/test_calls.py tests/test_meter.py tests/render/test_renderer.py
git commit -m "refactor: image and vision calls move to render/calls.py for bootstrap and compare; a rewrite's planner calls name their unit in the ledger" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The M4 items parked for M5

These are four small fixes, each with its test:
- **N1:** the run-start line counts a unit whose next step is a check as a check.
- **N2:** `recover()` removes a stale current copy.
- **Unreadable image:** it clears the unit's current version.
- **`VisionReport`:** it ignores extra keys.

**Files:**
- Modify: `src/stickman/cli.py` (`_print_run_start`)
- Modify: `src/stickman/render/recovery.py` (`_restore_current_images`)
- Modify: `src/stickman/render/renderer.py` (`_unit`, the `Check` branch)
- Modify: `src/stickman/qc/vision.py` (`VisionReport`)
- Test: `tests/test_cli_generate.py`, `tests/render/test_recovery.py`, `tests/render/test_renderer.py`, `tests/qc/test_vision.py`

**Interfaces:**
- Consumes: `chain_of` (`render/chain.py`), `StateStore.finish`, `Renderer._finish`.
- Produces: `cli._check_only(store: StateStore, jobs: list[RenderJob]) -> set[str]`. There are no new public names.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli_generate.py`, change the expected text in `test_images_made_before_qc_are_checked_without_a_new_image` to the new wording:

```python
    assert ("Checking 1 image(s) that have no check yet (made before QC, or left unchecked when the checker "
            "couldn't be reached). Each is checked, not made again, unless it fails its check; then it is "
            "retried like any other.") in result.output
```

Then add:

```python
def test_an_image_left_unchecked_by_a_vision_outage_is_counted_as_a_check(workspace, monkeypatch, fake_images):
    outage = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)
    (workspace / "config").mkdir()
    (workspace / "config" / "settings.yaml").write_text("retry:\n  transient_max: 0\n", encoding="utf-8")
    use_images(monkeypatch, fake_images(chat=lambda model, messages: outage))
    generate(workspace, "--limit", "1")
    assert statuses(workspace)["001"] == "failed"
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace, "--limit", "1")
    assert result.exit_code == 0, result.output
    assert "Checking 1 image(s) that have no check yet" in result.output
    assert not any(row.startswith("Generating") for row in result.output.splitlines())
    assert client.calls == [] and len(client.chat_calls) == 1
```

In `tests/render/test_recovery.py`:

```python
def test_the_current_copy_of_a_unit_with_no_current_version_is_removed(tmp_path):
    store = StateStore.load(tmp_path)
    history_image(tmp_path, 1)
    store.add_version("006a", version(1), status="generated")
    store.finish("006a", "failed", current=None, clear_current=True, error="refused: no")
    copy = tmp_path / "images" / "006a_00-21.0.png"
    copy.write_bytes(b"old design")
    recover(store, EXPECTED)
    assert not copy.exists()
```

In `tests/render/test_renderer.py`:

```python
def test_an_unreadable_image_is_made_again_next_run(tmp_path, plan_data, fake_images):
    run = Run(tmp_path, plan_data, fake_images(), qc=QCSettings(vision=False))
    run.go(run.jobs[:1])
    unit = run.store.unit("001")
    unit.versions[0].qc = None  # it still needs its check
    run.store.save()
    (run.project / unit.versions[0].file).write_bytes(b"not a png any more")
    run.qc = QCSettings()
    asyncio.run(run.make_renderer(fake_images()).run(run.jobs[:1]))
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("failed", None)
    assert unit.error.startswith("bad_image: can't read images/_history/001_v1.png")
    assert not (run.project / "images" / "001_00-00.0.png").exists()
    later = fake_images()
    asyncio.run(run.make_renderer(later).run(run.jobs[:1]))
    assert len(later.calls) == 1  # a new image, not the same failing check
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("generated", 2)
```

In `tests/qc/test_vision.py` (add `import json` if missing):

```python
def test_an_extra_key_in_the_reply_is_ignored():
    reply = "\n\n```json\n" + json.dumps({
        "has_text": False, "text_seen": "", "style_ok": True, "anatomy_ok": True, "watermark_like": False,
        "character_count": 1, "matches_visual_idea": 4, "mascot_matches_sheet": None, "notes": "",
        "confidence": 0.9,
    }) + "\n```"
    report, errors = parse_vision(reply, finish_reason="stop")
    assert errors == [] and report is not None
    assert "confidence" not in report.model_dump()
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_cli_generate.py tests/render/test_recovery.py tests/render/test_renderer.py tests/qc/test_vision.py -q`
Expected: 5 FAIL:
- the old run-start wording;
- the outage unit counted as "Generating";
- the copy not removed;
- the unit stuck on a failing check;
- the extra key rejected.

- [ ] **Step 3: Fix the run-start count (N1)**

In `src/stickman/cli.py`, add `from stickman.render.chain import chain_of`, and add this helper above `_print_run_start`:

```python
def _check_only(store: StateStore, jobs: list[RenderJob]) -> set[str]:
    """Units whose next step is a check of an image they already have: made before QC, saved just
    before a kill, or left unchecked because the checker couldn't be reached (spec §9.4 [M4])."""
    ids = set()
    for job in jobs:
        chain = chain_of(store.unit(job.unit_id), job.fingerprint)
        if chain and chain[-1].qc is None:
            ids.add(job.unit_id)
    return ids
```

In `_print_run_start`, replace the `check_only = …` line with `check_only = _check_only(store, jobs)`. Replace the message with:

```python
        console.print(escape(
            f"Checking {len(check_only)} image(s) that have no check yet (made before QC, or left unchecked when "
            "the checker couldn't be reached). Each is checked, not made again, unless it fails its check; then it "
            "is retried like any other."
        ))
```

- [ ] **Step 4: Remove a stale current copy (N2)**

In `src/stickman/render/recovery.py` `_restore_current_images`, replace

```python
        if version is None:
            continue
```

with

```python
        if version is None:
            # No version shows the unit's latest design (spec §5.2 [M4]): no current copy either.
            (store.project_dir / "images" / f"{want.stem}.png").unlink(missing_ok=True)
            continue
```

- [ ] **Step 5: An unreadable image clears the current version**

In `src/stickman/render/renderer.py` `_unit`, replace the `(OSError, ImageDecodeError)` handler of the `Check` branch with:

```python
                    except (OSError, ImageDecodeError) as exc:
                        # The file can't be checked, so no version counts as current: the next run
                        # makes a new image instead of failing the same check again.
                        self._finish(job, "failed", None, f"bad_image: can't read {chain[-1].file}: {exc}")
                        return
```

- [ ] **Step 6: `VisionReport` ignores extra keys**

In `src/stickman/qc/vision.py`, change `VisionReport`'s config and docstring:

```python
class VisionReport(BaseModel):
    """The vision model's answer (spec §11.2 schema). The one model with extra="ignore": it is parsed
    from an LLM reply, and a key the schema doesn't have is dropped rather than failing the check. The
    schema shown to the model still says additionalProperties: false, so the prompt is unchanged."""

    model_config = ConfigDict(extra="ignore", json_schema_extra={"additionalProperties": False})
```

- [ ] **Step 7: Run the tests, the full suite, and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/cli.py src/stickman/render/recovery.py src/stickman/render/renderer.py src/stickman/qc/vision.py tests/test_cli_generate.py tests/render/test_recovery.py tests/render/test_renderer.py tests/qc/test_vision.py
git commit -m "fix: the run start counts every unchecked image as a check, recovery removes a current copy with no current version, an unreadable image is made again, and extra keys in a vision reply are ignored" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Rebuild tool-built prompts when reference images appear

**Files:**
- Create: `src/stickman/plan/refresh.py`
- Modify: `src/stickman/cli.py` (`_generate`, new `_refresh_prompts`)
- Modify: `tests/conftest.py` (new `built_prompts` fixture)
- Test: `tests/plan/test_refresh.py` (new), `tests/test_cli_generate.py`

**Interfaces:**
- Consumes: `build_prompt`, `REFERENCE_INTRO`, `ReferenceAvailability` (`prompt/builder.py`); `cast_infos`; `find_references` (`library.py`); `update_unit`, `write_plan`, `LoadedPlan` (`plan/store.py`).
- Produces (`stickman.plan.refresh`):
  - `without_references(prompt: str) -> str`: the prompt with its "Reference images:" paragraph removed.
  - `PromptRefresh(rebuilt: dict[str, str], hand_edited: list[str])`: a frozen dataclass.
  - `refresh_prompts(plan: Plan, *, style: StyleConfig, mascot: MascotConfig, references: ReferenceAvailability) -> PromptRefresh`.
  - `with_prompts(plan: Plan, prompts: Mapping[str, str]) -> Plan`: an in-memory copy with those units' `image_prompt` replaced.
- The conftest fixture `built_prompts(data: dict) -> dict` sets every unit's `image_prompt` in plan data to the builder's output with no references, using the packaged default style and mascot. It changes `data` and returns it.

- [ ] **Step 1: Add the `built_prompts` fixture to `tests/conftest.py`**

Add the imports `from stickman.config_files import load_mascot, load_style`, `from stickman.plan.cast import cast_infos`, `from stickman.plan.models import parse_plan` and `from stickman.prompt.builder import build_prompt`, then:

```python
@pytest.fixture
def built_prompts(tmp_path_factory):
    """Plan data whose image prompts the builder made with no reference images, as `stickman new`
    writes them before bootstrap. Uses the packaged default style.yaml and mascot.yaml."""
    empty = tmp_path_factory.mktemp("defaults")
    style, mascot = load_style(empty), load_mascot(empty)

    def build(data):
        plan = parse_plan(data)
        table = cast_infos(plan.cast, mascot)
        units = {unit.id: unit for unit in plan.units()}
        for scene in data["scenes"]:
            for unit in scene["units"]:
                unit["image_prompt"] = build_prompt(units[unit["id"]], style=style, cast=table, references=None)
        return data

    return build
```

- [ ] **Step 2: Write the failing tests**

Create `tests/plan/test_refresh.py`:

```python
from stickman.config_files import load_mascot, load_style
from stickman.plan.cast import cast_infos
from stickman.plan.models import parse_plan
from stickman.plan.refresh import refresh_prompts, with_prompts, without_references
from stickman.prompt.builder import ReferenceAvailability, build_prompt

NONE = ReferenceAvailability()
ANCHOR_AND_MASCOT = ReferenceAvailability(anchor=True, sheets=frozenset({"mascot"}))


def refresh(tmp_path, plan, references):
    return refresh_prompts(plan, style=load_style(tmp_path), mascot=load_mascot(tmp_path), references=references)


def test_tool_built_prompts_get_the_reference_paragraph(tmp_path, plan_data, built_prompts):
    result = refresh(tmp_path, parse_plan(built_prompts(plan_data)), ANCHOR_AND_MASCOT)
    assert list(result.rebuilt) == ["001", "002a", "002b"] and result.hand_edited == []
    prompt = result.rebuilt["001"]
    assert "Reference images: image 0 shows the drawing style only" in prompt
    assert "Image 1 shows Everyman:" in prompt
    assert prompt.endswith(load_style(tmp_path).strict_clause.strip())


def test_prompts_that_already_match_are_left_alone(tmp_path, plan_data, built_prompts):
    result = refresh(tmp_path, parse_plan(built_prompts(plan_data)), NONE)
    assert (result.rebuilt, result.hand_edited) == ({}, [])


def test_a_prompt_built_with_references_goes_back_when_they_are_gone(tmp_path, plan_data, built_prompts):
    plan = parse_plan(built_prompts(plan_data))
    with_refs = with_prompts(plan, refresh(tmp_path, plan, ANCHOR_AND_MASCOT).rebuilt)
    back = refresh(tmp_path, with_refs, NONE)
    assert back.rebuilt == {unit.id: unit.image_prompt for unit in plan.units()}


def test_locked_prompts_are_never_touched(tmp_path, plan_data, built_prompts):
    data = built_prompts(plan_data)
    data["scenes"][0]["units"][0]["prompt_locked"] = True
    result = refresh(tmp_path, parse_plan(data), ANCHOR_AND_MASCOT)
    assert "001" not in result.rebuilt and "001" not in result.hand_edited


def test_a_hand_edited_prompt_is_left_and_reported_only_when_references_go_with_it(tmp_path, plan_data):
    plan = parse_plan(plan_data)  # its prompts are "prompt for <unit>": not what the builder makes
    assert refresh(tmp_path, plan, ANCHOR_AND_MASCOT).hand_edited == ["001", "002a", "002b"]
    assert refresh(tmp_path, plan, ANCHOR_AND_MASCOT).rebuilt == {}
    assert refresh(tmp_path, plan, NONE).hand_edited == []  # no reference images are sent with them


def test_an_empty_prompt_is_neither_rebuilt_nor_reported(tmp_path, plan_data):
    plan_data["scenes"][0]["units"][0]["image_prompt"] = ""
    result = refresh(tmp_path, parse_plan(plan_data), ANCHOR_AND_MASCOT)
    assert "001" not in result.rebuilt and "001" not in result.hand_edited


def test_without_references_removes_only_the_reference_paragraph(tmp_path, plan_data):
    style, mascot = load_style(tmp_path), load_mascot(tmp_path)
    plan = parse_plan(plan_data)
    unit = plan.units()[0]
    table = cast_infos(plan.cast, mascot)
    with_refs = build_prompt(unit, style=style, cast=table, references=["mascot"])
    bare = build_prompt(unit, style=style, cast=table, references=None)
    assert with_refs != bare
    assert without_references(with_refs) == bare
    assert without_references(bare) == bare


def test_with_prompts_replaces_only_the_given_units(plan_data):
    plan = parse_plan(plan_data)
    changed = with_prompts(plan, {"002a": "new prompt"})
    assert [unit.image_prompt for unit in changed.units()] == ["prompt for 001", "new prompt", "prompt for 002b"]
    assert [unit.image_prompt for unit in plan.units()] == ["prompt for 001", "prompt for 002a", "prompt for 002b"]
```

Add to `tests/test_cli_generate.py` (it already imports `Image`, `parse_plan`, `to_document`, `write_plan`):

```python
def anchor(workspace):
    path = workspace / "library" / "style" / "anchor_v1_ref.png"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (512, 384), "white").save(path, format="PNG")
    return path.read_bytes()


def test_generate_rebuilds_tool_built_prompts_once_the_anchor_exists(workspace, monkeypatch, fake_images, plan_data, built_prompts):
    path = project(workspace) / "plan.yaml"
    write_plan(path, to_document(parse_plan(built_prompts(plan_data))), expected_hash=None)
    path.write_text("# my notes\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    anchor_bytes = anchor(workspace)
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Rebuilt the image prompts of 3 unit(s) for the reference images that now exist: 001, 002a, 002b" in result.output
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# my notes\n") and text.count("Reference images: image 0 shows") == 3
    assert all("Reference images: image 0 shows" in call["prompt"] for call in client.calls)
    assert all(call["input_images"] == [anchor_bytes] for call in client.calls)
    again = generate(workspace)
    assert "Rebuilt the image prompts" not in again.output


def test_hand_edited_prompts_are_left_as_they_are_with_a_warning(workspace, monkeypatch, fake_images):
    anchor(workspace)
    before = (project(workspace) / "plan.yaml").read_bytes()
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "so they were left as they are" in result.output and "001, 002a, 002b" in result.output
    assert (project(workspace) / "plan.yaml").read_bytes() == before
    assert client.calls[0]["prompt"] == "prompt for 001"
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `uv run pytest tests/plan/test_refresh.py tests/test_cli_generate.py -q`
Expected: FAIL. `stickman.plan.refresh` doesn't exist, and `generate` sends the old prompts.

- [ ] **Step 4: Create `src/stickman/plan/refresh.py`**

```python
"""Rebuilding tool-built image prompts for the reference images that exist now (spec §7.4 [M5]).

A plan made before bootstrap has prompts with no "Reference images:" paragraph. Once the style anchor
and sheets exist, the images go out with reference images, and the prompt must say what each one is.
Only prompts the tool built are rebuilt: a prompt that, with that paragraph removed, is the builder's
output with no references. A hand-edited prompt is left as it is (M7 locks it), and a locked prompt is
never touched. Because the tool writes the rebuilt text, M7's lock detection never takes it for a hand edit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from stickman.config_files import MascotConfig, StyleConfig
from stickman.plan.cast import cast_infos
from stickman.plan.models import Plan
from stickman.prompt.builder import REFERENCE_INTRO, ReferenceAvailability, build_prompt

# The builder writes the paragraph as one line after a blank line: the intro, then one sentence per slot.
_REFERENCE_PARAGRAPH = re.compile(r"\n\n" + re.escape(REFERENCE_INTRO) + r"[^\n]*")


def without_references(prompt: str) -> str:
    return _REFERENCE_PARAGRAPH.sub("", prompt)


@dataclass(frozen=True)
class PromptRefresh:
    rebuilt: dict[str, str]  # unit id -> its new prompt, in plan order
    hand_edited: list[str]  # unlocked units whose prompt isn't the builder's, sent with references it doesn't describe


def refresh_prompts(
    plan: Plan, *, style: StyleConfig, mascot: MascotConfig, references: ReferenceAvailability
) -> PromptRefresh:
    table = cast_infos(plan.cast, mascot)
    rebuilt: dict[str, str] = {}
    hand_edited: list[str] = []
    for unit in plan.units():
        if unit.prompt_locked or not unit.image_prompt.strip():
            continue  # an empty prompt is `stickman replan`'s job (JobError says so)
        slots = references.for_unit(unit.characters)
        fresh = build_prompt(unit, style=style, cast=table, references=slots)
        if unit.image_prompt == fresh:
            continue
        if without_references(unit.image_prompt) == build_prompt(unit, style=style, cast=table, references=None):
            rebuilt[unit.id] = fresh
        elif slots is not None:
            hand_edited.append(unit.id)
    return PromptRefresh(rebuilt, hand_edited)


def with_prompts(plan: Plan, prompts: Mapping[str, str]) -> Plan:
    if not prompts:
        return plan
    scenes = [
        scene.model_copy(update={"units": [
            unit.model_copy(update={"image_prompt": prompts[unit.id]}) if unit.id in prompts else unit
            for unit in scene.units
        ]})
        for scene in plan.scenes
    ]
    return plan.model_copy(update={"scenes": scenes})
```

- [ ] **Step 5: Rebuild prompts in `generate`, under the lock**

In `src/stickman/cli.py`:
- add `from stickman.library import find_references` and `from stickman.plan.refresh import refresh_prompts, with_prompts`;
- add `LoadedPlan` to the `stickman.plan.store` import.

Add, above `_generate`:

```python
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
    refresh = refresh_prompts(plan, style=ctx.style, mascot=ctx.mascot, references=references)
    if refresh.hand_edited:
        console.print(f"[yellow]{escape(HAND_EDITED_NOTE + ', '.join(refresh.hand_edited))}[/yellow]")
    if not refresh.rebuilt:
        return plan
    for unit_id, prompt in refresh.rebuilt.items():
        update_unit(loaded.doc, unit_id, {"image_prompt": prompt})
    _write_plan(path, loaded.doc, expected_hash=loaded.hash, again=" Nothing was generated; run the command again.")
    console.print(escape(
        f"Rebuilt the image prompts of {len(refresh.rebuilt)} unit(s) for the reference images that now exist: "
        + ", ".join(refresh.rebuilt)
    ))
    return with_prompts(plan, refresh.rebuilt)
```

Change `_generate` so the builder is made after the lock is held and the prompts are rebuilt. Keep everything else as it is:

```python
    try:
        loaded = load_plan(directory / "plan.yaml", library_ids=ctx.library_ids)
    except PlanValidationError as exc:
        _fail("plan.yaml is invalid:\n" + "\n".join(exc.errors), EXIT_USER_ERROR)
    lock = ProjectLock(directory)
    try:
        lock.acquire()
    except LockHeld as exc:
        ...  # unchanged
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
```

(The old `builder = JobBuilder(ctx, plan)` / `expected = builder.expected()` block before the lock is removed.)

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/plan/test_refresh.py tests/test_cli_generate.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/plan/refresh.py src/stickman/cli.py tests/conftest.py tests/plan/test_refresh.py tests/test_cli_generate.py
git commit -m "feat: generate rebuilds tool-built prompts for the reference images that now exist; hand-edited and locked prompts are left as they are" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Bootstrap settings, reference copies and the bootstrap prompts

**Files:**
- Modify: `src/stickman/settings.py` (`BootstrapSettings`)
- Modify: `src/stickman/defaults/settings.yaml`
- Modify: `src/stickman/library.py` (`anchor_path`, `reference_copy`)
- Create: `src/stickman/bootstrap/__init__.py` (empty), `src/stickman/bootstrap/prompts.py`
- Test: `tests/test_settings.py`, `tests/test_library.py`, `tests/bootstrap/test_prompts.py` (new)

**Interfaces:**
- Consumes: `StyleConfig`, `MascotConfig`, `ExpectedPicture`, `REFERENCE_INTRO`.
- Produces:
  - `BootstrapSettings.mascot_candidates: int = 3` (≥ 1) and `BootstrapSettings.anchor_scene: Literal["two_figures", "one_figure"] = "two_figures"`.
  - `library.anchor_path(workspace: Path, style_version: int) -> Path`: `library/style/anchor_v<N>.png`.
  - `library.reference_copy(image: Image.Image, max_side: int) -> bytes`: PNG bytes, longest side ≤ `max_side`, aspect ratio kept (spec §8.3).
  - `stickman.bootstrap.prompts`:
    - `AnchorScene` (the Literal), `ANCHOR_SCENES: dict[AnchorScene, str]`, `ANCHOR_FIGURES: dict[AnchorScene, int]`;
    - `anchor_prompt(style: StyleConfig, scene: AnchorScene) -> str` and `mascot_prompt(style: StyleConfig, mascot: MascotConfig) -> str`;
    - `anchor_expected(scene: AnchorScene) -> ExpectedPicture` and `mascot_expected(mascot: MascotConfig) -> ExpectedPicture`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_settings.py` (it imports `Settings` and `load_settings` already; add `import pytest` if missing):

```python
def test_bootstrap_makes_three_mascot_candidates_from_a_two_figure_anchor_by_default():
    settings = Settings()
    assert (settings.bootstrap.mascot_candidates, settings.bootstrap.anchor_scene) == (3, "two_figures")


def test_the_anchor_scene_is_one_of_two(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("bootstrap:\n  anchor_scene: one_figure\n", encoding="utf-8")
    assert load_settings(path).bootstrap.anchor_scene == "one_figure"
    path.write_text("bootstrap:\n  anchor_scene: three\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path)
```

(Import `ConfigError` from `stickman.settings` if the file doesn't already.)

Add to `tests/test_library.py` (add the imports `import io` and `from PIL import Image`, and `from stickman.library import anchor_path, reference_copy`):

```python
def test_a_reference_copy_fits_the_limit_and_keeps_the_aspect_ratio():
    source = Image.new("RGB", (1024, 768), "white")
    data = reference_copy(source, 512)
    with Image.open(io.BytesIO(data)) as copy:
        assert (copy.format, copy.size) == ("PNG", (512, 384))
    assert source.size == (1024, 768)  # the source is never altered


def test_a_small_image_is_not_enlarged():
    with Image.open(io.BytesIO(reference_copy(Image.new("L", (300, 200), 255), 512))) as copy:
        assert (copy.size, copy.mode) == ((300, 200), "RGB")


def test_the_anchor_lives_in_library_style(tmp_path):
    assert anchor_path(tmp_path, 2) == tmp_path / "library" / "style" / "anchor_v2.png"
```

Create `tests/bootstrap/test_prompts.py`:

```python
from stickman.bootstrap.prompts import anchor_expected, anchor_prompt, mascot_expected, mascot_prompt
from stickman.config_files import load_mascot, load_style


def test_the_anchor_prompt_is_style_scene_and_strict_clause(tmp_path):
    style = load_style(tmp_path)
    prompt = anchor_prompt(style, "two_figures")
    assert prompt.startswith(style.style_text.strip() + "\n\nScene: two stickmen talking; the taller one gestures")
    assert "A light hatched ground shadow.\n\n" in prompt
    assert prompt.endswith(style.strict_clause.strip())
    assert "Reference images" not in prompt  # the anchor is made with no reference images


def test_the_one_figure_anchor_is_one_stickman_waving(tmp_path):
    prompt = anchor_prompt(load_style(tmp_path), "one_figure")
    assert "Scene: one stickman standing and waving with an open palm." in prompt
    assert anchor_expected("one_figure").figures == 1
    assert anchor_expected("two_figures").figures == 2


def test_the_mascot_sheet_prompt_describes_one_full_body_front_view(tmp_path):
    style, mascot = load_style(tmp_path), load_mascot(tmp_path)
    prompt = mascot_prompt(style, mascot)
    assert (f"Character sheet: a single full-body front view of {mascot.identity.strip()}, wearing "
            f"{mascot.default_outfit.strip()}, standing in a neutral pose, centred, arms relaxed.") in prompt
    assert "Reference images: image 0 shows the drawing style only" in prompt
    assert prompt.endswith(style.strict_clause.strip())


def test_a_mascot_without_an_outfit_is_described_by_identity_alone(tmp_path):
    mascot = load_mascot(tmp_path).model_copy(update={"default_outfit": ""})
    prompt = mascot_prompt(load_style(tmp_path), mascot)
    assert f"front view of {mascot.identity.strip()}, standing in a neutral pose" in prompt


def test_qc_expects_one_figure_on_the_mascot_sheet(tmp_path):
    mascot = load_mascot(tmp_path)
    expected = mascot_expected(mascot)
    assert (expected.figures, expected.fewest, expected.cast) == (1, 1, "Everyman: 1")
    assert expected.visual_idea.startswith("a character sheet: one full-body front view of the main character")
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_settings.py tests/test_library.py tests/bootstrap/test_prompts.py -q`
Expected: FAIL (missing settings, functions and module).

- [ ] **Step 3: Add the settings**

In `src/stickman/settings.py`:

```python
class BootstrapSettings(_Section):
    anchor_candidates: int = Field(4, ge=1)
    mascot_candidates: int = Field(3, ge=1)
    anchor_scene: Literal["two_figures", "one_figure"] = "two_figures"  # one_figure if the anchor leaks (spec §8.1)
    model: str = "@cf/black-forest-labs/flux-2-klein-9b"
```

In `src/stickman/defaults/settings.yaml`, make the `bootstrap:` section:

```yaml
bootstrap:
  anchor_candidates: 4
  mascot_candidates: 3
  anchor_scene: two_figures   # two_figures | one_figure (use one_figure if the anchor's figures leak into scenes)
  model: "@cf/black-forest-labs/flux-2-klein-9b"  # dev is unusable synchronously (M0: HTTP 408 timeouts)
```

- [ ] **Step 4: Add `anchor_path` and `reference_copy` to `src/stickman/library.py`**

Add `import io` and `from PIL import Image`, then, next to `anchor_ref_path`:

```python
def anchor_path(workspace: Path, style_version: int) -> Path:
    """The approved style anchor, full size (spec §2.2, §8.1)."""
    return workspace / "library" / "style" / f"anchor_v{style_version}.png"


def reference_copy(image: Image.Image, max_side: int) -> bytes:
    """A reference copy (spec §8.3): the longest side at most `max_side`, aspect ratio kept, PNG. The
    source image is never altered, and a smaller image is not enlarged."""
    copy = image.convert("RGB")
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="PNG")
    return buffer.getvalue()
```

- [ ] **Step 5: Create `src/stickman/bootstrap/__init__.py` (empty) and `src/stickman/bootstrap/prompts.py`**

```python
"""The bootstrap prompts (spec §8.1), and what QC expects each candidate to show."""

from __future__ import annotations

from typing import Literal

from stickman.config_files import MascotConfig, StyleConfig
from stickman.prompt.builder import REFERENCE_INTRO
from stickman.qc.vision import ExpectedPicture

AnchorScene = Literal["two_figures", "one_figure"]

ANCHOR_SCENES: dict[AnchorScene, str] = {
    "two_figures": "two stickmen talking; the taller one gestures with an open palm, the shorter one listens. "
                   "A light hatched ground shadow",
    "one_figure": "one stickman standing and waving with an open palm. A light hatched ground shadow",
}
ANCHOR_FIGURES: dict[AnchorScene, int] = {"two_figures": 2, "one_figure": 1}


def anchor_prompt(style: StyleConfig, scene: AnchorScene) -> str:
    """Made with no reference images: the anchor is what later images take their style from."""
    return f"{style.style_text.strip()}\n\nScene: {ANCHOR_SCENES[scene]}.\n\n{style.strict_clause.strip()}"


def mascot_prompt(style: StyleConfig, mascot: MascotConfig) -> str:
    """Sent with the anchor in slot 0, so it says, like every unit prompt, that image 0 is style only."""
    outfit = mascot.default_outfit.strip()
    wearing = f", wearing {outfit}" if outfit else ""
    sheet = (
        f"Character sheet: a single full-body front view of {mascot.identity.strip()}{wearing}, "
        "standing in a neutral pose, centred, arms relaxed."
    )
    return f"{style.style_text.strip()}\n\n{sheet}\n\n{REFERENCE_INTRO}\n\n{style.strict_clause.strip()}"


def anchor_expected(scene: AnchorScene) -> ExpectedPicture:
    figures = ANCHOR_FIGURES[scene]
    return ExpectedPicture(ANCHOR_SCENES[scene], figures, f"stickmen: {figures}" if figures > 1 else "stickman: 1")


def mascot_expected(mascot: MascotConfig) -> ExpectedPicture:
    return ExpectedPicture(
        f"a character sheet: one full-body front view of {mascot.identity.strip()}", 1, f"{mascot.name}: 1"
    )
```

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/test_settings.py tests/test_library.py tests/bootstrap/test_prompts.py -q`
Expected: PASS, including `test_packaged_default_settings_match_model_defaults`.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/settings.py src/stickman/defaults/settings.yaml src/stickman/library.py src/stickman/bootstrap tests/test_settings.py tests/test_library.py tests/bootstrap/test_prompts.py
git commit -m "feat: bootstrap prompts for the style anchor and mascot sheet, reference copies, and the mascot_candidates and anchor_scene settings" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The bootstrap store — candidates, approvals and recovery

**Files:**
- Create: `src/stickman/bootstrap/store.py`
- Test: `tests/bootstrap/test_store.py` (new)

**Interfaces:**
- Consumes: `QCResult` (`qc/decide.py`), `safe_write`, `read_metadata` (`render/images.py`), `anchor_path`, `anchor_ref_path` (`library.py`), `MascotConfig`.
- Produces (`stickman.bootstrap.store`):
  - `BOOTSTRAP_DIR = "library/_bootstrap"`, `STATE_FILE = "bootstrap.json"`, `Step = Literal["anchor", "mascot"]`.
  - `Candidate` (pydantic): `n`, `file` (relative to the workspace, forward slashes), `seed`, `model`, `width`, `height`, `prompt_sent`, `refs: list[str]`, `qc: QCResult | None`, `est_cost_usd`, `latency_s`, `created: AwareDatetime`.
  - `StepState`: `candidates: list[Candidate]`, `approved: int | None`, and `candidate(n) -> Candidate | None`.
  - `BootstrapState`: `schema_version`, `style_version`, `anchor: StepState`, `mascot: StepState`.
  - `BootstrapError(Exception)`: `bootstrap.json` can't be read (CLI exit 1).
  - `BootstrapStore`:
    - `BootstrapStore.load(workspace: Path, style_version: int)`; attributes `.workspace`, `.state`, `.folder` (`library/_bootstrap/v<N>`);
    - `.save()`, `.step(step)`, `.candidate_path(step, n) -> Path`, `.relative(path) -> str`, `.next_number(step) -> int`;
    - `.add(step, candidate)`, `.set_qc(step, n, qc)`, `.approve(step, n)`, `.recover() -> list[str]`.
  - `ranked(candidates: Sequence[Candidate]) -> list[Candidate]`: best first. Passed first, then the highest idea score, then checked before unchecked, then the lowest `n`.
  - `anchor_done(workspace, style_version) -> bool`, `mascot_done(workspace, mascot, style_version) -> bool`, `pending_step(workspace, mascot, style_version) -> Step | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/bootstrap/test_store.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.store import (
    BootstrapError,
    BootstrapStore,
    Candidate,
    anchor_done,
    mascot_done,
    pending_step,
    ranked,
)
from stickman.config_files import load_mascot
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.qc.vision import VisionReport
from stickman.render.images import encode_png
from stickman.settings import QCSettings

PK = timezone(timedelta(hours=5))
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


def candidate(n=1, step="anchor", **changes):
    data = dict(n=n, file=f"library/_bootstrap/v1/{step}/c{n}.png", seed=40 + n, model=KLEIN_9B, width=1024,
                height=768, prompt_sent="a prompt", est_cost_usd=0.015, latency_s=3.5,
                created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    return Candidate(**{**data, **changes})


def test_a_new_store_is_empty_and_saves_nothing_until_asked(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    assert store.folder == tmp_path / "library" / "_bootstrap" / "v1"
    assert store.state.anchor.candidates == [] and store.state.mascot.approved is None
    assert not (store.folder / "bootstrap.json").exists()


def test_candidates_and_approvals_are_saved_at_once(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    store.add("anchor", candidate(2))
    store.add("anchor", candidate(1))
    store.approve("anchor", 2)
    again = BootstrapStore.load(tmp_path, 1)
    assert [c.n for c in again.state.anchor.candidates] == [1, 2]
    assert again.state.anchor.approved == 2
    assert again.step("anchor").candidate(2).seed == 42


def test_approving_an_unknown_candidate_is_refused(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    with pytest.raises(KeyError):
        store.approve("anchor", 3)


def test_the_next_number_is_above_every_record_and_file(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    store.add("anchor", candidate(1))
    stray = store.candidate_path("anchor", 4)
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"left by a killed run")
    assert store.next_number("anchor") == 5
    assert store.next_number("mascot") == 1


def test_a_candidate_saved_just_before_a_kill_is_kept(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    record = candidate(3)
    path = tmp_path / record.file
    path.parent.mkdir(parents=True)
    path.write_bytes(encode_png(Image.new("RGB", (8, 8), "white"), record.model_dump(mode="json")))
    (path.parent / "c4.png.tmp").write_bytes(b"half written")
    (store.folder / "bootstrap.json.tmp").write_bytes(b"{")
    notes = store.recover()
    assert [c.n for c in store.state.anchor.candidates] == [3]
    assert store.state.anchor.candidates[0].qc is None  # checked by the next run
    assert not (path.parent / "c4.png.tmp").exists() and not (store.folder / "bootstrap.json.tmp").exists()
    assert any("c3.png" in note for note in notes)
    assert [c.n for c in BootstrapStore.load(tmp_path, 1).state.anchor.candidates] == [3]


def test_a_file_without_a_record_is_left_as_it_is(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    path = store.candidate_path("mascot", 1)
    path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(path, format="PNG")
    notes = store.recover()
    assert store.state.mascot.candidates == []
    assert any("no record inside" in note for note in notes)


def test_an_unreadable_bootstrap_json_is_an_error(tmp_path):
    folder = tmp_path / "library" / "_bootstrap" / "v1"
    folder.mkdir(parents=True)
    (folder / "bootstrap.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BootstrapError):
        BootstrapStore.load(tmp_path, 1)


def test_candidates_are_ranked_passes_first_then_idea_score(drawings):
    def checked(n, passed, score):
        report = VisionReport(has_text=not passed, style_ok=True, anatomy_ok=True, watermark_like=False,
                              character_count=2, matches_visual_idea=score)
        result = decide(pixel_check(drawings.clean(), QCSettings()), report, expected_figures=2, min_idea_score=3)
        return candidate(n, qc=result)

    order = ranked([checked(1, False, 5), candidate(2), checked(3, True, 3), checked(4, True, 5)])
    assert [c.n for c in order] == [4, 3, 1, 2]


def test_the_pending_step_is_the_anchor_then_the_mascot_then_none(tmp_path):
    mascot = load_mascot(tmp_path)
    assert pending_step(tmp_path, mascot, 1) == "anchor"
    for name in ("anchor_v1.png", "anchor_v1_ref.png"):
        path = tmp_path / "library" / "style" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    assert anchor_done(tmp_path, 1) and not anchor_done(tmp_path, 2)
    assert pending_step(tmp_path, mascot, 1) == "mascot"
    for relative in (mascot.sheet, mascot.ref):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    assert not mascot_done(tmp_path, mascot, 1)  # no seed yet: not approved
    approved = mascot.model_copy(update={"seed": 7, "model": KLEIN_9B})
    assert mascot_done(tmp_path, approved, 1) and not mascot_done(tmp_path, approved, 2)
    assert pending_step(tmp_path, approved, 1) is None
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/bootstrap/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError: stickman.bootstrap.store`.

- [ ] **Step 3: Create `src/stickman/bootstrap/store.py`**

```python
"""Bootstrap's candidates and approvals (spec §8.1), in library/_bootstrap/v<style_version>/.

bootstrap.json lists every candidate with its QC result and each step's approved candidate. Like a
history image, each candidate PNG carries its own record, so one saved just before a kill is added back.
Written only through BootstrapStore, safely, after every change.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from stickman.config_files import MascotConfig
from stickman.fsutil import safe_write
from stickman.library import anchor_path, anchor_ref_path
from stickman.qc.decide import QCResult
from stickman.render.images import read_metadata

BOOTSTRAP_DIR = "library/_bootstrap"
STATE_FILE = "bootstrap.json"
STEPS = ("anchor", "mascot")
Step = Literal["anchor", "mascot"]
_NAME = re.compile(r"^c(?P<n>[1-9]\d*)\.png$")


class BootstrapError(Exception):
    """bootstrap.json can't be read (CLI exit code 1). Nothing is written when this is raised."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Candidate(_Model):
    n: int = Field(ge=1)
    file: str  # relative to the workspace: library/_bootstrap/v1/anchor/c1.png
    seed: int
    model: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    prompt_sent: str
    refs: list[str] = Field(default_factory=list)  # "<path>#sha256:<hex>" per reference image, slot order
    qc: QCResult | None = None  # null until checked
    est_cost_usd: float = Field(ge=0)
    latency_s: float = Field(ge=0)
    created: AwareDatetime


class StepState(_Model):
    candidates: list[Candidate] = Field(default_factory=list)
    approved: int | None = None  # the approved candidate's n

    def candidate(self, n: int) -> Candidate | None:
        return next((c for c in self.candidates if c.n == n), None)


class BootstrapState(_Model):
    schema_version: Literal[1] = 1
    style_version: int = Field(ge=1)
    anchor: StepState = Field(default_factory=StepState)
    mascot: StepState = Field(default_factory=StepState)


class BootstrapStore:
    def __init__(self, workspace: Path, state: BootstrapState) -> None:
        self.workspace = workspace
        self.state = state

    @property
    def folder(self) -> Path:
        return self.workspace / BOOTSTRAP_DIR / f"v{self.state.style_version}"

    @property
    def path(self) -> Path:
        return self.folder / STATE_FILE

    @classmethod
    def load(cls, workspace: Path, style_version: int) -> BootstrapStore:
        store = cls(workspace, BootstrapState(style_version=style_version))
        if store.path.exists():
            try:
                store.state = BootstrapState.model_validate_json(store.path.read_bytes())
            except (ValidationError, ValueError, OSError) as exc:
                raise BootstrapError(f"{store.path}: {exc}") from exc
        return store

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)  # safe_write doesn't create folders
        safe_write(self.path, self.state.model_dump_json(indent=2).encode("utf-8"))

    def step(self, step: Step) -> StepState:
        return self.state.anchor if step == "anchor" else self.state.mascot

    def candidate_path(self, step: Step, n: int) -> Path:
        return self.folder / step / f"c{n}.png"

    def relative(self, path: Path) -> str:
        return path.relative_to(self.workspace).as_posix()

    def _files(self, step: Step) -> dict[int, Path]:
        folder = self.folder / step
        found: dict[int, Path] = {}
        if folder.is_dir():
            for path in folder.iterdir():
                match = _NAME.match(path.name)
                if match:
                    found[int(match["n"])] = path
        return found

    def next_number(self, step: Step) -> int:
        """One above every recorded candidate and every file, so a file a killed run left is never overwritten."""
        used = {c.n for c in self.step(step).candidates} | set(self._files(step))
        return max(used, default=0) + 1

    def add(self, step: Step, candidate: Candidate) -> None:
        state = self.step(step)
        state.candidates = sorted([*state.candidates, candidate], key=lambda c: c.n)
        self.save()

    def set_qc(self, step: Step, n: int, qc: QCResult) -> None:
        candidate = self.step(step).candidate(n)
        if candidate is None:
            raise KeyError(f"no {step} candidate {n}")
        candidate.qc = qc
        self.save()

    def approve(self, step: Step, n: int) -> None:
        if self.step(step).candidate(n) is None:
            raise KeyError(f"no {step} candidate {n}")
        self.step(step).approved = n
        self.save()

    def recover(self) -> list[str]:
        """Remove what an interrupted write left, and add back candidates saved just before a kill."""
        notes: list[str] = []
        self.path.with_name(STATE_FILE + ".tmp").unlink(missing_ok=True)
        adopted: list[str] = []
        unknown: list[str] = []
        for step in STEPS:
            folder = self.folder / step
            if folder.is_dir():
                for leftover in folder.glob("*.tmp"):
                    leftover.unlink(missing_ok=True)
            known = {c.n for c in self.step(step).candidates}
            for n, path in sorted(self._files(step).items()):
                if n in known:
                    continue
                candidate = self._saved(path, n)
                if candidate is None:
                    unknown.append(self.relative(path))
                    continue
                self.step(step).candidates = sorted([*self.step(step).candidates, candidate], key=lambda c: c.n)
                adopted.append(self.relative(path))
        if adopted:
            self.save()
            notes.append(f"Kept {len(adopted)} candidate(s) saved just before a run stopped: {', '.join(adopted)}")
        if unknown:
            notes.append(f"Left as they are (no record inside): {', '.join(unknown)}")
        return notes

    def _saved(self, path: Path, n: int) -> Candidate | None:
        metadata = read_metadata(path)
        if metadata is None:
            return None
        try:
            candidate = Candidate.model_validate(metadata)
        except ValidationError:
            return None
        if candidate.n != n or candidate.file != self.relative(path):
            return None
        return candidate.model_copy(update={"qc": None})


def ranked(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Best first: passed, then the highest idea score, then checked before unchecked, then the oldest."""
    def key(c: Candidate) -> tuple[bool, int, bool, int]:
        return (c.qc is not None and c.qc.passed, c.qc.score if c.qc is not None else 0, c.qc is not None, -c.n)

    return sorted(candidates, key=key, reverse=True)


def anchor_done(workspace: Path, style_version: int) -> bool:
    return anchor_path(workspace, style_version).is_file() and anchor_ref_path(workspace, style_version).is_file()


def mascot_done(workspace: Path, mascot: MascotConfig, style_version: int) -> bool:
    """Approved for this style version: the seed is set at approval, and both files exist (spec §8.1)."""
    return (
        mascot.seed is not None
        and mascot.style_version == style_version
        and (workspace / mascot.sheet).is_file()
        and (workspace / mascot.ref).is_file()
    )


def pending_step(workspace: Path, mascot: MascotConfig, style_version: int) -> Step | None:
    if not anchor_done(workspace, style_version):
        return "anchor"
    if not mascot_done(workspace, mascot, style_version):
        return "mascot"
    return None
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/bootstrap/test_store.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/bootstrap/store.py tests/bootstrap/test_store.py
git commit -m "feat: the bootstrap store keeps candidates and approvals, and adds back a candidate saved just before a kill" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Making and checking a bootstrap step's candidates

**Files:**
- Create: `src/stickman/bootstrap/generate.py`
- Test: `tests/bootstrap/test_generate.py` (new)

**Interfaces:**
- Consumes:
  - `Calls`, `RunControl`, `RunStopped`, `StopReason`, `STOPS`, `error_text` (Task 1);
  - `BootstrapStore`, `Candidate`, `Step` (Task 5);
  - the prompts module (Task 4);
  - `ReferenceFiles`, `RefImage`, `anchor_ref_path`, `image_cost_usd`, `PricingConfig`, `decode_image`, `encode_png`, `random_seed` (`render/jobs.py`), `RunResult` (`render/renderer.py`), `BudgetExceeded`, `local_now`.
- Produces (`stickman.bootstrap.generate`):
  - `LEDGER_KIND: dict[Step, Kind]`: anchor → `"anchor"`, mascot → `"sheet"`.
  - `CandidateJob`: a frozen dataclass satisfying `ImageSpec`, with `step`, `n`, `prompt`, `model`, `width`, `height`, `seed`, `steps`, `references`, `estimate_usd`, and the property `label -> str` (`"anchor-c1"`).
  - `StepPlan`: a frozen dataclass with `step`, `prompt`, `model`, `size`, `steps`, `references`, `estimate_usd`, `expected: ExpectedPicture`. Its method `jobs(store, count, *, seeds=random_seed) -> list[CandidateJob]` numbers them from `store.next_number(step)`.
  - `step_plan(step, *, workspace, settings, style, mascot, pricing, files: ReferenceFiles) -> StepPlan`. The mascot step reads the approved anchor's reference copy through `files`, which raises `ConfigError` for a `style_refs/` image.
  - `CandidateMaker(calls: Calls, store: BootstrapStore, *, concurrency: int, now=local_now, on_done: Callable[[], None] | None = None)`:
    - `async run(plan: StepPlan, jobs: Sequence[CandidateJob]) -> RunResult`: first it checks the step's recorded candidates with no QC result, with no new image; then it makes and checks the new ones;
    - `.errors: dict[str, str]`: label → the masked error of a candidate that couldn't be made or checked.

- [ ] **Step 1: Write the failing tests**

Create `tests/bootstrap/test_generate.py`:

```python
import asyncio
import itertools
import json
from collections import Counter

import pytest
from PIL import Image

from stickman.bootstrap.generate import CandidateMaker, step_plan
from stickman.bootstrap.store import BootstrapStore
from stickman.budget import Budget
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import load_mascot, load_style
from stickman.ledger import Ledger
from stickman.library import anchor_ref_path
from stickman.meter import Meter
from stickman.pricing import load_pricing
from stickman.render.calls import Calls, RunControl, StopReason
from stickman.render.images import read_metadata
from stickman.render.references import ReferenceFiles
from stickman.runlog import RunLog
from stickman.settings import BudgetSettings, QCSettings, RetrySettings, Settings

KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
VISION = "@cf/qwen/qwen3.8-27b"
TRANSIENT = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)


async def no_sleep(seconds):
    return None


class Setup:
    def __init__(self, tmp_path, client, *, retry=None, budget=None, concurrency=4, anchor=False):
        self.workspace = tmp_path
        if anchor:
            path = anchor_ref_path(tmp_path, 1)
            path.parent.mkdir(parents=True)
            Image.new("RGB", (512, 384), "white").save(path, format="PNG")
            self.anchor = path.read_bytes()
        self.client = client
        self.store = BootstrapStore.load(tmp_path, 1)
        retry = retry or RetrySettings()
        self.pricing = load_pricing(tmp_path)
        meter = Meter(project="bootstrap", ledger=Ledger(tmp_path / "ledger.jsonl"), budget=budget, pricing=self.pricing)
        self.calls = Calls(client, meter, RunLog(tmp_path / "run.jsonl"), RunControl(retry.circuit_breaker),
                           retry=retry, qc=QCSettings(), vision_model=VISION, sleep=no_sleep)
        self.maker = CandidateMaker(self.calls, self.store, concurrency=concurrency)

    def plan(self, step):
        return step_plan(step, workspace=self.workspace, settings=Settings(), style=load_style(self.workspace),
                         mascot=load_mascot(self.workspace), pricing=self.pricing,
                         files=ReferenceFiles(self.workspace, ref_max_side=512))

    def go(self, step, count):
        plan = self.plan(step)
        return asyncio.run(self.maker.run(plan, plan.jobs(self.store, count, seeds=itertools.count(100).__next__)))

    def ledger(self):
        return [json.loads(line) for line in (self.workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_anchor_candidates_are_made_saved_and_checked(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images())
    assert run.go("anchor", 4).stop is None
    candidates = run.store.state.anchor.candidates
    assert [c.n for c in candidates] == [1, 2, 3, 4]
    assert all(c.qc is not None and c.qc.passed for c in candidates)
    assert [c.seed for c in candidates] == [100, 101, 102, 103]
    for c in candidates:
        assert read_metadata(tmp_path / c.file)["n"] == c.n
    assert {(call["model"], call["width"], call["height"]) for call in run.client.calls} == {(KLEIN_9B, 1024, 768)}
    assert all(call["input_images"] == [] for call in run.client.calls)
    assert all(call["prompt"].startswith(load_style(tmp_path).style_text.strip()) for call in run.client.calls)
    assert Counter(e["kind"] for e in run.ledger()) == {"anchor": 4, "vision": 4}
    assert sorted({e["unit"] for e in run.ledger()}) == ["anchor-c1", "anchor-c2", "anchor-c3", "anchor-c4"]
    assert BootstrapStore.load(tmp_path, 1).state.anchor.candidates == candidates


def test_mascot_candidates_are_made_with_the_anchor_in_slot_0(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images(), anchor=True)
    run.go("mascot", 3)
    assert {(call["width"], call["height"]) for call in run.client.calls} == {(768, 1024)}
    assert all(call["input_images"] == [run.anchor] for call in run.client.calls)
    assert all("Character sheet: a single full-body front view" in call["prompt"] for call in run.client.calls)
    assert Counter(e["kind"] for e in run.ledger()) == {"sheet": 3, "vision": 3}
    [first, *_] = run.store.state.mascot.candidates
    assert first.refs[0].startswith("library/style/anchor_v1_ref.png#sha256:")


def test_estimates_follow_the_klein_9b_formula(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images(), anchor=True)
    assert run.plan("anchor").estimate_usd == pytest.approx(0.015)
    assert run.plan("mascot").estimate_usd == pytest.approx(0.017)  # plus one reference image under 1 MP


def test_numbers_continue_after_earlier_candidates(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images())
    run.go("anchor", 2)
    run.go("anchor", 2)
    assert [c.n for c in run.store.state.anchor.candidates] == [1, 2, 3, 4]


def test_a_daily_limit_stops_new_candidates(tmp_path, fake_images, jpeg):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    run = Setup(tmp_path, fake_images([jpeg, daily]), concurrency=1)
    result = run.go("anchor", 4)
    assert result.stop is StopReason.DAILY_LIMIT
    assert [c.n for c in run.store.state.anchor.candidates] == [1]
    assert len(run.client.calls) == 2


def test_a_rejected_candidate_is_skipped_and_the_others_go_on(tmp_path, fake_images, jpeg):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "bad prompt tok", status=400)
    run = Setup(tmp_path, fake_images(lambda call: rejected if call["seed"] == 101 else jpeg))
    assert run.go("anchor", 3).stop is None
    assert [c.n for c in run.store.state.anchor.candidates] == [1, 3]
    assert run.maker.errors == {"anchor-c2": "bad_request: bad prompt tok"}


def test_a_checker_that_cannot_be_reached_is_asked_again_next_run(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images(chat=lambda model, messages: TRANSIENT), retry=RetrySettings(transient_max=0))
    run.go("anchor", 2)
    assert [c.qc for c in run.store.state.anchor.candidates] == [None, None]
    assert run.maker.errors["anchor-c1"].startswith("transient: bad gateway")
    later = Setup(tmp_path, fake_images())
    assert later.go("anchor", 0).stop is None
    assert later.client.calls == [] and len(later.client.chat_calls) == 2
    assert all(c.qc.passed for c in later.store.state.anchor.candidates)


def test_the_weekly_budget_stops_before_any_call(tmp_path, fake_images):
    budget = Budget(BudgetSettings(weekly_usd=0.001), spent=0.0)
    run = Setup(tmp_path, fake_images(), budget=budget)
    assert run.go("anchor", 2).stop is StopReason.BUDGET
    assert run.client.calls == [] and run.store.state.anchor.candidates == []
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/bootstrap/test_generate.py -q`
Expected: FAIL with `ModuleNotFoundError: stickman.bootstrap.generate`.

- [ ] **Step 3: Create `src/stickman/bootstrap/generate.py`**

```python
"""Making and checking one bootstrap step's candidates, several at once (spec §8.1).

Each candidate is an image on bootstrap.model, saved with its record inside, then checked by QC like a
unit's image. There are no retries: the candidates are alternatives, and you choose one. A candidate the
checker couldn't reach is checked again on the next run, with no new image (spec §9.4 [M4]).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from stickman.bootstrap.prompts import anchor_expected, anchor_prompt, mascot_expected, mascot_prompt
from stickman.bootstrap.store import BootstrapStore, Candidate, Step
from stickman.budget import BudgetExceeded
from stickman.cf.errors import CFError
from stickman.config_files import MascotConfig, StyleConfig
from stickman.fsutil import safe_write
from stickman.ledger import Kind
from stickman.library import anchor_ref_path
from stickman.meter import local_now
from stickman.pricing import PricingConfig, image_cost_usd
from stickman.qc.vision import ExpectedPicture
from stickman.render.calls import STOPS, Calls, RunStopped, StopReason, error_text
from stickman.render.images import ImageDecodeError, decode_image, encode_png
from stickman.render.jobs import random_seed
from stickman.render.references import RefImage, ReferenceFiles
from stickman.render.renderer import RunResult
from stickman.settings import Settings

LEDGER_KIND: dict[Step, Kind] = {"anchor": "anchor", "mascot": "sheet"}


@dataclass(frozen=True)
class CandidateJob:
    step: Step
    n: int
    prompt: str
    model: str
    width: int
    height: int
    seed: int
    steps: int | None
    references: tuple[RefImage, ...]
    estimate_usd: float

    @property
    def label(self) -> str:
        """How the ledger and the run log name it."""
        return f"{self.step}-c{self.n}"


@dataclass(frozen=True)
class StepPlan:
    """What every candidate of one step shares."""

    step: Step
    prompt: str
    model: str
    size: tuple[int, int]
    steps: int | None
    references: tuple[RefImage, ...]
    estimate_usd: float
    expected: ExpectedPicture

    def jobs(self, store: BootstrapStore, count: int, *, seeds: Callable[[], int] = random_seed) -> list[CandidateJob]:
        first = store.next_number(self.step)
        return [
            CandidateJob(self.step, first + i, self.prompt, self.model, self.size[0], self.size[1], seeds(),
                         self.steps, self.references, self.estimate_usd)
            for i in range(count)
        ]


def step_plan(
    step: Step,
    *,
    workspace: Path,
    settings: Settings,
    style: StyleConfig,
    mascot: MascotConfig,
    pricing: PricingConfig,
    files: ReferenceFiles,
) -> StepPlan:
    """The anchor: no reference images, at image.anchor_size. The mascot sheet: the approved anchor's
    reference copy in slot 0, at image.sheet_size (spec §8.1)."""
    model = settings.bootstrap.model
    price = pricing.image(model)
    steps = settings.image.steps if price.supports_steps else None
    if step == "anchor":
        scene = settings.bootstrap.anchor_scene
        prompt, size, references, expected = anchor_prompt(style, scene), settings.image.anchor_size, (), anchor_expected(scene)
    else:
        anchor = files.load(anchor_ref_path(workspace, style.style_version))
        prompt, size, references, expected = mascot_prompt(style, mascot), settings.image.sheet_size, (anchor,), mascot_expected(mascot)
    estimate = image_cost_usd(price, size, [ref.size for ref in references], steps=steps or 1)
    return StepPlan(step, prompt, model, size, steps, references, estimate, expected)


class CandidateMaker:
    def __init__(
        self,
        calls: Calls,
        store: BootstrapStore,
        *,
        concurrency: int,
        now: Callable[[], datetime] = local_now,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self._calls = calls
        self._store = store
        self._concurrency = concurrency
        self._now = now
        self._on_done = on_done
        self.errors: dict[str, str] = {}

    async def run(self, plan: StepPlan, jobs: Sequence[CandidateJob]) -> RunResult:
        """The recorded candidates with no QC result are checked first (no new image), then the new ones
        are made and checked, at most `concurrency` at once. Once the run is stopping (daily limit,
        budget, a rejected token, the circuit breaker) nothing new starts and running work finishes."""
        started = time.perf_counter()
        control = self._calls.control
        semaphore = asyncio.Semaphore(self._concurrency)

        async def guarded(work: Callable[[], Awaitable[None]]) -> None:
            async with semaphore:
                if control.reason is not None:
                    return
                try:
                    await work()
                except RunStopped:
                    return
                except BudgetExceeded as exc:
                    control.stop(StopReason.BUDGET, str(exc))
                    return
                except CFError as exc:
                    if exc.category not in STOPS:
                        raise
                    control.stop(STOPS[exc.category], self._calls.log.mask(exc.message))
                    return
                if self._on_done is not None:
                    self._on_done()

        unchecked = [c for c in self._store.step(plan.step).candidates if c.qc is None]
        work: list[Callable[[], Awaitable[None]]] = [
            *(lambda c=c: self._check_saved(plan, c) for c in unchecked),
            *(lambda job=job: self._make(plan, job) for job in jobs),
        ]
        await asyncio.gather(*(guarded(item) for item in work))
        return RunResult(control.reason, control.detail, time.perf_counter() - started)

    async def _make(self, plan: StepPlan, job: CandidateJob) -> None:
        try:
            metered = await self._calls.image(job, unit=job.label, kind=LEDGER_KIND[plan.step])
            image = decode_image(metered.result.image_bytes)
        except CFError as exc:
            if exc.category in STOPS:
                raise
            self.errors[job.label] = error_text(exc, self._calls.log)
            return
        except ImageDecodeError as exc:
            self.errors[job.label] = f"bad_image: {exc}"
            return
        path = self._store.candidate_path(plan.step, job.n)
        candidate = Candidate(
            n=job.n, file=self._store.relative(path), seed=job.seed, model=job.model, width=image.width,
            height=image.height, prompt_sent=job.prompt, refs=[ref.label for ref in job.references],
            est_cost_usd=metered.usd, latency_s=round(metered.latency_s, 2), created=self._now(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_write(path, encode_png(image, candidate.model_dump(mode="json")))  # the file first, then bootstrap.json
        self._store.add(plan.step, candidate)
        await self._check(plan, candidate, image)

    async def _check_saved(self, plan: StepPlan, candidate: Candidate) -> None:
        label = f"{plan.step}-c{candidate.n}"
        try:
            image = decode_image((self._store.workspace / candidate.file).read_bytes())
        except (OSError, ImageDecodeError) as exc:
            self.errors[label] = f"bad_image: can't read {candidate.file}: {exc}"
            return
        await self._check(plan, candidate, image)

    async def _check(self, plan: StepPlan, candidate: Candidate, image: Image.Image) -> None:
        label = f"{plan.step}-c{candidate.n}"
        try:
            qc = await self._calls.check(image, expected=plan.expected, reference=None, unit=label)
        except CFError as exc:
            if exc.category in STOPS:
                raise
            # The checker couldn't be reached: the candidate stays unchecked, and the next run checks it.
            self.errors[label] = error_text(exc, self._calls.log) + " (checked again next run)"
            return
        self._store.set_qc(plan.step, candidate.n, qc)
```

Note on the lambdas: the default arguments (`c=c`, `job=job`) bind each item. Without them, every lambda would see the loop's last item.

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/bootstrap/test_generate.py -q`
Expected: PASS. In `test_a_rejected_candidate_is_skipped_and_the_others_go_on` the error is `"bad_request: bad prompt tok"`: the log's secrets are empty here, so nothing is masked.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/bootstrap/generate.py tests/bootstrap/test_generate.py
git commit -m "feat: bootstrap makes and checks a step's candidates several at once, stopping on the daily limit, budget or an outage" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Approving the anchor and the mascot sheet

**Files:**
- Create: `src/stickman/bootstrap/approve.py`
- Test: `tests/bootstrap/test_approve.py` (new)

**Interfaces:**
- Consumes: `BootstrapStore`, `anchor_done` (Task 5); `anchor_path`, `anchor_ref_path`, `reference_copy` (Task 4); `decode_image`; `safe_write`; `MascotConfig`, `read_config_text`, `default_config_text`.
- Produces (`stickman.bootstrap.approve`):
  - `ApprovalError(Exception)` (CLI exit 1).
  - `approve_anchor(store: BootstrapStore, n: int, *, ref_max_side: int) -> list[Path]`: returns the files written.
  - `approve_mascot(store: BootstrapStore, n: int, *, mascot: MascotConfig, ref_max_side: int) -> list[Path]`.
  - `set_mascot_approval(workspace: Path, *, seed: int, model: str) -> None`: writes `seed` and `model` into `config/mascot.yaml` with ruamel round-trip, and creates the file from the packaged default if it's missing.

- [ ] **Step 1: Write the failing tests**

Create `tests/bootstrap/test_approve.py`:

```python
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.approve import ApprovalError, approve_anchor, approve_mascot, set_mascot_approval
from stickman.bootstrap.store import BootstrapStore, Candidate, mascot_done
from stickman.config_files import load_mascot
from stickman.library import anchor_path, anchor_ref_path, find_references
from stickman.render.images import encode_png

PK = timezone(timedelta(hours=5))
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


def saved(store, step, n, size):
    record = Candidate(n=n, file=f"library/_bootstrap/v1/{step}/c{n}.png", seed=500 + n, model=KLEIN_9B,
                       width=size[0], height=size[1], prompt_sent="p", est_cost_usd=0.015, latency_s=3.0,
                       created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    path = store.workspace / record.file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(Image.new("RGB", size, "white"), record.model_dump(mode="json")))
    store.add(step, record)
    return path


def size_of(path):
    with Image.open(path) as image:
        return image.size


def test_approving_an_anchor_writes_it_and_its_reference_copy(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    source = saved(store, "anchor", 2, (1024, 768))
    written = approve_anchor(store, 2, ref_max_side=512)
    assert written == [anchor_path(tmp_path, 1), anchor_ref_path(tmp_path, 1)]
    assert anchor_path(tmp_path, 1).read_bytes() == source.read_bytes()
    assert size_of(anchor_ref_path(tmp_path, 1)) == (512, 384)
    assert BootstrapStore.load(tmp_path, 1).state.anchor.approved == 2


def test_approving_an_unknown_candidate_is_refused(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    with pytest.raises(ApprovalError, match="no anchor candidate 3"):
        approve_anchor(store, 3, ref_max_side=512)


def test_the_mascot_needs_an_approved_anchor_first(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "mascot", 1, (768, 1024))
    with pytest.raises(ApprovalError, match="approve the style anchor first"):
        approve_mascot(store, 1, mascot=load_mascot(tmp_path), ref_max_side=512)


def test_approving_the_mascot_writes_its_files_and_seed_and_model_to_mascot_yaml(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "anchor", 1, (1024, 768))
    approve_anchor(store, 1, ref_max_side=512)
    saved(store, "mascot", 2, (768, 1024))
    mascot = load_mascot(tmp_path)
    approve_mascot(store, 2, mascot=mascot, ref_max_side=512)
    assert size_of(tmp_path / mascot.sheet) == (768, 1024)
    assert size_of(tmp_path / mascot.ref) == (384, 512)
    text = (tmp_path / "config" / "mascot.yaml").read_text(encoding="utf-8")
    assert "# set at bootstrap approval" in text  # comments survive
    approved = load_mascot(tmp_path)
    assert (approved.seed, approved.model) == (502, KLEIN_9B)
    assert mascot_done(tmp_path, approved, 1)
    assert BootstrapStore.load(tmp_path, 1).state.mascot.approved == 2
    refs = find_references(tmp_path, use_references=True, style_version=1, mascot=approved, cast=[], library=[])
    assert refs.anchor and refs.sheets == frozenset({"mascot"})


def test_a_mascot_yaml_of_another_style_version_is_refused(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "anchor", 1, (1024, 768))
    approve_anchor(store, 1, ref_max_side=512)
    saved(store, "mascot", 1, (768, 1024))
    mascot = load_mascot(tmp_path).model_copy(update={"style_version": 2})
    with pytest.raises(ApprovalError, match="style_version 2"):
        approve_mascot(store, 1, mascot=mascot, ref_max_side=512)


def test_set_mascot_approval_keeps_an_existing_files_comments_and_other_keys(tmp_path):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "mascot.yaml"
    path.write_text(
        "schema_version: 1\nid: mascot\nname: \"Bob\"  # my mascot\nfigures: 1\nidentity: a stickman\n"
        "default_outfit: \"\"\nsheet: library/mascot/sheet_v1.png\nref: library/mascot/ref_v1.png\n"
        "seed: null\nmodel: null\nstyle_version: 1\n",
        encoding="utf-8",
    )
    set_mascot_approval(tmp_path, seed=9, model=KLEIN_9B)
    text = path.read_text(encoding="utf-8")
    assert "name: \"Bob\"  # my mascot" in text
    assert (load_mascot(tmp_path).seed, load_mascot(tmp_path).model) == (9, KLEIN_9B)
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/bootstrap/test_approve.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/stickman/bootstrap/approve.py`**

```python
"""Approving a bootstrap candidate (spec §8.1): copy it into the library, make its reference copy
(spec §8.3), and for the mascot record its seed and model in config/mascot.yaml.

The files are written before the approval is recorded, so a kill in between is put right by approving
again. The mascot counts as approved once mascot.yaml has its seed (bootstrap.store.mascot_done).
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from pydantic import ValidationError
from ruamel.yaml import YAML

from stickman.bootstrap.store import BootstrapStore, Step, anchor_done
from stickman.config_files import MascotConfig
from stickman.fsutil import safe_write
from stickman.library import anchor_path, anchor_ref_path, reference_copy
from stickman.render.images import ImageDecodeError, decode_image
from stickman.settings import default_config_text, read_config_text


class ApprovalError(Exception):
    """A candidate can't be approved (CLI exit code 1). Nothing is written when this is raised."""


def _source(store: BootstrapStore, step: Step, n: int) -> tuple[bytes, Image.Image]:
    candidate = store.step(step).candidate(n)
    if candidate is None:
        numbers = ", ".join(str(c.n) for c in store.step(step).candidates) or "none yet"
        raise ApprovalError(f"no {step} candidate {n} (candidates: {numbers})")
    path = store.workspace / candidate.file
    try:
        data = path.read_bytes()
        return data, decode_image(data)
    except (OSError, ImageDecodeError) as exc:
        raise ApprovalError(f"can't read {candidate.file}: {exc}") from exc


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, data)


def approve_anchor(store: BootstrapStore, n: int, *, ref_max_side: int) -> list[Path]:
    data, image = _source(store, "anchor", n)
    version = store.state.style_version
    full, ref = anchor_path(store.workspace, version), anchor_ref_path(store.workspace, version)
    _write(full, data)  # the candidate as it is, with its record inside
    _write(ref, reference_copy(image, ref_max_side))
    store.approve("anchor", n)
    return [full, ref]


def approve_mascot(store: BootstrapStore, n: int, *, mascot: MascotConfig, ref_max_side: int) -> list[Path]:
    version = store.state.style_version
    if not anchor_done(store.workspace, version):
        raise ApprovalError("approve the style anchor first: `stickman bootstrap --approve-anchor <N>`")
    if mascot.style_version != version:
        raise ApprovalError(
            f"config/mascot.yaml has style_version {mascot.style_version}, but bootstrap is for style v{version}; "
            f"set style_version: {version} there first"
        )
    data, image = _source(store, "mascot", n)
    candidate = store.step("mascot").candidate(n)
    sheet, ref = store.workspace / mascot.sheet, store.workspace / mascot.ref
    _write(sheet, data)
    _write(ref, reference_copy(image, ref_max_side))
    set_mascot_approval(store.workspace, seed=candidate.seed, model=candidate.model)
    store.approve("mascot", n)
    return [sheet, ref, store.workspace / "config" / "mascot.yaml"]


def set_mascot_approval(workspace: Path, *, seed: int, model: str) -> None:
    """seed and model in config/mascot.yaml, with comments and key order kept (ruamel round-trip). The
    file is made from the packaged default when it's missing. An invalid result is never written."""
    path = workspace / "config" / "mascot.yaml"
    text = read_config_text(path) if path.exists() else default_config_text("mascot.yaml")
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    doc = yaml.load(text.replace("\r\n", "\n"))
    doc["seed"] = seed
    doc["model"] = model
    buffer = io.StringIO()
    yaml.dump(doc, buffer)
    result = buffer.getvalue()
    try:
        MascotConfig.model_validate(YAML(typ="safe").load(result))
    except ValidationError as exc:
        raise ApprovalError(f"{path}: the approved mascot would not be valid: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, result.encode("utf-8"))
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/bootstrap/test_approve.py -q`
Expected: PASS. If `# set at bootstrap approval` disappears, the end-of-line comment was lost when the value changed. ruamel keeps a comment attached to the key when a `CommentedMap` value is replaced by assignment, so check that `doc` is a `CommentedMap` (round-trip `YAML()`, not `typ="safe"`).

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/bootstrap/approve.py tests/bootstrap/test_approve.py
git commit -m "feat: approving a bootstrap candidate writes it and its reference copy to the library, and the mascot's seed and model to mascot.yaml" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `stickman bootstrap`

**Files:**
- Modify: `src/stickman/cli.py`
- Test: `tests/test_cli_bootstrap.py` (new), `tests/test_cli_generate.py` (must still pass unchanged)

**Interfaces:**
- Consumes: Tasks 1 and 4–7; `ProjectLock`, `Budget`, `Ledger`, `Meter`, `RunLog`, `ReferenceFiles`, `load_style`, `load_mascot`, `load_pricing`.
- Produces:
  - the `stickman bootstrap` command: `--candidates N`, `--approve-anchor N`, `--approve-mascot N`, `--force`, `-w`;
  - `cli._free_plan_line(cfg, pricing, ledger, now, *, per_item_usd, count, noun, what, again) -> str | None`, the free-plan sentence shared by `generate`, `bootstrap` and `compare`;
  - `cli._exit_for(result, log, *, again="stickman resume")`: pause messages that name the command that continues;
  - `cli._check_usd(cfg, pricing)`, which now takes the pricing, not a `RenderContext`;
  - `BOOTSTRAP_PROJECT = "bootstrap"`, the ledger's `project` for bootstrap calls.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_bootstrap.py`:

```python
import json
from collections import Counter

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import load_mascot

runner = CliRunner()
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(cli, "_wait", no_wait)
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    return tmp_path


def use_images(monkeypatch, client):
    monkeypatch.setattr(cli, "build_client", lambda cfg: client)
    return client


def bootstrap(workspace, *args):
    return runner.invoke(cli.app, ["bootstrap", "-w", str(workspace), *args])


def ledger(workspace):
    return [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_the_first_run_makes_four_anchor_candidates_and_waits_for_approval(workspace, monkeypatch, fake_images):
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 2, result.output
    assert result.output.splitlines()[0] == "Bootstrap: style v1"
    assert f"Style anchor: making 4 candidate(s) on {KLEIN_9B} at 1024x768, each checked by" in result.output
    assert "Style anchor candidates, best first:" in result.output
    assert "library/_bootstrap/v1/anchor/c1.png" in result.output
    assert "stickman bootstrap --approve-anchor <N>" in result.output
    assert len(client.calls) == 4 and all(call["input_images"] == [] for call in client.calls)
    assert Counter(e["kind"] for e in ledger(workspace)) == {"anchor": 4, "vision": 4}
    assert {e["project"] for e in ledger(workspace)} == {"bootstrap"}
    assert list((workspace / "library/_bootstrap/v1/logs").glob("run-*.jsonl"))
    assert not (workspace / "library/_bootstrap/v1/.lock").exists()


def test_a_second_run_adds_nothing_until_more_are_asked_for(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 2 and client.calls == []
    result = bootstrap(workspace, "--candidates", "6")
    assert len(client.calls) == 2 and "anchor/c6.png" in result.output


def test_the_whole_bootstrap_anchor_then_mascot(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    result = bootstrap(workspace, "--approve-anchor", "2")
    assert result.exit_code == 0, result.output
    assert ("Approved style anchor candidate c2: library/style/anchor_v1.png, library/style/anchor_v1_ref.png"
            in result.output)
    anchor = (workspace / "library/style/anchor_v1_ref.png").read_bytes()
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 2, result.output
    assert f"Mascot sheet: making 3 candidate(s) on {KLEIN_9B} at 768x1024" in result.output
    assert all(call["input_images"] == [anchor] for call in client.calls)
    assert Counter(e["kind"] for e in ledger(workspace))["sheet"] == 3
    result = bootstrap(workspace, "--approve-mascot", "1")
    assert result.exit_code == 0, result.output
    assert "Bootstrap is complete" in result.output
    assert load_mascot(workspace).seed is not None
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 0 and "Bootstrap is complete" in result.output and client.calls == []


def test_approving_needs_no_credentials(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    (workspace / ".env").unlink()
    result = bootstrap(workspace, "--approve-anchor", "1")
    assert result.exit_code == 0, result.output


def test_approving_an_unknown_candidate_exits_1(workspace):
    result = bootstrap(workspace, "--approve-anchor", "9")
    assert result.exit_code == 1
    assert "no anchor candidate 9 (candidates: none yet)" in result.output


def test_one_approval_at_a_time(workspace):
    result = bootstrap(workspace, "--approve-anchor", "1", "--approve-mascot", "1")
    assert result.exit_code == 1 and "one candidate at a time" in result.output


def test_a_failed_candidate_can_be_approved_with_a_warning(workspace, monkeypatch, fake_images, vision):
    use_images(monkeypatch, fake_images(chat=lambda model, messages: vision.reply(has_text=True, character_count=2)))
    bootstrap(workspace)
    result = bootstrap(workspace, "--approve-anchor", "1")
    assert result.exit_code == 0, result.output
    assert "c1 failed QC (text); approved anyway." in result.output


def test_a_daily_limit_pauses_with_how_to_continue(workspace, monkeypatch, fake_images, jpeg):
    (workspace / "config").mkdir()
    (workspace / "config" / "settings.yaml").write_text("render:\n  concurrency: 1\n", encoding="utf-8")
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    use_images(monkeypatch, fake_images([jpeg, daily]))
    result = bootstrap(workspace)
    assert result.exit_code == 2, result.output
    assert "run `stickman bootstrap` after the daily reset" in result.output
    assert "anchor/c1.png" in result.output
    client = use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    assert len(client.calls) == 3


def test_an_anchor_copied_into_style_refs_is_never_sent(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    bootstrap(workspace, "--approve-anchor", "1")
    stock = workspace / "style_refs" / "copy.png"
    stock.parent.mkdir()
    stock.write_bytes((workspace / "library/style/anchor_v1_ref.png").read_bytes())
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 3 and "never sent to any API" in result.output
    assert client.calls == []


def test_no_secret_reaches_any_bootstrap_file(workspace, monkeypatch, fake_images):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "bad request for account acc123 with tok-secret", status=400)
    use_images(monkeypatch, fake_images(lambda call: rejected))
    result = bootstrap(workspace, "--candidates", "1")
    assert "tok-secret" not in result.output and "acc123" not in result.output
    for path in (workspace / "library").rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"tok-secret" not in data and b"acc123" not in data
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_cli_bootstrap.py -q`
Expected: FAIL. There's no `bootstrap` command, so typer exits 2 with "No such command".

- [ ] **Step 3: Generalise the free-plan line, the check cost and the pause messages**

In `src/stickman/cli.py`:

1. Change `_check_usd(cfg, ctx)` to take the pricing:

```python
def _check_usd(cfg: AppConfig, pricing: PricingConfig) -> float:
    """What a typical vision check costs (qwen's tokens; Task 11 of the M4 plan measured them)."""
    if not cfg.settings.qc.vision:
        return 0.0
    price = pricing.llm(cfg.settings.llm.vision_model)
    return 0.0 if price is None else llm_cost_usd(price, *TYPICAL_TOKENS)
```

   In `_print_run_start`, call it as `_check_usd(cfg, ctx.pricing)`. Import `PricingConfig` from `stickman.pricing`.

2. Add `_free_plan_line`, and use it for the tail of `_print_run_start`. The output stays word for word the same:

```python
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
```

   In `_print_run_start`, replace everything from `if cfg.settings.account.plan != "free":` to the end with:

```python
    line = _free_plan_line(
        cfg, ctx.pricing, ledger, now, per_item_usd=estimate / len(jobs), count=len(jobs), noun="unit(s)",
        what="image and check" if cfg.settings.qc.vision else "image", again="stickman resume",
    )
    if line is not None:
        console.print(escape(line))
```

3. Give `_exit_for` an `again` parameter:

```python
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
```

   Delete the now-unused `OUTAGE_MESSAGE` constant, after checking with `grep -rn OUTAGE_MESSAGE src tests` that nothing else uses it.

- [ ] **Step 4: Add the `bootstrap` command**

Add these imports to `src/stickman/cli.py`:

```python
from stickman.bootstrap.approve import ApprovalError, approve_anchor, approve_mascot
from stickman.bootstrap.generate import CandidateJob, CandidateMaker, StepPlan, step_plan
from stickman.bootstrap.store import BootstrapError, BootstrapStore, Candidate, Step, pending_step, ranked
from stickman.config_files import MascotConfig, StyleConfig, load_mascot, load_style
from stickman.library import anchor_path
from stickman.render.calls import Calls
from stickman.render.references import ReferenceFiles
```

Then add the command and its helpers, after `resume`:

```python
BOOTSTRAP_PROJECT = "bootstrap"  # the ledger's project for bootstrap calls
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
    try:
        store = BootstrapStore.load(root, style.style_version)
    except BootstrapError as exc:
        _fail(f"{exc}. Nothing was changed.", EXIT_USER_ERROR)
    store.folder.mkdir(parents=True, exist_ok=True)
    lock = ProjectLock(store.folder)
    try:
        lock.acquire()
    except LockHeld as exc:
        _fail(f"{exc}. Let it finish, then run this again.\nIf no stickman is running, delete `{lock.path}`.", EXIT_USER_ERROR)
    try:
        if lock.removed_stale is not None:
            console.print(f"[yellow]Removed a stale lock left by PID {lock.removed_stale}, which is no longer running.[/yellow]")
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
    candidate = store.step(step).candidate(n)
    if candidate.qc is None:
        console.print(f"[yellow]c{n} was never checked by QC; approved anyway.[/yellow]")
    elif not candidate.qc.passed:
        console.print(f"[yellow]c{n} failed QC ({candidate.qc.reason}); approved anyway.[/yellow]")
    console.print(escape(f"Approved {STEP_NAMES[step]} candidate c{n}: " + ", ".join(store.relative(p) for p in written)))
    if previous is not None and previous != n:
        console.print("Images made with the previous one become stale (their reference images changed).")
    if step == "anchor":
        console.print("Next: `stickman bootstrap` makes the mascot sheet candidates, with this anchor as their reference.")
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
    wanted = candidates or (settings.bootstrap.anchor_candidates if step == "anchor" else settings.bootstrap.mascot_candidates)
    try:
        plan = step_plan(step, workspace=root, settings=settings, style=style, mascot=mascot, pricing=pricing,
                         files=ReferenceFiles(root, ref_max_side=settings.image.ref_max_side))
    except ConfigError as exc:
        _fail(str(exc), EXIT_CONFIG_ERROR)
    existing = store.step(step).candidates
    jobs = plan.jobs(store, max(0, wanted - len(existing)))
    unchecked = [c for c in existing if c.qc is None]
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
    _print_candidates(store, step, errors)
    if result is not None:
        _exit_for(result, log, again="stickman bootstrap")
    if not store.step(step).candidates:
        _fail("No candidate could be made (see the errors above). Run `stickman bootstrap` again.", EXIT_USER_ERROR)
    option = "--approve-anchor" if step == "anchor" else "--approve-mascot"
    console.print(escape(
        f"Look at the candidates, then approve one: `stickman bootstrap {option} <N>`. "
        f"`stickman bootstrap --candidates {len(store.step(step).candidates) + 2}` adds more."
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


def _print_candidates(store: BootstrapStore, step: Step, errors: dict[str, str]) -> None:
    state = store.step(step)
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
        console.print(escape(f"  c{c.n:<3} {verdict:<26} {idea:<9} {c.file}{approved}"))
    for label, error in errors.items():
        console.print(f"[yellow]{escape(f'{label}: {error}')}[/yellow]")
```

`_exit_for` only returns when `result.stop is None`, so the approval hint is printed only after a run that finished.

- [ ] **Step 5: Run the tests to check they pass**

Run: `uv run pytest tests/test_cli_bootstrap.py tests/test_cli_generate.py -q`
Expected: PASS. `test_cli_generate.py`'s "Possible outage — run `stickman resume` later." and free-plan wording are unchanged.

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/cli.py tests/test_cli_bootstrap.py
git commit -m "feat: stickman bootstrap makes and checks anchor and mascot-sheet candidates, and approves one with --approve-anchor or --approve-mascot" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Picking the comparison's units

**Files:**
- Create: `src/stickman/plan/picking.py`
- Test: `tests/plan/test_picking.py` (new)

**Interfaces:**
- Consumes: `Plan`, `PlanUnit`, `MASCOT` (`plan/models.py`).
- Produces (`stickman.plan.picking`):
  - `complexity(unit: PlanUnit, figures: Mapping[str, int]) -> int`: spec §10.2, `2×Σfigures + len(props) + len(setting) + len(energy_marks)`. A ref missing from `figures` counts as 1.
  - `night_or_fire(unit: PlanUnit) -> bool`: `time_of_day == "night"`, or a prop or setting containing "fire" or "flame" (in any case).
  - `COMPARE_CATEGORIES: tuple[str, ...] = ("mascot", "extras", "night_or_fire", "metaphor", "split_part", "most_complex")`.
  - `Pick(category: str, unit_id: str, filled: bool = False)`: a frozen dataclass. `filled` means no unit matched, and the next most complex unit stands in (§10.2 rule 5).
  - `pick_compare_units(plan: Plan, figures: Mapping[str, int]) -> list[Pick]`: up to 6 picks, one per category in order, never reusing a unit.

**The rules for each category** (spec §14.4, following §10.2):

| Category | Rule |
|---|---|
| `mascot` | the first unit in time order with the mascot |
| `extras` | the first with a non-mascot character |
| `night_or_fire` | the most complex night or fire unit |
| `metaphor` | the first `visual_type: metaphor` unit |
| `split_part` | the first unit with a `part` |
| `most_complex` | the most complex unit |

- **No match:** the most complex unit not yet picked stands in, marked `filled`.
- **Ties** go to the earlier unit.
- **Fewer than 6 units:** the picks stop when the units run out.

- [ ] **Step 1: Write the failing tests**

Create `tests/plan/test_picking.py`:

```python
from stickman.plan.models import Plan
from stickman.plan.picking import COMPARE_CATEGORIES, Pick, complexity, night_or_fire, pick_compare_units

FIGURES = {"mascot": 1, "caveman_group": 3, "historian": 1}


def unit(unit_id, start, end, part=None, **fields):
    data = {
        "id": unit_id, "part": part, "start": start, "end": end, "source_text": "one two three four",
        "corrected_text": "one two three four", "visual_idea": f"Idea {unit_id}", "visual_type": "literal",
        "shot": "wide", "time_of_day": "day", "characters": [], "mood": None, "setting": [], "props": [],
        "composition": "centred", "energy_marks": [], "softened": False, "softened_reason": None,
        "seed": None, "image_prompt": f"prompt for {unit_id}", "prompt_locked": False,
    }
    return {**data, **fields}


def who(*refs):
    return [{"ref": ref, "action": "standing", "emotion": "calm"} for ref in refs]


def plan_of(*scenes):
    """One scene per argument: a unit dict, or a list of two for a split scene."""
    built, time = [], 0.0
    for number, units in enumerate(scenes, 1):
        units = units if isinstance(units, list) else [units]
        built.append({
            "id": f"{number:03d}", "lines": [number], "start": units[0]["start"], "end": units[-1]["end"],
            "source_text": "one two three four", "corrected_text": "one two three four",
            "split": {"status": "split", "cut_after_word": 2, "candidates": [2]} if len(units) == 2 else {"status": "none"},
            "units": units,
        })
        time = units[-1]["end"]
    return Plan.model_validate({
        "project": "demo", "aspect": "16:9", "style_version": 1,
        "image_model": "@cf/black-forest-labs/flux-2-klein-4b", "duration_end": time, "pace_wps": 2.5,
        "cast": [{"id": "mascot"},
                 {"id": "caveman_group", "name": "Caveman group", "figures": 3, "description": "cavemen", "library_ref": None},
                 {"id": "historian", "name": "Historian", "figures": 1, "description": "a historian", "library_ref": None}],
        "scenes": built,
    })


def six_kinds():
    return plan_of(
        unit("001", 0, 2, characters=who("mascot"), props=["cup"]),                          # 3
        unit("002", 2, 4, characters=who("historian")),                                       # 2
        unit("003", 4, 6, characters=who("caveman_group"), time_of_day="night", props=["campfire"]),  # 7
        unit("004", 6, 8, characters=who("mascot"), visual_type="metaphor"),                  # 2
        [unit("005a", 8, 9, "1 of 2", characters=who("mascot")), unit("005b", 9, 10, "2 of 2", characters=who("mascot"))],
        unit("006", 10, 12, characters=who("caveman_group", "historian"), props=["a", "b", "c"],
             setting=["x", "y"], energy_marks=["motion"]),                                    # 14
    )


def test_complexity_counts_figures_twice_plus_props_setting_and_energy():
    plan = six_kinds()
    assert [complexity(u, FIGURES) for u in plan.units()] == [3, 2, 7, 2, 2, 2, 14]


def test_night_or_fire_covers_night_and_fire_or_flame_words():
    plan = plan_of(
        unit("001", 0, 1, time_of_day="night"),
        unit("002", 1, 2, setting=["Flame lines around a pot"]),
        unit("003", 2, 3, props=["firelight on the wall"]),
        unit("004", 3, 4, props=["a cup"], setting=["a hill"]),
    )
    assert [night_or_fire(u) for u in plan.units()] == [True, True, True, False]


def test_one_unit_per_category_in_order():
    picks = pick_compare_units(six_kinds(), FIGURES)
    assert picks == [
        Pick("mascot", "001"), Pick("extras", "002"), Pick("night_or_fire", "003"),
        Pick("metaphor", "004"), Pick("split_part", "005a"), Pick("most_complex", "006"),
    ]
    assert [p.category for p in picks] == list(COMPARE_CATEGORIES)


def test_a_category_with_no_match_takes_the_next_most_complex_unit():
    plan = plan_of(
        unit("001", 0, 2, characters=who("mascot")),                                           # 2
        unit("002", 2, 4, characters=who("historian"), props=["a"]),                            # 3
        unit("003", 4, 6, characters=who("caveman_group")),                                     # 6
        unit("004", 6, 8, characters=who("mascot"), props=["a", "b"]),                          # 4
        unit("005", 8, 10, characters=who("mascot"), props=["a", "b", "c", "d"]),               # 6
        unit("006", 10, 12, characters=who("mascot")),                                          # 2
        unit("007", 12, 14, characters=who("mascot")),                                          # 2
    )
    picks = pick_compare_units(plan, FIGURES)
    # no night/fire unit: the most complex not yet picked stands in, the earlier of the two 6s
    assert picks[2] == Pick("night_or_fire", "003", filled=True)
    assert picks[3] == Pick("metaphor", "005", filled=True)
    assert picks[4] == Pick("split_part", "004", filled=True)
    assert picks[5] == Pick("most_complex", "006")


def test_picks_stop_when_the_units_run_out():
    plan = plan_of(unit("001", 0, 2, characters=who("mascot")), unit("002", 2, 4, characters=who("historian")))
    assert [p.unit_id for p in pick_compare_units(plan, FIGURES)] == ["001", "002"]


def test_the_night_pick_is_the_most_complex_night_unit_not_the_first():
    plan = plan_of(
        unit("001", 0, 2, characters=who("mascot")),                                    # 2
        unit("002", 2, 4, characters=who("historian")),                                 # 2
        unit("003", 4, 6, time_of_day="night"),                                         # 0
        unit("004", 6, 8, time_of_day="night", characters=who("caveman_group")),        # 6
    )
    assert pick_compare_units(plan, FIGURES)[2] == Pick("night_or_fire", "004")
```

In `test_a_category_with_no_match_takes_the_next_most_complex_unit`:
- `mascot` → 001 and `extras` → 002.
- `night_or_fire` has no match: the most complex left is 003 or 005 (both 6), and ties go to the earlier, so 003.
- `metaphor` has no match → 005 (6).
- `split_part` has no match → 004 (4).
- `most_complex` → the most complex left: 006 and 007 are both 2, so 006.

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/plan/test_picking.py -q`
Expected: FAIL with `ModuleNotFoundError: stickman.plan.picking`.

- [ ] **Step 3: Create `src/stickman/plan/picking.py`**

```python
"""Choosing units: the model comparison's six (spec §14.4), by the rules the test units use (spec §10.2).
M7 adds the test-unit picking here."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from stickman.plan.models import MASCOT, Plan, PlanUnit

COMPARE_CATEGORIES: tuple[str, ...] = ("mascot", "extras", "night_or_fire", "metaphor", "split_part", "most_complex")


def complexity(unit: PlanUnit, figures: Mapping[str, int]) -> int:
    """spec §10.2: 2×Σfigures + len(props) + len(setting) + len(energy_marks)."""
    drawn = sum(figures.get(character.ref, 1) for character in unit.characters)
    return 2 * drawn + len(unit.props) + len(unit.setting) + len(unit.energy_marks)


def night_or_fire(unit: PlanUnit) -> bool:
    words = " ".join([*unit.props, *unit.setting]).lower()
    return unit.time_of_day == "night" or "fire" in words or "flame" in words


@dataclass(frozen=True)
class Pick:
    category: str
    unit_id: str
    filled: bool = False  # nothing matched the category; the next most complex unit stands in


def pick_compare_units(plan: Plan, figures: Mapping[str, int]) -> list[Pick]:
    units = plan.units()
    position = {unit.id: index for index, unit in enumerate(units)}
    by_complexity = sorted(units, key=lambda unit: (-complexity(unit, figures), position[unit.id]))
    picked: set[str] = set()

    def first(match: Callable[[PlanUnit], bool]) -> PlanUnit | None:
        return next((u for u in units if u.id not in picked and match(u)), None)

    def most_complex(match: Callable[[PlanUnit], bool] = lambda u: True) -> PlanUnit | None:
        return next((u for u in by_complexity if u.id not in picked and match(u)), None)

    rules: dict[str, Callable[[], PlanUnit | None]] = {
        "mascot": lambda: first(lambda u: any(c.ref == MASCOT for c in u.characters)),
        "extras": lambda: first(lambda u: any(c.ref != MASCOT for c in u.characters)),
        "night_or_fire": lambda: most_complex(night_or_fire),
        "metaphor": lambda: first(lambda u: u.visual_type == "metaphor"),
        "split_part": lambda: first(lambda u: u.part is not None),
        "most_complex": lambda: most_complex(),
    }
    picks: list[Pick] = []
    for category in COMPARE_CATEGORIES:
        unit, filled = rules[category](), False
        if unit is None:
            unit, filled = most_complex(), True
        if unit is None:
            break
        picked.add(unit.id)
        picks.append(Pick(category, unit.id, filled))
    return picks
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/plan/test_picking.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/plan/picking.py tests/plan/test_picking.py
git commit -m "feat: pick the model comparison's six units by category, with the most complex unit standing in" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The comparison's folder and runs

**Files:**
- Create: `src/stickman/compare/__init__.py` (empty), `src/stickman/compare/setup.py`, `src/stickman/compare/runs.py`
- Modify: `tests/conftest.py` (new `bootstrapped` fixture)
- Test: `tests/compare/test_setup.py`, `tests/compare/test_runs.py` (new)

**Interfaces:**
- Consumes:
  - `pick_compare_units`, `Pick` (Task 9); `refresh_prompts`, `with_prompts` (Task 3); `RunControl` (Task 1);
  - `find_references`, `JobBuilder`, `RenderContext`, `RenderJob`, `recover`, `StateStore`, `needs_work`, `Renderer`, `RunResult`, `RunLog`, `load_plan`, `safe_write`, `Settings`, `Aspect`.
- Produces:
  - `stickman.compare.setup`:
    - `COMPARE_FILE = "compare.json"`, `FROZEN_PLAN = "source_plan.yaml"`, `RUNS_DIR = "runs"`, `KLEIN_4B`, `SMALL_SIZES`;
    - `CompareRun(id, model, width, height, references)`, `ComparePick(category, unit, filled=False)`, `CompareSetup(schema_version, source, created, picks, runs)`;
    - `CompareError(Exception)`;
    - `default_runs(settings: Settings, aspect: Aspect) -> list[CompareRun]`;
    - `compare_dir(workspace: Path, source: Path, day: date) -> Path`: a folder that doesn't exist yet;
    - `find_compare(workspace: Path, source: Path) -> Path | None`;
    - `create_compare(folder: Path, source: Path, setup: CompareSetup) -> None`;
    - `load_compare(folder: Path, *, library_ids) -> tuple[CompareSetup, Plan]`.
  - `stickman.compare.runs`:
    - `run_settings(settings, run, aspect) -> Settings`;
    - `PreparedRun` (a dataclass: `run`, `folder`, `store`, `builder`, `jobs`, `notes`, `settings`);
    - `prepare_run(compare_folder, setup, plan, run, base: RenderContext) -> PreparedRun`;
    - `async render_runs(client, prepared, meter, *, secrets, control, sleep, on_done=None) -> RunResult`.
  - The conftest fixture `bootstrapped(workspace: Path) -> None` writes an approved anchor and mascot sheet: `library/style/anchor_v1.png` and `_ref.png`, `library/mascot/sheet_v1.png` and `ref_v1.png`, and a `config/mascot.yaml` with seed 7 and model Klein 9B.

- [ ] **Step 1: Add the `bootstrapped` fixture to `tests/conftest.py`**

```python
MASCOT_YAML = (
    "schema_version: 1\nid: mascot\nname: \"Everyman\"\nfigures: 1\n"
    "identity: >-\n  the main character: an average-height stickman with a large round head, exactly three short\n"
    "  hair strokes curling to the right on top of the head, dot eyes and short curved eyebrows\n"
    "default_outfit: >-\n  a small solid-black necktie and solid-black shoes\n"
    "sheet: library/mascot/sheet_v1.png\nref: library/mascot/ref_v1.png\n"
    "seed: 7\nmodel: \"@cf/black-forest-labs/flux-2-klein-9b\"\nstyle_version: 1\n"
)


@pytest.fixture
def bootstrapped():
    """Writes what an approved bootstrap leaves: the anchor and the mascot sheet, full size and reference copy,
    and mascot.yaml with the approval's seed and model."""
    def write(workspace):
        files = {
            "library/style/anchor_v1.png": (1024, 768), "library/style/anchor_v1_ref.png": (512, 384),
            "library/mascot/sheet_v1.png": (768, 1024), "library/mascot/ref_v1.png": (384, 512),
        }
        for relative, size in files.items():
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", size, "white").save(path, format="PNG")
        (workspace / "config").mkdir(exist_ok=True)
        (workspace / "config" / "mascot.yaml").write_text(MASCOT_YAML, encoding="utf-8")

    return write
```

(`Image` is already imported in conftest.)

- [ ] **Step 2: Write the failing tests**

Create `tests/compare/test_setup.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest

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
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def source_project(workspace, plan_data, name="2026-09-25_demo"):
    folder = workspace / "projects" / name
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    return folder


def setup_for(source):
    return CompareSetup(source=source.name, created=datetime(2026, 9, 26, 9, 0, tzinfo=PK),
                        picks=[ComparePick(category="mascot", unit="001")], runs=default_runs(Settings(), "16:9"))


def test_the_default_runs_are_klein_4b_with_and_without_references_and_small():
    runs = default_runs(Settings(), "16:9")
    assert [(r.id, r.model, r.width, r.height, r.references) for r in runs] == [
        ("klein-4b-refs", KLEIN_4B, 1920, 1088, True),
        ("klein-4b-no-refs", KLEIN_4B, 1920, 1088, False),
        ("klein-4b-small-refs", KLEIN_4B, 1280, 720, True),
    ]
    assert [(r.width, r.height) for r in default_runs(Settings(), "9:16")] == [(1088, 1920), (1088, 1920), (720, 1280)]


def test_a_comparison_is_created_found_and_loaded(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    folder = compare_dir(tmp_path, source, date(2026, 9, 26))
    assert folder == tmp_path / "projects" / "2026-09-26_compare_demo"
    create_compare(folder, source, setup_for(source))
    assert (folder / "source_plan.yaml").read_bytes() == (source / "plan.yaml").read_bytes()
    assert not (folder / "plan.yaml").exists()  # never taken for a project
    assert find_compare(tmp_path, source) == folder
    setup, plan = load_compare(folder, library_ids=set())
    assert setup == setup_for(source)
    assert [u.id for u in plan.units()] == ["001", "002a", "002b"]


def test_a_second_comparison_on_the_same_day_gets_its_own_folder(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    first = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(first, source, setup_for(source))
    second = compare_dir(tmp_path, source, date(2026, 9, 26))
    assert second.name == "2026-09-26_compare_demo-2"
    create_compare(second, source, setup_for(source))
    assert find_compare(tmp_path, source) == second


def test_comparisons_of_other_projects_are_not_found(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    other = source_project(tmp_path, plan_data, name="2026-09-25_other")
    create_compare(compare_dir(tmp_path, other, date(2026, 9, 26)), other, setup_for(other))
    assert find_compare(tmp_path, source) is None


def test_an_unreadable_compare_json_is_an_error(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    folder = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(folder, source, setup_for(source))
    (folder / "compare.json").write_text("{", encoding="utf-8")
    with pytest.raises(CompareError):
        load_compare(folder, library_ids=set())
```

Create `tests/compare/test_runs.py`:

```python
import asyncio
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from stickman.cf.errors import CFError, ErrorCategory
from stickman.compare.runs import prepare_run, render_runs
from stickman.compare.setup import ComparePick, CompareSetup, compare_dir, create_compare, default_runs, load_compare
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.plan.models import parse_plan
from stickman.plan.picking import pick_compare_units
from stickman.plan.store import to_document, write_plan
from stickman.render.jobs import RenderContext
from stickman.render.renderer import RunControl, StopReason
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))


async def no_sleep(seconds):
    return None


def make_compare(tmp_path, plan_data, built_prompts, bootstrapped):
    bootstrapped(tmp_path)
    source = tmp_path / "projects" / "2026-09-25_demo"
    source.mkdir(parents=True)
    write_plan(source / "plan.yaml", to_document(parse_plan(built_prompts(plan_data))), expected_hash=None)
    plan = parse_plan(plan_data)
    picks = [ComparePick(category=p.category, unit=p.unit_id, filled=p.filled)
             for p in pick_compare_units(plan, {"mascot": 1, "caveman_group": 3})]
    setup = CompareSetup(source=source.name, created=datetime(2026, 9, 26, 9, 0, tzinfo=PK), picks=picks,
                         runs=default_runs(Settings(), "16:9"))
    folder = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(folder, source, setup)
    return folder


def prepare_all(tmp_path, folder):
    ctx = RenderContext.load(tmp_path, Settings())
    setup, plan = load_compare(folder, library_ids=ctx.library_ids)
    return [prepare_run(folder, setup, plan, run, ctx) for run in setup.runs]


def render(tmp_path, prepared, client):
    meter = Meter(project="cmp", ledger=Ledger(tmp_path / "ledger.jsonl"))
    return asyncio.run(render_runs(client, prepared, meter, secrets=(), control=RunControl(5), sleep=no_sleep))


def statuses(folder, run_id):
    data = json.loads((folder / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
    return {unit: entry["status"] for unit, entry in data["units"].items()}


def test_each_run_renders_the_picked_units_in_its_own_folder(tmp_path, plan_data, built_prompts, bootstrapped, fake_images):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    client = fake_images()
    assert render(tmp_path, prepare_all(tmp_path, folder), client).stop is None
    for run_id in ("klein-4b-refs", "klein-4b-no-refs", "klein-4b-small-refs"):
        assert set(statuses(folder, run_id).values()) == {"generated"}
    assert Counter((c["width"], c["height"]) for c in client.calls) == {(1920, 1088): 6, (1280, 720): 3}
    assert Counter(len(c["input_images"]) for c in client.calls) == {2: 6, 0: 3}  # anchor + mascot sheet, or none


def test_each_runs_prompts_say_what_its_reference_images_are(tmp_path, plan_data, built_prompts, bootstrapped, fake_images):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    frozen = (folder / "source_plan.yaml").read_bytes()
    client = fake_images()
    render(tmp_path, prepare_all(tmp_path, folder), client)
    with_refs = [c["prompt"] for c in client.calls if c["input_images"]]
    without = [c["prompt"] for c in client.calls if not c["input_images"]]
    assert all("Reference images: image 0 shows" in p and "Image 1 shows Everyman:" in p for p in with_refs)
    assert not any("Reference images" in p for p in without)
    assert (folder / "source_plan.yaml").read_bytes() == frozen  # the frozen plan is never written


def test_there_are_no_qc_retries(tmp_path, plan_data, built_prompts, bootstrapped, fake_images, vision):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    client = fake_images(chat=lambda model, messages: vision.reply(has_text=True, character_count=vision.figures(messages)))
    render(tmp_path, prepare_all(tmp_path, folder), client)
    assert len(client.calls) == 9
    assert set(statuses(folder, "klein-4b-refs").values()) == {"needs_review"}


def test_a_paused_comparison_continues_where_it_stopped(tmp_path, plan_data, built_prompts, bootstrapped, fake_images, jpeg):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    outcomes = iter([jpeg] * 4 + [daily] * 20)
    first = fake_images(lambda call: next(outcomes))
    assert render(tmp_path, prepare_all(tmp_path, folder), first).stop is StopReason.DAILY_LIMIT
    later = fake_images()
    again = prepare_all(tmp_path, folder)
    assert render(tmp_path, again, later).stop is None
    total = sum(len(p.store.state.units[u].versions) for p in again for u in p.store.state.units)
    assert total == 9  # no lost and no duplicate images
    assert all(set(statuses(folder, run_id).values()) == {"generated"}
               for run_id in ("klein-4b-refs", "klein-4b-no-refs", "klein-4b-small-refs"))
```

- [ ] **Step 3: Run the tests to check they fail**

Run: `uv run pytest tests/compare -q`
Expected: FAIL with `ModuleNotFoundError: stickman.compare`.

- [ ] **Step 4: Create `src/stickman/compare/__init__.py` (empty) and `src/stickman/compare/setup.py`**

```python
"""A model comparison's folder (spec §14.4 [M5]): projects/<date>_compare_<source slug>/.

    compare.json        the source project, the picked units, the runs
    source_plan.yaml    the source's plan.yaml as it was when the comparison started (never written again)
    runs/<run id>/      state.json, images/, logs/ of one model/size/reference setting
    export/compare.html the report

There is no plan.yaml at the top, so resolve_project never takes the folder for a project.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from stickman.fsutil import safe_write
from stickman.plan.models import Plan, PlanValidationError
from stickman.plan.store import load_plan
from stickman.settings import Aspect, Settings

COMPARE_FILE = "compare.json"
FROZEN_PLAN = "source_plan.yaml"
RUNS_DIR = "runs"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
# plan.md's small-size candidate: about 44% of 1920x1088's area; both sides are multiples of 16.
SMALL_SIZES: dict[Aspect, tuple[int, int]] = {"16:9": (1280, 720), "9:16": (720, 1280)}


class CompareError(Exception):
    """A comparison's files can't be read (CLI exit code 1)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompareRun(_Model):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    model: str
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    references: bool


class ComparePick(_Model):
    category: str
    unit: str
    filled: bool = False


class CompareSetup(_Model):
    schema_version: Literal[1] = 1
    source: str  # the source project's folder name
    created: AwareDatetime
    picks: list[ComparePick]
    runs: list[CompareRun]


def default_runs(settings: Settings, aspect: Aspect) -> list[CompareRun]:
    """Klein 4B at the project's size with and without references, and at the small size with them
    (the user's choice, 2026-09-26: Klein 9B is left out)."""
    width, height = settings.image.sizes[aspect]
    small_w, small_h = SMALL_SIZES[aspect]
    return [
        CompareRun(id="klein-4b-refs", model=KLEIN_4B, width=width, height=height, references=True),
        CompareRun(id="klein-4b-no-refs", model=KLEIN_4B, width=width, height=height, references=False),
        CompareRun(id="klein-4b-small-refs", model=KLEIN_4B, width=small_w, height=small_h, references=True),
    ]


def compare_dir(workspace: Path, source: Path, day: date) -> Path:
    """A new folder's path: -2, -3 … is added when one of that name exists."""
    slug = source.name.split("_", 1)[-1]
    base = workspace / "projects" / f"{day.isoformat()}_compare_{slug}"
    folder, number = base, 1
    while folder.exists():
        number += 1
        folder = base.with_name(f"{base.name}-{number}")
    return folder


def _read_setup(folder: Path) -> CompareSetup:
    try:
        return CompareSetup.model_validate_json((folder / COMPARE_FILE).read_bytes())
    except (ValidationError, ValueError, OSError) as exc:
        raise CompareError(f"{folder / COMPARE_FILE}: {exc}") from exc


def find_compare(workspace: Path, source: Path) -> Path | None:
    """The newest comparison of this source project."""
    projects = workspace / "projects"
    found: list[Path] = []
    if projects.is_dir():
        for folder in projects.glob("*_compare_*"):
            if not (folder / COMPARE_FILE).is_file():
                continue
            try:
                setup = _read_setup(folder)
            except CompareError:
                continue
            if setup.source == source.name:
                found.append(folder)
    return max(found, key=lambda folder: (folder.stat().st_mtime, folder.name)) if found else None


def create_compare(folder: Path, source: Path, setup: CompareSetup) -> None:
    """The frozen plan first, then compare.json, whose presence marks a finished setup."""
    folder.mkdir(parents=True)
    safe_write(folder / FROZEN_PLAN, (source / "plan.yaml").read_bytes())
    safe_write(folder / COMPARE_FILE, setup.model_dump_json(indent=2).encode("utf-8"))


def load_compare(folder: Path, *, library_ids: Collection[str] | None) -> tuple[CompareSetup, Plan]:
    setup = _read_setup(folder)
    try:
        plan = load_plan(folder / FROZEN_PLAN, library_ids=library_ids).plan
    except (PlanValidationError, OSError) as exc:
        raise CompareError(f"{folder / FROZEN_PLAN}: {exc}") from exc
    return setup, plan
```

`find_compare` picks by modification time, then name. The same-day test creates `-2` after the first, so `-2` is newer. If the two times are equal on a coarse clock, the name (`…demo-2` > `…demo`) breaks the tie.

- [ ] **Step 5: Create `src/stickman/compare/runs.py`**

```python
"""The comparison's runs (spec §14.4): each is the source plan rendered with one model, size and reference
setting into runs/<run id>/, by the normal Renderer. A run paused by the daily limit continues next time,
like `stickman resume`. There are no QC retries: the report measures how often the first image passes."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from stickman.compare.setup import RUNS_DIR, CompareRun, CompareSetup
from stickman.library import find_references
from stickman.meter import Meter
from stickman.plan.models import Plan
from stickman.plan.refresh import refresh_prompts, with_prompts
from stickman.render.jobs import JobBuilder, RenderContext, RenderJob
from stickman.render.recovery import recover
from stickman.render.renderer import Renderer, RenderClient, RunControl, RunResult
from stickman.render.state import StateStore, needs_work
from stickman.runlog import RunLog
from stickman.settings import Aspect, Settings


def run_settings(settings: Settings, run: CompareRun, aspect: Aspect) -> Settings:
    image = settings.image.model_copy(
        update={"sizes": {**settings.image.sizes, aspect: (run.width, run.height)}, "use_references": run.references}
    )
    retry = settings.retry.model_copy(update={"qc_max": 0})
    return settings.model_copy(update={"image": image, "retry": retry})


@dataclass
class PreparedRun:
    run: CompareRun
    folder: Path
    store: StateStore
    builder: JobBuilder
    jobs: list[RenderJob]  # the picked units that still need work
    notes: list[str]  # what recover() put right
    settings: Settings


def prepare_run(compare_folder: Path, setup: CompareSetup, plan: Plan, run: CompareRun, base: RenderContext) -> PreparedRun:
    """The run's plan is the frozen plan on the run's model, with the tool-built prompts rebuilt in memory
    for the run's reference images (spec §7.4 [M5])."""
    settings = run_settings(base.settings, run, plan.aspect)
    ctx = dataclasses.replace(base, settings=settings)
    references = find_references(
        ctx.workspace, use_references=run.references, style_version=plan.style_version, mascot=ctx.mascot,
        cast=plan.cast, library=ctx.library,
    )
    refresh = refresh_prompts(plan, style=ctx.style, mascot=ctx.mascot, references=references)
    run_plan = with_prompts(plan, refresh.rebuilt).model_copy(update={"image_model": run.model})
    builder = JobBuilder(ctx, run_plan)
    folder = compare_folder / RUNS_DIR / run.id
    folder.mkdir(parents=True, exist_ok=True)
    store = StateStore.load(folder)
    picked = {pick.unit for pick in setup.picks}
    expected = {unit_id: want for unit_id, want in builder.expected().items() if unit_id in picked}
    notes = recover(store, expected)
    units = [unit for unit in run_plan.units() if unit.id in picked and needs_work(store.unit(unit.id))]
    return PreparedRun(run, folder, store, builder, builder.jobs(units), notes, settings)


async def render_runs(
    client: RenderClient,
    prepared: Sequence[PreparedRun],
    meter: Meter,
    *,
    secrets: Sequence[str],
    control: RunControl,
    sleep: Callable[[float], Awaitable[None]],
    on_done: Callable[[str], None] | None = None,
) -> RunResult:
    """The runs one after another, sharing the meter (budget) and the stop flag: a daily limit in one
    stops the rest. Each run logs to its own folder."""
    started = time.perf_counter()
    for run in prepared:
        if control.reason is not None:
            break
        if not run.jobs:
            continue
        renderer = Renderer(
            client, run.store, meter, run.builder, qc=run.settings.qc, vision_model=run.settings.llm.vision_model,
            retry=run.settings.retry, concurrency=run.settings.render.concurrency,
            log=RunLog.for_project(run.folder, secrets=secrets), rewriter=None, sleep=sleep, on_done=on_done,
            control=control,
        )
        await renderer.run(run.jobs)
    return RunResult(control.reason, control.detail, time.perf_counter() - started)
```

- [ ] **Step 6: Run the tests to check they pass**

Run: `uv run pytest tests/compare -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/compare tests/conftest.py tests/compare/test_setup.py tests/compare/test_runs.py
git commit -m "feat: a model comparison's folder and runs, each rendered by the normal renderer with no QC retries, continuing after a pause" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The comparison report

**Files:**
- Create: `src/stickman/compare/report.py`
- Test: `tests/compare/test_report.py` (new)

**Interfaces:**
- Consumes: `CompareSetup`, `CompareRun`, `RUNS_DIR` (Task 10); `StateStore`, `Version`; `format_usd`, `usd_neurons`; `safe_write`; `Plan`.
- Produces (`stickman.compare.report`):
  - `REPORT = "export/compare.html"`.
  - `percentile(values: Sequence[float], q: float) -> float | None` (nearest rank).
  - `RunStats`: a frozen dataclass with `run`, `images`, `checked`, `passed`, `vision_checked`, `text_failures`, `count_failures`, `median_s`, `p90_s`, `usd_per_image`, and the properties `pass_rate` and `text_rate`.
  - `Cell(status: str, version: Version | None, image: str | None)`: `image` is the path from `export/`.
  - `collect(folder: Path, setup: CompareSetup) -> tuple[list[RunStats], dict[tuple[str, str], Cell]]`: cells are keyed by `(run id, unit id)`.
  - `suggestion(stats) -> tuple[str, int, int] | None`: `(run id, timeout_s, est_seconds)` from the first run with timings.
  - `report_lines(stats: Sequence[RunStats]) -> list[str]`.
  - `write_report(folder, setup, plan, stats, cells, *, now: datetime) -> Path`.

- [ ] **Step 1: Write the failing tests**

Create `tests/compare/test_report.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from stickman.compare.report import collect, percentile, report_lines, suggestion, write_report
from stickman.compare.setup import ComparePick, CompareSetup, default_runs
from stickman.plan.models import parse_plan
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.qc.vision import VisionReport
from stickman.render.state import StateStore, Version
from stickman.settings import QCSettings, Settings

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
SETUP = CompareSetup(
    source="2026-09-25_demo", created=datetime(2026, 9, 26, 9, 0, tzinfo=PK),
    picks=[ComparePick(category="mascot", unit="001"), ComparePick(category="extras", unit="002a", filled=True),
           ComparePick(category="night_or_fire", unit="002b", filled=True)],
    runs=default_runs(Settings(), "16:9"),
)


def qc_of(drawings, *, text=False, count=1):
    report = VisionReport(has_text=text, style_ok=True, anatomy_ok=True, watermark_like=False,
                          character_count=count, matches_visual_idea=4)
    return decide(pixel_check(drawings.clean(), QCSettings()), report, expected_figures=1, min_idea_score=3)


def fill(folder, run_id, entries):
    """entries: unit -> (seconds, qc result)."""
    run_dir = folder / "runs" / run_id
    run_dir.mkdir(parents=True)
    store = StateStore.load(run_dir)
    for unit, (seconds, qc) in entries.items():
        version = Version(v=1, file=f"images/_history/{unit}_v1.png", seed=1, model=KLEIN_4B, width=1920,
                          height=1088, fingerprint="sha256:x", prompt_sent="p", qc=qc, est_cost_usd=0.0023,
                          latency_s=seconds, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
        store.add_version(unit, version, status="generated" if qc.passed else "needs_review")


def test_percentile_is_the_nearest_rank():
    assert percentile([float(n) for n in range(1, 11)], 0.9) == 9.0
    assert percentile([5.0], 0.9) == 5.0
    assert percentile([], 0.9) is None


def test_collect_counts_passes_text_failures_times_and_cost(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {
        "001": (10.0, qc_of(drawings)), "002a": (20.0, qc_of(drawings)), "002b": (30.0, qc_of(drawings, text=True)),
    })
    stats, cells = collect(tmp_path, SETUP)
    refs, no_refs, small = stats
    assert (refs.images, refs.checked, refs.passed, refs.vision_checked, refs.text_failures) == (3, 3, 2, 3, 1)
    assert refs.pass_rate == pytest.approx(2 / 3) and refs.text_rate == pytest.approx(1 / 3)
    assert (refs.median_s, refs.p90_s) == (20.0, 30.0)
    assert refs.usd_per_image == pytest.approx(0.0023)
    assert (no_refs.images, no_refs.pass_rate, no_refs.median_s) == (0, None, None)
    assert cells[("klein-4b-refs", "001")].image == "../runs/klein-4b-refs/images/_history/001_v1.png"
    assert cells[("klein-4b-no-refs", "001")].status == "planned"
    assert cells[("klein-4b-no-refs", "001")].image is None


def test_character_count_failures_are_counted(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {"001": (10.0, qc_of(drawings, count=3))})
    [refs, *_] = collect(tmp_path, SETUP)[0]
    assert refs.count_failures == 1


def test_the_lines_show_each_run_and_suggest_the_timeout(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {
        "001": (10.0, qc_of(drawings)), "002a": (20.0, qc_of(drawings)), "002b": (30.0, qc_of(drawings, text=True)),
    })
    stats, _ = collect(tmp_path, SETUP)
    assert suggestion(stats) == ("klein-4b-refs", 90, 20)
    text = "\n".join(report_lines(stats))
    assert "klein-4b-refs" in text and "67%" in text and "33%" in text
    assert "klein-4b-small-refs" in text
    assert "render.timeout_s: 90, render.est_seconds_per_image: 20" in text


def test_the_timeout_suggestion_is_at_least_30_seconds(tmp_path, drawings):
    fill(tmp_path, "klein-4b-refs", {"001": (3.0, qc_of(drawings))})
    assert suggestion(collect(tmp_path, SETUP)[0]) == ("klein-4b-refs", 30, 3)


def test_no_suggestion_without_any_image(tmp_path):
    assert suggestion(collect(tmp_path, SETUP)[0]) is None


def test_the_html_report_shows_every_image_and_escapes_text(tmp_path, drawings, plan_data):
    plan_data["scenes"][0]["units"][0]["visual_idea"] = "A <b>bold</b> idea"
    fill(tmp_path, "klein-4b-refs", {"001": (10.0, qc_of(drawings))})
    stats, cells = collect(tmp_path, SETUP)
    path = write_report(tmp_path, SETUP, parse_plan(plan_data), stats, cells, now=datetime(2026, 9, 26, 12, 0, tzinfo=PK))
    assert path == tmp_path / "export" / "compare.html"
    page = path.read_text(encoding="utf-8")
    assert '<img src="../runs/klein-4b-refs/images/_history/001_v1.png"' in page
    assert "A &lt;b&gt;bold&lt;/b&gt; idea" in page and "<b>bold</b>" not in page
    assert "No-text failure rate" in page and "QC pass rate" in page
    assert "(stand-in)" in page and "not made yet" in page
    assert "http" not in page  # no external resources
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/compare/test_report.py -q`
Expected: FAIL with `ModuleNotFoundError: stickman.compare.report`.

- [ ] **Step 3: Create `src/stickman/compare/report.py`**

```python
"""The model comparison's report (spec §14.4): per run, the QC pass rate, the no-text failure rate,
character_count failures, the time and cost per image; export/compare.html shows every image with its
QC result. With no QC retries in a comparison, every version is a first try."""

from __future__ import annotations

import html
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stickman.compare.setup import RUNS_DIR, CompareRun, CompareSetup
from stickman.fsutil import safe_write
from stickman.plan.models import Plan
from stickman.pricing import format_usd, usd_neurons
from stickman.render.state import StateStore, Version

REPORT = "export/compare.html"
MIN_TIMEOUT_S = 30


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest rank: the smallest value with at least a share `q` of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


@dataclass(frozen=True)
class RunStats:
    run: CompareRun
    images: int
    checked: int
    passed: int
    vision_checked: int
    text_failures: int
    count_failures: int
    median_s: float | None
    p90_s: float | None
    usd_per_image: float | None

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.checked if self.checked else None

    @property
    def text_rate(self) -> float | None:
        """The no-text failure rate: images where the vision model saw text, of those it looked at."""
        return self.text_failures / self.vision_checked if self.vision_checked else None


@dataclass(frozen=True)
class Cell:
    status: str
    version: Version | None  # the current version, else the latest
    image: str | None  # the image's path from export/, with forward slashes


def collect(folder: Path, setup: CompareSetup) -> tuple[list[RunStats], dict[tuple[str, str], Cell]]:
    picked = [pick.unit for pick in setup.picks]
    stats: list[RunStats] = []
    cells: dict[tuple[str, str], Cell] = {}
    for run in setup.runs:
        state = StateStore.load(folder / RUNS_DIR / run.id).state
        versions: list[Version] = []
        for unit_id in picked:
            unit = state.units.get(unit_id)
            if unit is None or not unit.versions:
                cells[(run.id, unit_id)] = Cell(unit.status if unit is not None else "planned", None, None)
                continue
            versions += unit.versions
            current = unit.version(unit.current_version) if unit.current_version is not None else None
            shown = current or unit.versions[-1]
            cells[(run.id, unit_id)] = Cell(unit.status, shown, f"../{RUNS_DIR}/{run.id}/{shown.file}")
        checked = [v for v in versions if v.qc is not None]
        looked = [v for v in checked if v.qc.vision is not None]
        seconds = [v.latency_s for v in versions]
        stats.append(RunStats(
            run=run,
            images=len(versions),
            checked=len(checked),
            passed=sum(v.qc.passed for v in checked),
            vision_checked=len(looked),
            text_failures=sum(v.qc.vision.has_text for v in looked),
            count_failures=sum(v.qc.reason == "character_count" for v in checked),
            median_s=statistics.median(seconds) if seconds else None,
            p90_s=percentile(seconds, 0.9),
            usd_per_image=sum(v.est_cost_usd for v in versions) / len(versions) if versions else None,
        ))
    return stats, cells


def suggestion(stats: Sequence[RunStats]) -> tuple[str, int, int] | None:
    """render.timeout_s ≈ 3 × the 90th percentile, rounded up to 10 s (at least 30), and
    render.est_seconds_per_image ≈ the median, from the first run that has times (spec §14.4)."""
    for item in stats:
        if item.p90_s is not None and item.median_s is not None:
            timeout = max(MIN_TIMEOUT_S, math.ceil(3 * item.p90_s / 10) * 10)
            return item.run.id, timeout, max(1, round(item.median_s))
    return None


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def _secs(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}s"


def _cost(value: float | None) -> str:
    return "-" if value is None else f"{format_usd(value)} (≈ {usd_neurons(value):,.0f} neurons)"


def report_lines(stats: Sequence[RunStats]) -> list[str]:
    lines = [
        f"{'Run':<22} {'size':<10} {'refs':<5} {'images':>6} {'QC pass':>8} {'no-text fail':>13} "
        f"{'count fail':>11} {'median':>8} {'p90':>8}  per image"
    ]
    for item in stats:
        size = f"{item.run.width}x{item.run.height}"
        lines.append(
            f"{item.run.id:<22} {size:<10} {'yes' if item.run.references else 'no':<5} {item.images:>6} "
            f"{_pct(item.pass_rate):>8} {_pct(item.text_rate):>13} {item.count_failures:>11} "
            f"{_secs(item.median_s):>8} {_secs(item.p90_s):>8}  {_cost(item.usd_per_image)}"
        )
    found = suggestion(stats)
    if found is not None:
        run_id, timeout, seconds = found
        lines.append(
            f"Suggested for config/settings.yaml (from {run_id}): render.timeout_s: {timeout}, "
            f"render.est_seconds_per_image: {seconds}. Times were measured with render.concurrency requests at once, "
            "so they include waiting in Cloudflare's queue."
        )
    return lines


_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--pass:#1a7f37;--fail:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#161618;--fg:#f2f2f7;--muted:#a1a1a6;--line:#3a3a3c;--pass:#4ac26b;--fail:#ff6b61}}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
table{border-collapse:collapse}th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
.stats td{font-variant-numeric:tabular-nums;white-space:nowrap}.scroll{overflow-x:auto}
.grid img{display:block;width:320px;max-width:40vw;height:auto;background:#fff;border:1px solid var(--line)}
.muted,.empty{color:var(--muted)}.pass{color:var(--pass)}.fail{color:var(--fail)}
.cat{font-weight:600}.unit{font-family:ui-monospace,monospace}.idea{max-width:260px;color:var(--muted)}
"""


def _cell(cell: Cell) -> str:
    if cell.version is None or cell.image is None:
        return f"<td class=empty>{html.escape('not made yet' if cell.status == 'planned' else cell.status)}</td>"
    qc = cell.version.qc
    if qc is None:
        verdict, css = "not checked", "muted"
    elif qc.passed:
        verdict, css = "passed", "pass"
    else:
        verdict, css = f"failed: {qc.reason}", "fail"
    idea = f" · idea {qc.score}/5" if qc is not None and qc.vision is not None else ""
    src = html.escape(cell.image, quote=True)
    return (
        f'<td><a href="{src}"><img src="{src}" loading=lazy alt=""></a>'
        f"<div class={css}>{html.escape(verdict)}{idea} · {cell.version.latency_s:.1f}s</div></td>"
    )


def write_report(
    folder: Path, setup: CompareSetup, plan: Plan, stats: Sequence[RunStats],
    cells: dict[tuple[str, str], Cell], *, now: datetime,
) -> Path:
    e = html.escape
    ideas = {unit.id: unit.visual_idea for unit in plan.units()}
    stat_rows = "\n".join(
        f"<tr><th scope=row>{e(item.run.id)}</th><td>{item.run.width}×{item.run.height}</td>"
        f"<td>{'yes' if item.run.references else 'no'}</td><td>{item.images}</td><td>{_pct(item.pass_rate)}</td>"
        f"<td>{_pct(item.text_rate)}</td><td>{item.count_failures}</td><td>{_secs(item.median_s)}</td>"
        f"<td>{_secs(item.p90_s)}</td><td>{e(_cost(item.usd_per_image))}</td></tr>"
        for item in stats
    )
    found = suggestion(stats)
    advice = (
        f"<p>Suggested for config/settings.yaml (from {e(found[0])}): <code>render.timeout_s: {found[1]}</code>, "
        f"<code>render.est_seconds_per_image: {found[2]}</code>. Times include waiting in Cloudflare's queue.</p>"
        if found is not None else ""
    )
    head = "".join(f"<th scope=col>{e(run.id)}</th>" for run in setup.runs)
    rows = []
    for pick in setup.picks:
        label = e(pick.category.replace("_", " ")) + (" <span class=muted>(stand-in)</span>" if pick.filled else "")
        row_cells = "".join(_cell(cells[(run.id, pick.unit)]) for run in setup.runs)
        rows.append(
            f"<tr><th scope=row><div class=cat>{label}</div><div class=unit>{e(pick.unit)}</div>"
            f"<div class=idea>{e(ideas.get(pick.unit, ''))}</div></th>{row_cells}</tr>"
        )
    page = "".join([
        "<!doctype html>\n<html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width, initial-scale=1">',
        f"<title>Model comparison · {e(setup.source)}</title><style>{_CSS}</style></head><body>",
        f"<h1>Model comparison</h1><p class=muted>Source: {e(setup.source)} · {len(setup.picks)} units × "
        f"{len(setup.runs)} runs · written {e(now.isoformat(timespec='minutes'))}. "
        "No QC retries: every image is a first try.</p>",
        "<h2>Per run</h2><div class=scroll><table class=stats><thead><tr><th>Run</th><th>Size</th><th>References</th>"
        "<th>Images</th><th>QC pass rate</th><th>No-text failure rate</th><th>character_count failures</th>"
        "<th>Median time</th><th>90th percentile</th><th>Cost per image</th></tr></thead><tbody>",
        stat_rows,
        "</tbody></table></div>",
        advice,
        "<h2>Images</h2><div class=scroll><table class=grid><thead><tr><th>Unit</th>",
        head,
        "</tr></thead><tbody>",
        "\n".join(rows),
        "</tbody></table></div></body></html>\n",
    ])
    path = folder / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, page.encode("utf-8"))
    return path
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/compare/test_report.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/compare/report.py tests/compare/test_report.py
git commit -m "feat: the comparison report: pass and no-text failure rates, times and cost per run, and every image in export/compare.html" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: `stickman compare`, and the spec notes

**Files:**
- Modify: `src/stickman/cli.py`
- Modify: `spec.md`, `plan.md`
- Test: `tests/test_cli_compare.py` (new)

**Interfaces:**
- Consumes: Tasks 8–11; `resolve_project`, `cast_infos`, `load_plan`, `pending_step`.
- Produces: the `stickman compare` command: `-p/--project`, `--new`, `--yes/-y`, `--force`, `-w`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_compare.py`:

```python
import json
from datetime import date

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan
from stickman.project import resolve_project

runner = CliRunner()
SOURCE = "2026-09-25_demo"
COMPARE = "2026-09-26_compare_demo"


@pytest.fixture
def workspace(tmp_path, monkeypatch, plan_data, built_prompts):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 9, 26))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(cli, "_wait", no_wait)
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    folder = tmp_path / "projects" / SOURCE
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(built_prompts(plan_data))), expected_hash=None)
    return tmp_path


def use_images(monkeypatch, client):
    monkeypatch.setattr(cli, "build_client", lambda cfg: client)
    return client


def compare(workspace, *args, input=None):
    return runner.invoke(cli.app, ["compare", "-w", str(workspace), *args], input=input)


def test_compare_needs_a_finished_bootstrap(workspace, monkeypatch, fake_images):
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace, "--yes")
    assert result.exit_code == 1, result.output
    assert "Bootstrap isn't complete: run `stickman bootstrap` first." in result.output
    assert client.calls == []


def test_compare_renders_every_run_and_writes_the_report(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace, "--yes")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:2] == [f"Project: {COMPARE}", f"Source: {SOURCE}"]
    assert "Compared units: 001 (mascot), 002a (extras, stand-in), 002b (night or fire, stand-in)" in result.output
    assert "Comparing 3 unit(s) × 3 run(s): 9 image(s) to make" in result.output
    assert len(client.calls) == 9
    assert "klein-4b-small-refs" in result.output and "Suggested for config/settings.yaml" in result.output
    report = workspace / "projects" / COMPARE / "export" / "compare.html"
    assert report.is_file() and f"Report: {report}" in result.output
    entries = [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {entry["project"] for entry in entries} == {COMPARE}
    assert resolve_project(workspace, None).name == SOURCE  # generate never takes the comparison for a project


def test_running_it_again_only_writes_the_report_again(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    use_images(monkeypatch, fake_images())
    compare(workspace, "--yes")
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace)  # nothing to make, so nothing to confirm
    assert result.exit_code == 0, result.output
    assert client.calls == [] and "Report:" in result.output


def test_new_starts_another_comparison(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    use_images(monkeypatch, fake_images())
    compare(workspace, "--yes")
    result = compare(workspace, "--new", "--yes")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"Project: {COMPARE}-2"


def test_it_asks_before_spending(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace, input="n\n")
    assert result.exit_code == 0, result.output
    assert "Nothing was generated." in result.output and client.calls == []
    result = compare(workspace, input="y\n")
    assert result.exit_code == 0, result.output
    assert len(client.calls) == 9


def test_a_daily_limit_pauses_with_how_to_continue(workspace, monkeypatch, fake_images, bootstrapped, jpeg):
    bootstrapped(workspace)
    (workspace / "config" / "settings.yaml").write_text("render:\n  concurrency: 1\n", encoding="utf-8")
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    outcomes = iter([jpeg, jpeg])
    use_images(monkeypatch, fake_images(lambda call: next(outcomes, daily)))
    result = compare(workspace, "--yes")
    assert result.exit_code == 2, result.output
    assert f"run `stickman compare -p {SOURCE}` after the daily reset" in result.output
    assert (workspace / "projects" / COMPARE / "export" / "compare.html").is_file()
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_cli_compare.py -q`
Expected: FAIL ("No such command 'compare'").

- [ ] **Step 3: Add the `compare` command**

Add these imports to `src/stickman/cli.py`:

```python
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
from stickman.plan.cast import cast_infos
from stickman.plan.picking import pick_compare_units
```

Add the command and its helpers after `bootstrap`'s helpers:

```python
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
        picks=[ComparePick(category=p.category, unit=p.unit_id, filled=p.filled) for p in picks],
        runs=default_runs(ctx.settings, plan.aspect),
    )
    create_compare(folder, source, setup)
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
        estimate = sum(job.estimate_usd for job in jobs) + _check_usd(cfg, ctx.pricing) * len(jobs)
        how = f", each checked by {cfg.settings.llm.vision_model}" if cfg.settings.qc.vision else ", pixel checks only"
        per_run = ", ".join(f"{run.run.id} {len(run.jobs)}" for run in prepared if run.jobs)
        console.print(escape(
            f"Comparing {len(setup.picks)} unit(s) × {len(setup.runs)} run(s): {len(jobs)} image(s) to make "
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
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `uv run pytest tests/test_cli_compare.py -q`
Expected: PASS.

- [ ] **Step 5: Add the spec and plan notes**

In `spec.md`, add these notes. Keep the existing text, and mark each note `[M5]` like the `[M3]`/`[M4]` notes:

1. **§2.3 table:** after the `bootstrap.anchor_candidates` row, add:

```
| `bootstrap.mascot_candidates` | `3` | [M5] Mascot-sheet candidates per `bootstrap` run (§8.1). |
| `bootstrap.anchor_scene` | `two_figures` | [M5] `two_figures` or `one_figure`: the anchor's scene (§8.1). Switch to `one_figure` if the comparison shows the anchor's figures leaking into scenes. |
```

2. **§5.4:** at the end of the `[M3]` list, add:

```
- **[M5]** Anchor and mascot-sheet candidates are ledgered as `anchor` and `sheet`, with `unit` `anchor-c<N>` / `mascot-c<N>` and `project` `bootstrap`. A comparison's calls use the compare folder's name as `project`. A soften's or redesign's planner calls name their unit.
```

3. **§7.4:** after the "Small wording rules [M2]" paragraph, add:

```
**[M5] Prompts built before the references existed:** before rendering, `generate` rebuilds the `image_prompt` of every unlocked unit whose prompt the tool built — one that, with its "Reference images:" paragraph removed, equals the builder's output with no references — for the reference images that exist now, and writes them to `plan.yaml` hash-checked, with a notice. A prompt that doesn't match is taken as hand-edited and left as it is, with a warning when its images go out with references it doesn't describe. Locked prompts are never touched. Because the tool writes the rebuilt text, the M7 lock detection doesn't take it for a hand edit. `compare` does the same in memory for each run.
```

4. **§8.1:** at the end of the section, add:

```
**[M5] How bootstrap runs:**
- `stickman bootstrap` works on the first step not done: the anchor (`library/style/anchor_v<N>.png` and `_ref.png` exist), then the mascot (`mascot.yaml` has a `seed` for this style version and both files exist).
- It makes candidates until the step has `--candidates` of them (default `bootstrap.anchor_candidates`, or `bootstrap.mascot_candidates`), checks each with QC (§11; the anchor expects 2 figures, or 1 with `anchor_scene: one_figure`, the sheet 1), prints them best first, and exits 2 waiting for approval. There are no QC retries: the candidates are alternatives.
- Candidates live in `library/_bootstrap/v<style_version>/anchor|mascot/c<N>.png`, each with its record inside, listed in `bootstrap.json`. A candidate the checker couldn't reach is checked again next run, with no new image.
- Until the review page (M6), approval is on the command line: `stickman bootstrap --approve-anchor N` / `--approve-mascot N`. A candidate that failed QC can still be approved, with a warning. Re-approving the anchor is allowed; images made with the old one become stale.
- The mascot-sheet prompt also carries the unit prompts' "image 0 shows the drawing style only" sentence, since it is sent with the anchor in slot 0.
- The anchor-leak check isn't a separate probe: the comparison's `klein-4b-refs` and `klein-4b-no-refs` runs show `character_count` failures with and without the anchor (§14.4).
```

5. **§9.5:** at the end of the `[M4]` paragraph, add:

```
**[M5]** Anchor and mascot-sheet candidate calls count as `image` calls for the breaker.
```

6. **§11.2 / §9.4:** after the `[M4] The call` list in §9.4, add:

```
- **[M5]** A key the §11.2 schema doesn't have is dropped from the reply rather than failing the check (the schema shown to the model is unchanged).
```

7. **§13:** after the `[M4]` list, add:

```
**[M5] `bootstrap` and `compare`:**
- `stickman bootstrap [--candidates N] [--approve-anchor N | --approve-mascot N] [--force]`. Its first line is `Bootstrap: style v<N>`, as it has no project. Approving needs no credentials. It holds `library/_bootstrap/v<N>/.lock`.
- `stickman compare [-p <planned project>] [--new] [--yes] [--force]` replaces `--script`: plan the script with `stickman new` first. It shows the estimate and asks before spending, unless `--yes`. Running it again continues the newest comparison of that project; `--new` starts another. It needs a finished bootstrap.
- Pause messages name the command that continues the work (`stickman bootstrap`, `stickman compare -p …`).
```

8. **§14.4:** at the end of the section, add:

```
**[M5] As built (the user's decisions, 2026-09-26):**
- **Runs:** Klein 4B only, as `klein-4b-refs` (the project's size, with references), `klein-4b-no-refs` (no references) and `klein-4b-small-refs` (1280×720 or 720×1280, with references — plan.md's small-size candidate). Klein 9B is left out: about 22k neurons, and commercial use of its output is unconfirmed (§15 #10).
- **Folder:** `projects/<date>_compare_<source slug>/` with `compare.json`, `source_plan.yaml` (a frozen copy), `runs/<run id>/` (state, images, logs) and `export/compare.html`. There's no `plan.yaml` at the top, so it's never taken for a project.
- **No QC retries** (`retry.qc_max: 0`): the report measures first images. Each run is rendered by the normal renderer, so a daily-limit pause continues on the next run.
- **Extras** have no sheets until M7, so they appear in the text only.
- **Report:** per run, images, QC pass rate, no-text failure rate (images where the vision model saw text), `character_count` failures, median and 90th-percentile time, and cost per image in USD and neurons; plus a suggested `render.timeout_s` (3 × p90, rounded up to 10 s, at least 30) and `render.est_seconds_per_image` (the median). The same numbers are printed on the console.
```

9. **§15 #12:** append: ` **[M5]** Checked through the comparison instead: see §8.1 [M5] and docs/m5-bootstrap-compare.md.`

In `plan.md`:
- In the M5 row's "Delivers" cell, after "a comparison report", add: " **[M5 as built]** Klein 4B only (refs on, refs off, 1280×720 with refs) at the user's decision (2026-09-26); command-line approval until M6."
- Add this at the end of §6 "Decisions log":

```
- **M5 (2026-09-26):** `compare` runs Klein 4B with and without references and at 1280×720 with references; Klein 9B is left out (cost on the free plan; commercial use unconfirmed). `bootstrap.model` stays Klein 9B. Bootstrap approval is on the command line until the review page (M6). M5 and M6 run as SDD without per-task reviews, on stacked branches.
```

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/stickman/cli.py tests/test_cli_compare.py spec.md plan.md
git commit -m "feat: stickman compare renders the picked units in each run and writes the report; spec and plan notes for M5" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: The live bootstrap and comparison (needs the user's go-ahead, and the user at two approvals)

This task is **not** for a subagent. The controller runs it with the user after the whole-branch review and its fix wave, because it spends neurons and needs the user to pick the anchor and the mascot sheet.

**Estimated cost on the free plan** (10,000 neurons a day, reset at 00:00 UTC = 17:00 PDT):

| Step | Calls | Neurons (about) |
|---|---|---|
| Anchor candidates | 4 × Klein 9B 1024×768 (≈1,364 each) + 4 checks (≈284 each) | 6,600 |
| Mascot-sheet candidates | 3 × Klein 9B 768×1024 with one reference (≈1,545 each) + 3 checks | 5,500 |
| Comparison | 12 × Klein 4B 1920×1088 (≈210–250) + 6 × 1280×720 (≈100) + 18 checks | 8,500 |

The total is about 20,600 neurons, roughly 3 free days. Each command pauses at the daily limit, and running it again after the reset continues.

- [ ] **Step 1: Ask the user for the go-ahead**, showing the table above. Also ask the user to check `config/pricing.yaml` against the dashboard (spec §9.6 [M5]); the live numbers below will show whether the estimates hold.

- [ ] **Step 2: The anchor candidates**

Run: `uv run stickman bootstrap`
Expected: exit 2, with 4 candidates listed best first. Give the user the paths (`library/_bootstrap/v1/anchor/c<N>.png`) to look at.

- [ ] **Step 3: The user picks the anchor.** Run `uv run stickman bootstrap --approve-anchor <N>` with the user's choice. If none is good enough, run `uv run stickman bootstrap --candidates 6` to add more.

- [ ] **Step 4: The mascot-sheet candidates.** Run `uv run stickman bootstrap`. It pauses at the daily limit if needed; run it again after the reset. Show the user the candidates.

- [ ] **Step 5: The user picks the mascot sheet.** Run `uv run stickman bootstrap --approve-mascot <N>`, then check that `config/mascot.yaml` has the seed and model.

- [ ] **Step 6: The comparison.** Run `uv run stickman compare -p 2026-09-25_first-sleep --yes`. Run it again after each daily reset until it finishes. Open `projects/<date>_compare_first-sleep/export/compare.html` with the user.

- [ ] **Step 7: Check that no secrets leaked:** search `library/_bootstrap/`, the compare folder and the ledger for the token and the account id without printing them. Use a Python script that reads `.env` itself and prints only counts of matches.

- [ ] **Step 8: Write `docs/m5-bootstrap-compare.md`:**
  - the candidates' QC results;
  - which ones the user approved, and why, in the user's words;
  - the measured neurons per call (from `ledger.jsonl`) against the estimates;
  - the report's table;
  - the anchor-leak reading (`character_count` failures with and without references);
  - the small-size verdict;
  - the no-secrets check.

- [ ] **Step 9: The decision point.** Ask the user to choose, and write each choice into `config/settings.yaml` (committed) only as the user decides:
  - `image.model` (Klein 4B stays unless the user says otherwise);
  - whether to generate at 1280×720 (`image.sizes`);
  - `render.timeout_s` and `render.est_seconds_per_image` (the suggestions);
  - any QC threshold changes;
  - `budget.weekly_usd`.

  Record the choices in the doc, then commit `docs/m5-bootstrap-compare.md` (and `config/settings.yaml` if it changed):

```bash
git add docs/m5-bootstrap-compare.md config/settings.yaml
git commit -m "docs: M5 live bootstrap and model comparison, and the user's decisions" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes (written with the plan)

- **Spec coverage:**
  - §8.1: Tasks 4–8. The anchor-leak check is covered through §14.4 (decision above).
  - §8.3 reference copies: Task 4.
  - §14.4: Tasks 9–12. Runs changed at the user's decision; `-p` in place of `--script`.
  - §18 #7 (bootstrap outputs, `style_refs/` never sent): Task 8's tests.
  - §18 #9 (reference slots ≤ `ref_max_side`): `reference_copy` plus `ReferenceFiles`.
  - §18 #23 (compare report): Task 11.
  - §9.6 [M5] price re-check: Task 13.
  - M5 exit criteria (the user's choices written to config): Task 13 step 9.
- **Deferred to M6/M7:** the Sheets view of the review page; extras' sheets and library reuse; `stickman library list`; the strict `-p` rule; freezing the mascot's identity after approval (a later edit already makes units stale through the cast description in the fingerprint).
- **Types used across tasks:**
  - `Calls.image(spec, *, unit, kind)` and `Calls.check(image, *, expected, reference, unit)`: Task 1, used in Task 6.
  - `StepPlan.jobs(store, count, *, seeds)`: Task 6, used in Task 8.
  - `refresh_prompts(...) -> PromptRefresh(rebuilt, hand_edited)` and `with_prompts`: Task 3, used in Tasks 3 and 10.
  - `Pick(category, unit_id, filled)`: Task 9, used in Tasks 10 and 12.
  - `PreparedRun.jobs/.notes/.run/.settings`: Task 10, used in Task 12.
  - `collect/report_lines/write_report/suggestion`: Task 11, used in Task 12.
  - `_free_plan_line`, `_exit_for(…, again=…)` and `_check_usd(cfg, pricing)`: Task 8, used in Task 12.
