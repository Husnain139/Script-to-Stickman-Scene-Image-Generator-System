"""The global cost ledger, ledger.jsonl (spec §5.4, §9.7): one JSON object per line, append-only."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

LEDGER_FILE = "ledger.jsonl"  # at the workspace root

Kind = Literal["image", "sheet", "anchor", "llm", "vision"]
Billing = Literal["billed", "possibly_billed", "not_billed"]
SPENT: frozenset[str] = frozenset({"billed", "possibly_billed"})


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: AwareDatetime
    project: str
    unit: str | None = None
    kind: Kind
    model: str
    est_usd: float = Field(ge=0)
    billing: Billing
    request_id: str | None = None
    neurons: float | None = None  # [M3] the cf-ai-neurons header of a successful call


def week_start(now: datetime) -> datetime:
    """Monday 00:00 of `now`'s week, in `now`'s own time zone; callers pass local time (§9.7)."""
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def utc_day_start(now: datetime) -> datetime:
    """00:00 UTC of `now`'s UTC day, when the free daily allocation resets."""
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: LedgerEntry) -> None:
        """Add one line. A torn last line (a killed write) is ended first, so this entry stays readable."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(entry.model_dump_json().encode("utf-8") + b"\n")

    def entries(self) -> Iterator[LedgerEntry]:
        """Every readable entry. A line that isn't a valid entry, such as a torn write, is skipped."""
        if not self.path.is_file():
            return
        # errors="replace": a torn write can cut a multi-byte character; that line then fails validation.
        with self.path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    yield LedgerEntry.model_validate_json(line)
                except ValidationError:
                    continue

    def spent_since(self, start: datetime) -> float:
        return sum(e.est_usd for e in self.entries() if e.billing in SPENT and e.ts >= start)

    def neurons_since(self, start: datetime) -> float:
        return sum(e.neurons or 0.0 for e in self.entries() if e.ts >= start)
