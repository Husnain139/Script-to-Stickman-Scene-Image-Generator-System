# M0 findings — 2026-09-23

Evidence: `tests/fixtures/cf/*.json` (recorded Cloudflare responses, Task 13) and
`m0_out/style/*.png` (generated images, Task 13 addendum). Full per-call detail is
in `.superpowers/sdd/2026-09-22-m0-m1-foundation/task-13-report.md`.

| # | Question (spec §15) | Answer | Evidence | Change made |
|---|---|---|---|---|
| 1 | FLUX.2 response shape and image format | `result.image` base64, **JPEG** (not PNG) | tests/fixtures/cf/image_klein4b_1920x1080.json | none — client already decodes by magic bytes and saves as PNG |
| 2 | 1080 accepted? | No — height/width `1080` is **silently rounded down to 1072** (nearest multiple of 16); `1088` is honored **exactly** | image_klein4b_1920x1080.json (→1920x1072), image_klein4b_1920x1088.json (→1920x1088 exact) | `image.sizes` → `"16:9": [1920, 1088]`, `"9:16": [1088, 1920]`; export centre-crops to 1920×1080 / 1080×1920 (spec §14.2) |
| 3 | Reference size limit (512 vs 513), max count (4 vs 5) | Both 512 and 513 px accepted (no strict ≤512 rejection); hard max is **4** reference images — a 5th is rejected with HTTP 400, code 3030, "provided too many input images. max=4"; max request **width is 2048** (also code 3030) | refs_one_ref_512.json, refs_one_ref_513.json, refs_four_refs_512.json, refs_five_refs_512.json, errors_width_too_big.json | none — `image.ref_max_side` stays 512 |
| 4 | `steps` on Klein | Accepted, **silently ignored** — no error, no measurable change in time or neuron cost | image_klein4b_with_steps.json | none — `supports_steps: false` stays |
| 5 | gpt-oss on `/ai/v1/chat/completions`, `response_format` | Works with and without `response_format: json_schema`; `content` is a JSON string; extra `reasoning`/`reasoning_content` fields hold chain-of-thought (not JSON). Native `/ai/run` wraps the same shape one level deeper under `result` | llm_planner_chat.json, llm_planner_chat_schema.json, llm_planner_native.json | none — client already reads `choices[0].message.content` |
| 6 | qwen ID, image format, multiple images | `@cf/qwen/qwen3.8-27b` is correct; **2 images per call** accepted as `image_url` content parts — composite fallback **not needed**. Output is **not clean JSON** (leading `\n\n`, or wrapped in a ` ```json ` fence) | ids_qwen.json, vision_one_image.json, vision_two_images.json | `llm.vision_model` unchanged; composite fallback not implemented; noted in spec §9.3/§9.4 that M2/M4 need a lenient JSON extractor (not implemented in this task) |
| 7 | Error bodies (401, 400; 429/daily/refusal pending) | 401 → code 10000 "Authentication error". 400 → code 3030 (invalid params: width, ref count) or code 7000 "No route for that URI" (unknown model). **Free-plan daily limit → HTTP 429, code 4006**, message contains "daily free allocation of 10,000 neurons". **FLUX.2 dev timeout → HTTP 408, code 3046** "Request timeout". **Content-refusal body: still unknown** (never triggered) | errors_bad_token.json, errors_width_too_big.json, errors_unknown_model.json, style_dev_clock_3am.json, style_dev_old_firmware.json, anchor_leak_anchor_two_figures.json, anchor_leak_anchor_two_figures_klein9b.json (all 429/4006), image_dev_1920x1080.json, style_dev_fire_night.json (both 408/3046) | See "Error-marker fix" below — `errors.py` `DAILY_LIMIT_MARKERS` extended; `REFUSAL_MARKERS` unchanged (still no recorded refusal body) |
| 8 | Cost/neuron count in responses? | **Yes** — every 2xx response carries a `cf-ai-neurons` header, absent on errors. Observed: Klein 4B 1920×1072/1088 ≈ 206–208 neurons (≈$0.0023); Klein 9B 1920×1072 ≈ 1541 neurons (≈$0.017, ~7.5× Klein 4B); qwen vision 67–107 neurons; LLM (gpt-oss/llama) 4–10 neurons. These fit an **area-based** tile/MP formula (not the old per-side-ceiling one) | headers in image_*.json, style_*.json, llm_*.json, vision_*.json | Ledger records `neurons × $0.011/1000` from the header on every 2xx (spec §9.7); §9.6 formulas become area-based (`tiles = w·h/262144`, `MP = w·h/1048576`) and are used only for pre-call estimates / calls with no header; `pricing.yaml` comment updated |
| 9 | Latency per model (single sample) | Klein 4B ≈15–28 s, Klein 9B ≈3.5 s, qwen vision ≈7–19 s, LLM ≈0.5–1.8 s. **FLUX.2 dev failed (HTTP 408) at both 1920×1080/25 steps and 1024×768/20 steps, after ~237 s** — unusable synchronously | `elapsed_s` across image_*.json, style_*.json, vision_*.json, llm_*.json | none — `render.est_seconds_per_image` (10 s) is explicitly deferred to M5 remeasurement per spec §15 #9; recorded here for that remeasurement |
| 10 | Commercial use (Cloudflare written confirmation for Klein 9B and dev) | **USER ACTION: pending** — no support ticket opened yet | — | none yet; if unconfirmed by M5, restrict `compare` and the default model to Klein 4B per spec §15 #10 |
| 11 | Workers Paid monthly fee | **USER ACTION: pending** — not yet checked in the dashboard. **New fact found:** the Cloudflare account is currently on the **Workers AI free daily allocation** (10,000 neurons/day), not usage-based Workers Paid billing — M0's `style`/`anchor-leak` run hit HTTP 429/4006 partway through (~10,650 neurons used) | task-13-report.md, "Important new finding" section | plan.md §4 cost table now states Workers Paid is required; the fee itself is still open |
| 12 | Anchor leak (count figures in m0_out/anchor_leak/single_*.png) | **NOT RUN** — blocked by the daily limit. Both the primary (dev) and fallback (Klein 9B) anchor attempts were rejected with HTTP 429/4006 before any generation; `m0_out/anchor_leak/` does not exist | anchor_leak_anchor_two_figures.json, anchor_leak_anchor_two_figures_klein9b.json | none — rerun `anchor-leak` after upgrading to Workers Paid, or after the daily reset |

## Error-marker fix (Task 14, TDD)

`tests/cf/test_recorded_errors.py` reuses the brief's two tests (`errors_bad_token.json` →
AUTH, `errors_width_too_big.json` → BAD_REQUEST) plus five more built from the recorded
fixtures. Six of the seven passed immediately with no code change:
`errors_bad_token` → AUTH, `errors_width_too_big` → BAD_REQUEST,
`style_dev_clock_3am` on `plan="paid"` → RATE_LIMITED, `image_dev_1920x1080` (408) →
TRANSIENT, `errors_unknown_model` → BAD_REQUEST, `refs_five_refs_512` → BAD_REQUEST.

One failed as written: `style_dev_clock_3am` (429, code 4006) on `plan="free"` returned
`RATE_LIMITED` instead of the expected `DAILY_LIMIT`. Cause: `scripts/m0_probe.py`'s own
fixture recorder truncates long error messages at 60 characters
(`value[:60] + f"...<{len(value)} chars>"`), which cuts the real Cloudflare message
*mid-word*, just before "allocation" finishes — the fixture body reads
`"...your daily free allocatio...<205 chars>"`, one letter short of the
`"daily free allocation"` string that `DAILY_LIMIT_MARKERS` checked for. This is an
artifact of the M0 probe's own recording, not of Cloudflare (the live response is not
truncated). Fix applied: added the shorter, still-specific substring `"daily free"` to
`DAILY_LIMIT_MARKERS` in `src/stickman/cf/errors.py`, which matches both the truncated
fixture and the real untruncated message. The recorded body (truncated form) was also
added as two new parametrized cases in `tests/cf/test_errors.py`
(`plan="free"` → DAILY_LIMIT, `plan="paid"` → RATE_LIMITED). All 7 tests in
`test_recorded_errors.py` and all cases in `test_errors.py` pass after the fix.

`REFUSAL_MARKERS` is unchanged — no content-refusal body was ever recorded (spec §15 #7
stays open on that point).

## Style feasibility (m0_out/style)

| Model | fire_night | clock_3am | old_firmware | Any text? | Verdict |
|---|---|---|---|---|---|
| klein4b | Clean line art; night via moon+stars, fire lines good. Bodies are chunky outlined shapes, not thin stick limbs; the middle figure's body merges into the flames. | Good (no numerals, blanket, moon) but the body is drawn as a big blob. | Good metaphor; mascot hair reduced to 1 stroke. | No | Usable, but needs stronger thin-limb wording |
| klein9b | Clean, expressive faces, but **two moons** (duplicate); outlined chunky bodies. | Very good — tick marks, no numerals, moon in window, blanket. | Excellent — closest to the reference style: thin limbs, mitten hands, oval feet, ground shadow, surprise marks. | No | Best match; use as default |
| dev | No images (408 timeout) | No images (408 timeout) | No images (408 timeout) | — | Unusable synchronously |

Both Klein models: the "exactly three hair strokes" instruction was ignored in every
image, and thin stick bodies were not held in multi-figure or lying-down scenes — needs
the anchor and character sheets as references plus stronger "single-line body and
limbs" wording. QC should also catch duplicate moons (klein9b/fire_night).

## Same-seed reproducibility

`klein4b_same_seed_repeat` (same prompt, size, `seed=1` as `klein4b_1920x1080`): HTTP
200, 15.5 s, 206.06 neurons, returned 1920x1072 — same as the original call. Pixel
comparison against `klein4b_1920x1080.png`: **same seed reproduces identical pixels:
False.** Same seed does **not** guarantee bit-identical output; anything relying on
seed-based reproducibility does not hold in practice.

## Cost outlook

Per 4-minute video (≈90 generations) at 1920×1088 with ~2–3 reference images:
- **Klein 9B** ≈ $0.0185/image → ≈ $1.67 for images + ≈ $0.15 vision/LLM ≈ **$1.8/video ≈
  $12.7/week** (≈ $12 net of the free 10,000 neurons/day) — **inside the $15/week
  budget**.
- **Klein 4B** ≈ $0.0025/image → ≈ **$0.4/video ≈ $2.8/week**.
- **FLUX.2 dev**: unusable synchronously (see row 9) — not a viable default.

Both figures **require Workers Paid** (usage-based billing enabled) — the free plan
stops at 10,000 neurons/day, about 6 Klein 9B images, which is what M0's `style`/
`anchor-leak` run hit (row 11).

## Decisions

- **M0 exit: style is achievable — yes.** Klein 9B is the best match (especially
  `old_firmware`); Klein 4B is also usable but chunkier. FLUX.2 dev is unusable
  synchronously and is dropped from the M5 `compare` model list (spec §14.4) and from
  `bootstrap.model` (now Klein 9B).
- Rows 10 (commercial-use confirmation) and 11 (Workers Paid fee) remain **user
  actions**, still pending as of this task. Row 12 (anchor leak) is **not run**,
  blocked by the account's daily neuron limit; rerun after the Workers Paid upgrade or
  the daily reset, per the recommendation in `task-13-report.md`.
