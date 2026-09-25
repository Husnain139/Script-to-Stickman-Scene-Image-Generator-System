"""The image prompt, assembled by code from style.yaml, the cast and a unit's fields (spec §7.4)."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from stickman.config_files import StyleConfig
from stickman.plan.cast import CastInfo
from stickman.plan.models import MASCOT, CharacterRef, UnitDesign

MAX_CHARACTER_SLOTS = 3  # slots 1-3; slot 0 is the style anchor
DAY_LINE = "A small sun in an upper corner."
NIGHT_LINE = "A small crescent moon and a few stars; the background stays white."
NO_SETTING = "minimal, just a simple ground line"
REFERENCE_INTRO = (
    "Reference images: image 0 shows the drawing style only — match its line weight and look, "
    "not its content, and do not copy its figures."
)
REFERENCE_SLOT = (
    " Image {i} shows {name}: draw this character with exactly the same head and hair "
    "(and the same clothing when visible), but in the pose described above."
)


def reference_slots(characters: Sequence[CharacterRef], with_sheet: Collection[str]) -> list[str]:
    """Slot 1 is the mascot if present, otherwise the first extra; then the next extras in order."""
    refs = [c.ref for c in characters if c.ref in with_sheet]
    ordered = ([MASCOT] if MASCOT in refs else []) + [r for r in refs if r != MASCOT]
    slots: list[str] = []
    for ref in ordered:
        if ref not in slots:
            slots.append(ref)
    return slots[:MAX_CHARACTER_SLOTS]


@dataclass(frozen=True)
class ReferenceAvailability:
    """Which reference images exist: the style anchor, and cast ids with an approved sheet."""

    anchor: bool = False
    sheets: frozenset[str] = field(default_factory=frozenset)

    def for_unit(self, characters: Sequence[CharacterRef]) -> list[str] | None:
        """Cast ids for slots 1-3, or None: without an anchor no reference images are sent."""
        if not self.anchor:
            return None
        return reference_slots(characters, self.sheets)


def _clause(text: str) -> str:
    """Trim spaces and trailing full stops, so the template's own full stop isn't doubled."""
    return text.strip().rstrip(".").rstrip()


def _sentence(text: str) -> str:
    clause = _clause(text)
    return clause[:1].upper() + clause[1:]


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _join_words(words: Sequence[str]) -> str:
    items = list(words)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def build_prompt(
    design: UnitDesign,
    *,
    style: StyleConfig,
    cast: Mapping[str, CastInfo],
    references: Sequence[str] | None = None,
) -> str:
    lines = [style.style_text.strip(), "", f"Scene: {_sentence(design.visual_idea)}."]
    if design.characters:
        lines.append("Characters:")
        for character in design.characters:
            emotion = character.emotion.strip()
            lines.append(
                f"- {_clause(cast[character.ref].description)} — {_clause(character.action)}, "
                f"with {_article(emotion)} {emotion} expression."
            )
    else:
        lines.append("Characters: none.")
    lines.append(f"Setting: {', '.join(design.setting) if design.setting else NO_SETTING}.")
    lines.append(f"Props: {', '.join(design.props) if design.props else 'none'}.")
    if design.time_of_day == "day":
        lines.append(DAY_LINE)
    elif design.time_of_day == "night":
        lines.append(NIGHT_LINE)
    lines.append(f"Composition: {design.shot} shot. {_sentence(design.composition)}.")
    if design.energy_marks:
        lines.append(f"Add simple cartoon {_join_words(design.energy_marks)} lines for energy.")
    if references is not None:
        slots = "".join(REFERENCE_SLOT.format(i=i, name=cast[ref].name) for i, ref in enumerate(references, 1))
        lines += ["", REFERENCE_INTRO + slots]
    lines += ["", style.strict_clause.strip()]
    return "\n".join(lines)
