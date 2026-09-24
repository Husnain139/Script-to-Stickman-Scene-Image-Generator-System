# M2 planning checklist (spec §17)

Sample script: `tests/fixtures/scripts/first-sleep.txt`. Planner: `@cf/openai/gpt-oss-120b`. LLM cost: this report re-scored the cached plan (0 neurons). The plan itself came from the live run `m2_out/logs/run-20260924-063915.jsonl`: 2,733 neurons (about $0.030), inside the free daily allocation.

**Target met: yes.** The target is at least 4 of 5 expected corrections, lines 13 and 14 merged, and the mascot rule followed in at least 90% of units. Other corrections and merges the fragment rules didn't flag are judged by hand below.

## Expected corrections: 5 of 5

| Expected | Found | Result |
|---|---|---|
| "90 at night" → "9 at night" | scene 001: "90" → "9" | found |
| "Roger E. Kirch" → "Roger Ekirch" | scene 013: "Roger E. Kirch" → "Roger Ekirch" | found |
| "Thomas Ware" → "Thomas Wehr" | scene 023: "Thomas Ware" → "Thomas Wehr" | found |
| "2 sleep" → "second sleep" | scene 014: "2" → "second" | found |
| "Zhuansi" → "Ju/'hoansi" | scene 006: "Zhuansi" → "Ju/'hoansi" | found |

## Other corrections

None.

## Merges

- Lines 13 and 14 merged: yes
- Merges the fragment rules didn't flag (judge by hand): 024 (lines 25, 26)

## Mascot rule: 94% of 35 units

- Units breaking it: 009, 011

## Extras

- Prehistoric-people cast entries: early_humans
- Units using them: 001, 002, 003, 004, 005, 014, 015a, 015b, 018, 019, 020, 021, 022

## Prompt-tuning tasks

- Mascot rule: the mascot is wrong in 2 units (009, 011).

## Hand judgement (controller, 2026-09-24)

- **Other corrections:** none, so there are no wrong corrections.
- **Merge 024 (lines 25 + 26): reasonable.** Line 26, "A hormone associated with deep calm.", is a fragment that continues line 25. No complete sentences were merged.
- **Mascot rule, 2 misses (still above the 90% target):**
  - 011 (0:52, "…here's the part that should mess with you.") speaks to "you", so the mascot there is defensible.
  - 009 (0:43, "Night is where human imagination got its training ground.") is general human experience, so it's borderline.
- **Extras: fine.** `early_humans` is one cast entry, used across the fire scenes 001–005 and the other prehistoric scenes.

## How the target was reached

1. **First live run: planning stopped.** gpt-oss failed the analyse stage twice. One reply was probably cut off at the token limit, and in the other the model confused group positions with line numbers. The Llama fallback then answered with no cast, which made describe fail.
2. **Task 13a:** cut-off replies are now logged and explained to the model, the token budget went up to 16k, and corrections now name a line instead of a group position.
3. **Tuning round 1** (`prompts.py`, generic guidance only):
   - numbers stay numbers;
   - names keep their exact spelling;
   - groups the pictures need go in the cast, even when a line only implies them.
4. **Second live run:** the whole plan came from gpt-oss. The two describe hiccups were fixed by the automatic retry.
5. **Task 13b:** the checklist now scores corrections by the scene's corrected text (the model's `from` text can be shorter than the expected phrase) and leaves merges to a person, as spec §17 says.
