from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.store import BootstrapStore, Candidate
from stickman.config_files import load_mascot, load_style
from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.render.images import encode_png
from stickman.render.state import StateStore, Version
from stickman.review.access import ProjectAccess
from stickman.review.actions import (
    PLAN_CHANGED,
    ActionError,
    approve_plan,
    approve_remaining,
    approve_sheet,
    approve_tests,
    approve_unit,
    edit_prompt,
    select_version,
)
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))
IDS = {"001", "002a", "002b"}
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def version(unit, v=1):
    return Version(v=v, file=f"images/_history/{unit}_v{v}.png", seed=v, model=KLEIN_4B, width=8, height=8,
                   fingerprint="sha256:x", prompt_sent="p", est_cost_usd=0.0, latency_s=1.0,
                   created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))


def status_of(error):
    return error.value.status


def test_approving_a_unit(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version("001"), status="generated")
    approve_unit(store, "001", unit_ids=IDS)
    assert store.unit("001").status == "approved"


@pytest.mark.parametrize(("unit_id", "busy", "code"), [("009", None, 404), ("002a", None, 409), ("001", "001", 409)])
def test_approving_an_unknown_imageless_or_busy_unit_is_refused(tmp_path, unit_id, busy, code):
    store = StateStore.load(tmp_path)
    store.add_version("001", version("001"), status="generated")
    with pytest.raises(ActionError) as error:
        approve_unit(store, unit_id, unit_ids=IDS, busy=busy)
    assert status_of(error) == code


def test_approve_remaining_takes_only_generated_units(tmp_path):
    store = StateStore.load(tmp_path)
    for unit in ("001", "002a", "002b"):
        store.add_version(unit, version(unit), status="generated")
    done = approve_remaining(store, {"001": "generated", "002a": "stale", "002b": "needs_review"})
    assert done == ["001"]
    assert [store.unit(u).status for u in ("001", "002a", "002b")] == ["approved", "generated", "generated"]


def test_selecting_a_version(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version("001", 1), status="generated")
    store.add_version("001", version("001", 2), status="generated")
    select_version(store, "001", 1, unit_ids=IDS, stem="001_00-00.0")
    assert store.unit("001").current_version == 1
    with pytest.raises(ActionError) as error:
        select_version(store, "001", 9, unit_ids=IDS, stem="001_00-00.0")
    assert status_of(error) == 404


def test_the_plan_is_approved_only_without_errors(tmp_path):
    store = StateStore.load(tmp_path)
    with pytest.raises(ActionError) as error:
        approve_plan(store, ["scenes[0].shot: bad"])
    assert status_of(error) == 409 and not store.state.plan_approved
    approve_plan(store, [])
    assert StateStore.load(tmp_path).state.plan_approved


def test_tests_are_approved_only_once_there_are_test_units(tmp_path):
    store = StateStore.load(tmp_path)
    with pytest.raises(ActionError) as error:
        approve_tests(store)
    assert status_of(error) == 409 and "M7" in error.value.message
    store.state.test_units = ["001"]
    approve_tests(store)
    assert StateStore.load(tmp_path).state.tests_approved


def planned(tmp_path, plan_data):
    path = tmp_path / "plan.yaml"
    write_plan(path, to_document(parse_plan(plan_data)), expected_hash=None)
    path.write_text("# my notes\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    return path, load_plan(path).hash


def test_a_prompt_edit_is_written_and_locks_the_prompt(tmp_path, plan_data):
    path, loaded_hash = planned(tmp_path, plan_data)
    new_hash = edit_prompt(path, "002a", "A hand-written prompt.\r\nSecond line.", loaded_hash, library_ids=set())
    loaded = load_plan(path)
    unit = next(u for u in loaded.plan.units() if u.id == "002a")
    assert (unit.image_prompt, unit.prompt_locked) == ("A hand-written prompt.\nSecond line.", True)
    assert loaded.hash == new_hash and path.read_text(encoding="utf-8").startswith("# my notes\n")


def test_a_prompt_edit_after_the_file_changed_on_disk_gives_409_and_writes_nothing(tmp_path, plan_data):
    path, loaded_hash = planned(tmp_path, plan_data)
    path.write_text(path.read_text(encoding="utf-8") + "# edited elsewhere\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ActionError) as error:
        edit_prompt(path, "002a", "new", loaded_hash, library_ids=set())
    assert (status_of(error), error.value.message) == (409, PLAN_CHANGED)
    assert path.read_bytes() == before


@pytest.mark.parametrize(("unit_id", "prompt", "code"), [("009", "x", 404), ("001", "  ", 422)])
def test_a_prompt_edit_of_an_unknown_unit_or_an_empty_prompt_is_refused(tmp_path, plan_data, unit_id, prompt, code):
    path, loaded_hash = planned(tmp_path, plan_data)
    with pytest.raises(ActionError) as error:
        edit_prompt(path, unit_id, prompt, loaded_hash, library_ids=set())
    assert status_of(error) == code


def test_approving_an_anchor_candidate_from_the_page(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    record = Candidate(n=1, file="library/_bootstrap/v1/anchor/c1.png", seed=5,
                       model="@cf/black-forest-labs/flux-2-klein-9b", width=1024, height=768, prompt_sent="p",
                       est_cost_usd=0.015, latency_s=3.0, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    path = tmp_path / record.file
    path.parent.mkdir(parents=True)
    path.write_bytes(encode_png(Image.new("RGB", (1024, 768), "white"), record.model_dump(mode="json")))
    store.add("anchor", record)
    written = approve_sheet(tmp_path, "anchor", 1, settings=Settings(), style=load_style(tmp_path),
                            mascot=load_mascot(tmp_path), access=ProjectAccess(tmp_path))
    assert written == ["library/style/anchor_v1.png", "library/style/anchor_v1_ref.png"]
    assert not (tmp_path / "library" / "_bootstrap" / "v1" / ".lock").exists()


def test_extras_sheets_come_later_and_unknown_candidates_are_refused(tmp_path):
    kwargs = dict(settings=Settings(), style=load_style(tmp_path), mascot=load_mascot(tmp_path), access=ProjectAccess(tmp_path))
    with pytest.raises(ActionError) as error:
        approve_sheet(tmp_path, "caveman_group", 1, **kwargs)
    assert status_of(error) == 400 and "M7" in error.value.message
    with pytest.raises(ActionError) as error:
        approve_sheet(tmp_path, "anchor", 3, **kwargs)
    assert status_of(error) == 409 and "no anchor candidate 3" in error.value.message


def test_selecting_a_version_makes_it_the_current_copy_in_images(tmp_path):
    store = StateStore.load(tmp_path)
    (tmp_path / "images" / "_history").mkdir(parents=True)
    for v in (1, 2):
        (tmp_path / version("001", v).file).write_bytes(f"version {v}".encode())
        store.add_version("001", version("001", v), status="generated")
    copy = tmp_path / "images" / "001_00-00.0.png"
    copy.write_bytes(b"version 2")
    select_version(store, "001", 1, unit_ids=IDS, stem="001_00-00.0")
    assert copy.read_bytes() == b"version 1"


def test_approving_a_stale_unit_is_refused(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("001", version("001"), status="generated")
    with pytest.raises(ActionError) as error:
        approve_unit(store, "001", unit_ids=IDS, status="stale")
    assert status_of(error) == 409
    assert error.value.message == "001 is stale: its plan fields changed after the image was made; regenerate it first"
    assert store.unit("001").status == "generated"
