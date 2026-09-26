from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.store import (
    BootstrapError,
    BootstrapStore,
    Candidate,
    anchor_done,
    mascot_done,
    pending_step,
    ranked,
)
from stickman.config_files import load_mascot
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.qc.vision import VisionReport
from stickman.render.images import encode_png
from stickman.settings import QCSettings

PK = timezone(timedelta(hours=5))
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


def candidate(n=1, step="anchor", **changes):
    data = dict(n=n, file=f"library/_bootstrap/v1/{step}/c{n}.png", seed=40 + n, model=KLEIN_9B, width=1024,
                height=768, prompt_sent="a prompt", est_cost_usd=0.015, latency_s=3.5,
                created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    return Candidate(**{**data, **changes})


def test_a_new_store_is_empty_and_saves_nothing_until_asked(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    assert store.folder == tmp_path / "library" / "_bootstrap" / "v1"
    assert store.state.anchor.candidates == [] and store.state.mascot.approved is None
    assert not (store.folder / "bootstrap.json").exists()


def test_candidates_and_approvals_are_saved_at_once(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    store.add("anchor", candidate(2))
    store.add("anchor", candidate(1))
    store.approve("anchor", 2)
    again = BootstrapStore.load(tmp_path, 1)
    assert [c.n for c in again.state.anchor.candidates] == [1, 2]
    assert again.state.anchor.approved == 2
    assert again.step("anchor").candidate(2).seed == 42


def test_approving_an_unknown_candidate_is_refused(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    with pytest.raises(KeyError):
        store.approve("anchor", 3)


def test_the_next_number_is_above_every_record_and_file(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    store.add("anchor", candidate(1))
    stray = store.candidate_path("anchor", 4)
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"left by a killed run")
    assert store.next_number("anchor") == 5
    assert store.next_number("mascot") == 1


def test_a_candidate_saved_just_before_a_kill_is_kept(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    record = candidate(3)
    path = tmp_path / record.file
    path.parent.mkdir(parents=True)
    path.write_bytes(encode_png(Image.new("RGB", (8, 8), "white"), record.model_dump(mode="json")))
    (path.parent / "c4.png.tmp").write_bytes(b"half written")
    (store.folder / "bootstrap.json.tmp").write_bytes(b"{")
    notes = store.recover()
    assert [c.n for c in store.state.anchor.candidates] == [3]
    assert store.state.anchor.candidates[0].qc is None  # checked by the next run
    assert not (path.parent / "c4.png.tmp").exists() and not (store.folder / "bootstrap.json.tmp").exists()
    assert any("c3.png" in note for note in notes)
    assert [c.n for c in BootstrapStore.load(tmp_path, 1).state.anchor.candidates] == [3]


def test_a_file_without_a_record_is_left_as_it_is(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    path = store.candidate_path("mascot", 1)
    path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(path, format="PNG")
    notes = store.recover()
    assert store.state.mascot.candidates == []
    assert any("no record inside" in note for note in notes)


def test_an_unreadable_bootstrap_json_is_an_error(tmp_path):
    folder = tmp_path / "library" / "_bootstrap" / "v1"
    folder.mkdir(parents=True)
    (folder / "bootstrap.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BootstrapError):
        BootstrapStore.load(tmp_path, 1)


def test_candidates_are_ranked_passes_first_then_idea_score(drawings):
    def checked(n, passed, score):
        report = VisionReport(has_text=not passed, style_ok=True, anatomy_ok=True, watermark_like=False,
                              character_count=2, matches_visual_idea=score)
        result = decide(pixel_check(drawings.clean(), QCSettings()), report, expected_figures=2, min_idea_score=3)
        return candidate(n, qc=result)

    order = ranked([checked(1, False, 5), candidate(2), checked(3, True, 3), checked(4, True, 5)])
    assert [c.n for c in order] == [4, 3, 1, 2]


def test_the_pending_step_is_the_anchor_then_the_mascot_then_none(tmp_path):
    mascot = load_mascot(tmp_path)
    assert pending_step(tmp_path, mascot, 1) == "anchor"
    for name in ("anchor_v1.png", "anchor_v1_ref.png"):
        path = tmp_path / "library" / "style" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    assert anchor_done(tmp_path, 1) and not anchor_done(tmp_path, 2)
    assert pending_step(tmp_path, mascot, 1) == "mascot"
    for relative in (mascot.sheet, mascot.ref):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    assert not mascot_done(tmp_path, mascot, 1)  # no seed yet: not approved
    approved = mascot.model_copy(update={"seed": 7, "model": KLEIN_9B})
    assert mascot_done(tmp_path, approved, 1) and not mascot_done(tmp_path, approved, 2)
    assert pending_step(tmp_path, approved, 1) is None
