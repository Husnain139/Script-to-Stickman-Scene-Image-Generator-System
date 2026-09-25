import os
import subprocess
import sys
import time

import pytest

from stickman.render.lock import LockHeld, ProjectLock, pid_alive


def test_acquire_writes_the_pid_and_release_removes_it(tmp_path):
    lock = ProjectLock(tmp_path, pid=4242)
    lock.acquire()
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "4242"
    lock.release()
    assert not (tmp_path / ".lock").exists()


def test_a_lock_held_by_a_running_process_is_refused(tmp_path):
    ProjectLock(tmp_path, pid=4242, alive=lambda pid: True).acquire()
    with pytest.raises(LockHeld, match="PID 4242") as info:
        ProjectLock(tmp_path, pid=5151, alive=lambda pid: True).acquire()
    assert info.value.pid == 4242
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "4242"


def test_a_lock_left_by_a_process_that_is_gone_is_removed(tmp_path):
    (tmp_path / ".lock").write_text("4242", encoding="ascii")
    lock = ProjectLock(tmp_path, pid=5151, alive=lambda pid: False)
    lock.acquire()
    assert lock.removed_stale == 4242
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "5151"


def test_a_lock_with_this_processs_own_pid_is_a_leftover(tmp_path):
    (tmp_path / ".lock").write_text("5151", encoding="ascii")
    lock = ProjectLock(tmp_path, pid=5151, alive=lambda pid: True)
    lock.acquire()
    assert lock.removed_stale == 5151


def test_an_empty_lock_is_another_process_starting_until_it_is_old(tmp_path):
    (tmp_path / ".lock").write_text("", encoding="ascii")
    with pytest.raises(LockHeld):
        ProjectLock(tmp_path, pid=5151, alive=lambda pid: True).acquire()
    old = time.time() - 60
    os.utime(tmp_path / ".lock", (old, old))
    lock = ProjectLock(tmp_path, pid=5151, alive=lambda pid: True)
    lock.acquire()
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "5151"


def test_release_never_removes_another_processs_lock(tmp_path):
    (tmp_path / ".lock").write_text("4242", encoding="ascii")
    ProjectLock(tmp_path, pid=5151).release()
    assert (tmp_path / ".lock").read_text(encoding="ascii") == "4242"


def test_the_lock_is_released_when_the_block_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with ProjectLock(tmp_path, pid=4242):
            raise RuntimeError("boom")
    assert not (tmp_path / ".lock").exists()


def test_pid_alive_tells_a_running_process_from_a_finished_one():
    assert pid_alive(os.getpid()) is True
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    assert pid_alive(process.pid) is False
    assert pid_alive(0) is False
