"""Safe file writes: temp file, fsync, then replace (spec §5.2)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

REPLACE_RETRIES = 10
REPLACE_WAIT_S = 0.1


def safe_write(
    path: Path,
    data: bytes,
    *,
    replace: Callable[[Path, Path], None] = os.replace,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Write `data` so readers see the old file or the new one, never half of one.

    Windows can briefly lock a file (antivirus, sync clients). The replace is then
    retried up to REPLACE_RETRIES times, REPLACE_WAIT_S apart.
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_RETRIES:
                raise
            sleep(REPLACE_WAIT_S)
