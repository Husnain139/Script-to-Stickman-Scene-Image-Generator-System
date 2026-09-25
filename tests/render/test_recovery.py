from datetime import datetime, timedelta, timezone

from PIL import Image

from stickman.render.images import encode_png
from stickman.render.recovery import ExpectedUnit, recover
from stickman.render.state import StateStore, Version

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
EXPECTED = {"001": ExpectedUnit("001_00-00.0", "sha256:abc"), "006a": ExpectedUnit("006a_00-21.0", "sha256:abc")}
CHANGED = {**EXPECTED, "006a": ExpectedUnit("006a_00-21.0", "sha256:new")}


def version(v=1, unit="006a", **changes):
    data = dict(v=v, file=f"images/_history/{unit}_v{v}.png", seed=1, model=KLEIN_4B, width=8, height=8,
                fingerprint="sha256:abc", refs=[], prompt_sent="a prompt", est_cost_usd=0.0023, latency_s=7.4,
                created=datetime(2026, 9, 22, 14, 3, 11, tzinfo=PK))
    return Version(**{**data, **changes})


def history_image(project, v=1, unit="006a", **changes):
    """A history PNG as the renderer writes it: the image, with its version record inside."""
    record = version(v, unit, **changes)
    path = project / record.file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(Image.new("RGB", (8, 8), "white"), record.model_dump(mode="json")))
    return path


def test_units_left_generating_go_back_to_planned(tmp_path):
    store = StateStore.load(tmp_path)
    store.set_status("001", "generating")
    notes = recover(store, EXPECTED)
    assert store.unit("001").status == "planned"
    assert StateStore.load(tmp_path).state.units["001"].status == "planned"
    assert any("generating" in note and "001" in note for note in notes)


def test_every_plan_unit_gets_a_state_entry(tmp_path):
    store = StateStore.load(tmp_path)
    recover(store, EXPECTED)
    assert set(StateStore.load(tmp_path).state.units) == {"001", "006a"}


def test_an_image_saved_just_before_a_kill_is_kept(tmp_path):
    store = StateStore.load(tmp_path)
    store.set_status("006a", "generating")
    path = history_image(tmp_path)
    notes = recover(store, EXPECTED)
    unit = store.unit("006a")
    assert (unit.status, unit.current_version, [v.v for v in unit.versions]) == ("generated", 1, [1])
    assert (tmp_path / "images" / "006a_00-21.0.png").read_bytes() == path.read_bytes()
    assert any("006a_v1.png" in note for note in notes)


def test_a_history_file_without_its_record_is_left_alone_and_its_number_skipped(tmp_path):
    store = StateStore.load(tmp_path)
    history = tmp_path / "images" / "_history"
    history.mkdir(parents=True)
    (history / "006a_v1.png").write_bytes(b"not a png")
    notes = recover(store, EXPECTED)
    assert store.unit("006a").versions == []
    assert store.next_version("006a") == 2
    assert any("006a_v1.png" in note for note in notes)


def test_a_missing_or_outdated_current_image_is_copied_again(tmp_path):
    store = StateStore.load(tmp_path)
    path = history_image(tmp_path)
    store.add_version("006a", version(), status="generated")
    current = tmp_path / "images" / "006a_00-21.0.png"
    recover(store, EXPECTED)
    assert current.read_bytes() == path.read_bytes()
    current.write_bytes(b"an older version")
    recover(store, EXPECTED)
    assert current.read_bytes() == path.read_bytes()


def test_temp_files_from_interrupted_writes_are_removed(tmp_path):
    history = tmp_path / "images" / "_history"
    history.mkdir(parents=True)
    for path in (tmp_path / "state.json.tmp", history / "006a_v2.png.tmp", tmp_path / "images" / "001_00-00.0.png.tmp"):
        path.write_bytes(b"half")
    recover(StateStore.load(tmp_path), EXPECTED)
    assert not list(tmp_path.rglob("*.tmp"))


def test_a_unit_whose_fingerprint_changed_is_stale_until_edited_back(tmp_path):
    store = StateStore.load(tmp_path)
    history_image(tmp_path)
    store.add_version("006a", version(), status="generated")
    notes = recover(store, CHANGED)
    assert store.unit("006a").status == "stale"
    assert any(note.startswith("Stale") and "006a" in note for note in notes)
    notes = recover(store, EXPECTED)
    assert store.unit("006a").status == "generated"
    assert "No longer stale: 006a" in notes


def test_an_approved_unit_is_compared_with_its_approved_version(tmp_path):
    store = StateStore.load(tmp_path)
    history_image(tmp_path, 1)
    history_image(tmp_path, 2, fingerprint="sha256:new")
    store.add_version("006a", version(1), status="generated")
    store.add_version("006a", version(2, fingerprint="sha256:new"), status="generated")
    unit = store.unit("006a")
    unit.approved_version, unit.status = 1, "approved"
    store.save()
    recover(store, EXPECTED)
    assert store.unit("006a").status == "approved"
    recover(store, CHANGED)
    assert store.unit("006a").status == "stale"
    recover(store, EXPECTED)
    assert store.unit("006a").status == "approved"
