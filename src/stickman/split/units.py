"""Units: one image each, a whole scene or one part of a split scene (spec §3, §4.6)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from stickman.split.engine import SplitDecision
from stickman.split.scenes import Scene

Part = Literal["1 of 2", "2 of 2"]


def unit_filename(unit_id: str, start: float) -> str:
    """`<unit>_<MM-SS.s>.png`, start rounded to 0.1 s."""
    tenths = round(start * 10)
    minutes, remainder = divmod(tenths, 600)
    return f"{unit_id}_{minutes:02d}-{remainder / 10:04.1f}.png"


@dataclass(frozen=True)
class Unit:
    id: str
    scene_number: int
    part: Part | None
    start: float
    end: float
    source_text: str

    @property
    def filename(self) -> str:
        return unit_filename(self.id, self.start)


def build_units(scenes: Sequence[Scene], decisions: Mapping[int, SplitDecision]) -> list[Unit]:
    units: list[Unit] = []
    for scene in scenes:
        decision = decisions.get(scene.number, SplitDecision("none"))
        base = f"{scene.number:03d}"
        if decision.status == "split" and decision.cut_after_word is not None and decision.cut_time is not None:
            words = scene.tokens
            k = decision.cut_after_word
            units.append(Unit(f"{base}a", scene.number, "1 of 2", scene.start, decision.cut_time, " ".join(words[:k])))
            units.append(Unit(f"{base}b", scene.number, "2 of 2", decision.cut_time, scene.end, " ".join(words[k:])))
        else:
            units.append(Unit(base, scene.number, None, scene.start, scene.end, scene.source_text))
    return units
