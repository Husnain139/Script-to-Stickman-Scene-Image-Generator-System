"""Stage 2, cut: candidate cut points for long scenes (spec §4.5, §6.3)."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from stickman.plan.llm import StageRequest, StageRunner
from stickman.plan.prompts import CUT_SYSTEM
from stickman.settings import SplitSettings
from stickman.split.engine import needs_split
from stickman.split.scenes import Scene


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CutScene(_Reply):
    id: str
    candidates: list[int] = Field(default_factory=list, max_length=3)


class CutResult(_Reply):
    scenes: list[CutScene]


def cut_user_message(scenes: Sequence[Scene]) -> str:
    blocks = []
    for scene in scenes:
        words = " ".join(f"{index}:{word}" for index, word in enumerate(scene.tokens, 1))
        blocks.append(f"id: {scene.number:03d}\nduration: {scene.duration:.1f} s\nwords: {words}")
    return "\n\n".join(blocks)


def check_cut(result: CutResult, scenes: Sequence[Scene]) -> list[str]:
    expected = [f"{scene.number:03d}" for scene in scenes]
    got = [item.id for item in result.scenes]
    errors = []
    if sorted(got) != sorted(expected):
        errors.append(f"scenes: ids must be exactly {expected}, got {got}")
    words = {f"{scene.number:03d}": scene.words for scene in scenes}
    for index, item in enumerate(result.scenes):
        n = words.get(item.id)
        if n is None:
            continue
        for k in item.candidates:
            if not 1 <= k <= n - 1:
                errors.append(f"scenes[{index}].candidates: {k} is outside 1..{n - 1}")
        if len(set(item.candidates)) != len(item.candidates):
            errors.append(f"scenes[{index}].candidates: duplicates in {item.candidates}")
    return errors


async def cut_candidates(
    runner: StageRunner, scenes: Sequence[Scene], split: SplitSettings
) -> dict[int, list[int]]:
    targets = [scene for scene in scenes if needs_split(scene, split)]
    if not targets:
        return {}
    request = StageRequest(
        stage="cut",
        system=CUT_SYSTEM,
        user=cut_user_message(targets),
        schema=CutResult,
        check=lambda result: check_cut(result, targets),
    )
    result = await runner.run(request)
    return {int(item.id): list(item.candidates) for item in result.scenes}
