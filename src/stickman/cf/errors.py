"""Cloudflare error categories (spec §9.5)."""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    RATE_LIMITED = "rate_limited"
    DAILY_LIMIT = "daily_limit"
    AUTH = "auth"
    BAD_REQUEST = "bad_request"
    REFUSED = "refused"
    TRANSIENT = "transient"


# Lower-case substrings of Cloudflare error bodies. Refine them from the real
# responses recorded in M0 (Task 14; tests/fixtures/cf/).
DAILY_LIMIT_MARKERS: tuple[str, ...] = ("daily free allocation", "daily limit")
REFUSAL_MARKERS: tuple[str, ...] = ("nsfw", "content policy", "safety", "flagged")


class CFError(Exception):
    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        status: int | None = None,
        possibly_billed: bool = False,
        retry_after: float | None = None,
    ) -> None:
        self.category = category
        self.message = message
        self.status = status
        self.possibly_billed = possibly_billed
        self.retry_after = retry_after
        where = f" (HTTP {status})" if status is not None else ""
        super().__init__(f"{category}{where}: {message}")


def classify(status: int, body: str, *, plan: str) -> ErrorCategory:
    text = body.lower()
    if plan == "free" and status < 500 and any(m in text for m in DAILY_LIMIT_MARKERS):
        return ErrorCategory.DAILY_LIMIT
    if status in (401, 403):
        return ErrorCategory.AUTH
    if status == 429:
        return ErrorCategory.RATE_LIMITED
    if status >= 500:
        return ErrorCategory.TRANSIENT
    if 400 <= status < 500:
        if any(m in text for m in REFUSAL_MARKERS):
            return ErrorCategory.REFUSED
        return ErrorCategory.BAD_REQUEST
    return ErrorCategory.TRANSIENT
