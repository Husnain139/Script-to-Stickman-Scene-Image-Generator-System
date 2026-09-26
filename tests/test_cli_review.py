import signal
import socket
import threading
import time

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan
from stickman.review.app import create_app
from stickman.settings import Settings

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
    """A fake server that runs until the browser has opened (when one should: calls["browser"])."""
    calls = {"serve": [], "open": [], "browser": True}
    opened = threading.Event()

    def serve(app, host, port):
        calls["serve"].append((app, host, port))
        if calls["browser"]:
            opened.wait(5)

    def open_browser(url):
        calls["open"].append(url)
        opened.set()

    monkeypatch.setattr(cli, "_serve", serve)
    monkeypatch.setattr(cli, "_open_browser", open_browser)
    monkeypatch.setattr(cli, "_port_free", lambda host, port: True)
    monkeypatch.setattr(cli, "BROWSER_DELAY_S", 0.0)
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
    served["browser"] = False
    assert review(workspace, "--no-browser").exit_code == 0
    assert served["open"] == [] and len(served["serve"]) == 1


def test_without_credentials_the_page_still_opens_with_a_note(workspace, served):
    (workspace / ".env").unlink()
    served["browser"] = False
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


def test_the_browser_opens_only_once_the_server_has_had_time_to_listen(workspace, served, monkeypatch):
    monkeypatch.setattr(cli, "BROWSER_DELAY_S", 0.8)
    seen = []
    monkeypatch.setattr(cli, "_serve", lambda app, host, port: seen.append(list(served["open"])))
    assert review(workspace).exit_code == 0
    assert seen == [[]]  # nothing opened before the server started
    time.sleep(1.0)
    assert served["open"] == []  # and nothing after it stopped: a stopped server has no page to show


def test_a_project_outside_the_workspace_is_refused(workspace, served, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "proj"
    elsewhere.mkdir(parents=True)
    (elsewhere / "plan.yaml").write_bytes((workspace / "projects" / FOLDER / "plan.yaml").read_bytes())
    inside = workspace / "ws"
    inside.mkdir()
    result = runner.invoke(cli.app, ["review", "-w", str(inside), "-p", str(elsewhere), "--no-browser"])
    assert result.exit_code == 1, result.output
    assert result.output.splitlines()[0] == "Project: proj"
    assert "is outside the workspace" in result.output
    assert served["serve"] == []


def test_ctrl_c_gives_open_connections_two_seconds(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    cli._serve(object(), "127.0.0.1", 8765)
    assert seen["timeout_graceful_shutdown"] == 2
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 8765)


def test_ctrl_c_stops_a_real_server_while_the_page_holds_its_event_stream(tmp_path, plan_data, fake_images):
    import httpx
    import uvicorn

    project = tmp_path / "p"
    project.mkdir()
    write_plan(project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(tmp_path, project, Settings(), client_factory=lambda: fake_images(), watch=False,
                     allowed_hosts={f"127.0.0.1:{port}"})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, **cli.SERVE_OPTIONS))
    thread = threading.Thread(target=server.run, daemon=True)  # off the main thread: no signal handlers
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    with httpx.stream("GET", f"http://127.0.0.1:{port}/api/events", timeout=10) as stream:
        chunks = stream.iter_text()  # kept: a dropped iterator would close the connection
        assert next(chunks).startswith("retry:")
        server.handle_exit(signal.SIGINT, None)  # Ctrl+C, with the page still open
        thread.join(5)
        assert not thread.is_alive()
