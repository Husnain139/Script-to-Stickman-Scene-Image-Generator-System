import os
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from stickman.compare.setup import (
    CompareError,
    ComparePick,
    CompareSetup,
    compare_dir,
    create_compare,
    default_runs,
    find_compare,
    load_compare,
)
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def source_project(workspace, plan_data, name="2026-09-25_demo"):
    folder = workspace / "projects" / name
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    return folder


def setup_for(source, *, hour=9):
    return CompareSetup(source=source.name, created=datetime(2026, 9, 26, hour, 0, tzinfo=PK),
                        picks=[ComparePick(category="mascot", unit="001", seed=11)], runs=default_runs(Settings(), "16:9"))


def test_the_default_runs_are_klein_4b_with_and_without_references_and_small():
    runs = default_runs(Settings(), "16:9")
    assert [(r.id, r.model, r.width, r.height, r.references) for r in runs] == [
        ("klein-4b-refs", KLEIN_4B, 1920, 1088, True),
        ("klein-4b-no-refs", KLEIN_4B, 1920, 1088, False),
        ("klein-4b-small-refs", KLEIN_4B, 1280, 720, True),
    ]
    assert [(r.width, r.height) for r in default_runs(Settings(), "9:16")] == [(1088, 1920), (1088, 1920), (720, 1280)]


def test_a_comparison_is_created_found_and_loaded(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    folder = compare_dir(tmp_path, source, date(2026, 9, 26))
    assert folder == tmp_path / "projects" / "2026-09-26_compare_demo"
    create_compare(folder, source, setup_for(source))
    assert (folder / "source_plan.yaml").read_bytes() == (source / "plan.yaml").read_bytes()
    assert not (folder / "plan.yaml").exists()  # never taken for a project
    assert find_compare(tmp_path, source) == folder
    setup, plan = load_compare(folder, library_ids=set())
    assert setup == setup_for(source)
    assert [u.id for u in plan.units()] == ["001", "002a", "002b"]


def test_a_second_comparison_on_the_same_day_gets_its_own_folder(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    first = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(first, source, setup_for(source))
    second = compare_dir(tmp_path, source, date(2026, 9, 26))
    assert second.name == "2026-09-26_compare_demo-2"
    create_compare(second, source, setup_for(source))
    assert find_compare(tmp_path, source) == second


def test_creating_a_comparison_in_a_folder_that_exists_is_refused(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    folder = compare_dir(tmp_path, source, date(2026, 9, 26))
    folder.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        create_compare(folder, source, setup_for(source))
    assert list(folder.iterdir()) == []


def test_the_newest_comparison_is_chosen_by_when_it_was_created_not_by_folder_time(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    first = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(first, source, setup_for(source, hour=11))
    second = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(second, source, setup_for(source, hour=10))
    os.utime(first, (1_000_000, 1_000_000))
    os.utime(second, (2_000_000, 2_000_000))
    assert find_compare(tmp_path, source) == first


def test_a_pick_needs_its_seed():
    with pytest.raises(ValidationError):
        ComparePick(category="mascot", unit="001")


def test_comparisons_of_other_projects_are_not_found(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    other = source_project(tmp_path, plan_data, name="2026-09-25_other")
    create_compare(compare_dir(tmp_path, other, date(2026, 9, 26)), other, setup_for(other))
    assert find_compare(tmp_path, source) is None


def test_an_unreadable_compare_json_is_an_error(tmp_path, plan_data):
    source = source_project(tmp_path, plan_data)
    folder = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(folder, source, setup_for(source))
    (folder / "compare.json").write_text("{", encoding="utf-8")
    with pytest.raises(CompareError):
        load_compare(folder, library_ids=set())
