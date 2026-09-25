"""Who can appear in a picture: the mascot plus the project's cast (spec §1.1, §7.2)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stickman.config_files import MascotConfig
from stickman.plan.models import MASCOT, CastMember, MascotEntry


@dataclass(frozen=True)
class CastInfo:
    id: str
    name: str
    figures: int
    description: str


def cast_infos(cast: Sequence[MascotEntry | CastMember], mascot: MascotConfig) -> dict[str, CastInfo]:
    table = {MASCOT: CastInfo(MASCOT, mascot.name, mascot.figures, mascot.description)}
    for member in cast:
        if isinstance(member, CastMember):
            table[member.id] = CastInfo(member.id, member.name, member.figures, member.description)
    return table
