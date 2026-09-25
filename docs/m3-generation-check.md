# M3 live generation check (spec §17, plan.md M3 exit criteria)

Run on 2026-09-25 against the real Cloudflare API: Workers Free, one account. Image model: `@cf/black-forest-labs/flux-2-klein-4b`, 1920×1088, no reference images. Sample project: `tests/fixtures/scripts/first-sleep.txt`, planned from M2's cached LLM replies.

**Exit criteria met: yes.**
- Real images are generated.
- Every call is in the ledger at its measured cost.
- A run killed mid-batch resumes with no lost or duplicate images, and no stray files.

Total: 9 images, 1,868.31 neurons (≈ $0.0206), inside the free daily allocation of 10,000.

## Smoke test

`uv run pytest -m live -q -s`: **1 passed**.
- `neurons=207.59`, and the request id was set.
- The image decoded at 1920×1088.
- The call went through the meter, so the ledger got one `live-smoke` entry: `billed`, 207.59 neurons, $0.00228349.

## Neurons per image: measured against the estimate

| | Estimate | Measured |
|---|---|---|
| One image | ≈ 208 neurons ($0.00228703 from `pricing.yaml`) | 207.59 neurons ($0.00228349), identical for all 9 images |
| `generate --limit 3` | ≈ 624 neurons ($0.0069) | 622.77 neurons ($0.0069) |
| `resume --limit 4` | ≈ 832 neurons ($0.0091) | 830.36 neurons ($0.0091) |

The estimate is 0.2% high, which is on the safe side. Every image's `cf-ai-neurons` header was present, so no ledger entry fell back to the estimate.

## A real project from the cached plan

- **Setup:** I created today's folder `projects/2026-09-25_first-sleep/`, copied `m2_out/.cache/llm` into it, then ran `uv run stickman new tests/fixtures/scripts/first-sleep.txt --aspect 16:9`.
  - Exit code 0: 27 scenes became 35 units, 8 of them split.
  - **No API calls**: the ledger stayed at the smoke test's single entry.
- **`uv run stickman generate --limit 3`:** exit code 0, 33 s, 3 images.
  - `images/` holds `001_00-00.0.png`, `002_00-02.0.png` and `003_00-06.0.png`.
  - `images/_history/` holds `001_v1.png`, `002_v1.png` and `003_v1.png`.
  - Every file is a 1920×1088 RGB PNG carrying the `stickman` text chunk.
  - `state.json` shows the 3 units as `generated`, each with v1.
  - The run log `logs/run-20260925-050251.jsonl` has one `image` entry per call, with `unit`, `seed`, the size, `refs: []`, the shortened prompt, `latency_s`, `billing`, `usd`, `neurons` and `request_id`.
  - `ledger.jsonl` gained 3 `image` entries, each `billed` at 207.59 neurons.
  - Checked by eye: `002` is black line art on a white background.
- **Output redirected to a file** (not a console): both `new` and `generate` worked. Before the C1 fix, `new` crashed on this plan's U+2011 and `generate` crashed on `≈`.

## Killed mid-batch, then resumed

**The kill:**
- I started `uv run stickman generate --limit 4` for units 004, 005, 006a and 006b, with `concurrency: 4`.
- The first new history file appeared at 24.4 s: `006a_v1.png.tmp`. I killed the whole process tree at that moment with `taskkill /F /T` (the uv, python and stickman processes).

**What the kill left on disk:**
- `006a`: fully saved. The history file, the `state.json` entry (`generated`, v1) and the current copy were all present.
- `004`, `005` and `006b`: left as `generating`. Their requests were in flight when the process died.
- No `.tmp` files.
- A stale `.lock` holding the dead PID.

**`uv run stickman resume --limit 4`:** exit code 0, 34 s. It printed:

```
Removed a stale lock left by PID 21072, which is no longer running.
Reset 3 unit(s) a stopped run left generating: 004, 005, 006b
Generating 4 unit(s) on @cf/black-forest-labs/flux-2-klein-4b ≈ $0.0091 (≈ 832 neurons).
Free plan: about 1,038 of 10,000 neurons used today (UTC), so about 43 more image(s) fit before the reset at 17:00 local time.
Run finished · 35 units · done 8 · needs_review 0 · failed 0 · stale 0 · skipped 27
Cost this run ≈ $0.0091 (≈ 830 neurons) · week ≈ $0.0206 / $15.00 · time 34s
```

It rendered 004, 005 and 006b, then 007 as the fourth unit.

**State and history check** (a Python snippet loading `state.json` and listing `images/_history`):
- 8 units are `generated`, each with exactly one version (`[1]`, current version 1). The other 27 units are `planned`.
- No history file is missing from `state.json`, and no `state.json` version is missing on disk.
- Every current image is byte-identical to its history file.
- Every history PNG's embedded record matches `state.json`.
- There are no `.tmp` files and no `.lock`.

**Secrets:** I searched the 32 files the runs wrote (the ledger, `state.json`, the run logs, `plan.yaml` and every PNG) for the token and the account id, printing only the count. They appeared in 0 files.

## Surprises

- **Requests in flight at a hard kill have no ledger entry.**
  - The kill left three requests unrecorded (004, 005 and 006b). A killed process can't record anything.
  - If Cloudflare finished them, today's dashboard can show up to about 623 neurons more than the ledger.
  - This matches the design: a hard kill loses only images still in flight, and Ctrl+C records them as `possibly_billed`.
- **This kill didn't exercise the "kept image" note.** `006a`'s history, state and current copy were all written before the process died, so `resume` had no orphaned image to adopt. That path is covered offline by the kill-and-resume test (`tests/render/test_resume.py`). With adoption switched off as a check, 4 of its 6 seeds fail with a duplicate version, so the test does reach that path.
- **The progress bar floods redirected output.** This shell sets `FORCE_COLOR=3`, which makes rich treat a file or pipe as a terminal. So the live progress bar wrote each refresh, about 300 frames joined by `\r`, into the redirected output. It is harmless, and without `FORCE_COLOR`, rich doesn't draw the bar into a file.
- **Latency:** 18–23 s per image with 4 requests in parallel. The fourth request of a batch took about 33 s, which suggests Cloudflare runs about 3 of our requests at once and queues the rest. 4 images took 34 s end to end.
- **This machine runs on UTC−7 (PDT).** The ledger's timestamps are in that zone, and the free allocation resets at UTC midnight, shown as "17:00 local time".
