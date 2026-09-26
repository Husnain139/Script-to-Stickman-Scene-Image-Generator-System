import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.approve import ApprovalError, approve_anchor, approve_mascot, set_mascot_approval
from stickman.bootstrap.store import BootstrapStore, Candidate, mascot_done
from stickman.config_files import load_mascot
from stickman.library import anchor_path, anchor_ref_path, find_references
from stickman.render.images import encode_png
from stickman.render.references import ReferenceFiles
from stickman.settings import default_config_text

PK = timezone(timedelta(hours=5))
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


def saved(store, step, n, size, *, refs=None, color="white"):
    """A recorded candidate. A mascot candidate is made with the approved anchor as it is now, unless `refs` says otherwise."""
    if refs is None:
        refs = [current_anchor(store.workspace)] if step == "mascot" and anchor_ref_path(store.workspace, 1).is_file() else []
    record = Candidate(n=n, file=f"library/_bootstrap/v1/{step}/c{n}.png", seed=500 + n, model=KLEIN_9B,
                       width=size[0], height=size[1], prompt_sent="p", refs=refs, est_cost_usd=0.015, latency_s=3.0,
                       created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    path = store.workspace / record.file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(Image.new("RGB", size, color), record.model_dump(mode="json")))
    store.add(step, record)
    return path


def current_anchor(workspace):
    return ReferenceFiles(workspace, ref_max_side=512).load(anchor_ref_path(workspace, 1)).label


def size_of(path):
    with Image.open(path) as image:
        return image.size


def test_approving_an_anchor_writes_it_and_its_reference_copy(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    source = saved(store, "anchor", 2, (1024, 768))
    written = approve_anchor(store, 2, ref_max_side=512)
    assert written == [anchor_path(tmp_path, 1), anchor_ref_path(tmp_path, 1)]
    assert anchor_path(tmp_path, 1).read_bytes() == source.read_bytes()
    assert size_of(anchor_ref_path(tmp_path, 1)) == (512, 384)
    assert BootstrapStore.load(tmp_path, 1).state.anchor.approved == 2


def test_approving_an_unknown_candidate_is_refused(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    with pytest.raises(ApprovalError, match="no anchor candidate 3"):
        approve_anchor(store, 3, ref_max_side=512)


def test_the_mascot_needs_an_approved_anchor_first(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "mascot", 1, (768, 1024))
    with pytest.raises(ApprovalError, match="approve the style anchor first"):
        approve_mascot(store, 1, mascot=load_mascot(tmp_path), ref_max_side=512)


def test_approving_the_mascot_writes_its_files_and_seed_and_model_to_mascot_yaml(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "anchor", 1, (1024, 768))
    approve_anchor(store, 1, ref_max_side=512)
    saved(store, "mascot", 2, (768, 1024))
    mascot = load_mascot(tmp_path)
    approve_mascot(store, 2, mascot=mascot, ref_max_side=512)
    assert size_of(tmp_path / mascot.sheet) == (768, 1024)
    assert size_of(tmp_path / mascot.ref) == (384, 512)
    text = (tmp_path / "config" / "mascot.yaml").read_text(encoding="utf-8")
    assert "# set at bootstrap approval" in text  # comments survive
    approved = load_mascot(tmp_path)
    assert (approved.seed, approved.model) == (502, KLEIN_9B)
    assert mascot_done(tmp_path, approved, 1)
    assert BootstrapStore.load(tmp_path, 1).state.mascot.approved == 2
    refs = find_references(tmp_path, use_references=True, style_version=1, mascot=approved, cast=[], library=[])
    assert refs.anchor and refs.sheets == frozenset({"mascot"})


def test_a_mascot_yaml_of_another_style_version_is_refused(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "anchor", 1, (1024, 768))
    approve_anchor(store, 1, ref_max_side=512)
    saved(store, "mascot", 1, (768, 1024))
    mascot = load_mascot(tmp_path).model_copy(update={"style_version": 2})
    with pytest.raises(ApprovalError, match="style_version 2"):
        approve_mascot(store, 1, mascot=mascot, ref_max_side=512)


def test_set_mascot_approval_keeps_an_existing_files_comments_and_other_keys(tmp_path):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "mascot.yaml"
    path.write_text(
        "schema_version: 1\nid: mascot\nname: \"Bob\"  # my mascot\nfigures: 1\nidentity: a stickman\n"
        "default_outfit: \"\"\nsheet: library/mascot/sheet_v1.png\nref: library/mascot/ref_v1.png\n"
        "seed: null\nmodel: null\nstyle_version: 1\n",
        encoding="utf-8",
    )
    set_mascot_approval(tmp_path, seed=9, model=KLEIN_9B)
    text = path.read_text(encoding="utf-8")
    assert "name: \"Bob\"  # my mascot" in text
    assert (load_mascot(tmp_path).seed, load_mascot(tmp_path).model) == (9, KLEIN_9B)


def test_a_mascot_candidate_made_with_a_previous_anchor_is_refused(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "anchor", 1, (1024, 768))
    saved(store, "anchor", 2, (1024, 768), color="ivory")
    approve_anchor(store, 1, ref_max_side=512)
    saved(store, "mascot", 1, (768, 1024))  # made with anchor c1
    approve_anchor(store, 2, ref_max_side=512)
    mascot = load_mascot(tmp_path)
    with pytest.raises(ApprovalError, match="made with a previous anchor"):
        approve_mascot(store, 1, mascot=mascot, ref_max_side=512)
    assert not (tmp_path / mascot.sheet).exists() and not (tmp_path / mascot.ref).exists()
    assert not (tmp_path / "config" / "mascot.yaml").exists()
    saved(store, "mascot", 2, (768, 1024))  # made with anchor c2
    approve_mascot(store, 2, mascot=mascot, ref_max_side=512)
    assert load_mascot(tmp_path).seed == 502


def test_an_invalid_mascot_yaml_means_nothing_is_written(tmp_path):
    store = BootstrapStore.load(tmp_path, 1)
    saved(store, "anchor", 1, (1024, 768))
    approve_anchor(store, 1, ref_max_side=512)
    saved(store, "mascot", 1, (768, 1024))
    mascot = load_mascot(tmp_path)
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "mascot.yaml"
    before = default_config_text("mascot.yaml").replace("figures: 1", "figures: 0")
    path.write_text(before, encoding="utf-8")
    with pytest.raises(ApprovalError, match="would not be valid"):
        approve_mascot(store, 1, mascot=mascot, ref_max_side=512)
    assert not (tmp_path / mascot.sheet).exists() and not (tmp_path / mascot.ref).exists()
    assert path.read_text(encoding="utf-8") == before
    assert BootstrapStore.load(tmp_path, 1).state.mascot.approved is None
