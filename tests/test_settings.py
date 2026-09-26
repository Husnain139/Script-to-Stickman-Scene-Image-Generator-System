import pytest
from ruamel.yaml import YAML

from stickman.settings import (
    DEFAULT_CONFIG_FILES,
    ConfigError,
    Settings,
    default_config_text,
    load_config,
    load_secrets,
    load_settings,
)


@pytest.fixture(autouse=True)
def _no_cf_env(monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_packaged_default_settings_match_model_defaults(tmp_path):
    path = write(tmp_path / "settings.yaml", default_config_text("settings.yaml"))
    assert load_settings(path).model_dump() == Settings().model_dump()


def test_defaults_target_the_free_plan_on_klein_4b():
    s = Settings()
    assert s.account.plan == "free"
    assert s.image.model == "@cf/black-forest-labs/flux-2-klein-4b"
    assert s.bootstrap.model == "@cf/black-forest-labs/flux-2-klein-9b"


def test_missing_settings_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.yaml") == Settings()


def test_partial_file_overrides_only_given_keys(tmp_path):
    s = load_settings(write(tmp_path / "s.yaml", "split:\n  split_seconds: 5\n"))
    assert s.split.split_seconds == 5.0
    assert s.split.split_words == 20
    assert s.image.sizes["16:9"] == (1920, 1088)


def test_max_parts_other_than_two_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="max_parts"):
        load_settings(write(tmp_path / "s.yaml", "split:\n  max_parts: 3\n"))


def test_review_host_must_be_loopback(tmp_path):
    with pytest.raises(ConfigError, match="127.0.0.1"):
        load_settings(write(tmp_path / "s.yaml", "review:\n  host: 0.0.0.0\n"))


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="split_secs"):
        load_settings(write(tmp_path / "s.yaml", "split:\n  split_secs: 5\n"))


def test_invalid_yaml_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_settings(write(tmp_path / "s.yaml", "split: [unclosed\n"))


def test_secrets_are_read_from_env_file(tmp_path):
    env = write(tmp_path / ".env", "CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n")
    secrets = load_secrets(env)
    assert secrets.cf_account_id == "acc123"
    assert secrets.cf_api_token.get_secret_value() == "tok-secret"
    assert "tok-secret" not in repr(secrets)


def test_missing_secret_is_named_in_the_error(tmp_path):
    env = write(tmp_path / ".env", "CF_ACCOUNT_ID=acc123\n")
    with pytest.raises(ConfigError, match="CF_API_TOKEN"):
        load_secrets(env)


def test_empty_secret_is_rejected(tmp_path):
    env = write(tmp_path / ".env", "CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=\n")
    with pytest.raises(ConfigError, match="CF_API_TOKEN"):
        load_secrets(env)


def test_load_config_without_secrets(tmp_path):
    cfg = load_config(tmp_path, need_secrets=False)
    assert cfg.secrets is None
    assert cfg.settings == Settings()


@pytest.mark.parametrize("name", DEFAULT_CONFIG_FILES)
def test_every_default_config_file_parses_with_schema_version(name):
    data = YAML(typ="safe").load(default_config_text(name))
    assert data["schema_version"] == 1


def test_a_settings_file_that_is_not_utf8_is_a_config_error(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_bytes("account:\n  plan: free  # café\n".encode("cp1252"))
    with pytest.raises(ConfigError, match="not UTF-8"):
        load_settings(path)


def test_the_vision_check_is_on_by_default_and_can_be_turned_off(tmp_path):
    assert Settings().qc.vision is True
    assert load_settings(write(tmp_path / "settings.yaml", "qc:\n  vision: false\n")).qc.vision is False


def test_bootstrap_makes_three_mascot_candidates_from_a_two_figure_anchor_by_default():
    settings = Settings()
    assert (settings.bootstrap.mascot_candidates, settings.bootstrap.anchor_scene) == (3, "two_figures")


def test_the_anchor_scene_is_one_of_two(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("bootstrap:\n  anchor_scene: one_figure\n", encoding="utf-8")
    assert load_settings(path).bootstrap.anchor_scene == "one_figure"
    path.write_text("bootstrap:\n  anchor_scene: three\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path)
