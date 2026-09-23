import pytest
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.settings import DEFAULT_CONFIG_FILES

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_cf_env(monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.models = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def chat(self, model, messages, **kwargs):
        self.models.append(model)
        if self.error:
            raise self.error


def write_env(tmp_path):
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")


def test_help_lists_init():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output


def test_init_creates_config_and_folders_then_fails_without_secrets(tmp_path):
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 3
    assert "CF_ACCOUNT_ID" in result.output
    for name in DEFAULT_CONFIG_FILES:
        assert (tmp_path / "config" / name).exists()
    assert (tmp_path / "library").is_dir()
    assert (tmp_path / "projects").is_dir()
    assert "CF_API_TOKEN=" in (tmp_path / ".env.example").read_text(encoding="utf-8")


def test_init_keeps_existing_config(tmp_path):
    (tmp_path / "config").mkdir()
    custom = "schema_version: 1\nsplit:\n  split_seconds: 5\n"
    (tmp_path / "config" / "settings.yaml").write_text(custom, encoding="utf-8")
    runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert (tmp_path / "config" / "settings.yaml").read_text(encoding="utf-8") == custom


def test_init_verifies_token_with_fallback_model(tmp_path, monkeypatch):
    write_env(tmp_path)
    fake = FakeClient()
    monkeypatch.setattr(cli, "build_client", lambda cfg: fake)
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert fake.models == ["@cf/meta/llama-3.3-70b-instruct-fp8-fast"]
    assert "token works" in result.output


def test_init_rejected_token_exits_3(tmp_path, monkeypatch):
    write_env(tmp_path)
    fake = FakeClient(CFError(ErrorCategory.AUTH, "bad token", status=401))
    monkeypatch.setattr(cli, "build_client", lambda cfg: fake)
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 3
    assert "CF_API_TOKEN" in result.output  # single token: safe from rich line-wrapping


def test_init_other_api_error_exits_1(tmp_path, monkeypatch):
    write_env(tmp_path)
    fake = FakeClient(CFError(ErrorCategory.TRANSIENT, "timeout"))
    monkeypatch.setattr(cli, "build_client", lambda cfg: fake)
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path)])
    assert result.exit_code == 1


def test_init_skip_token_check(tmp_path, monkeypatch):
    write_env(tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda cfg: pytest.fail("must not call the API"))
    result = runner.invoke(cli.app, ["init", "-w", str(tmp_path), "--skip-token-check"])
    assert result.exit_code == 0
