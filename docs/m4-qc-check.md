# M4 live QC check (spec §11, plan.md M4 Task 11)

Run on 2026-09-26 (local, UTC−7), against the real Cloudflare API: Workers Free, one account.
- Image model: `@cf/black-forest-labs/flux-2-klein-4b`, 1920×1088, no reference images.
- Vision model: `@cf/qwen/qwen3.8-27b`, with `qc.vision` on and `qc.min_idea_score: 3`. No mascot reference copy yet, so `mascot_matches_sheet` is not compared.
- Project: `projects/2026-09-25_first-sleep/`, the M3 sample. M3 made 8 of its images (001–007) before QC existed.

**Result:**
- QC runs end to end. Every image got a pixel check, and every image that passed the pixel check got a vision check.
- Every failure led to a retry, up to the chain's limit, and the best version went to review.
- Every call is in the ledger at its measured cost. No secret appears in any file the run wrote.
- **But the run cost 4.7× its estimate, because every one of the 10 units failed its first check.** About 9 of the 22 failures were false positives. Most came from one cause: the expected figure count (see Findings).

Total: 48 calls, 11,847.83 neurons (≈ $0.130). With this run, today's (UTC) usage in our ledger is over the free 10,000. Cloudflare returned no daily-limit error (see Findings).

## What was run

```bash
uv run pytest -m live -s 2>&1 | tail -20
uv run stickman generate -p 2026-09-25_first-sleep --limit 10 2>&1 | tail -30
```

The numbers below come from a read-only script. It read `ledger.jsonl`, the run log `logs/run-20260926-002945.jsonl` and `state.json`. The secret check loaded the secrets through `stickman.settings.load_secrets(Path('.env'))` inside the script, and printed only True/False.

## Smoke tests

`uv run pytest -m live -s`: **2 passed** in 19.7 s.
- **Image:** 207.59 neurons, and the request id was set. This is the same as M3.
- **Vision** (`tests/fixtures/qc/klein4b_clock.png`, 1 expected figure): 98.81 neurons.
  - It used 1,050 input and 192 output tokens.
  - The report parsed with no errors: `character_count=1`, `matches_visual_idea=5`, and no text, watermark, style or anatomy problem.
  - Arithmetic check: (1,050 × 0.45 + 192 × 3.20) / 1e6 = $0.0010869, which is 98.81 neurons at $0.011 per 1,000. That matches the `cf-ai-neurons` header.

## The generate run

The run-start message printed:

```
Generating 2 unit(s) on @cf/black-forest-labs/flux-2-klein-4b, each checked by
@cf/qwen/qwen3.8-27b ≈ $0.0269 (≈ 2,443 neurons; retries not included).
Checking 8 image(s) made before QC existed. Each is checked, not made again,
unless it fails its check; then it is retried like any other.
Free plan: about 306 of 10,000 neurons used today (UTC), so about 39 more
unit(s) fit (image and check) before the reset at 17:00 local time.
```

The run finished with exit code 0 after 7 min 40 s:

```
Run finished · 35 units · done 6 · needs_review 4 · failed 0 · stale 0 · skipped 25
Cost this run ≈ $0.13 (≈ 11,541 neurons) · week ≈ $0.15 / $15.00 · time 7m40s
Needs review: 005 (character_count)   006b (style)   008a (character_count)   008b (character_count)
```

- **Calls:** 20 images (2 new v1s and 18 retries), and 26 vision checks. That is 28 versions minus the 2 that failed the pixel check, which skips the vision check.
- **Vision call health:** every vision call succeeded on its first attempt, with `finish_reason: stop` and no parse errors. No call was unreachable, and no call was softened.
- **Leftover files:** there are no `.tmp` or `.lock` files. `images/_history/` holds 28 PNGs, matching the 28 versions in `state.json`.

### Neurons per call: measured against the estimate

| | Estimate | Measured |
|---|---|---|
| Image | 207.59 | 207.59 (all 20) |
| Vision check, neurons | ≈ 203 (`TYPICAL_TOKENS` = 1,400 in / 500 out) | min 105.90 · **mean 284.22** · max 553.90 (n = 26) |
| Vision input tokens | 1,400 | min 1,239 · mean 1,248.8 · max 1,269 |
| Vision output tokens | 500 | min 187 · mean 801.4 · max 1,727 |
| Vision latency | – | min 7.5 s · mean 38.0 s · max 107.6 s |
| Image latency | – | min 19.9 s · mean 28.1 s · max 57.8 s |
| This run | ≈ 2,443 neurons (retries not included) | 11,541.43 neurons (4,151.80 for images, 7,389.63 for checks) |

- The input side is stable: the prompt plus one 1024 px image comes to about 1,250 tokens.
- The output side varies 9-fold, from 187 to 1,727 tokens. Qwen's reasoning is billed as output, and output costs 7× as much per token as input. So output dominates the cost of a check.
- The longest reply, 1,727 tokens, was close to `VISION_MAX_TOKENS` (2,048).

### Correcting the typical-check estimate (Step 4)

`TYPICAL_TOKENS` is now **(1300, 900)**: the measured means, 1,248.8 and 801.4, each rounded up to the next 100.

- **A typical check:** (1,300 × 0.45 + 900 × 3.20) / 1e6 = $0.003465. At $0.011 per 1,000 neurons, that is **315.0 neurons**. The old value was (1,400 × 0.45 + 500 × 3.20) / 1e6 = $0.00223, or 202.7 neurons.
- **The fit number** in `test_generate_makes_every_units_image_and_prints_the_summary`: 10,000 / (207.9 + 315.0) = 10,000 / 522.9 = 19.12, rounded down to **19**. It was 24.

### Free-plan daily throughput (10,000 neurons)

| | Neurons per unit | Units per day |
|---|---|---|
| `qc.vision` off (pixel checks only) | 207.59 | 48 |
| `qc.vision` on, passing first time, at the measured mean | 207.59 + 284.22 = 491.8 | 20 |
| `qc.vision` on, as the run-start message now estimates | 207.9 + 315.0 = 522.9 | 19 |
| `qc.vision` on, **at this run's retry rate**, all units made fresh | (11,541 + 8 × 207.59) / 10 = 1,320 | **7** |

The last row counts the 8 v1 images that M3 had already paid for. At this run's false-positive rate, QC cuts the free plan from 48 units a day to about 7. Most of that is the retries, not the checks.

## Per unit

"exp" is the expected figure count that QC used, and "seen" is the vision model's `character_count`. Each unit's `current` is the version its chain chose as the best.

| Unit | v | Pixel | Vision (seen / exp, idea score, notes) | QC | Retry reason |
|---|---|---|---|---|---|
| 001 | 1 | ok | 1 / 3, idea 2: "only one early human … instead of three" | fail: character_count | – |
| | 2 | ok | 3 / 3, idea 5 | **pass** (current) | character_count |
| 002 | 1 | ok | 1 / 3, idea 3 | fail: character_count | – |
| | 2 | ok | 3 / 3, idea 5 | **pass** (current) | character_count |
| 003 | 1 | ok | 4 / 3, idea 4, "grey shading for the predator shadow" | fail: style | – |
| | 2 | ok | 3 / 3, idea 5, "grey-filled predator shadow" | fail: style | style |
| | 3 | ok (blob 0.0387) | 3 / 3, idea 4 | **pass** (current) | style |
| 004 | 1 | ok | 1 / 3, idea 3 | fail: character_count | – |
| | 2 | ok | 3 / 3, idea 4, "torch flame coloured orange/yellow" | fail: style | character_count |
| | 3 | ok | 3 / 3, idea 5 | **pass** (current) | style |
| 005 | 1 | ok | 4 / 3, idea 2, "grey shadow under the fire; moons instead of a rising sun" | fail: style | – |
| | 2 | ok | 4 / 3, idea 2 | fail: character_count (current) | style |
| | 3 | **background_filled** (blob 0.0436) | not run | fail: background_filled | character_count |
| 006a | 1 | ok | 3 / 5, idea 3, "only one Ju/'hoansi" | fail: character_count | – |
| | 2 | **background_filled** (blob 0.0495) | not run | fail: background_filled | character_count |
| | 3 | ok | 6 / 5, idea 3, "three anthropologists instead of two" | **pass** (current) | background_filled |
| 006b | 1 | ok | 4 / 2, idea 4, "grey ground shadows and a faint yellow glow" | fail: style (current) | – |
| | 2 | ok | 4 / 2, idea 3, "pink-tinted fire logs, grey ground shadows" | fail: style | style |
| | 3 | ok | 5 / 2, idea 3 | fail: character_count | style |
| 007 | 1 | ok | 1 / 3, idea 5, "light grey ground shadow" | fail: style | – |
| | 2 | ok | 1 / 3, idea 2, "a pencil instead of a spear" | fail: character_count | style |
| | 3 | ok | 3 / 3, idea 5 | **pass** (current) | character_count |
| 008a (new) | 1 | ok | 2 / 9, idea 2, "no spirit silhouettes" | fail: character_count | – |
| | 2 | ok | 5 / 9, idea 3 | fail: character_count (current) | character_count |
| | 3 | ok | 4 / 9, idea 2 | fail: character_count | character_count |
| 008b (new) | 1 | ok | 1 / 3, idea 1, "no ancestral wisps" | fail: character_count | – |
| | 2 | ok | 4 / 3, idea 1 | fail: character_count | character_count |
| | 3 | ok | 4 / 3, idea 1 | fail: character_count (current) | character_count |

**Outcomes by unit:**
- Passed first time: **0 of 10**.
- Passed after 1 retry: 2 (001 and 002).
- Passed after 2 retries: 4 (003, 004, 006a and 007).
- `needs_review` after 2 retries: 4 (005, 006b, 008a and 008b).
- `failed`: 0.

**Outcomes by version:** 28 versions, of which 6 passed and 22 failed. The failures were character_count 13, style 7 and background_filled 2.

## False positives (each failing image looked at by eye)

I read every failing image in `images/_history/` as a labelled contact strip, one per unit.

**Character count: 7 false positives.** All 7 come from the expected count, not from the vision model's counting.
- `JobBuilder.expected_picture` sums the cast's `figures` over each character entry in the unit. A group cast member counts as its full size every time it is referenced: `early_humans` and `ju_hoansi` are 3 each.
- **Singular visual idea, group cast member:**
  - 001 v1, 002 v1 and 004 v1 each show exactly the one early human that the visual idea asks for ("An early human stands…"). They were expected to show 3.
  - 007 v2 shows the one Ju/'hoansi figure its visual idea asks for, and was expected to show 3.
  - 006a v1 shows 2 anthropologists and "a Ju/'hoansi", exactly as its visual idea says, and was expected to show 5.
  - 008b v1 shows the single figure that the composition asks for ("a single figure silhouetted"), and was expected to show 3. Its idea score of 1 is a real miss, though: there are no ancestral wisps.
- **The cast contradicts the visual idea:** 006b's cast is only `anthropologists` (2), but its visual idea is "the same Ju/'hoansi group". So 006b v3's 5 figures fit the idea, and it failed against an expected 2.
- **Repeated references:** 008a references `ju_hoansi` three times, for the "three Ju/'hoansi" of its visual idea, so it expected 9 figures, which is unreachable.
  - Against the intended 3, its versions (2, 4 and 4 figures) would still fail. So those failures are real, but no retry could ever have passed.
- **The fix makes it worse:** the character-count fix tells the image model to draw the expected count, which pushes the images away from their visual idea.
  - 001 v2, 004 v3 and 007 v3 now show 3 people where the idea says one, and they passed.
  - 008b v2 and v3 show 4 people where the composition says one.

**Pixel background_filled: 2 false positives.** 005 v3 and 006a v2 are clean line art on white. Their white fraction is 0.92 and 0.90 respectively.
- The largest connected black area, 0.0436 and 0.0495 of the image, is the line art itself: the figures, the fire and the ground line are joined into one component. It is not a filled area.
- 003 v3 passed at 0.0387, just under the limit of 0.04.

**Style: 1 borderline false positive.** 007 v1 failed only for "a light grey ground shadow".
- The style prompt itself asks for "a light hatched shadow under the feet", and Klein draws it as soft grey.
- Other passing versions have the same kind of shadow, for example 004 v3.

**Style failures that were real:**
- 003 v1 and v2 have a large grey-filled predator shadow.
- 004 v2 has an orange and yellow flame.
- 006b v2 has pink logs.
- 005 v1 and 006b v1 are minor but real: a grey smudge under the fire, and a faint yellow glow.

**Character-count failures that were real:**
- 005 v2 shows 4 figures against 3.
- 008a v1–v3 and 008b v2–v3 are wrong against the intended count too.

**Misses the other way (false negatives, noted but not counted):**
- 003 v3 passed with a count of 3, but I see 4 faces.
- 005 v1 has 5 figures, and the vision model counted 4.
- 008a's smoke is grey, but it got `style_ok: true`.
- 006a v3 passed with 6 figures against 5, within the ±1 tolerance, although it draws 3 anthropologists instead of 2.
- 002 v1 has two moons, and the check didn't mention them.

**In total, about 9 or 10 of the 22 failures were false positives:** 7 from the figure count, 2 from the pixel check and 1 borderline style failure. 8 of the 18 retries followed a false positive (001, 002, 004, 006a twice, 007 twice, and 008b). 008a's 2 retries chased an expected count of 9, which no image could reach.

## Secrets

The script checked 41 files for the real API token and account id, comparing bytes and printing only True/False:
- `ledger.jsonl`, `state.json`, the new run log, and every PNG in `images/` and `images/_history/`.
- Every file printed **False**: no secret appears in any file the run wrote.

The literal words `tok` and `acc` are the fake secrets used by the offline tests, and they weren't searched for. Words like "tokens" in the log would match them anyway.

## Surprises

- **Cloudflare didn't stop the run at 10,000 neurons.** Our ledger has 11,847.83 neurons for this UTC day (the smoke tests, then this run). Every call was billed and succeeded, and no daily-limit error came back.
  - The run pauses only on Cloudflare's error, so it didn't pause.
  - Either Cloudflare's own count differs from the `cf-ai-neurons` headers, or its enforcement lags. Check today's figure on the Cloudflare dashboard.
- **The estimate excludes retries**, and the message says so. But at a 100% first-check failure rate, the real cost was 4.7× the estimate.
- **The vision check takes as long as the image:** 38 s on average, and up to 108 s. That is why 10 units took 7 min 40 s.
- **The progress bar flooded the redirected output again** (`FORCE_COLOR=3`, as in M3): 341 KB of frames.

## Follow-ups for M5 (threshold tuning)

1. **Expected figure count** (the biggest cost). Don't multiply a group cast member by its size on every reference, and don't expect a group's full size when the visual idea names one member. Options:
   - count each distinct `ref` once;
   - let the planner state a per-unit figure count;
   - treat a group as a range, from 1 to its size.

   Then the count fix will stop steering images away from the visual idea.
2. **`max_black_blob_fraction: 0.04`** fails clean line art whose strokes connect into one component. Tune it on the comparison images, or measure only filled black: for example, erode first, or ignore thin components.
3. **The ground shadow.** Either tell the vision prompt that the light ground shadow is allowed, or drop it from the style prompt. At the moment it fails some images and passes others.
4. **`qc.min_idea_score: 3`** never became the first reason, because an earlier failure always fired first. 008b scored 1 on all 3 versions, though, so it would have fired. Check it on the comparison images.
5. **The vision model's own counting** is off by one on busy group scenes (003 v3, 005 v1). With 3 or fewer expected figures the rule is exact, so the ±1 tolerance doesn't absorb that. Decide whether small counts need a tolerance too.
6. **Vision output length** varies from 187 to 1,727 tokens, which is close to the 2,048 cap. If qwen's reasoning can be shortened, the check gets cheaper and faster, and there is less risk of a cut-off.
7. **Whether `qc.vision` stays on for the free plan:** at this run's rate it fits about 7 units a day, against 48 with pixel checks only. Re-measure after items 1–3.
8. **The daily limit.** On the free plan, consider pausing from our own ledger before reaching 10,000, rather than relying on Cloudflare's error, which didn't come.
