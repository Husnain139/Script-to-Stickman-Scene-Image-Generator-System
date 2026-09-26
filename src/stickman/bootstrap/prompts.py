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
