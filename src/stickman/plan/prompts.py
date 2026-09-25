"""Every LLM prompt text in one place (spec §6.2–6.4). Prompt tuning edits only this file."""

from __future__ import annotations

MASCOT_RULE = (
    'MASCOT RULE: the mascot appears only in lines addressed to "you" or about general, present-day '
    "human experience. Historical, scientific or third-person scenes use cast characters instead."
)

_ANALYSE_SYSTEM = """You plan illustrations for a stickman explainer video. You receive a narration script produced
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

{mascot_rule}"""


def analyse_system(max_lines: int) -> str:
    return _ANALYSE_SYSTEM.format(max_lines=max_lines, mascot_rule=MASCOT_RULE)


CUT_SYSTEM = """Each scene below is too long for one picture and will be shown as two pictures. Its words are
numbered. Propose up to 3 cut points, best first. "k" means: cut after word k. Cut only at a
natural idea boundary (clause, list item, "but/and then/so"), so that each half can be drawn as
its own clear picture. Return ONLY JSON matching the schema."""

DESCRIBE_SYSTEM = """You design one illustration per unit for a stickman explainer video. Style is fixed and handled
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
- visual_idea: one sentence a human can scan quickly."""
