"""The project lock: one generating process per project (spec §3)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

LOCK_FILE = ".lock"
STARTING_GRACE_S = 5.0  # an empty lock this new belongs to a process that is still writing its PID

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5


class LockHeld(Exception):
    def __init__(self, pid: int | None) -> None:
        self.pid = pid
        who = f"(PID {pid})" if pid is not None else "(starting up)"
        super().__init__(f"another stickman process {who} is generating this project")


def pid_alive(pid: int) -> bool:
    """Whether a process with this PID is running. It never signals the process: on Windows,
    os.kill(pid, 0) would end it."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # it exists, but we may not look at it
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class ProjectLock:
    """`.lock` holds the generating process's PID. A lock whose process is gone is removed, and
    `removed_stale` says whose it was, so the CLI can warn."""

    def __init__(self, project_dir: Path, *, pid: int | None = None, alive: Callable[[int], bool] | None = None) -> None:
        self.path = project_dir / LOCK_FILE
        self.pid = os.getpid() if pid is None else pid
        self._alive = alive or pid_alive
        self.removed_stale: int | None = None

    def acquire(self) -> None:
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                other = self._read_pid()
                if other is None and self._age() < STARTING_GRACE_S:
                    raise LockHeld(None) from None
                if other is not None and other != self.pid and self._alive(other):
                    raise LockHeld(other) from None
                self.removed_stale = other
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(str(self.pid))
            return
        raise LockHeld(self._read_pid())

    def release(self) -> None:
        if self._read_pid() == self.pid:
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def _read_pid(self) -> int | None:
        try:
            return int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    def _age(self) -> float:
        try:
            return time.time() - self.path.stat().st_mtime
        except OSError:
            return STARTING_GRACE_S
