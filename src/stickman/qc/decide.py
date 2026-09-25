"""Pass or fail, and the main reason for a failure (spec §11.3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stickman.qc.pixel import PixelResult
from stickman.qc.vision import VisionReport

QCReason = Literal[
    "safety_filtered", "empty", "text", "background_filled", "style", "watermark",
    "anatomy", "character_count", "mascot_mismatch", "weak_idea", "vision_error",
]
# spec §11.3; vision_error (the checker gave no usable answer) is ours and only ever alone.
REASON_ORDER: tuple[QCReason, ...] = (
    "safety_filtered", "empty", "text", "background_filled", "style", "watermark",
    "anatomy", "character_count", "mascot_mismatch", "weak_idea", "vision_error",
)


class QCResult(BaseModel):
    """A version's QC result, kept in state.json (spec §5.2 `qc`)."""

    model_config = ConfigDict(extra="forbid")

    pixel: PixelResult
    vision: VisionReport | None = None  # None: the pixel checks failed, the check is off, or vision_error
    vision_error: str | None = None
    expected_figures: int = Field(ge=0)
    reference: bool = False  # a reference character image went with the vision check
    passed: bool
    reason: QCReason | None = None

    @property
    def score(self) -> int:
        """How clearly the image shows its idea (1-5), 0 without a vision report."""
        return self.vision.matches_visual_idea if self.vision is not None else 0


def figures_match(expected: int, seen: int) -> bool:
    """Exactly when 3 or fewer are expected, within one above that (spec §11.3)."""
    return seen == expected if expected <= 3 else abs(seen - expected) <= 1


def _vision_failures(report: VisionReport, *, expected_figures: int, reference: bool, min_idea_score: int) -> list[QCReason]:
    failures: list[QCReason] = []
    if report.has_text:
        failures.append("text")
    if not report.style_ok:
        failures.append("style")
    if report.watermark_like:
        failures.append("watermark")
    if not report.anatomy_ok:
        failures.append("anatomy")
    if not figures_match(expected_figures, report.character_count):
        failures.append("character_count")
    if reference and report.mascot_matches_sheet is False:
        failures.append("mascot_mismatch")
    if report.matches_visual_idea < min_idea_score:
        failures.append("weak_idea")
    return failures


def decide(
    pixel: PixelResult,
    vision: VisionReport | None = None,
    *,
    expected_figures: int,
    reference: bool = False,
    min_idea_score: int,
    vision_error: str | None = None,
) -> QCResult:
    if pixel.reason is not None:
        # a pixel failure is fatal to the image itself, so it outranks any vision failure.
        failures: list[QCReason] = [pixel.reason]
    elif vision is not None:
        failures = _vision_failures(
            vision, expected_figures=expected_figures, reference=reference, min_idea_score=min_idea_score
        )
    elif vision_error is not None:
        failures = ["vision_error"]
    else:
        failures = []
    reason = min(failures, key=REASON_ORDER.index) if failures else None
    return QCResult(
        pixel=pixel, vision=vision, vision_error=vision_error, expected_figures=expected_figures,
        reference=reference, passed=reason is None, reason=reason,
    )
