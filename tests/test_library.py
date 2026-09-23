from datetime import date

import pytest

from stickman.library import load_library
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
