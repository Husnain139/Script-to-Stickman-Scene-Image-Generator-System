import pytest

from stickman.config_files import TEXT_FREE, load_mascot, load_style, load_visual_rules
from stickman.settings import ConfigError


def test_packaged_defaults_are_used_when_config_is_missing(tmp_path):
    style = load_style(tmp_path)
    assert style.style_version == 1
    assert style.strict_clause.startswith("Absolutely no text of any kind")
    assert "exactly three short hair strokes" in load_mascot(tmp_path).identity
    assert len(load_visual_rules(tmp_path).rules) == 10


def test_workspace_config_overrides_the_defaults(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "visual_rules.yaml").write_text(
        'schema_version: 1\nrules: ["Only one rule."]\n', encoding="utf-8"
    )
    assert load_visual_rules(tmp_path).rules == ["Only one rule."]


def test_invalid_config_names_the_file(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "style.yaml").write_text("schema_version: 1\nstyle_version: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="style.yaml"):
        load_style(tmp_path)


def test_mascot_description_adds_the_optional_outfit(tmp_path):
    mascot = load_mascot(tmp_path)
    assert mascot.description.startswith(mascot.identity + ", usually wearing a small solid-black necktie")
    assert mascot.description.endswith("(these may be hidden or left out when the scene calls for it)")
    assert mascot.model_copy(update={"default_outfit": ""}).description == mascot.identity


def test_a_config_file_that_is_not_utf8_names_the_file(tmp_path):
    (tmp_path / "config").mkdir()
    rules = 'schema_version: 1\nrules: ["Café signs are blank."]\n'
    (tmp_path / "config" / "visual_rules.yaml").write_bytes(rules.encode("cp1252"))
    with pytest.raises(ConfigError, match=r"visual_rules\.yaml.*UTF-8"):
        load_visual_rules(tmp_path)


def test_a_config_file_that_cannot_be_read_names_the_file(tmp_path):
    (tmp_path / "config" / "style.yaml").mkdir(parents=True)  # a folder where the file should be
    with pytest.raises(ConfigError, match=r"style\.yaml"):
        load_style(tmp_path)


def test_the_text_free_table_defaults_to_the_packaged_one(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "visual_rules.yaml").write_text("schema_version: 1\nrules: [one rule]\n", encoding="utf-8")
    assert load_visual_rules(tmp_path).text_free == TEXT_FREE  # a config file from before M4
    assert load_visual_rules(tmp_path / "empty").text_free == TEXT_FREE  # the packaged defaults file
