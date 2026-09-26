import pytest

from stickman.pricing import load_pricing
from stickman.runtime import build_client, check_usd, secrets_of
from stickman.settings import AppConfig, ConfigError, QCSettings, Secrets, Settings


def test_secrets_are_the_token_and_the_account_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acc123")
    monkeypatch.setenv("CF_API_TOKEN", "tok-secret")
    cfg = AppConfig(workspace=tmp_path, settings=Settings(), secrets=Secrets())
    assert secrets_of(cfg) == ("tok-secret", "acc123")
    assert secrets_of(AppConfig(workspace=tmp_path, settings=Settings(), secrets=None)) == ()


def test_a_client_needs_the_credentials(tmp_path):
    with pytest.raises(ConfigError):
        build_client(AppConfig(workspace=tmp_path, settings=Settings(), secrets=None))


def test_a_typical_check_costs_something_unless_the_vision_check_is_off(tmp_path):
    pricing = load_pricing(tmp_path)
    assert check_usd(Settings(), pricing) > 0
    assert check_usd(Settings(qc=QCSettings(vision=False)), pricing) == 0.0
