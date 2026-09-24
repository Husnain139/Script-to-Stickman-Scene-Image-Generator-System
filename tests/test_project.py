import os
from datetime import date
from pathlib import Path

import pytest

from stickman.project import ProjectError, create_project, project_dir, resolve_project, slugify


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
