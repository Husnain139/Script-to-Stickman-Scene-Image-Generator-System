# Plan — Script-to-Stickman Scene Image Generator

Companion to `spec.md`, which holds the exact rules, schemas, prompts and acceptance criteria.

## 1. Goal

Turn a timestamped narration script into a set of consistent, high-quality stickman illustrations, one per scene, plus a ready-to-use export for CapCut. Typical use: 7 videos a week, **at most 4 minutes each (about 72 images per video)**. The design limit is **5 minutes (about 90 images)**. Longer scripts still run, but with a warning that they are outside the tested range.

### Success looks like
- A 4-minute script produces a full set of approved images, an MP4 slideshow and an SRT file in one working session. Human effort is limited to reviewing the plan, approving sheets and test images, and a final gallery pass.
- The mascot and recurring extras are recognisably the same character across every scene of a video.
- No text, colour or watermark-like artifacts reach the final export.
- Weekly spend stays inside the configured budget ($15/week at launch), and a crash or rate limit never loses finished work.

### Non-goals (v1)
- A hosted, multi-user web app. The tool is used by one person on their own machine.
- CapCut project files. The format is undocumented and may break with updates.
- Any image generation provider other than Cloudflare Workers AI.
- Scripts in languages other than English.

## 2. Scope

### v1 (MVP)
1. **Input:** `M:SS`, `H:MM:SS` and SRT scripts. The last line's end time comes from the narrator's measured pace, or from `--duration`.
2. **Planning:**
   - The LLM merges fragments and fixes speech-to-text errors, recording every fix.
   - It builds the cast, and proposes cut points for long scenes. The split engine (plain code) decides the cuts.
   - Scenes are then described in batches.
   - Everything is written to a hand-editable `plan.yaml`.
3. **Characters:**
   - A fixed mascot is kept in config.
   - The style anchor and mascot sheet are made once, during bootstrap.
   - Extras' sheets are generated per script. Once approved they go into a character library that future scripts reuse.
4. **Rendering:**
   - Uses FLUX.2 through Workers AI with up to 4 reference images.
   - Several images are generated at once.
   - The budget is checked before every call, and progress is saved for resume.
5. **Quality control:**
   - Pixel checks plus a vision-model check.
   - Retries change the prompt according to the failure reason.
   - Detects `safety_filtered` output and applies "softened" retries.
6. **Review page:** a local page (127.0.0.1) for:
   - checking the plan, with live reload and validation errors
   - approving sheets and test images
   - a gallery with flagged images first, side-by-side versions, prompt editing and keyboard shortcuts
7. **Workflow:** the 3 auto-picked test images are generated first and the run pauses. The full batch runs only after approval.
8. **Maintenance commands:**
   - Stale detection, plus `regen`, `rebuild-prompt`, and `replan` (one scene, with an optional hint).
9. **Vertical version:** `recompose --aspect 9:16` makes a Short from an existing plan.
10. **Export:**
    - an MP4 slideshow (30 fps, frame-accurate, optional slow zoom)
    - numbered images
    - an SRT file (from source or corrected text)
    - JSON and CSV manifests
11. **Tracking:** a cost ledger, weekly budget (warn at 80%, stop at 100% unless `--force`), run logs, and an end-of-run summary.

### Candidate: small-size generation + local upscale (free-plan throughput)
- **What:** generate at a smaller size, such as 1280×720 (about 44% of the area of 1920×1088, so about 90 instead of 206 neurons on Klein 4B), then upscale locally to 1920×1080 at export. Clean black line art on white upscales well.
- **Effect:** roughly doubles the images per free day (to about 100 or more), which is about 1 video a day.
- **Status:** proposed on 2026-09-23. Scheduled as a setting for M3 (generation size) and M8 (export upscale). Checked in M5 against native 1920×1088 quality before it becomes a default.

### Later (in priority order)
1. **Audio input:** transcribe the voiceover with Whisper on Workers AI to produce the timestamped script. Per-word times make split points exact.
2. **Batch queue:** several scripts run overnight within the budget.
3. **Per-scene zoom and pan settings.** v1 only has a single global slow zoom flag.
4. **YouTube thumbnails:** generate thumbnails in the house style with the approved cast sheets. Any title text is added in an editor, following the no-text rule.
5. **Simple animation:** 2–3 pose variants per scene using FLUX.2 editing, for blinks and small movements.
6. **More visual styles.** `style_version` and the config-based style already allow for this.
7. More than 2 parts per split. The setting exists, but v1 only accepts 2.

## 3. Build order and milestones

Build in this order: resolve the riskiest unknowns first, and keep the no-API core testable from day one. Each milestone ends in something runnable.

| # | Milestone | Delivers | Exit criteria |
|---|---|---|---|
| **M0** | Setup and API checks | uv project, config loading, `.env`, `cf` client skeleton, `stickman init`. **Style feasibility test:** 3 hand-written prompts × 3 FLUX.2 models (about $0.50). **Anchor-leak check** (spec §8.1). **Commercial-use confirmation** from Cloudflare for Klein 9B and dev (spec §15 #10). Checks every open API question (spec §15). | Token works. All spec §15 questions answered and written into the spec. At least one model produces the style acceptably. The anchor type is chosen. The commercial-use answer is recorded; if unconfirmed, the model choice is limited to Klein 4B. |
| **M1** | Script reading and splitting | `ingest` (3 formats, pace, end times), word counting, split engine, the merge fallback rules | The sample script produces exactly the expected result (spec §4.7) with no API calls |
| **M2** | Planning | LLM pass 1 and pass 2, Pydantic schemas, retry and fallback to Llama, prompt builder, `plan.yaml` read/write (ruamel), `replan` | `stickman new sample.txt` writes a valid `plan.yaml`. Hand edits survive the tool rewriting the file. **The planning checklist report (spec §17) is saved:** expected corrections found (target 4 of 5), no wrong corrections, the merge done, the mascot rule followed. |
| **M3** | Image generation | FLUX.2 client (multipart, reference images), reference-slot filling, parallel runs, `state.json` safe writes, ledger, budget checks, error categories, the 5-failures pause, resume, run summary | Kill the process mid-batch, then `resume` finishes with no duplicate or lost images. Budget stop and `--force` work. |
| **M4** | Quality control | Pixel checks, vision check, `safety_filtered` detection, prompt fixes per failure reason, `needs_review` | Unit tests pass on fixture images: text, colour, filled background, black, blurred, clean |
| **M5** | Bootstrap and model comparison **(decision point)** | `bootstrap` (anchor candidates, then mascot sheet, on Klein 9B — see M0 note below), `compare` (6 scenes × 2 models — Klein 4B and Klein 9B; FLUX.2 dev excluded, unusable: 408 timeouts — with and without references), a comparison report **[M5 as built]** Klein 4B only (refs on, refs off, 1280×720 with refs) at the user's decision (2026-09-26); command-line approval until M6. | **You choose:** the default model, the QC thresholds, the timeout and the budget. The choices are written to config. |
| **M6** | Review page | FastAPI server, plan view with live reload and validation errors, sheet and test approval, gallery, hash-checked prompt edits, keyboard shortcuts, history, side-by-side view **[M6 as built]** Approvals are recorded; M7 enforces them. Test units and extras' sheets appear once M7 makes them. | The whole review flow works in the browser for the sample project |
| **M7** | End-to-end workflow | Extras' sheets and library reuse, test-first-then-batch, test-scene picking, stale detection, `regen`, `rebuild-prompt`, `--no-review`, the project-selection rule (`-p` required when several projects are recent) | A real 4-minute script goes from plan to fully approved gallery |
| **M8** | Export | Manifests, MP4 (frame-accurate, optional zoom), SRT (both text modes), numbered images, blocking on unapproved scenes | The MP4 imports into CapCut and every cut lands within one frame of its timestamp |
| **M9** | Vertical (9:16) version | `recompose --aspect 9:16`, a sibling project that reuses the sheets | A 9:16 set is produced from an existing 16:9 plan |

M1 and M2 can run alongside M0's style test. M5 is a deliberate stop: the rest of the build depends on which model wins, and on how strict QC turns out to need to be.

### Estimated one-off costs
- M0 style test: about $0.50
- M5 bootstrap (4 anchor candidates and a mascot sheet on Klein 9B — FLUX.2 dev is unusable: 408 timeouts, see §4): about $0.10
- M5 comparison test (24 images, Klein 4B + Klein 9B only — dev excluded, see §4): about $0.25

**Correction:** during brainstorming I said the comparison would cost about $1, then revised it to $2.50–3 assuming FLUX.2 dev pricing. **M0 update:** dev is unusable synchronously and is dropped from `compare` (spec §14.4), so the real cost with just Klein 4B and 9B is far lower — about $0.25 for 24 images (see `docs/m0-findings.md`).

## 4. Running costs (measured in M0; to be re-checked in M5)

**Basis:** a 4-minute video is 72 units. With 25% retries that is **about 90 generations per video**. Per-image prices are **measured** from the `cf-ai-neurons` response header (spec §9.6/§9.7), not estimated, at 1920×1088 with 2–3 reference images. Retries are counted **once**, in the 90.

| Default image model | Per image | Images per video (90) | + LLM, QC, new extras' sheets | **Per week (7 videos)** |
|---|---|---|---|---|
| FLUX.2 [klein] 4B | about $0.0025 | about $0.23 | about $0.15 | **about $2.7** |
| FLUX.2 [klein] 9B | about $0.0185 | about $1.67 | about $0.15 | **about $12.7** (about $12 net of the free 10,000 neurons/day) |
| FLUX.2 [dev] | — | — | — | **Unusable: HTTP 408 timeouts** at both 1920×1080/25 steps and 1024×768/20 steps (~237 s). Not a viable default; dropped from `bootstrap.model` and the M5 `compare` model list. |

Both figures **require Workers Paid** (usage-based billing) — the account's free plan stops at 10,000 neurons/day, about 6 Klein 9B images, which is what M0's `style`/`anchor-leak` probe hit.

**Workers Paid plan fee:** **USER ACTION pending** — check the dashboard (spec §15 #11). It is not counted in the budget.

**Klein 9B now fits inside the $15/week budget** (about $12.7, or about $12 net of the free daily allocation) — the M0 measurement is well below the earlier estimate. Klein 4B is comfortably inside it, at about $2.7/week. See `docs/m0-findings.md` for the full per-call evidence.

## 5. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The style or consistency isn't good enough on any FLUX.2 model | High: this is the core value | M0 style test before any big build. Reference images (anchor and sheets). Fixed descriptions, word for word. The M5 comparison. |
| "No text" is ignored (no negative prompt on FLUX.2) | High | Strong no-text wording in the prompt, a "no-text" rule for props in `visual_rules.yaml`, vision QC `has_text`, retries tailored to text failures, the final gallery review |
| Safety filters on sensitive lines | Medium | Symbolic depiction rule, `safety_filtered` detection, one softened retry, "softened" label |
| Unknown API details (response shape, 1080 px, exact reference-image limit, qwen with multiple images, the gpt-oss endpoint) | Medium | All listed in spec §15 and resolved in M0. Each has a planned fallback. |
| Costs higher than estimated (unclear tile counting, retries) | Medium | Conservative estimates (ceiling-based tile counts; timeouts recorded as "possibly billed"). Budget checked before every call. Compare against the dashboard weekly. |
| gpt-oss-120b isn't good at visual metaphors | Medium | Batches with neighbouring lines and the cast list, schema checks and fallback, `replan` with a hint, hand editing of `plan.yaml` |
| **FLUX.2 licence or Black Forest Labs terms restrict commercial use of the images** | **High for a monetised channel. It may decide the model on its own.** | Klein 4B is Apache 2.0, so its outputs are clearly fine for commercial use. For Klein 9B and dev (FLUX Non-Commercial License), outputs appear to be allowed commercially, but BFL's general terms leave some doubt. **Get written confirmation from Cloudflare during M0. If it isn't confirmed, use Klein 4B only.** See spec §15 #10. |
| Workers AI models or prices change | Low–medium | Model IDs and prices live in config (`pricing.yaml`), not in code |
| ffmpeg zoom wobble | Low | Enlarge 2×, apply the zoom, then shrink back. Zoom is off by default. |
| Windows file locks (antivirus, sync clients) break safe saves | Low | Retry `os.replace` with a short wait. Keep projects out of synced folders. |

## 6. Decisions log (from brainstorming)

- **Style:** reference images #1 and #2 are the base look. Thin lines, rounded mitten hands, outlined oval feet, a hatched ground shadow. Filled shoes, tie and cape are optional costume.
- **Planning LLM:** gpt-oss-120b on Workers AI, with Llama 3.3 70B (JSON mode) as fallback. There is no second LLM provider.
- **Image model:** FLUX.2 family. **Klein 4B is the default** (changed from 9B on 2026-09-23, after M0). The user is staying on Workers Free, where about 48 Klein 4B images fit in a day's free allowance, compared with about 6 on Klein 9B. `bootstrap.model` stays Klein 9B, because it runs once (about 5 images) and the anchor's quality matters most. M5 may still switch the default to Klein 9B.
- **Text handling:**
  - Fragments are always merged, and the LLM decides which lines are fragments.
  - Short complete lines stay separate by default, with merging available as a setting.
  - Speech-to-text fixes are applied and every fix is recorded.
- **Splitting:** a scene is split at ≥7 s or ≥20 words. The LLM suggests the cut points. The split engine checks that both parts are at least 2.5 s. At most 2 parts.
- **Mascot:** one fixed mascot across all videos. It appears only in "you" or general human-experience scenes. **Identity is the head and hair** (three strokes curling right), which are visible in every shot. The tie and shoes are a default outfit that may be hidden or left out.
- **Corrections:** applied at scene level after merging, so a fix can span a line break.
- **Split timing:** cut times are calculated inside the original line that contains the cut, using that line's own timing.
- **Project safety:** every command prints the project name. `generate`, `resume`, `regen` and `export` require `-p` when more than one project was modified in the last 24 h.
- **Video length:** at most 4 minutes in practice, with a design limit of 5 minutes.
- **Files:** `plan.yaml` holds your content. `state.json` holds the tool's data. Manifests are always regenerated from both.
- **Interface:** a command-line tool plus a local review page. The plan is read-only in the browser, except for `image_prompt`.
- **Editor:** CapCut. The export is an MP4, images and an SRT, with no project files.
- **Budget:** $15/week, a warning at 80%, a stop at 100% unless `--force`.
- **Cloudflare plan (2026-09-23):** the user stays on **Workers Free** and has no plan to pay. `account.plan` defaults to `free`, so the daily-limit stop and `resume` (next day) carry a video across days (about 2 days per 90-image video on Klein 4B). **Only one account:** rotating several free accounts or tokens to get around the daily limit is ruled out. It likely breaks Cloudflare's terms and puts every account at risk of suspension.
- **Single-scene replanning (confirmed):** `stickman replan <unit> --hint "..."` asks the LLM to redesign one unit, with an optional hint.
- **M5 (2026-09-26):** `compare` runs Klein 4B with and without references and at 1280×720 with references; Klein 9B is left out (cost on the free plan; commercial use unconfirmed). `bootstrap.model` stays Klein 9B. Bootstrap approval is on the command line until the review page (M6). M5 and M6 run as SDD without per-task reviews, on stacked branches.
