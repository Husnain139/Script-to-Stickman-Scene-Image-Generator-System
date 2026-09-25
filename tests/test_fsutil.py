import os

import pytest

from stickman.fsutil import REPLACE_RETRIES, REPLACE_WAIT_S, safe_write


def test_safe_write_replaces_the_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "state.json"
    target.write_bytes(b"old")
    safe_write(target, b"new")
    assert target.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [target]


def test_a_locked_file_is_retried(tmp_path):
    target = tmp_path / "plan.yaml"
    failures = [PermissionError("locked"), PermissionError("locked")]
    waits = []

    def replace(src, dst):
        if failures:
            raise failures.pop(0)
        os.replace(src, dst)

    safe_write(target, b"data", replace=replace, sleep=waits.append)
    assert target.read_bytes() == b"data"
    assert waits == [REPLACE_WAIT_S, REPLACE_WAIT_S]


def test_a_file_that_stays_locked_raises(tmp_path):
    waits = []

    def replace(src, dst):
        raise PermissionError("locked")

    with pytest.raises(PermissionError):
        safe_write(tmp_path / "plan.yaml", b"data", replace=replace, sleep=waits.append)
    assert len(waits) == REPLACE_RETRIES
