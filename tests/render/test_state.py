import json
from datetime import datetime, timedelta, timezone

import pytest

from stickman.render.state import ProjectState, StateError, StateStore, Version

PK = timezone(timedelta(hours=5))
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def version(v=1, unit="006a", **changes):
    data = dict(v=v, file=f"images/_history/{unit}_v{v}.png", seed=48213377, model=KLEIN_4B, width=1920,
                height=1088, fingerprint="sha256:abc", refs=[], prompt_sent="a prompt", est_cost_usd=0.0023,
                latency_s=7.4, created=datetime(2026, 9, 22, 14, 3, 11, tzinfo=PK))
    return Version(**{**data, **changes})


def test_a_missing_state_file_is_an_empty_state(tmp_path):
    store = StateStore.load(tmp_path)
    assert store.state == ProjectState()
    assert store.unit("001").status == "planned"
    assert not (tmp_path / "state.json").exists()


def test_state_round_trips_through_state_json(tmp_path):
    StateStore.load(tmp_path).add_version("006a", version(), status="generated")
    unit = StateStore.load(tmp_path).state.units["006a"]
    assert (unit.status, unit.current_version, unit.versions) == ("generated", 1, [version()])
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["units"]["006a"]["versions"][0]["created"] == "2026-09-22T14:03:11+05:00"
    assert (data["plan_approved"], data["tests_approved"], data["test_units"]) == (False, False, [])
    assert not (tmp_path / "state.json.tmp").exists()


def test_every_status_change_is_saved_at_once(tmp_path):
    StateStore.load(tmp_path).set_status("001", "failed", error="bad_request: invalid")
    unit = StateStore.load(tmp_path).state.units["001"]
    assert (unit.status, unit.error) == ("failed", "bad_request: invalid")


def test_a_new_version_clears_the_last_error(tmp_path):
    store = StateStore.load(tmp_path)
    store.set_status("006a", "failed", error="transient: bad gateway")
    store.add_version("006a", version(), status="generated")
    assert store.unit("006a").error is None
    assert store.unit("006a").version(1) == version()
    assert store.unit("006a").version(2) is None


@pytest.mark.parametrize("text", ["{not json", '{"schema_version": 1, "surprise": true}'])
def test_an_unreadable_state_file_is_a_state_error(tmp_path, text):
    (tmp_path / "state.json").write_text(text, encoding="utf-8")
    with pytest.raises(StateError, match="state.json"):
        StateStore.load(tmp_path)


def test_version_numbers_skip_files_already_in_the_history(tmp_path):
    store = StateStore.load(tmp_path)
    store.add_version("006a", version(1), status="generated")
    history = tmp_path / "images" / "_history"
    history.mkdir(parents=True)
    (history / "006a_v3.png").write_bytes(b"left by a killed run")
    (history / "006b_v7.png").write_bytes(b"another unit")
    assert store.next_version("006a") == 4
    assert store.next_version("001") == 1
