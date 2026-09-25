"""Per-run JSONL log of API calls (spec §16). It never contains the API token."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

PROMPT_LOG_CHARS = 300


def shorten(text: str, limit: int = PROMPT_LOG_CHARS) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…<{len(text)} chars>"


def mask(text: str, secrets: Sequence[str]) -> str:
    """`text` with every secret replaced by ***."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class RunLog:
    def __init__(self, path: Path | None, *, secrets: Sequence[str] = ()) -> None:
        self.path = path
        self._secrets = tuple(secret for secret in secrets if secret)

    def mask(self, text: str) -> str:
        return mask(text, self._secrets)

    @classmethod
    def for_project(
        cls, project_dir: Path, *, secrets: Sequence[str] = (), now: datetime | None = None
    ) -> RunLog:
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        return cls(project_dir / "logs" / f"run-{stamp}.jsonl", secrets=secrets)

    def write(self, **entry: Any) -> None:
        if self.path is None:
            return
        record = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **entry}
        line = self.mask(json.dumps(record, ensure_ascii=False))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
