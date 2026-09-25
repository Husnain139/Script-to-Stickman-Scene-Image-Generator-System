import json
from datetime import datetime

from stickman.runlog import RunLog, mask, shorten


def test_entries_are_json_lines_with_a_timestamp(tmp_path):
    log = RunLog(tmp_path / "logs" / "run.jsonl")
    log.write(kind="llm", stage="analyse")
    log.write(kind="llm", stage="cut")
    rows = [json.loads(line) for line in (tmp_path / "logs" / "run.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["stage"] for row in rows] == ["analyse", "cut"]
    assert all("ts" in row for row in rows)


def test_secrets_are_masked(tmp_path):
    log = RunLog(tmp_path / "run.jsonl", secrets=("tok-secret",))
    log.write(message="Bearer tok-secret failed")
    text = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert "tok-secret" not in text and "***" in text


def test_for_project_names_the_file_by_time(tmp_path):
    log = RunLog.for_project(tmp_path, now=datetime(2026, 9, 23, 14, 3, 11))
    assert log.path == tmp_path / "logs" / "run-20260923-140311.jsonl"


def test_a_log_without_a_path_writes_nothing(tmp_path):
    RunLog(None).write(kind="llm")
    assert list(tmp_path.iterdir()) == []


def test_shorten_keeps_short_text_and_marks_long_text():
    assert shorten("abc", 5) == "abc"
    assert shorten("abcdefgh", 5) == "abcde…<8 chars>"


def test_mask_replaces_every_secret():
    assert mask("token tok-secret and tok-secret", ("tok-secret", "")) == "token *** and ***"
    assert RunLog(None, secrets=("tok-secret",)).mask("from tok-secret") == "from ***"
