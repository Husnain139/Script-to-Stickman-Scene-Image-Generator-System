from datetime import date

import pytest

from stickman.config_files import load_mascot
from stickman.library import find_references, load_library
from stickman.plan.models import CastMember, MascotEntry
from stickman.prompt.builder import ReferenceAvailability
from stickman.settings import ConfigError

ENTRY = """schema_version: 1
id: cavemen_v1
name: "Caveman group"
figures: 3
description: "three cavemen in fur loincloths"
tags: [prehistoric, group, fire]
style_version: 1
model: "@cf/black-forest-labs/flux-2-klein-9b"
seed: 771203
sheet: sheet.png
ref: ref.png
approved: 2026-09-22
"""


def add_entry(workspace, folder="cavemen_v1", text=ENTRY):
    directory = workspace / "library" / "characters" / folder
    directory.mkdir(parents=True)
    (directory / "character.yaml").write_text(text, encoding="utf-8")
    return directory


def test_no_library_folder_means_no_characters(tmp_path):
    assert load_library(tmp_path) == []


def test_entries_are_loaded(tmp_path):
    add_entry(tmp_path)
    [entry] = load_library(tmp_path)
    assert (entry.id, entry.figures, entry.tags, entry.approved) == (
        "cavemen_v1", 3, ["prehistoric", "group", "fire"], date(2026, 9, 22)
    )


def test_entry_id_must_match_its_folder(tmp_path):
    add_entry(tmp_path, folder="other")
    with pytest.raises(ConfigError, match="other"):
        load_library(tmp_path)


def test_invalid_entry_is_a_config_error(tmp_path):
    add_entry(tmp_path, text="schema_version: 1\nid: cavemen_v1\n")
    with pytest.raises(ConfigError, match="character.yaml"):
        load_library(tmp_path)


CAST = [
    MascotEntry(id="mascot"),
    CastMember(id="caveman_group", name="Caveman group", figures=3, description="three cavemen", library_ref="cavemen_v1"),
    CastMember(id="historian", name="Historian", figures=1, description="a historian"),
]


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")


def references(workspace, *, style_version=1, use_references=True, seed=1234):
    mascot = load_mascot(workspace).model_copy(update={"seed": seed})
    return find_references(
        workspace, use_references=use_references, style_version=style_version,
        mascot=mascot, cast=CAST, library=load_library(workspace),
    )


def test_no_anchor_means_no_reference_images(tmp_path):
    assert references(tmp_path) == ReferenceAvailability()


def test_anchor_mascot_and_library_sheets_are_found(tmp_path):
    touch(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    touch(tmp_path / "library" / "mascot" / "ref_v1.png")
    add_entry(tmp_path)
    touch(tmp_path / "library" / "characters" / "cavemen_v1" / "ref.png")
    assert references(tmp_path) == ReferenceAvailability(anchor=True, sheets=frozenset({"mascot", "caveman_group"}))


def test_unapproved_mascot_and_old_style_sheets_are_skipped(tmp_path):
    touch(tmp_path / "library" / "style" / "anchor_v2_ref.png")
    touch(tmp_path / "library" / "mascot" / "ref_v1.png")
    add_entry(tmp_path)
    touch(tmp_path / "library" / "characters" / "cavemen_v1" / "ref.png")
    assert references(tmp_path, style_version=2) == ReferenceAvailability(anchor=True)
    touch(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    assert references(tmp_path, seed=None).sheets == frozenset({"caveman_group"})


def test_references_can_be_switched_off(tmp_path):
    touch(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    assert references(tmp_path, use_references=False) == ReferenceAvailability()


def test_an_entry_that_is_not_utf8_names_the_file(tmp_path):
    directory = add_entry(tmp_path)
    text = ENTRY.replace("three cavemen", "three cavemen, one café owner")
    (directory / "character.yaml").write_bytes(text.encode("cp1252"))
    with pytest.raises(ConfigError, match=r"character\.yaml.*UTF-8"):
        load_library(tmp_path)
