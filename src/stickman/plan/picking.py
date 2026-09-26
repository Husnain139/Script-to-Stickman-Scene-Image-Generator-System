"""Choosing units: the model comparison's six (spec §14.4), by the rules the test units use (spec §10.2).
M7 adds the test-unit picking here."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from stickman.plan.models import MASCOT, Plan, PlanUnit

COMPARE_CATEGORIES: tuple[str, ...] = ("mascot", "extras", "night_or_fire", "metaphor", "split_part", "most_complex")


def complexity(unit: PlanUnit, figures: Mapping[str, int]) -> int:
    """spec §10.2: 2×Σfigures + len(props) + len(setting) + len(energy_marks)."""
    drawn = sum(figures.get(character.ref, 1) for character in unit.characters)
    return 2 * drawn + len(unit.props) + len(unit.setting) + len(unit.energy_marks)


def night_or_fire(unit: PlanUnit) -> bool:
    words = " ".join([*unit.props, *unit.setting]).lower()
    return unit.time_of_day == "night" or "fire" in words or "flame" in words


@dataclass(frozen=True)
class Pick:
    category: str
    unit_id: str
    filled: bool = False  # nothing matched the category; the next most complex unit stands in


def pick_compare_units(plan: Plan, figures: Mapping[str, int]) -> list[Pick]:
    units = plan.units()
    position = {unit.id: index for index, unit in enumerate(units)}
    by_complexity = sorted(units, key=lambda unit: (-complexity(unit, figures), position[unit.id]))
    picked: set[str] = set()

    def first(match: Callable[[PlanUnit], bool]) -> PlanUnit | None:
        return next((u for u in units if u.id not in picked and match(u)), None)

    def most_complex(match: Callable[[PlanUnit], bool] = lambda u: True) -> PlanUnit | None:
        return next((u for u in by_complexity if u.id not in picked and match(u)), None)

    rules: dict[str, Callable[[], PlanUnit | None]] = {
        "mascot": lambda: first(lambda u: any(c.ref == MASCOT for c in u.characters)),
        "extras": lambda: first(lambda u: any(c.ref != MASCOT for c in u.characters)),
        "night_or_fire": lambda: most_complex(night_or_fire),
        "metaphor": lambda: first(lambda u: u.visual_type == "metaphor"),
        "split_part": lambda: first(lambda u: u.part is not None),
        "most_complex": lambda: most_complex(),
    }
    picks: list[Pick] = []
    for category in COMPARE_CATEGORIES:
        unit, filled = rules[category](), False
        if unit is None:
            unit, filled = most_complex(), True
        if unit is None:
            break
        picked.add(unit.id)
        picks.append(Pick(category, unit.id, filled))
    return picks
