# Spec — Script-to-Stickman Scene Image Generator

Status: draft v1 · 2026-09-22 · See `plan.md` for goals, scope, milestones and risks.

Conventions:
- "Setting" means a value in `config/settings.yaml` (§2.2). The value given in the text is its default.
- Times are handled internally as seconds (float, millisecond precision).
- Items marked **[M0]** are unverified API details. They must be confirmed in milestone M0 (§15), and the spec updated.

---

## 1. Overview

A single-user Python command-line tool, `stickman`, with a local review page. The pipeline runs these stages in order:

```
script ─► ingest ─► plan (LLM: analyse → cut → describe; split engine; prompt builder)
       ─► [review plan.yaml]
       ─► sheets for extras
       ─► [approve sheets]
       ─► render + qc on 3 test scenes
       ─► [approve tests]
       ─► render + qc on the full batch
       ─► [gallery review]
       ─► export (MP4 · images · SRT · manifests)
```

One-time setup:
- `init` checks the Cloudflare credentials.
- `bootstrap` creates the style anchor and the mascot sheet.
- `compare` runs the model comparison test.

### 1.1 Glossary
- **Line:** one timestamped entry in the input script.
- **Scene:** one or more lines merged together (fragments). Scenes are numbered `001…` after merging.
- **Unit:** one image. It is a whole scene, or one part of a split scene. Unit IDs are `006`, or `006a` and `006b` for a split scene.
- **Cast:** the characters used in a project. It contains the `mascot` (from config) plus the extras for this script.
- **Sheet:** an approved, watermark-free reference image of one character or group, stored full size plus a reference copy of at most 512 px.
- **Style anchor:** one approved image showing the house style. It is always sent in reference slot 0.
- **Version:** one generated image for a unit. A unit can have many versions, and at most one is approved.

---

## 2. Environment and configuration

### 2.1 Stack
- **Language and tooling:** Python 3.12, managed with `uv`.
- **Libraries:**
  - `typer`: the command-line interface
  - `rich`: progress bars and the end-of-run summary
  - `pydantic` v2 for data models and schemas, plus `pydantic-settings` to load `.env` and the YAML config
  - `ruamel.yaml`: reads and writes YAML while keeping comments and field order
  - `httpx` (async): Cloudflare calls
  - `Pillow` and `numpy`: image handling and pixel QC
  - `fastapi` and `uvicorn`: the review server
  - `watchfiles`: reloads the page when `plan.yaml` is saved
- **Review page front end:** plain HTML and JS served by the review server, with no build step.
- **External tool:** `ffmpeg` and `ffprobe` must be on PATH (`winget install ffmpeg`).
- **Tests:** `pytest`.

### 2.2 Files

```
<workspace>/
  .env                       # CF_ACCOUNT_ID, CF_API_TOKEN   (in .gitignore; never in config or logs)
  config/
    settings.yaml            # all numeric and behaviour settings (table below)
    style.yaml               # style_version, style_text, negative_prompt, strict_clause
    mascot.yaml              # fixed mascot description, sheet paths, seed
    visual_rules.yaml        # rules for showing things with symbols instead of text (§7.3)
    pricing.yaml             # per-model cost formulas and parameters (§9.6)
  library/
    style/anchor_v1.png, anchor_v1_ref.png
    mascot/sheet_v1.png, ref_v1.png
    characters/<char_id>/character.yaml, sheet.png, ref.png
  projects/<YYYY-MM-DD>_<slug>/        # see §3
  ledger.jsonl               # global cost ledger, append-only (§9.7)
  style_refs/                # the 4 stock reference images; humans only, NEVER sent to any model
```

### 2.3 Settings (`config/settings.yaml`)

| Key | Default | Notes |
|---|---|---|
| `account.plan` | `free` | `free` or `paid`. The daily-limit stop applies only when this is `free`. The default is `free` because the user decided to stay on the Workers Free plan (2026-09-23). |
| `input.max_minutes` | `5` | The design limit. Longer scripts are processed with a warning that they are outside the tested range. |
| `project.recent_hours` | `24` | Window used by the project-selection rule (§13) |
| `timing.fallback_wps` | `2.5` | Words per second, used when the pace can't be measured |
| `timing.min_pace_lines` | `5` | The minimum number of timed lines needed to measure the pace |
| `merge.max_lines` | `3` | The most lines one scene may merge |
| `merge.min_scene_seconds` | `0` | 0 = off. When above 0, a complete line shorter than this is merged into the previous scene. |
| `split.split_seconds` | `7.0` | A scene is a split candidate at or above this duration |
| `split.split_words` | `20` | …or at or above this word count |
| `split.min_part_seconds` | `2.5` | Each part must be at least this long |
| `split.max_parts` | `2` | v1 accepts only `2`. Other values are rejected when config loads. |
| `llm.planner_model` | `@cf/openai/gpt-oss-120b` | **[M0]** settled: ID confirmed, works on `/ai/v1/chat/completions` (§15 #5). See `docs/m0-findings.md`. |
| `llm.fallback_model` | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | Always called with JSON mode |
| `llm.vision_model` | `@cf/qwen/qwen3.8-27b` | **[M0]** settled: ID confirmed, 2 images per call (§15 #6). See `docs/m0-findings.md`. |
| `llm.batch_size` | `8` | Units per describe batch |
| `llm.temperature` | `0.4` | |
| `image.model` | `@cf/black-forest-labs/flux-2-klein-4b` | The free-plan default (user decision, 2026-09-23). It costs about 206 neurons an image, so about 48 images a day fit in the free allowance, and a run resumes after the daily reset. M5 may switch it to Klein 9B. |
| `image.steps` | `25` | Sent only to models that accept it (FLUX.2 dev). Klein models use a fixed number of steps. |
| `image.use_references` | `true` | |
| `image.sizes` | `{"16:9": [1920,1088], "9:16": [1088,1920]}` | **[M0]** settled: `1080` is silently rounded down to `1072`; `1088` is exact. Export centre-crops to 1920×1080 / 1080×1920 (§14.2). See `docs/m0-findings.md`. |
| `image.sheet_size` | `[768,1024]` | Single character. Groups use `[1024,768]`. |
| `image.anchor_size` | `[1024,768]` | |
| `image.ref_max_side` | `512` | **[M0]** settled: 512 and 513 both accepted; hard max is 4 reference images (a 5th → HTTP 400); max width 2048. See `docs/m0-findings.md`. |
| `render.concurrency` | `4` | |
| `render.timeout_s` | `120` | Replaced by a measured value after M5 |
| `render.est_seconds_per_image` | `10` | Used for time estimates until M5 measures real speeds |
| `retry.rate_limit_max` | `6` | |
| `retry.transient_max` | `3` | |
| `retry.qc_max` | `2` | Automatic QC retries per generation request |
| `retry.circuit_breaker` | `5` | Temporary errors in a row before the run pauses |
| `budget.weekly_usd` | `15.0` | Usage only. The plan fee is not included. |
| `budget.warn_ratio` | `0.8` | |
| `budget.expected_retry_rate` | `0.25` | Used only for estimates |
| `qc.*` | see §11.1 | All pixel thresholds |
| `qc.min_idea_score` | `3` | `matches_visual_idea` below this counts as a fail |
| `test.count` | `3` | |
| `bootstrap.anchor_candidates` | `4` | |
| `bootstrap.model` | `@cf/black-forest-labs/flux-2-klein-9b` | **[M0]** changed from `flux-2-dev`: dev is unusable synchronously (HTTP 408 timeouts at usable sizes/step counts). Klein 9B used as the bootstrap fallback (user-approved). See `docs/m0-findings.md`. |
| `export.fps` | `30` | |
| `export.zoom` | `false` | |
| `export.zoom_max` | `1.06` | End scale for the slow zoom |
| `export.captions` | `corrected` | `source` or `corrected` |
| `export.caption_max_chars` | `42` | Per caption line, at most 2 lines |
| `review.host` | `127.0.0.1` | Other values are rejected when config loads |
| `review.port` | `8765` | |

`.env` is loaded by pydantic-settings. If either variable is missing, every command except `--help` exits with a clear message.

---

## 3. Project layout

```
projects/2026-09-22_first-sleep/
  script.txt            # copy of the input, never modified
  plan.yaml             # YOUR content (§5.1) — hand-editable
  state.json            # TOOL data (§5.2) — written only by the tool, atomically
  manifest.json         # always regenerated (§5.5), never edit
  manifest.csv          # always regenerated, never edit
  sheets/<char_id>/v<N>.png          # extras' sheet candidates for this project
  images/<unit>_<MM-SS.s>.png        # current version of each unit
  images/_history/<unit>_v<N>.png    # every version ever generated; never deleted
  export/
    slideshow.mp4
    images/<unit>_<MM-SS.s>.png      # approved versions only
    captions.srt
  logs/run-<YYYYMMDD-HHMMSS>.jsonl
  .cache/llm/<sha256>.json           # cached LLM responses by input hash (planning can pick up after a failure)
  .lock                              # PID lock; only one generating process per project
```

**File names.** `<unit>` is the unit ID: `006`, `006a`, `006b`. `MM-SS.s` is the unit's start time, rounded to 0.1 s. Examples: `006a_00-21.0.png`, `006b_00-24.3.png`. Scene numbers are fixed when the plan is first written. Later edits never renumber scenes.

**Lock file.** `.lock` holds a PID. A lock whose process is no longer running is removed with a warning. A second `generate` run, or a regeneration from the review page, while a live lock exists either queues behind it or fails with a clear message. It never runs at the same time.

---

## 4. Input, timing, merging and splitting

### 4.1 Accepted formats
Detected automatically, in this order:
1. **SRT:** numbered cues with `HH:MM:SS,mmm --> HH:MM:SS,mmm` lines. The lines of a cue's text are joined with single spaces.
2. **Timestamped lines:** each non-blank line matches
   `^\s*(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\s*[:\-–]?\s+(.+)$`
   This covers `M:SS`, `MM:SS` and `H:MM:SS`, with optional fractions and an optional `:` or `-` separator.

Blank lines are ignored.

A file is rejected with the line number when:
- a non-blank line doesn't parse
- timestamps are not strictly increasing
- the file contains no lines

### 4.2 Word counting
A word is any token between whitespace in the **original** text. `4`, `80%`, `1992`, `40,000`, `didn't` and `E.` each count as one. Counting is done before speech-to-text corrections, so corrections never change splitting.

### 4.3 End times
- Line *i* ends where line *i+1* starts.
- For SRT, a cue's own end time is used only for the last cue. Gaps between cues are covered by the previous image, so there are never gaps.
- **The last line's end time:**
  1. `--duration` if it was given
  2. otherwise, for SRT, the last cue's end
  3. otherwise `start + words / pace`, where `pace` = total words of all lines except the last ÷ (last start − first start)
  4. if fewer than `min_pace_lines` timed lines exist, `pace = fallback_wps`
- The video always starts at 0:00. If the first line starts later, the first unit covers the time from 0:00.

### 4.4 Merging fragments
- The **analyse** LLM stage (§6.2) returns `groups`: runs of consecutive line numbers that together cover every line exactly once, with at most `merge.max_lines` lines per group.
- A merged scene's `source_text` is its lines' texts joined with single spaces. Its start is the first line's start and its end is the last line's end.
- **Fragment rules, used as hints and as a cross-check.** A line is flagged as a likely fragment when any of these is true:
  - (a) its last character is not one of `. ! ? … " ” ' )`
  - (b) its last token is a single capital initial (`^[A-Z]\.$`) or a known abbreviation (`Mr. Mrs. Ms. Dr. Prof. St. Jr. Sr. vs. etc. e.g. i.e. U.S.`)
  - (c) the next line starts with a lowercase letter
- The flagged lines are passed to the LLM as hints. Wherever the LLM's grouping differs from the flags, the difference is listed on the review page as **"Check merges"**. It is informational only.
- **Merging short lines** (`merge.min_scene_seconds` > 0) is applied after fragment merging. A complete scene shorter than the threshold is merged into the previous scene, or into the next one if it's the first scene. `merge.max_lines` still applies.

### 4.5 Split engine (plain code, same result every time)
For each scene with `duration ≥ split_seconds` **or** `words ≥ split_words`:
1. The **cut** LLM stage (§6.3) returns up to 3 candidates. Each candidate is `k`, meaning "cut after word *k*" (1 ≤ k ≤ n−1, n = the scene's word count). They come best first, at idea boundaries.
2. For each candidate in order, find the cut time **inside the original line that contains word k**, using that line's own timing:
   - Find line L, the line whose words include word k of the scene, counting through the lines in order.
   - Let j be word k's position within L (1-based), and let `L.words` be L's word count.
   - Then `t_cut = L.start + (j / L.words) × (L.end − L.start)`. When j = `L.words`, the cut falls exactly at `L.end`.
   - For an unmerged scene this is the same as `start + (k / n) × duration`.
   - Why per line: lines inside a merged scene can be spoken at very different speeds.

   The candidate is valid if both `t_cut − start` and `end − t_cut` are at least `min_part_seconds`.
3. The first valid candidate is used. Part `a` = words 1..k over [start, t_cut). Part `b` = words k+1..n over [t_cut, end).
4. If no candidate is valid, or the LLM returned none, the scene is **not split**. The review page shows the reason ("no valid cut").

The split engine takes no LLM output other than the candidate list, so it is unit-tested with fixed candidates.

### 4.6 Units
- Units are the images to generate: unsplit scenes plus split parts, in time order.
- A unit's `source_text` is its own words. For parts, `corrected_text` is the part of the scene's corrected text matching those words.
- **[M2]** Code sets it: a part's `corrected_text` is its own words with the corrections that lie inside them applied, so part a and part b joined with a space are the scene's corrected text. Only when a correction straddles the cut does the describe stage's `corrected_text` give the parts' texts, checked as §6.4 says.

### 4.7 Expected result for the sample script (golden test, milestone M1)
The sample script in the brief has 29 lines.
- **Measured pace:** 337 words in lines 1–28 over 126 s = **2.6746 words per second**.
- **Last line:** 21 words, so it runs 7.852 s and **ends at 133.852 s (2:13.9)**.
- **Merges:** lines 13 and 14 ("Historian Roger E." + "Kirch went digging…") become scene 013, with 23 words over 61.0–70.0. **28 scenes** in total.

With these fixed cut candidates, the split engine must produce exactly this:

| Scene | Span | Words | Candidates (k) | Result |
|---|---|---|---|---|
| 005 | 15.0–21.0 | 20 | [6, 15] | **not split**: 1.8 s and 1.5 s are both below 2.5 s |
| 006 | 21.0–29.0 | 17 | [7] | cut at 24.294 → `006a` and `006b` |
| 008 | 34.0–43.0 | 17 | [10] | cut at 39.294 |
| 013 | 61.0–70.0 | 23 | [13] | cut at **66.500**: word 13 is word 10 of line 14's 20 words, and line 14 runs 63.0–70.0 (per-line timing; spreading across the whole scene would give 66.087) |
| 015 | 74.0–82.0 | 26 | [11] | cut at 77.385 |
| 023 | 95.0–104.0 | 23 | [10] | cut at 98.913 |
| 024 | 104.0–114.0 | 28 | [19] | cut at 110.786 |
| 026 | 117.0–124.0 | 24 | [12] | cut at 120.500 |
| 028 | 126.0–133.852 | 21 | [6, 9] | k=6 is invalid (2.243 s), so k=9 gives a cut at 129.365 |

Total: **36 units**. No other scene is a candidate.

---

## 5. Data models

All models are Pydantic v2 with `extra="forbid"`. Every file has a top-level `schema_version: 1`.

### 5.1 `plan.yaml` (your content)

```yaml
schema_version: 1
project: first-sleep
aspect: "16:9"               # "16:9" | "9:16"
style_version: 1
image_model: "@cf/black-forest-labs/flux-2-klein-4b"   # copied from settings at creation
duration_end: 133.852         # computed end of video
pace_wps: 2.6746
cast:
  - id: mascot                # always present; description comes from config/mascot.yaml (not editable here)
  - id: caveman_group
    name: "Caveman group"
    figures: 3                # number of individual stick figures this entry draws
    description: >-
      a group of three cavemen stickmen, each wearing a simple fur loincloth drawn as a jagged-edged
      shape, messy hair of 4-5 strokes, the tallest one has a big scribbled beard
    library_ref: null         # or an id from library/characters when reused
corrections:                 # scene-level: "from" occurs in the merged scene text (§6.2)
  - scene: "001"
    from: "90 at night"
    to: "9 at night"
    reason: "speech-to-text number error; 90 is not a clock time"
  - scene: "013"             # spans lines 13–14
    from: "Roger E. Kirch"
    to: "Roger Ekirch"
    reason: "historian A. Roger Ekirch; name split by speech-to-text"
merge_check: []               # disagreements between the LLM and the fragment rules (§4.4)
scenes:
  - id: "006"
    lines: [6]
    start: 21.0
    end: 29.0
    source_text: "Anthropologists studying the Zhuansi in the Kalahari recorded what people talk about by daylight versus by firelight."
    corrected_text: "Anthropologists studying the Ju/'hoansi in the Kalahari recorded what people talk about by daylight versus by firelight."
    split: {status: split, cut_after_word: 7, candidates: [7]}   # status: none | split | no_valid_cut
    units:
      - id: "006a"
        part: "1 of 2"
        start: 21.0
        end: 24.294
        source_text: "Anthropologists studying the Zhuansi in the Kalahari"
        corrected_text: "Anthropologists studying the Ju/'hoansi in the Kalahari"
        visual_idea: "An anthropologist stickman with a notebook watches a caveman group in bright daylight"
        visual_type: literal          # literal | metaphor
        shot: wide                    # wide | medium | close-up
        time_of_day: day              # day | night | unspecified
        characters:
          - {ref: anthropologist, action: "crouching, sketching in a small notebook", emotion: curious}
          - {ref: caveman_group, action: "standing and talking, one pointing into the distance", emotion: neutral}
        mood: calm                    # optional
        setting: ["flat savanna ground line", "one small acacia tree"]   # max 2
        props: ["notebook drawn with blank pages", "small sun in the top corner"]
        composition: "anthropologist small in the left foreground, caveman group centre-right, big white sky"
        energy_marks: []              # any of: motion, surprise, wind, emphasis
        softened: false
        softened_reason: null
        seed: null                    # null = tool chooses; an integer pins it
        image_prompt: "…assembled by the prompt builder (§7)…"
        prompt_locked: false
```

**Validation rules** (Pydantic plus checks across the whole plan):
- IDs are unique. Units are in time order and cover [0, duration_end) with no gaps.
- `characters[].ref` must be `mascot` or an ID in `cast`. There are at most 3 character entries per unit.
- `setting` has at most 2 entries. `shot`, `time_of_day`, `visual_type` and `energy_marks` use their fixed value lists.
- `cast[].figures` is ≥ 1. Library references must exist.
- Timing fields (`lines`, `start`, `end`, `split`, `part`) may be changed by hand, but such changes are validated again. Breaking the rules above is an error.

**What happens on edits:**
- When you edit `image_prompt` by hand, the tool detects it: the text no longer matches the builder's output for the current fields. It then sets `prompt_locked: true` at the next load and prints a notice.
- When any visual field of a locked unit changes, the tool warns: *"Unit 006a has a locked prompt; field changes won't affect it. Run `stickman rebuild-prompt 006a` to unlock and rebuild."*
- When the tool writes `plan.yaml`, it uses ruamel round-trip mode, so your comments and field order are kept.

**`merge_check` entries [M2]:** `{line: 13, rules: merge, llm: separate}`. `rules` is what the fragment rules (§4.4) say about merging that line with the next one, and `llm` is what the analyse stage did. The last line is never listed.

### 5.2 `state.json` (tool data)

```json
{
  "schema_version": 1,
  "plan_approved": true,
  "sheets_approved": true,
  "test_units": ["015a", "006a", "008a"],
  "tests_approved": false,
  "units": {
    "006a": {
      "status": "generated",
      "current_version": 2,
      "approved_version": null,
      "versions": [
        {
          "v": 1, "file": "images/_history/006a_v1.png", "seed": 48213377,
          "model": "@cf/black-forest-labs/flux-2-klein-9b", "width": 1920, "height": 1080,
          "fingerprint": "sha256:…", "refs": ["anchor_v1_ref.png#sha256:…", "…"],
          "prompt_sent": "…", "retry_of": null, "retry_reason": null,
          "qc": {"pixel": {…}, "vision": {…}, "pass": false, "reason": "text"},
          "est_cost_usd": 0.0198, "latency_s": 7.4, "created": "2026-09-22T14:03:11+05:00"
        }
      ]
    }
  }
}
```

**Unit status values:**
- `planned`
- `generating`: a request is in progress. Reset to `planned` when the tool starts, since it means a previous run crashed.
- `generated`: passed QC and is waiting for your approval
- `needs_review`: failed QC after retries, or was refused after a softened retry
- `approved`
- `failed`: an API error, so no image was produced
- `stale`: the unit's current fingerprint (§10.4) differs from the fingerprint of its current or approved version

**Safe writes:** write to `state.json.tmp`, call `fsync`, then `os.replace`. If Windows reports the file as locked, retry up to 10 times, 100 ms apart. The state is written after **every** status change, and within one second of each image being saved.

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

### 5.3 Character library entry (`library/characters/<id>/character.yaml`)
```yaml
schema_version: 1
id: cavemen_v1
name: "Caveman group"
figures: 3
description: "…exact wording, reused word for word…"
tags: [prehistoric, group, fire]
style_version: 1
model: "@cf/black-forest-labs/flux-2-klein-9b"
seed: 771203
sheet: sheet.png
ref: ref.png
approved: 2026-09-22
```
An entry whose `style_version` is not the current `style.yaml` version is flagged as **needs regeneration**. It is never sent as a reference without warning.

### 5.4 Ledger entry (`ledger.jsonl`, one JSON object per line)
```json
{"ts":"2026-09-22T14:03:11+05:00","project":"2026-09-22_first-sleep","unit":"006a","kind":"image",
 "model":"@cf/black-forest-labs/flux-2-klein-9b","est_usd":0.0198,"billing":"billed","request_id":"…"}
```
- `kind` is one of `image`, `sheet`, `anchor`, `llm`, `vision`.
- `billing` is one of `billed`, `possibly_billed` (timeouts), `not_billed` (errors raised before the request was sent).
- **[M3]** Each entry also has `neurons`: the `cf-ai-neurons` header of a successful call, or null.
  - A `billed` entry's `est_usd` is that header's cost (§9.7), or the §9.6 estimate when the header is missing.
  - A timeout is `possibly_billed`, at its estimate.
  - Any other error response is `not_billed`.
  - When reading, a torn last line is skipped, and the next entry is still written on a line of its own.

### 5.5 Manifests
- `manifest.json` is an array of units: every plan field, plus the state status, approved version file, seed, model, QC result, `softened` and all corrections.
- `manifest.csv` has the columns `unit,start,end,duration,part,file,status,source_text,corrected_text,visual_idea,shot,seed,model,qc_pass,qc_reason,softened`.
- Both are regenerated after every state change and on export.

---

## 6. Planning (LLM)

### 6.1 General
- **Stages, in order:**
  1. **analyse**: groups, corrections, cast
  2. **cut**: split candidates, only for scenes that are split candidates
  3. **describe**: units in batches of `llm.batch_size`
- **API:** all LLM calls use the OpenAI-compatible chat endpoint (§9.3), `temperature = llm.temperature`.
- **Checking responses:** every response is checked against its Pydantic schema and the checks for its stage.
  - **1st failure:** the same model is asked again, with the list of validation errors added: *"Your previous JSON had these errors: … Return corrected JSON only."*
  - **2nd failure:** the same request goes to `llm.fallback_model` with `response_format: {type: json_schema, json_schema: <schema>}`.
  - **The fallback also fails twice:** planning stops. The errors are written to `logs/`. Batches that already succeeded stay in `.cache/llm/`, so running the command again continues from the failed stage.
- **Caching:** the cache key is `sha256(model + stage + prompt + schema)`.
  - **[M2]** `model` is always `llm.planner_model`, even when the fallback produced the cached answer, so a rerun finds it. `prompt` is the system prompt plus the user message. The stage's JSON schema is appended to the system prompt (`SCHEMA:`), because the planner's first attempts are sent without `response_format`. `replan` never reads the cache, so asking again gives a new design.

### 6.2 Stage 1: analyse
**System prompt:**
```
You plan illustrations for a stickman explainer video. You receive a narration script produced
by speech-to-text: numbered lines with start times, durations and word counts.
Return ONLY JSON matching the provided schema. Tasks:

1. GROUPS — Merge a line with the following line(s) only when it is a sentence fragment that
   continues into the next line (e.g. a name split across lines: "Historian Roger E." + "Kirch went...").
   Never merge two complete sentences, even very short ones. Groups are consecutive, cover every
   line exactly once, max {max_lines} lines per group. HINT lines were flagged by rules as likely
   fragments; decide for yourself.

2. CORRECTIONS — Fix only obvious speech-to-text errors that would change what should be drawn or
   shown in captions: impossible numbers ("90 at night" → "9 at night"), misheard names and terms.
   A wrong number is replaced by the right number, never by a word. A misheard name is replaced by the
   real name in its standard spelling (keep special characters), never by a different or broader name.
   Number-word mix-ups count as misheard terms ("the 2 half" → "the second half").
   If unsure, do not correct. Never rephrase or improve style. Corrections apply to GROUPS: a
   group's text is its lines joined with one space, so a correction may span a line break
   ("Roger E. Kirch" → "Roger Ekirch"). For each correction
   give "line" (the number of the line where the "from" text starts), the exact "from" text as it
   appears in that line's group text, the "to" text and a short reason.

3. CAST — List the recurring or important NON-mascot characters (people or groups), including people
   or groups the pictures will need even when a line only implies them (for example the people living
   in the time or place a line describes). The mascot
   ("mascot") is a fixed everyman character that is NOT listed here. For each: id (snake_case),
   name, figures (how many stick figures), and a 1–2 sentence visual description using BIG, SIMPLE,
   drawable features only: one clothing item, a hair shape, one accessory (beard, glasses, hat, book).
   Black-and-white only: features are outlines or solid black fills; no colours, no fine detail,
   no text or logos. A character that appears in many lines gets ONE entry. Prefer a matching
   LIBRARY character (use its id as library_ref) over inventing a new one.

MASCOT RULE: the mascot appears only in lines addressed to "you" or about general, present-day
human experience. Historical, scientific or third-person scenes use cast characters instead.
```
**User message:** a `LINES` block with `[n] M:SS (d.s s, w words) text` per line, then `HINTS: [line numbers]`, then `LIBRARY: [{id, name, figures, description, tags}]`.

**Schema:** `{groups: [[int]], corrections: [{line, from, to, reason}], cast: [{id, name, figures, description, library_ref|null}]}`

**[M2]** Corrections name a line, not a group position: in the live run the model confused group positions with line numbers once lines 13 and 14 had merged. The code maps the line to its group.

**Applying corrections (code, not the LLM):**
- Corrections are applied at **scene level, after grouping**. Each scene's `corrected_text` is its `source_text` with every correction for that scene replaced, first occurrence only, in the order given.
- In `plan.yaml` each correction is stored by scene ID (§5.1).
- If short-line merging (§4.4) later merges scenes together, the code carries each correction over to the resulting scene.

**Stage checks:**
- The groups are valid (§4.4).
- Each correction's `line` exists, and its `from` text occurs in the merged text of the group holding that line (lines joined with one space).
- **[M2]** `from` must appear exactly once as whole words in its group (a word edge is needed only where `from` starts or ends with a letter or digit), and two corrections in one group must not overlap, so every correction has exactly one place.
- Cast IDs are unique and don't equal `mascot`.
- Each `library_ref` exists.

### 6.3 Stage 2: cut
Runs only for scenes that meet the split trigger.

**System prompt:**
```
Each scene below is too long for one picture and will be shown as two pictures. Its words are
numbered. Propose up to 3 cut points, best first. "k" means: cut after word k. Cut only at a
natural idea boundary (clause, list item, "but/and then/so"), so that each half can be drawn as
its own clear picture. Return ONLY JSON matching the schema.
```
**User message:** for each scene, `id`, `duration`, and the numbered words (`1:Anthropologists 2:studying …`).

**Schema:** `{scenes: [{id, candidates: [int]}]}`

**Stage checks:** `1 ≤ k ≤ n−1`, with no duplicates.

### 6.4 Stage 3: describe
Runs over the units in time order, `batch_size` units per request.

**System prompt:**
```
You design one illustration per unit for a stickman explainer video. Style is fixed and handled
elsewhere: black ink stickman line art on a plain white background. Your job is WHAT the picture
shows. Return ONLY JSON matching the schema.

Rules:
- Every picture tells the story of its unit's text in one clear image, centred on stick-figure
  characters. Visual metaphors are allowed and encouraged for abstract lines (e.g. "running very
  old firmware" → the mascot with an old computer screen for a chest showing a loading bar).
- NEVER any text: no words, letters, numbers, captions, labels, speech bubbles with text, "Zzz",
  signs, logos. Follow VISUAL RULES for things that normally contain text.
- Characters: use only ids from CAST or "mascot". Max 3 character entries. Follow the MASCOT RULE.
  Give each character an action/pose and an emotion (expression).
- setting: at most 2 simple elements. props: only what the idea needs. Lots of white space.
- shot: wide | medium | close-up. Vary shots; do not use the same shot 3 times in a row
  (PREVIOUS shows the last units).
- time_of_day: day | night | unspecified. Day scenes show a small sun; night scenes show a small
  crescent moon and stars; never fill the background.
- energy_marks: any of motion, surprise, wind, emphasis — only when the moment has energy.
- SENSITIVE content (sex, violence, death, injury, drugs): show it in a tasteful, symbolic way
  (e.g. a couple sitting close by the fire with a small heart above them). Set softened=true and
  a short softened_reason whenever you tone something down.
- corrected_text: for split parts, return exactly the part of the scene's corrected text that
  corresponds to this unit's words.
- visual_type: "metaphor" if the picture is not a literal depiction, else "literal".
- visual_idea: one sentence a human can scan quickly.
```
**User message:**
- `CAST` (id, name, figures, description) and the `MASCOT RULE`
- `VISUAL RULES` (all of `visual_rules.yaml`)
- `PREVIOUS`: the last 2 units' visual_idea and shot
- `NEXT`: the next 2 units' text, for context only
- `UNITS`: id, start–end, source_text, the scene's corrected_text, and part

**Schema (per unit):** `{id, corrected_text, visual_idea, visual_type, shot, time_of_day, characters: [{ref, action, emotion}], mood|null, setting: [str], props: [str], composition, energy_marks: [str], softened, softened_reason|null}`

**Stage checks:**
- The IDs exactly match the batch.
- The rules in §5.1 hold.
- `corrected_text` is not empty.
- **[M2]** For a split part whose text code can't derive (§4.6), `corrected_text` must be the start (part `1 of 2`) or the end (part `2 of 2`) of the scene's corrected text, at a word edge and shorter than the whole (whitespace normalised). Every other unit's `corrected_text` answer is ignored, because code sets it.
- **Text-word filter:**
  - It applies **only to the fields that go into the image prompt**: `visual_idea`, `characters[].action`, `setting`, `props` and `composition`.
  - It never applies to `source_text` or `corrected_text`. Line 14 of the sample, for example, contains "medical texts".
  - It uses case-insensitive **whole-word** matching: `\b(text|label|caption|writing|zzz)\b` plus the phrase `sign that says`. So "texts", "context" and "labelled" don't match.
  - A match counts as a validation error.

### 6.5 `replan <unit> [--hint "..."]`
- Reruns stage 3 for one unit, with the same context plus `HINT: <text>` if one was given.
- The unit's visual fields are replaced and its prompt is rebuilt, which sets `prompt_locked: false`. Timing is not changed.
- **[M2]** `corrected_text` is not changed either, so a caption you edited by hand survives.
- The write to `plan.yaml` is checked against the file hash (§12.4). The unit becomes `stale` if it had an image.

### 6.6 `recompose --aspect 9:16`
- Creates the sibling project `projects/<date>_<slug>_9x16/`. It gets a copy of `plan.yaml` with `aspect: "9:16"`, and the same cast and library references. Sheets are reused and not regenerated.
- One LLM pass (batched like stage 3) rewrites **only** `shot` and `composition` for a tall frame. The system prompt adds: *"The frame is vertical 9:16. Stack elements vertically, keep characters large and central, avoid wide horizontal spreads."*
- Prompts are rebuilt for units that aren't locked. Locked units are listed with a warning, and their prompts are kept unchanged.
- The new project starts at `plan_approved: false`. Test images are picked and generated again.

---

## 7. Image prompt

### 7.1 `config/style.yaml` (v1)
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
`negative_prompt` is sent only to models whose `pricing.yaml` entry has `supports_negative_prompt: true`. No FLUX.2 model accepts it.

### 7.2 `config/mascot.yaml` (the description is a draft, finalised at bootstrap)
```yaml
schema_version: 1
id: mascot
name: "Everyman"
figures: 1
# identity: what makes him HIM; visible in every shot (close-ups, lying under a blanket, etc.)
identity: >-
  the main character: an average-height stickman with a large round head, exactly three short
  hair strokes curling to the right on top of the head, dot eyes and short curved eyebrows
# default_outfit: optional costume (Q5); may be hidden (blanket, close-up) or omitted per scene
default_outfit: >-
  a small solid-black necktie and solid-black shoes
sheet: library/mascot/sheet_v1.png
ref: library/mascot/ref_v1.png
seed: null            # set at bootstrap approval
model: null
style_version: 1
```

### 7.3 `config/visual_rules.yaml` (v1 contents, extend over time)
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

### 7.4 Prompt builder (template)
The prompt is assembled by code, the same way every time, from `style.yaml`, the cast and the unit's fields:

```
{style_text}

Scene: {visual_idea}.
Characters:
{for each character c:}
- {cast[c.ref].description} — {c.action}, with a {c.emotion} expression.
  (for the mascot, the description is: "{identity}, usually wearing {default_outfit}
   (these may be hidden or left out when the scene calls for it)")
Setting: {setting joined with ", " | "minimal, just a simple ground line"}.
Props: {props joined with ", " | "none"}.
{time_of_day == day   → "A small sun in an upper corner."}
{time_of_day == night → "A small crescent moon and a few stars; the background stays white."}
Composition: {shot} shot. {composition}.
{energy_marks → "Add simple cartoon {marks} lines for energy."}

{if references:}
Reference images: image 0 shows the drawing style only — match its line weight and look,
not its content, and do not copy its figures. {for slot i ≥ 1:} Image {i} shows {name}: draw
this character with exactly the same head and hair (and the same clothing when visible), but in
the pose described above.

{strict_clause}
```

**Reference slots** (when `image.use_references` is on):
- **Slot 0:** the style anchor's reference copy.
- **Slot 1:** the mascot's reference if the mascot is in the unit. Otherwise the reference of the first extra in `characters` order.
- **Slots 2–3:** the next extras' references, in `characters` order.
- A cast member without an approved sheet appears in the text only.

**Small wording rules [M2]:** trailing full stops of `visual_idea`, `composition`, cast descriptions and actions are trimmed, so the template's own full stop isn't doubled. `visual_idea` and `composition` start with a capital letter. "a" or "an" matches the emotion. A unit with no characters says `Characters: none.`

### 7.5 Prompt fixes for retries (keyed by QC reason, §11.3)
| Reason | Change on retry |
|---|---|
| `text` | New seed. Put `strict_clause` **at the very start** of the prompt as well as the end. Rewrite text-attracting props using the text-free wording in `visual_rules.yaml`. |
| `background_filled` | New seed. Add: *"The entire background is plain white. Do not fill or darken the sky or the ground."* |
| `style` (colour or shading or realism) | New seed. Add: *"Pure black ink lines on pure white. Flat. No colour, no grey, no shading, not realistic."* |
| `watermark` | New seed. Add the strict clause at the start. |
| `anatomy` | New seed. Add: *"Each character has exactly one head, two arms and two legs."* |
| `character_count` | New seed. Add: *"Exactly {N} stick figures in total: {list}."* |
| `mascot_mismatch` | New seed. Add: *"The main character must have exactly the same head and hair as image 1: {identity}."* It deliberately doesn't mention the tie or shoes. |
| `weak_idea` | Retry 1: new seed. Retry 2: stage 3 rewrites this unit (like `replan`, with the vision model's notes as the hint). |
| `safety_filtered` | One LLM "soften" rewrite of the unit's visual fields, with the hint *"make it more symbolic and tasteful"*. Sets `softened: true`. If that is also filtered, the unit becomes `needs_review`. |
| `empty` | New seed. |

**Locked units** (`prompt_locked: true`): only seed changes and added fixed clauses are applied. LLM rewrites (`weak_idea` retry 2 and `safety_filtered`) are skipped, and the unit becomes `needs_review`.

---

## 8. Characters: bootstrap, sheets and library

### 8.1 `bootstrap` (one-time; again after a `style_version` change)
1. **Style anchor.**
   - Generate `bootstrap.anchor_candidates` images at `image.anchor_size` on `bootstrap.model`, with **no reference images**. Each gets a different seed.
   - Prompt: `style_text` + *"Scene: two stickmen talking; the taller one gestures with an open palm, the shorter one listens. A light hatched ground shadow."* + `strict_clause`.
   - **[M0] Check whether the anchor's content leaks into scenes.** Generate 5 single-figure units with the two-figure anchor in slot 0. If any of them shows extra figures, which would fail `character_count`, switch to the single-figure anchor prompt: *"Scene: one stickman standing and waving with an open palm. A light hatched ground shadow."* Record the result in §15.
   - Run QC (§11) on each candidate. The review page shows them all, with QC results.
   - Approve one. It is saved as `library/style/anchor_v1.png`, and `anchor_v1_ref.png` is made by resizing it to fit within `ref_max_side`.
2. **Mascot sheet.**
   - Generate candidates (default 3) at `image.sheet_size`, with slot 0 = the anchor.
   - Prompt: `style_text` + *"Character sheet: a single full-body front view of {identity}, wearing {default_outfit}, standing in a neutral pose, centred, arms relaxed."* + `strict_clause`.
   - Approve one. Its sheet, reference copy, `seed` and `model` are written to `mascot.yaml`. The approved `identity` and `default_outfit` are frozen.

### 8.2 Extras' sheets (per project, after plan approval)
- For each cast member without `library_ref`: generate 2 candidates on `image.model`, using the §8.1 prompt with the member's description. The size is `sheet_size`, or `[1024,768]` if `figures > 1`, and slot 0 is the anchor.
- Approve one per member on the review page, or regenerate. Approval saves it to `library/characters/<id>/` (§5.3) and sets `library_ref` in `plan.yaml`.
- Members with `library_ref` reuse the stored sheet without asking. The review page still shows them, with a **"swap for a new sheet"** action.
- `--no-review` approves the candidate with the best QC result automatically.

### 8.3 Reference copies
Reference copies are always made by the tool: longest side at most `ref_max_side`, aspect ratio kept, PNG. The source sheet is never altered.

---

## 9. Cloudflare Workers AI integration

### 9.1 Auth
- **Credentials:** `CF_ACCOUNT_ID` and `CF_API_TOKEN` in `.env`. The token is created from the **"Create a Workers AI API Token"** template, or as a custom token with *Workers AI – Read* and *Workers AI – Edit*.
- **Header:** `Authorization: Bearer <token>`.
- **Logging:** the token is never logged. The logger masks any string that matches the token.
- **`stickman init`:** checks the token with a tiny LLM call. A 401 or 403 prints how to fix the token.

### 9.2 Image generation (FLUX.2 family)
```
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
Content-Type: multipart/form-data
fields: prompt, width, height, seed, [steps — dev only], [guidance — optional],
        input_image_0 … input_image_3   (binary PNG, each ≤ ref_max_side)
```
- **Response:** JSON containing a base64 image. **[M0]** Settled: it sits at `result.image`, and the format is JPEG. See `docs/m0-findings.md`.
- **Saving:** detect the format from the magic bytes and always save as PNG. Write to a temp file, then `os.replace`.
- **Seeds:** when `seed` is null, the seed is a random 32-bit integer, stored with the version. **[M0]** The same seed does **not** reproduce identical pixels on Klein 4B. The seed is kept as a record of how an image was made, not as a way to recreate it. Nothing may rely on regenerating an identical image from a stored seed. See `docs/m0-findings.md`.
- **[M3]** A null seed is drawn from 0 … 2³¹−1, valid whether the API reads it as signed or unsigned 32-bit.

### 9.3 LLM
```
POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions
JSON: {model, messages:[{role:system,…},{role:user,…}], temperature, max_tokens,
       [response_format: {type:"json_schema", json_schema:{name, schema}}]}
```
- **[M0]** Settled: gpt-oss-120b works on this endpoint, with and without `response_format: json_schema`. `content` holds the JSON as a string; a separate `reasoning`/`reasoning_content` field carries chain-of-thought and is ignored. The native `/ai/run/{model}` format wraps the same shape one level deeper, under `result`, and isn't needed. See `docs/m0-findings.md`.
- **Reading replies:** the JSON is taken from the first `{` to the last `}` of the reply content, which tolerates text before or after it — this lenient extraction is also what `llm.vision_model` replies need (§9.4; M0 found they aren't clean JSON either). The `usage` token counts are used for the ledger. If they're missing, the count is estimated as characters ÷ 4.

### 9.4 Vision QC
- **Request:** the same chat endpoint, with `model = llm.vision_model`. The user message holds image content parts: the unit image, plus the mascot reference in mascot units.
- **[M0]** Settled: `@cf/qwen/qwen3.8-27b` accepts **2 images per call** as `image_url` content parts. See `docs/m0-findings.md`.
- **Fallback when only one image is accepted:** not needed — 2 images per call are accepted (above). (If this ever regresses: send one composite made by the tool, with the mascot reference on the left and the unit image (scaled to 768 px tall) on the right. The prompt then says *"left = reference, right = image to check"*.)
- **Reply parsing:** qwen's replies are **not clean JSON** — expect a leading `\n\n`, or the JSON wrapped in a ` ```json ` fence, plus a separate `reasoning` field like gpt-oss's. Extract JSON the same lenient way as §9.3 (first `{` to last `}`); a markdown fence's backticks fall outside that range, so it still works. **[M0]** See `docs/m0-findings.md`.

### 9.5 Sorting errors into categories (the `cf` module)

| Category | How it's detected | What the tool does |
|---|---|---|
| `rate_limited` | HTTP 429 (not the daily limit) | Waits using `Retry-After`, or 1, 2, 4… s up to 60 s with some randomness. Up to `rate_limit_max` tries. |
| `daily_limit` | Only when `account.plan: free`: the daily-neuron-limit error (**[M0]** settled: HTTP 429, code 4006, message contains "daily free allocation"; §15 #7) | Stops the run: no new requests, lets in-flight requests finish, saves state, prints the resume message |
| `auth` | 401 or 403 | Stops the whole run at once |
| `bad_request` | Other 4xx | No retry. The unit becomes `failed` with the API message. It doesn't count toward the circuit breaker. |
| `refused` | Content-policy error **[M0]** | Treated as `safety_filtered` in QC (§7.5). Doesn't count toward the circuit breaker. |
| `transient` | 5xx, network error, timeout | Waits and retries up to `transient_max`. After that the unit is `failed`. Counts toward the circuit breaker. |
| timeout | No response within `render.timeout_s` | `transient`, and the ledger entry is `billing: possibly_billed` |

**Circuit breaker:** when `retry.circuit_breaker` `transient` failures happen in a row (a success resets the count), the run pauses. It saves state and prints *"Possible outage — run `stickman resume` later."*

**Retry counts [M2]:** `retry.rate_limit_max` and `retry.transient_max` count retries after the first try.

**[M3] What the breaker counts:** each API attempt that ends in a `transient` error, including attempts that are then retried. So with `transient_max: 3`, two units' failures can trip it. A `refused` request ends `failed` until M4 adds the softened retry (§7.5).

### 9.6 Cost estimates (`config/pricing.yaml`)
These formulas are used only for **pre-call estimates** and for calls with no
`cf-ai-neurons` response header (errors, timeouts); when the header is present the
ledger uses it directly (§9.7).
- **Tiles:** `w × h / 262,144` (area-based). **[M0]** replaces the earlier per-side
  ceiling formula: measured neuron costs (e.g. Klein 4B 1920×1072 ≈ 206 neurons, Klein
  9B 1920×1072 ≈ 1541 neurons) fit an area-based count better. See `docs/m0-findings.md`.
- **Megapixels:** `w × h / 1,048,576` (area-based, same reasoning).

```yaml
"@cf/black-forest-labs/flux-2-klein-4b": {kind: image, out_tile: 0.000287, in_tile: 0.000059, supports_negative_prompt: false, supports_steps: false}
"@cf/black-forest-labs/flux-2-klein-9b": {kind: image, first_mp: 0.015, extra_mp: 0.002, in_mp: 0.002, supports_negative_prompt: false, supports_steps: false}
"@cf/black-forest-labs/flux-2-dev":      {kind: image, out_tile_step: 0.00041, in_tile_step: 0.00021, supports_negative_prompt: false, supports_steps: true}
"@cf/openai/gpt-oss-120b":               {kind: llm, in_per_m: 0.35, out_per_m: 0.75}
"@cf/meta/llama-3.3-70b-instruct-fp8-fast": {kind: llm, in_per_m: 0.293, out_per_m: 2.253}
"@cf/qwen/qwen3.8-27b":                  {kind: llm, in_per_m: 0.45, out_per_m: 3.20}
free_daily_usd: 0.11      # 10,000 neurons × $0.011/1k
```

**Formulas:**
- **Klein 4B:** `out_tiles×out_tile + Σ in_tiles×in_tile`
- **Klein 9B:** `first_mp + max(0, ceil(mp−1))×extra_mp + Σ ceil(in_mp)×in_mp`
- **FLUX.2 dev:** `steps × (out_tiles×out_tile_step + Σ in_tiles×in_tile_step)`

**[M5]** Re-compare the formula-based estimates against the dashboard at volume, and adjust further if they're consistently off.

### 9.7 Ledger and budget
- **Actual cost (M0):** every 2xx response carries a `cf-ai-neurons` header (absent on errors). The ledger records the **actual** cost as `neurons × $0.011/1000` from that header when it's present; the §9.6 formulas are the fallback, used only for pre-call estimates and for calls with no header. See `docs/m0-findings.md`.
- **Recording:** every billable call adds an entry to `ledger.jsonl` (§5.4) straight after the response arrives. Before sending, an in-memory "in flight" amount is reserved.
- **Week:** Monday 00:00 to Sunday 23:59:59, local time.
- **Checking the budget:** before **every** call:
  `spent_this_week (billed + possibly_billed) + in_flight + this_call_estimate`
  - At or above `warn_ratio × weekly_usd`: warn once per run, in the CLI and on the review page.
  - Above `weekly_usd`: without `--force`, stop starting new calls, let in-flight calls finish, save state and print the budget message. With `--force`, continue with a visible warning.
- **The free allocation:** estimates on the review page show *"up to $0.11 of today's usage may be covered by the free daily allocation"*. The ledger and budget always use the **gross** cost, which keeps them on the safe side.
- **[M3]** Planning's LLM calls (`new`, `replan`) go in the ledger too. The weekly budget never stops them: a plan costs about $0.03, and `new` has no `--force`.
  - The budget check applies to image calls.
  - A run reads the week's spend from the ledger when it starts, and adds its own calls as they finish.

---

## 10. Rendering

### 10.1 Project states and generate flow
`generate` (and `resume`, which is the same command) does whichever of these comes first:
1. `plan_approved` is false: print "approve the plan in the review page" and exit.
2. Some extras have no approved sheet: generate their candidates, then exit and ask for approval.
3. `tests_approved` is false:
   - generate the test units (§10.2) that aren't done yet, run QC, and pause
   - then print "approve tests in the review page"
4. Otherwise: generate every unit whose status is `planned` or `failed`, run QC, then print the run summary.

**Rules for step 4:**
- Units already `generated`, `needs_review` or `approved` are skipped.
- `stale` units are **not** regenerated automatically. The summary lists them.
- Approved test units are kept and reused.

**`--no-review`:**
- auto-approves the plan and extras' sheets (§8.2)
- skips the test pause
- still runs QC, and still leaves the final gallery review to you

**[M3] Until M6/M7:** there's no review page (M6) and no test-first flow (M7) yet, so `generate` has no approval steps.
- It generates every `planned` or `failed` unit in time order.
- `--limit N` generates at most N of them in one run.
- On the free plan it first prints how many images fit in today's allocation.
- A run that reaches the daily limit pauses with exit code 2. `resume` continues after 00:00 UTC.

### 10.2 Picking the test units
Run over units in time order:
1. **Mascot unit:** the first unit whose `characters` include `mascot`.
2. **Extras unit:** the first unit with at least one non-mascot character, not already picked.
3. **Most complex unit:** the highest `2×Σfigures + len(props) + len(setting) + len(energy_marks)`, not already picked. Ties go to the earlier unit.
4. **Night or fire rule:** if none of the picks has `time_of_day: night`, or a prop or setting containing "fire" or "flame", replace pick 3 with the most complex unit that does, if one exists.
5. If a category has no match, fill it with the next most complex unit not already picked.

`--test-scenes 3,12,20` names the scenes directly and skips this process. Values are scene numbers; for a split scene, part `a` is used. Unit IDs such as `012b` are also accepted.

### 10.3 Running requests in parallel
- An `asyncio.Semaphore(render.concurrency)` limits image requests. QC vision calls share the same limit.
- Order of work for one unit:
  1. Build the prompt and references.
  2. Check the budget.
  3. Set the status to `generating` and save the state.
  4. Make the request.
  5. Save the image.
  6. Run QC.
  7. Apply the QC retries, if needed (§11.3).
  8. Set the final status and save the state.
- Progress is shown in the CLI with rich: overall bar, per-status counts, cost so far.
- **[M3]** There's no QC step until M4: a saved image makes the unit `generated`. The history file is written first, then `state.json`, then the current copy. A kill between any two is put right when the next run starts (§5.2).

### 10.4 Fingerprints and stale detection
- **What the fingerprint covers:** `fingerprint = sha256(canonical_json({visual fields of the unit, image_prompt, seed-override, model, aspect, width, height, style_version, [sha256 of each reference file used]}))`, where:
  - canonical JSON means sorted keys, UTF-8 and no whitespace
  - the visual fields are `visual_idea`, `visual_type`, `shot`, `time_of_day`, `characters`, `mood`, `setting`, `props`, `composition`, `energy_marks`, and the cast descriptions of referenced characters
  - it is computed from the **parsed** plan, so changes to comments or formatting never make a unit stale
- **When it's checked:** on every plan load and on every reload triggered by the file watcher. A unit with an image whose stored fingerprint differs becomes `stale`. Editing it back clears the stale status.
- **Stale is never regenerated automatically.**

### 10.5 End-of-run summary
```
Run finished · 36 units · done 31 · needs_review 3 · failed 1 · stale 0 · skipped 1
Cost this run ≈ $0.74 (2 possibly billed) · week ≈ $6.12 / $15.00 · time 6m12s
Failed: 024b (bad_request: …)   Needs review: 013a (text), 020 (safety_filtered), 026b (weak_idea)
```

---

## 11. Quality control

### 11.1 Pixel checks (free, on a 480 px-tall grayscale/HSV copy)

| Key (`qc.`) | Default | Rule → failure reason |
|---|---|---|
| `near_white_lum` | `215` | Luminance at or above this counts as "near-white". Low enough to accept cream and off-white. |
| `color_sat` | `0.25` | A pixel counts as colour if its saturation is above this and its value is above 0.3 |
| `max_color_fraction` | `0.01` | Colour pixels above this fraction → `style` |
| `min_white_fraction` | `0.55` | Near-white pixels below this fraction → `background_filled`. Tune it on dense scenes in M5. |
| `black_lum` | `50` | A pixel counts as black below this luminance |
| `max_black_blob_fraction` | `0.04` | The **largest connected** black area (8-connected) above this fraction of the image → `background_filled`. This lets shoes and ties pass. |
| `min_ink_fraction` | `0.005` | Ink pixels (luminance below 128) below this fraction → `empty` |
| `uniform_std_max` | `6` | A luminance standard deviation below this means the image is uniform (see the order below) |
| `blur_lap_var_min` | `15` | Variance of the Laplacian below this means the image is blurred (see the order below) |

**The checks run in this order, and the first match decides the result:**
1. **Uniform** (std < `uniform_std_max`):
   - if the mean luminance is at least `near_white_lum`, the result is **`empty`** (a blank white or cream image)
   - otherwise it is **`safety_filtered`** (uniform black or grey)
2. **Almost no ink** (ink fraction < `min_ink_fraction`): **`empty`**.
3. **Blurred** (Laplacian variance < `blur_lap_var_min`): **`safety_filtered`**. This check runs only after steps 1 and 2. A blank image would also have almost no Laplacian variance, so steps 1 and 2 must catch it first, or it would get a false soften rewrite.
4. **Colour, filled background, black areas:** `style` or `background_filled`, as in the table.

So `safety_filtered` is only ever given to a **dark or blurred** image, never to a blank white one. A dark image that still has line detail fails as `background_filled`. All values are initial guesses and are tuned in M5.

### 11.2 Vision check
Runs only if the pixel checks pass, since there's no point paying for the vision call otherwise.

**Prompt:**
```
You are a strict quality checker for black-and-white stickman illustrations. Look at the image
{and the reference character} and return ONLY JSON matching the schema. Expected: {visual_idea}.
Expected figures: {N} ({list of cast names with figure counts}).
- has_text: true if ANY letters, numbers, words, symbols like "Zzz", or pseudo-text squiggles appear.
- style_ok: false if there is colour, grey shading, gradients, a filled background, or a realistic/3D look.
- anatomy_ok: false if any figure has extra/missing heads or limbs, or merged bodies.
- watermark_like: true if there is any faint overlaid pattern, logo or watermark-like mark.
- character_count: number of stick figures you see.
- matches_visual_idea: 1–5, how clearly the image shows the expected idea.
- mascot_matches_sheet: (only when a reference is given) true if the main character has the same
  HEAD and HAIR as the reference: round head, exactly three short hair strokes curling right.
  Judge head and hair only. Ignore clothing: the necktie and shoes may be hidden (blanket,
  close-up) or absent, and that is NOT a mismatch.
- notes: one short sentence on the most important problem, or "".
```
**Schema:** `{has_text, text_seen, style_ok, anatomy_ok, watermark_like, character_count, matches_visual_idea, mascot_matches_sheet|null, notes}`

### 11.3 Deciding pass or fail, and retries
- **Passing:** every pixel check passes, and:
  - `has_text` is false, `style_ok` is true, `anatomy_ok` is true and `watermark_like` is false
  - `character_count` matches the expected figures: exactly when the expected count is ≤ 3, within ±1 above that
  - `matches_visual_idea ≥ qc.min_idea_score`
  - `mascot_matches_sheet` is not false
- **The main reason for a failure** is the first match in this order: `safety_filtered` > `empty` > `text` > `background_filled` > `style` > `watermark` > `anatomy` > `character_count` > `mascot_mismatch` > `weak_idea`.
- **Retries:** a failing image gets up to `retry.qc_max` retries, using the fix in §7.5 for its main reason. Each retry is a new version, linked to the one before by `retry_of` and `retry_reason`, and counts toward the budget.
- **Choosing the current version:** after the last retry, the best-scoring version (passes first, then the highest `matches_visual_idea`) becomes `current_version`. The status is `generated` if that version passes, and `needs_review` otherwise.
- **Recording:** every QC result is stored with its version and copied into the manifest.
- **Honest limitation:** the vision model will sometimes miss small text or miscount limbs. The final gallery review is the real gate.

---

## 12. Review page

### 12.1 Server
- FastAPI and uvicorn, bound to **127.0.0.1 only** (`review.host` must be 127.0.0.1). There's no login, because the page is only reachable from this machine.
- `stickman review` starts the server and opens the browser.
- Image-generating actions run inside the server process, using the render module. They respect the lock file (§3) and the budget.
- **Live updates:** server-sent events at `/api/events` for plan reloads, validation errors, generation progress and budget warnings.

### 12.2 Views
1. **Plan** (read-only):
   - a table of units showing time, part, text, corrected text, visual_idea, shot, characters, and badges (locked, softened, split, "no valid cut")
   - panels for the corrections list, "Check merges", the cast, and validation errors
   - the **cost and time estimate**: units × per-image estimate × (1 + expected_retry_rate) + sheets + LLM + QC, plus the free-allocation note. Time = units × measured median time ÷ concurrency.
   - the **Approve plan** button, disabled while there are validation errors
2. **Sheets:** the style anchor and mascot (bootstrap only), plus extras' candidates. Approve, regenerate and "swap for a new sheet".
3. **Tests:** the 3 test units, with the gallery controls, and an **Approve tests → start batch** button.
4. **Gallery:**
   - Order: `needs_review`, then `stale`, then `failed`, then everything else in time order.
   - Each card shows the image, timestamp, part, line text, visual_idea, status, QC reason and notes, and badges (softened, stale, locked).
   - Actions: **Approve**, **Regenerate**, **Edit prompt** (§12.4), **History** (pick any earlier version as current), **Replan with hint**.
   - After a regeneration, the old and new versions are shown **side by side** until one is chosen.
   - **Approve all remaining:** approves every `generated` unit, skipping `needs_review`, `stale` and `failed`.

### 12.3 Keyboard shortcuts
`A` approve · `R` regenerate · `E` edit prompt · `H` history · `J` or `→` next · `K` or `←` previous · `1` or `2` pick left or right in the side-by-side view · `Esc` close.

### 12.4 Reloading and safe edits
- **Reloading:** `watchfiles` watches `plan.yaml`. When the file is saved, the server loads and validates it again, updates stale statuses, and pushes an event. The page redraws and shows validation errors clearly, with the YAML path and message.
- **Ignoring its own writes:** after writing `plan.yaml`, the tool records the new file hash, and the watcher ignores changes with that hash.
- **Prompt edits from the browser:**
  1. The page sends `{unit, new_prompt, plan_hash_when_loaded}`.
  2. The server compares that hash with the file on disk. If they differ, it returns **409**, and the page shows *"plan.yaml changed on disk since this page loaded — reload before saving"*. Nothing is overwritten.
  3. Otherwise it writes the prompt with ruamel and sets `prompt_locked: true`.

### 12.5 API (internal)
```
GET  /api/project                     GET /api/events (SSE)
POST /api/plan/approve
POST /api/sheets/{char_id}/approve    POST /api/sheets/{char_id}/regenerate
POST /api/tests/approve
POST /api/units/{id}/approve          POST /api/units/{id}/regenerate
POST /api/units/{id}/select-version   PUT  /api/units/{id}/prompt   (409 on hash mismatch)
POST /api/units/{id}/replan           POST /api/units/approve-remaining
```

---

## 13. Command-line interface

**Choosing the project:**
- Commands run against `-p/--project <dir>`.
- **Every command prints the project name on its first line** (`Project: 2026-09-22_first-sleep_9x16`).
- Without `-p`, the default is the most recently modified project, **except** for `generate`, `resume`, `regen` and `export`. Those commands **refuse to run without `-p`** when more than one project was modified in the last `project.recent_hours` hours. They exit with code 1 and list those projects.
- `resume` is included in that rule because it is the same as `generate`.
- This prevents a 9:16 sibling created by `recompose` from quietly becoming the target.

| Command | Behaviour |
|---|---|
| `stickman init` | Creates `config/` with the defaults and `.env.example`, checks the credentials and ffmpeg |
| `stickman bootstrap [--candidates N]` | §8.1. Opens the review page for approvals. |
| `stickman compare [--script samples/first-sleep.txt]` | §14.4 |
| `stickman new <script> --aspect 16:9\|9:16 [--duration M:SS] [--name slug] [--no-review]` | Creates the project, runs planning, writes `plan.yaml` and opens the review page (unless `--no-review`, which continues straight into generation) |
| `stickman generate [--force] [--test-scenes 3,12,20] [--no-review]` | §10.1 |
| `stickman resume [--force]` | Same as `generate` |
| `stickman status` | Counts per status, stale list, estimated cost of the remaining work, weekly spend |
| `stickman review` | Starts the review page |
| `stickman regen <unit…>` | Generates a new version for each unit, with QC |
| `stickman rebuild-prompt <unit>` | Unlocks the unit and rebuilds `image_prompt` from its fields. The unit becomes stale if it has an image. |
| `stickman replan <unit> [--hint "..."]` | §6.5 |
| `stickman recompose --aspect 9:16` | §6.6 |
| `stickman export [--zoom] [--captions source\|corrected] [--allow-unapproved]` | §14 |
| `stickman library list` | Lists the library characters, flagging any with an outdated style version |

Exit codes: `0` success; `1` user or validation error; `2` a pause (budget, daily limit, circuit breaker, waiting for approval); `3` an auth or config error.

**[M2] `new` and `replan`:** `new` refuses to run when the project already has a `plan.yaml`. If planning stopped (daily limit or a failure), running the same `new` command again continues from the cached stages. `replan` uses `-p`, or else the most recently modified project. The strict `-p` rule, `--no-review`, opening the review page, prompt-lock detection (§5.1) and stale marking (§10.4) come in M3–M7.

**[M2] Continuing on a later date:** when today's `<date>_<slug>` folder doesn't exist, `new` continues in the newest `<date>_<slug>` folder that has no `plan.yaml` and holds the same script, and says so after the `Project:` line, so a rerun after the 00:00 UTC reset finds the cached stages even when the local date has changed.

**[M3] `generate` and `resume`:**
- They take `-p`, or else use the most recently modified planned project. The strict `-p` rule comes in M7.
- Their other options are `--force` (go past the weekly budget) and `--limit N`.
- Both hold the project's `.lock` while they run.
- Ctrl+C exits with code 130; finished images are kept.

---

## 14. Export

### 14.1 Prerequisites
- Every unit must be `approved`. Otherwise export stops and lists the units that aren't.
- **`--allow-unapproved`:** uses the current version of each unapproved unit, or a plain mid-grey frame if the unit has no image. It prints a warning listing those units.

### 14.2 MP4 slideshow (`export/slideshow.mp4`)
- **Frames:**
  - each unit's frame range is `f_start = round(start × fps)` to the next unit's `f_start`
  - the first unit's `f_start` is 0
  - the last unit ends at `round(duration_end × fps)`
  - so there are no gaps and no accumulated drift, and every cut lands within half a frame of its timestamp
- **Assembly:** ffmpeg concat demuxer, with each image's `duration = frames / fps`.
  - **The concat demuxer ignores the `duration` of the last entry unless that file is listed once more at the end, with no duration.** The list file must therefore end like this:
    ```
    file 'images/028b_02-09.4.png'
    duration 4.500000
    file 'images/028b_02-09.4.png'
    ```
  - Without this, the last unit is one frame long, and the ffprobe frame check fails.
  - The frame check below is the safeguard. Output is also trimmed to exactly `round(duration_end × fps)` frames with `-frames:v`.
  - **Output settings:** `-r 30 -fps_mode cfr -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -movflags +faststart`.
  - **Frame size:** exactly `image.sizes[aspect]`. An image of a different size is scaled to fit and padded with white.
  - **Crop note [M0]:** `image.sizes` generates at 1920×1088 / 1088×1920 (the exact sizes FLUX.2 honors — `1080` is silently rounded to `1072`; §15 #2). Each frame is centre-cropped to 1920×1080 / 1080×1920 before assembly, so the exported video keeps standard dimensions. See `docs/m0-findings.md`.
- **`--zoom`:**
  - Each unit zooms slowly and evenly from 1.00 to `export.zoom_max` over its duration, centred.
  - To avoid ffmpeg's wobble: scale to 2× first, apply `zoompan` with `d=frames` and `s=<2×size>`, then scale down to the output size.
- **No audio:** the voiceover is added in CapCut.
- **Check after encoding:** ffprobe must report the expected total frame count and frame size. Otherwise export fails.

### 14.3 Images and captions
- **`export/images/`:** the approved version of each unit, copied under its file name (§3).
- **`export/captions.srt`:**
  - **`captions: source`:** one cue per **original script line**, at that line's times.
  - **`captions: corrected`** (default): one cue per **merged scene**, at the scene's times before splitting, since corrections can span lines.
  - **Line wrapping:** at most `caption_max_chars` per line and 2 lines per cue.
  - **Longer text:** the cue is split at the scene's image split point if there is one. Otherwise it's split at the punctuation closest to the middle, with times by word position.
  - The review page's corrections panel is the place to check corrected captions before exporting.
- **Manifests:** regenerated and copied into `export/`.

### 14.4 Model comparison test (`compare`)
- **Prerequisites:** bootstrap is complete (anchor and mascot sheet exist). The script is the sample script by default, planned normally, with extras' sheets approved first.
- **Scene choice:** 6 units are picked automatically, one per category:
  - a mascot unit
  - an extras unit
  - a night or fire unit
  - a `visual_type: metaphor` unit
  - a split part
  - the most complex unit
  - Categories are filled in that order without reusing a unit, following the §10.2 rules.
- **Runs:** each unit on FLUX.2 [klein] 4B and [klein] 9B, **with and without references**, at the project's real aspect ratio. That is 24 images, each with full QC. The cost estimate is shown first and must be confirmed. **[M0]:** FLUX.2 dev is excluded — it's unusable synchronously (HTTP 408 timeouts at both 1920×1080/25 steps and 1024×768/20 steps, ~237 s). See `docs/m0-findings.md`.
- **Report:** `projects/<compare-project>/export/compare.html`, a grid of units × runs. Each cell shows its QC result. Per run, the report shows:
  - no-text failure rate, and overall QC pass rate
  - median and 90th-percentile time per image
  - estimated cost per image
- **Your decisions:** you set `image.model`, the QC thresholds, `render.timeout_s` (about 3× the 90th-percentile time), `render.est_seconds_per_image` and `budget.weekly_usd` in `settings.yaml`.

---

## 15. Open questions to settle in M0 (update this spec with the answers)

Settled in Task 14 from the M0 live probes (Task 13). Full evidence trail — recorded
Cloudflare fixtures, generated images, neuron costs and the style verdict — is in
`docs/m0-findings.md`.

1. The FLUX.2 REST response: where the base64 image sits (`result.image`?) and in what format. — **Settled:** `result.image`, base64 **JPEG** (not PNG). No client change — the decode-by-magic-bytes / save-as-PNG plan (§9.2) already handles it. See `docs/m0-findings.md` row 1.
2. Whether `height=1080` / `width=1080` is accepted, or dimensions must be multiples of 16. **Fallback:** generate at 1088 and crop the centre to 1080. — **Settled:** not accepted exactly — `1080` is silently rounded down to `1072`; `1088` (a multiple of 16) is honored exactly. The fallback applies: `image.sizes` now defaults to `{"16:9": [1920,1088], "9:16": [1088,1920]}` (§2.3), and export centre-crops each frame to 1920×1080 / 1080×1920 (§14.2). See `docs/m0-findings.md` row 2.
3. The reference image limit: ≤512 or <512 per side, the accepted formats, and whether PNG transparency matters. — **Settled:** both 512 and 513 px accepted (no strict ≤512 rejection); the hard limit is **4** reference images — a 5th is rejected with HTTP 400, code 3030 ("provided too many input images. max=4"); max request width is 2048 (same code 3030). `image.ref_max_side` stays 512. PNG transparency wasn't exercised. See `docs/m0-findings.md` row 3.
4. Whether sending `steps` to the Klein models causes an error. Until confirmed, it isn't sent (`supports_steps: false`). — **Settled:** no error — `steps` is accepted and silently ignored (no measurable effect on time or cost). `supports_steps: false` stays unchanged. See `docs/m0-findings.md` row 4.
5. gpt-oss-120b on `/ai/v1/chat/completions`, and whether it supports `response_format`. **Fallback:** the native `/ai/run` format. — **Settled:** works on this endpoint, with and without `response_format: json_schema`; `content` is the JSON string, plus a separate `reasoning`/`reasoning_content` chain-of-thought field. The native `/ai/run` fallback is not needed. See `docs/m0-findings.md` row 5.
6. qwen3.8-27b's exact model ID, image input format, and whether it accepts multiple images. **Fallback:** a composite image (§9.4). — **Settled:** `@cf/qwen/qwen3.8-27b` is correct; it accepts **2 images per call** as `image_url` content parts, so the composite-image fallback is not needed. Its replies are not clean JSON (a leading `\n\n` or a ` ```json ` fence) — M2/M4 need the lenient JSON extraction described in §9.3/§9.4. See `docs/m0-findings.md` row 6.
7. The error bodies and codes for: 429, the free-plan daily limit, and content refusal. Also whether safety filtering returns an error or a black or blurred image. — **Settled, partially:** 401 → code 10000 "Authentication error"; 400 → code 3030 (invalid params) or code 7000 ("No route for that URI", unknown model); free-plan daily limit → **HTTP 429, code 4006**, message contains "daily free allocation of 10,000 neurons"; FLUX.2 dev timeout → **HTTP 408, code 3046** "Request timeout". **Content-refusal body is still unknown** — never triggered; safety-filter behaviour (error vs. black/blurred image) remains open. See `docs/m0-findings.md` row 7.
8. Whether a response includes a cost or neuron count. If so, record it in the ledger alongside the estimate. — **Settled:** yes — every 2xx response carries a `cf-ai-neurons` header (absent on errors). The ledger records `neurons × $0.011/1000` from that header (§9.7); the §9.6 formulas become area-based and are used only as a fallback. See `docs/m0-findings.md` row 8.
9. Real speed per model (measured again in M5). — **Settled, single samples:** Klein 4B ≈15–28 s, Klein 9B ≈3.5 s, qwen vision ≈7–19 s, LLM ≈0.5–1.8 s. **FLUX.2 dev failed (HTTP 408) at both 1920×1080/25 steps and 1024×768/20 steps (~237 s)** — unusable synchronously. `render.est_seconds_per_image` is left at its placeholder value pending the M5 remeasurement this question already calls for. See `docs/m0-findings.md` row 9.
10. **Commercial use of generated images (monetised YouTube channel). Must be settled before M5, because it may decide the model on its own.** Findings so far, as of 2026-09-22. This is not legal advice.
    - **FLUX.2 [klein] 4B:** Apache 2.0. Commercial use of outputs is clearly allowed. **Lowest risk.**
    - **FLUX.2 [klein] 9B and FLUX.2 [dev]:** FLUX Non-Commercial License. BFL's own documentation summaries say outputs *may* be used commercially, with limits such as not training competing models on them. The licence restricts *serving the model*, which is Cloudflare's responsibility as BFL's partner. I have **not** read the primary licence text; its Hugging Face link returned 404.
    - **Open issue:** each Cloudflare model page links BFL's general terms of service. Section 1.3(e) of those terms forbids commercial use of BFL's "Services" except where "expressly permitted". It's unclear whether this applies to access through Cloudflare.
    - **Action:** before M5, get written confirmation from Cloudflare (support ticket, or its service-specific terms for Workers AI partner models) that outputs of `flux-2-klein-9b` and `flux-2-dev` may be used commercially. **If that isn't confirmed, restrict `compare` and the default model to Klein 4B.**
    - **USER ACTION: still pending as of Task 14** — no support ticket opened yet. See `docs/m0-findings.md` row 10.
11. The Workers Paid plan's monthly fee. — **USER ACTION: still pending as of Task 14** — not yet checked in the dashboard. **New fact found in M0:** the Cloudflare account is currently on the **Workers AI free daily allocation** (10,000 neurons/day), not usage-based Workers Paid billing — M0's `style`/`anchor-leak` probe run hit HTTP 429/code 4006 partway through. See `docs/m0-findings.md` row 11 and plan.md §4.
12. Whether the style anchor's content leaks into scenes (§8.1). If it does, switch to the single-figure anchor. — **Not run** — blocked by the account's daily neuron limit (both the dev and Klein-9B-fallback anchor attempts got HTTP 429/code 4006 before any generation). Pending: rerun `anchor-leak` after the Workers Paid upgrade or the daily reset. See `docs/m0-findings.md` row 12.

---

## 16. Error handling summary

- **API errors:** sorted into categories and handled per §9.5. The circuit breaker counts only `transient` errors.
- **Timeouts:** recorded in the ledger as `possibly_billed`.
- **Budget:** checked before every call (§9.7).
- **Crashes:**
  - State is saved safely after every change (§5.2), and images are written safely too.
  - On start, units stuck in `generating` go back to `planned`.
  - The lock file prevents two processes working on the same project at once.
- **Invalid `plan.yaml`:** the command exits with code 1, showing the YAML path and message. Nothing is overwritten. The review page shows the errors live.
- **LLM planning failure:** handled per §6.1. Batches already cached are kept.
- **Missing ffmpeg:** export fails with the command to install it.
- **Logging:** every run writes `logs/run-*.jsonl` with one entry per API call: timestamp, category, model, parameters (prompt shortened to 300 characters, reference files by hash), time taken, HTTP status, error category, estimated cost and billing flag. The token is never logged.
- **[M3] Log entries per attempt:** each API attempt gets its own entry, including attempts that are then retried.
  - LLM entries also carry `cache_key`, `call` (the try within one validation attempt), `max_tokens`, `json_mode`, `billing`, `usd` and `request_id`.
  - Image entries carry `unit`, `seed`, the size, `refs` (`path#sha256:…`), `billing`, `usd`, `neurons` and `request_id`.
  - The token is also masked in console messages built from Cloudflare errors, and in errors kept in `state.json`.

---

## 17. Testing

- **Unit tests (no network):**
  - parsers for all three formats and bad input
  - word counting and pace, including the last-line estimate
  - fragment rules
  - the split engine, including the **golden test** (§4.7) and per-line cut timing in merged scenes (scene 013 → 66.500)
  - the stage-3 text-word filter: "medical texts" in `corrected_text` passes, and "a sign with text" in `props` fails
  - applying corrections at scene level, including one that spans a merge ("Roger E. Kirch" → "Roger Ekirch")
  - the project-selection rule (§13): with two projects modified within 24 h, `generate`, `regen` and `export` without `-p` exit with code 1
  - plan validation, lock detection, and the fingerprint (including changes that only touch comments or formatting and must not make a unit stale)
  - the prompt builder, filling of reference slots, and the prompt fixes for retries
  - cost formulas and budget maths (the week boundary, in-flight reservations)
  - safe-write behaviour with a simulated Windows file lock
  - test-unit picking, and SRT cue building for both modes
- **Pixel QC:** fixture images for clean, text, colour, filled background, shoes-and-tie (must pass), all black → `safety_filtered`, blurred → `safety_filtered`, **all white or cream → `empty` (never `safety_filtered`)**, and almost-empty (a few lines) → `empty`.
- **Planning checklist for the sample script** (milestone M2; a report, not a strict golden test). Run the real planner on the sample and report:
  - **Corrections:** how many of these 5 expected corrections were found: "90 at night" → "9 at night", "Roger E. Kirch" → "Roger Ekirch", "Thomas Ware" → "Thomas Wehr", "2 sleep" → "second sleep", "Zhuansi" → "Ju/'hoansi". Also list every **other** correction, marked as either a reasonable extra or a wrong correction.
  - **Merges:** whether lines 13 and 14 were merged, and whether any complete sentences were wrongly merged.
  - **Mascot rule:** whether the mascot appears in 0:58, 1:57, 2:04 and 2:06, and is absent from the historical scenes (0:00–0:52 and 1:01–1:54).
  - **Extras:** whether the caveman group is a single cast entry, reused across the fire scenes.
  - **Target:** at least 4 of the 5 expected corrections, no wrong corrections, the merge done, and the mascot rule followed in at least 90% of units. A miss is a prompt-tuning task, not a test failure.
- **Cloudflare client:** `httpx.MockTransport` with recorded responses (captured in M0) for success, 429, 5xx, timeout, 400, 401, refusal and the daily limit. Check that each lands in its category, the retries and backoff, the circuit breaker, and the ledger billing flags.
- **Planning:** LLM responses are replayed from saved files. Includes invalid JSON, then the retry, then the fallback path.
- **Resume:** generation is killed at random points using a fake client that fails on command, on a **synthetic 5-minute script of about 90 units** (the design limit). `resume` must finish with no duplicate versions and no lost images.
- **Review API:** FastAPI TestClient covering approve, regenerate, select-version, prompt edits with a hash mismatch (409), and the watcher ignoring the tool's own writes.
- **Export:** a 3-unit project and the synthetic 5-minute project exported to MP4. ffprobe checks the frame count, frame size and fps, and that each cut lands at the right frame, **including the full length of the last unit** (the concat list's repeated last entry, §14.2). The SRT's contents are checked against expected files.
- **Live smoke test** (`pytest -m live`, opt-in, under $0.10): one LLM call, one Klein 4B image and one vision call against the real API.

---

## 18. Acceptance criteria (v1)

1. All three input formats are read. Bad input gives an error with the line number.
2. The sample script produces exactly the §4.7 result from the split engine with fixed candidates. That means 28 scenes, 36 units, and the listed cut times to within 1 ms, including scene 013 at 66.500 (per-line timing). The last line ends at 133.852 s.
3. Changing `split_seconds`, `split_words`, `min_part_seconds` or `merge.min_scene_seconds` changes the result as specified, with no code change.
4. `stickman new` writes a valid `plan.yaml` containing corrections, cast, merge_check and every unit field in §5.1. Hand edits and comments survive any rewrite the tool makes.
5. No unit's `visual_idea`, `characters[].action`, setting, props or composition asks for text. This is enforced by the whole-word filter in stage 3, which never checks `source_text` or `corrected_text`. The sample script plans without triggering the filter. The prompt builder always includes `strict_clause`.
6. Editing `image_prompt` by hand locks it. A field change in a locked unit prints the warning. `rebuild-prompt` unlocks and rebuilds it.
7. `bootstrap` produces an approved anchor and mascot sheet, and records the mascot's seed and model in `mascot.yaml`. The stock images in `style_refs/` are never sent to any API (checked by a test on the request log).
8. Extras' sheets need approval (unless `--no-review`), are saved to the library, and are reused in a second project without being regenerated.
9. Reference slots are filled as specified in §7.4, and every reference image is at most `ref_max_side` px.
10. `generate` makes exactly 3 test units first (including a night or fire unit when one exists), then pauses. After approval, the batch reuses the approved test images.
11. Killing the process at any point, then running `resume`, finishes the batch with no lost or duplicated images and a valid `state.json`.
12. Budget: a warning at 80%. At 100% no new calls start and the state is saved, unless `--force`. Timeouts are recorded as `possibly_billed`.
13. On `account.plan: paid`, the daily-limit handling never triggers. On `free`, it stops the run with the resume message.
14. Five temporary errors in a row pause the run. Refusals and 400s never do.
15. The pixel QC fixtures give the expected result for each case, and shoes and ties pass. A uniform black or blurred image is classified as `safety_filtered` and gets a softened retry, which shows the "softened" label. A blank white or cream image is classified as `empty`, gets a new-seed retry, and is never labelled softened.
16. Each QC failure reason applies the fix listed in §7.5. Locked units never get LLM rewrites.
17. Changing a unit's visual fields, the mascot sheet, the model or the aspect ratio makes the affected units `stale`. Changing only comments or formatting doesn't.
18. The review page is reachable only on 127.0.0.1. It reloads within 2 s of `plan.yaml` being saved and shows validation errors. A prompt edit after the file changed on disk returns 409 and overwrites nothing.
19. The gallery shows flagged units first, a side-by-side view after regeneration, a history picker, "Approve all remaining", and all the keyboard shortcuts in §12.3.
20. Export stops if any unit isn't approved, unless `--allow-unapproved`. The MP4 has the exact expected frame count at 30 fps and the right frame size, and every cut is within one frame of its timestamp. `--zoom` produces smooth motion with no visible wobble.
21. The SRT follows the rules for `source` or `corrected` text in §14.3.
22. `recompose --aspect 9:16` creates a sibling project that reuses the sheets. Only `shot` and `composition` change, locked units are listed, and test units are generated again.
23. `compare` produces the report with no-text failure rate, QC pass rate, speed and cost for each model and reference setting.
24. Every run ends with the summary in §10.5, and writes a run log that never contains the API token.
25. Corrections are applied at scene level. A correction spanning a merge ("Roger E. Kirch" → "Roger Ekirch") is accepted and appears in scene 013's `corrected_text`.
26. The mascot's identity check uses head and hair only. A correct mascot image in a sleep scene (under a blanket) or a close-up, with the tie or shoes not visible, passes `mascot_matches_sheet`.
27. Every command prints the project name first. `generate`, `resume`, `regen` and `export` refuse to run without `-p` when more than one project was modified in the last 24 h.
28. The M2 planning checklist (§17) is run on the sample script and its report is saved. It reaches the target, or any misses are logged as prompt-tuning tasks.
29. A script longer than `input.max_minutes` prints the out-of-range warning and is still processed.
