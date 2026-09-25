import os
from datetime import date
from pathlib import Path

import pytest

from stickman.project import (
    ProjectError,
    check_unplanned,
    choose_project_dir,
    create_project,
    project_dir,
    resolve_project,
    slugify,
)


def test_slugify():
    assert slugify("First Sleep v2!") == "first-sleep-v2"
    assert slugify("Ünïcode Name") == "unicode-name"
    assert slugify("  !! ") == "project"


def test_project_dir_is_dated(tmp_path):
    assert project_dir(tmp_path, "first-sleep", date(2026, 9, 22)) == tmp_path / "projects" / "2026-09-22_first-sleep"


def test_create_project_copies_the_script_and_refuses_a_planned_folder(tmp_path):
    script = tmp_path / "s.txt"
    script.write_bytes(b"0:00 Hi.\n")
    directory = project_dir(tmp_path, "s", date(2026, 9, 22))
    create_project(directory, script)
    assert (directory / "script.txt").read_bytes() == b"0:00 Hi.\n"
    create_project(directory, script)  # planning never finished: the folder is reused
    (directory / "plan.yaml").write_text("x", encoding="utf-8")
    with pytest.raises(ProjectError, match="already has a plan.yaml"):
        create_project(directory, script)


DAY = date(2026, 9, 24)


def unfinished(workspace, name, script=b"0:00 Hi.\n", plan=False):
    directory = workspace / "projects" / name
    directory.mkdir(parents=True)
    (directory / "script.txt").write_bytes(script)
    if plan:
        (directory / "plan.yaml").write_text("x", encoding="utf-8")
    return directory


def script_file(tmp_path, content=b"0:00 Hi.\n"):
    path = tmp_path / "s.txt"
    path.write_bytes(content)
    return path


def test_a_new_project_goes_into_todays_folder(tmp_path):
    assert choose_project_dir(tmp_path, "s", DAY, script_file(tmp_path)) == (tmp_path / "projects" / "2026-09-24_s", False)


def test_the_newest_unfinished_folder_with_the_same_script_is_continued(tmp_path):
    unfinished(tmp_path, "2026-09-20_s")
    wanted = unfinished(tmp_path, "2026-09-21_s")
    unfinished(tmp_path, "2026-09-22_s", plan=True)  # finished
    unfinished(tmp_path, "2026-09-23_s", script=b"0:00 Bye.\n")  # another script
    unfinished(tmp_path, "2026-09-23_s_9x16")  # another project's folder
    unfinished(tmp_path, "2026-09-23_other-s")
    assert choose_project_dir(tmp_path, "s", DAY, script_file(tmp_path)) == (wanted, True)


def test_todays_folder_wins_when_it_exists(tmp_path):
    unfinished(tmp_path, "2026-09-23_s")
    today = unfinished(tmp_path, "2026-09-24_s", plan=True)
    assert choose_project_dir(tmp_path, "s", DAY, script_file(tmp_path)) == (today, False)


def test_an_unreadable_script_means_todays_folder(tmp_path):
    unfinished(tmp_path, "2026-09-23_s")
    assert choose_project_dir(tmp_path, "s", DAY, tmp_path / "missing.txt") == (tmp_path / "projects" / "2026-09-24_s", False)


def test_check_unplanned_refuses_a_folder_with_a_plan(tmp_path):
    check_unplanned(unfinished(tmp_path, "2026-09-23_s"))
    with pytest.raises(ProjectError, match="already has a plan.yaml"):
        check_unplanned(unfinished(tmp_path, "2026-09-24_s", plan=True))


def planned(workspace, name, mtime):
    directory = workspace / "projects" / name
    directory.mkdir(parents=True)
    (directory / "plan.yaml").write_text("x", encoding="utf-8")
    for path in (directory / "plan.yaml", directory):
        os.utime(path, (mtime, mtime))
    return directory


def test_resolve_project_prefers_the_named_one_else_the_latest(tmp_path):
    old = planned(tmp_path, "2026-09-20_old", 1000)
    new = planned(tmp_path, "2026-09-22_new", 2000)
    (tmp_path / "projects" / "2026-09-23_unplanned").mkdir()
    assert resolve_project(tmp_path, None) == new.resolve()
    assert resolve_project(tmp_path, Path("2026-09-20_old")) == old.resolve()
    with pytest.raises(ProjectError, match="missing"):
        resolve_project(tmp_path, Path("missing"))


def test_resolve_project_without_projects(tmp_path):
    with pytest.raises(ProjectError, match="stickman new"):
        resolve_project(tmp_path, None)
