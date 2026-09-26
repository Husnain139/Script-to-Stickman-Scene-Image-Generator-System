"""How a retry's prompt changes for each QC reason (spec §7.5)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stickman.qc.decide import QCReason
from stickman.qc.vision import ExpectedPicture

BACKGROUND_FIX = "The entire background is plain white. Do not fill or darken the sky or the ground."
STYLE_FIX = "Pure black ink lines on pure white. Flat. No colour, no grey, no shading, not realistic."
ANATOMY_FIX = "Each character has exactly one head, two arms and two legs."
COUNT_FIX = "Exactly {figures} stick {noun} in total: {cast}."
NO_FIGURES_FIX = "No stick figures at all."
MASCOT_FIX = "The main character must have exactly the same head and hair as image 1: {identity}."
MASCOT_FIX_NO_REF = "The main character must have exactly this head and hair: {identity}."
STRICT_FIRST: frozenset[str] = frozenset({"text", "watermark"})  # strict_clause goes at the very start


@dataclass(frozen=True)
class FixContext:
    """What the fixes need to know about a unit besides its prompt."""

    strict_clause: str
    props: tuple[str, ...]
    text_free: Mapping[str, str]
    expected: ExpectedPicture
    identity: str  # the mascot's identity (head and hair), never the outfit
    mascot_in_image_1: bool  # the request's reference slot 1 is the mascot's sheet
    locked: bool  # prompt_locked: only fixed clauses, never a props rewrite


def rewrite_props(props: Sequence[str], text_free: Mapping[str, str]) -> list[str]:
    """Each prop with the text-free wording of the first table key it contains as a whole word."""
    rewritten = []
    for prop in props:
        wording = next(
            (words for key, words in text_free.items() if re.search(rf"\b{re.escape(key)}(?:e?s)?\b", prop, re.IGNORECASE)),
            None,
        )
        if wording is None or wording.lower() in prop.lower():
            rewritten.append(prop)
        else:
            rewritten.append(f"{prop} ({wording})")
    return rewritten


def _props_line(prompt: str, fix: FixContext) -> str:
    """The prompt builder's `Props:` line (spec §7.4) with text-attracting props rewritten."""
    if not fix.props:
        return prompt
    old = f"Props: {', '.join(fix.props)}."
    new = f"Props: {', '.join(rewrite_props(fix.props, fix.text_free))}."
    return prompt.replace(old, new, 1) if old in prompt else prompt


def _sentence(reason: str, fix: FixContext) -> str | None:
    if reason == "background_filled":
        return BACKGROUND_FIX
    if reason == "style":
        return STYLE_FIX
    if reason == "anatomy":
        return ANATOMY_FIX
    if reason == "character_count":
        if fix.expected.figures == 0:
            return NO_FIGURES_FIX
        noun = "figure" if fix.expected.figures == 1 else "figures"
        return COUNT_FIX.format(figures=fix.expected.figures, noun=noun, cast=fix.expected.cast)
    if reason == "mascot_mismatch":
        return (MASCOT_FIX if fix.mascot_in_image_1 else MASCOT_FIX_NO_REF).format(identity=fix.identity)
    return None  # empty, weak_idea and safety_filtered: a new seed or an LLM rewrite, no prompt change


def retry_prompt(prompt: str, reasons: Sequence[QCReason], fix: FixContext) -> str:
    """The prompt with the fix for every reason fixed so far in the chain, each once, in first-seen order."""
    seen = list(dict.fromkeys(reasons))
    body = prompt.strip()
    if "text" in seen and not fix.locked:
        body = _props_line(body, fix)
    sentences = [s for s in (_sentence(reason, fix) for reason in seen) if s is not None]
    parts = [fix.strict_clause.strip()] if STRICT_FIRST.intersection(seen) else []
    parts.append(body)
    if sentences:
        parts.append(" ".join(sentences))
    return "\n\n".join(parts)
