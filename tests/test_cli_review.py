import socket

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan

runner = CliRunner()
FOLDER = "2026-09-25_demo"


@pytest.fixture
def workspace(tmp_path, monkeypatch, plan_data):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    folder = tmp_path / "projects" / FOLDER
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    return tmp_path


@pytest.fixture
def served(monkeypatch):
    calls = {"serve": [], "open": []}
    monkeypatch.setattr(cli, "_serve", lambda app, host, port: calls["serve"].append((app, host, port)))
    monkeypatch.setattr(cli, "_open_browser", lambda url: calls["open"].append(url))
    monkeypatch.setattr(cli, "_port_free", lambda host, port: True)
    return calls


def review(workspace, *args):
    return runner.invoke(cli.app, ["review", "-w", str(workspace), *args])


def test_review_serves_the_project_on_127_0_0_1_and_opens_the_browser(workspace, served):
    result = review(workspace)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"Project: {FOLDER}"
    assert "Review page: http://127.0.0.1:8765/ (Ctrl+C stops it)" in result.output
    [(app, host, port)] = served["serve"]
    assert (host, port) == ("127.0.0.1", 8765)
    assert served["open"] == ["http://127.0.0.1:8765/"]
    assert app.state.access.project_dir.name == FOLDER


def test_no_browser_opens_nothing(workspace, served):
    assert review(workspace, "--no-browser").exit_code == 0
    assert served["open"] == [] and len(served["serve"]) == 1


def test_without_credentials_the_page_still_opens_with_a_note(workspace, served):
    (workspace / ".env").unlink()
    result = review(workspace, "--no-browser")
    assert result.exit_code == 0, result.output
    assert "regenerating, replanning and making candidates need CF_ACCOUNT_ID and CF_API_TOKEN" in result.output


def test_a_port_in_use_exits_1_with_how_to_fix_it(workspace, monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        (workspace / "config").mkdir()
        (workspace / "config" / "settings.yaml").write_text(f"review:\n  port: {port}\n", encoding="utf-8")
        monkeypatch.setattr(cli, "_serve", lambda app, host, p: pytest.fail("must not serve"))
        result = review(workspace, "--no-browser")
    assert result.exit_code == 1
    assert f"Port {port} on 127.0.0.1 is in use" in result.output


def test_no_project_exits_1(tmp_path, served, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=300))
    result = runner.invoke(cli.app, ["review", "-w", str(tmp_path)])
    assert result.exit_code == 1 and result.output.splitlines()[0] == "Project: (none found)"
