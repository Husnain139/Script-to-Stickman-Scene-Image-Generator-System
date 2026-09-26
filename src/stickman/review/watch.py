"""Watching the project folder for the review page (spec §12.4): a saved plan.yaml sends a `plan` event and
a changed state.json a `state` event, within DEBOUNCE_MS. After the server writes plan.yaml it records the
new hash, and a change with that hash is ignored."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path

from watchfiles import awatch

from stickman.plan.store import file_hash
from stickman.review.events import EventHub

DEBOUNCE_MS = 300


class PlanWatcher:
    def __init__(self, project_dir: Path, hub: EventHub) -> None:
        self._project = project_dir
        self._hub = hub
        self._own: set[str] = set()
        self._last = self._plan_hash()

    def _plan_hash(self) -> str | None:
        try:
            return file_hash((self._project / "plan.yaml").read_bytes())
        except OSError:
            return None

    def wrote(self, written_hash: str) -> None:
        """This server wrote plan.yaml with this content: its change isn't news to the page."""
        self._own.add(written_hash)

    def changed(self, paths: Iterable[Path | str]) -> list[str]:
        names = {Path(path).name for path in paths}
        kinds: list[str] = []
        if "plan.yaml" in names:
            current = self._plan_hash()
            if current != self._last:
                self._last = current
                if current not in self._own:
                    self._hub.publish("plan", {"hash": current})
                    kinds.append("plan")
        if "state.json" in names:
            self._hub.publish("state", {})
            kinds.append("state")
        return kinds

    async def run(self, stop: asyncio.Event) -> None:
        async for changes in awatch(self._project, recursive=False, debounce=DEBOUNCE_MS, stop_event=stop):
            self.changed(path for _, path in changes)
