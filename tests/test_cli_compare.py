import json
from datetime import date

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan
from stickman.project import resolve_project

runner = CliRunner()
SOURCE = "2026-09-25_demo"
COMPARE = "2026-09-26_compare_demo"


@pytest.fixture
def workspace(tmp_path, monkeypatch, plan_data, built_prompts):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 9, 26))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(cli, "_wait", no_wait)
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    folder = tmp_path / "projects" / SOURCE
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(built_prompts(plan_data))), expected_hash=None)
    return tmp_path


def use_images(monkeypatch, client):
    monkeypatch.setattr(cli, "build_client", lambda cfg: client)
    return client


def compare(workspace, *args, input=None):
    return runner.invoke(cli.app, ["compare", "-w", str(workspace), *args], input=input)


def test_compare_needs_a_finished_bootstrap(workspace, monkeypatch, fake_images):
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace, "--yes")
    assert result.exit_code == 1, result.output
    assert "Bootstrap isn't complete: run `stickman bootstrap` first." in result.output
    assert client.calls == []


def test_compare_renders_every_run_and_writes_the_report(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace, "--yes")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:2] == [f"Project: {COMPARE}", f"Source: {SOURCE}"]
    assert "Compared units: 001 (mascot), 002a (extras, stand-in), 002b (night or fire, stand-in)" in result.output
    assert "Comparing 3 unit(s) × 3 run(s): 9 image(s) to make" in result.output
    assert len(client.calls) == 9
    assert "klein-4b-small-refs" in result.output and "Suggested for config/settings.yaml" in result.output
    report = workspace / "projects" / COMPARE / "export" / "compare.html"
    assert report.is_file() and f"Report: {report}" in result.output
    entries = [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {entry["project"] for entry in entries} == {COMPARE}
    assert resolve_project(workspace, None).name == SOURCE  # generate never takes the comparison for a project


def test_running_it_again_only_writes_the_report_again(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    use_images(monkeypatch, fake_images())
    compare(workspace, "--yes")
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace)  # nothing to make, so nothing to confirm
    assert result.exit_code == 0, result.output
    assert client.calls == [] and "Report:" in result.output


def test_new_starts_another_comparison(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    use_images(monkeypatch, fake_images())
    compare(workspace, "--yes")
    result = compare(workspace, "--new", "--yes")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"Project: {COMPARE}-2"


def test_a_folder_another_run_created_first_is_a_clean_error(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    real = cli.compare_dir

    def raced(*args):
        folder = real(*args)
        folder.mkdir(parents=True)  # a concurrent first run got there between the choice and the mkdir
        return folder

    monkeypatch.setattr(cli, "compare_dir", raced)
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace, "--yes")
    assert result.exit_code == 1, result.output
    assert f"Another stickman created {COMPARE} just now" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert client.calls == [] and list((workspace / "projects" / COMPARE).iterdir()) == []


def test_it_asks_before_spending(workspace, monkeypatch, fake_images, bootstrapped):
    bootstrapped(workspace)
    client = use_images(monkeypatch, fake_images())
    result = compare(workspace, input="n\n")
    assert result.exit_code == 0, result.output
    assert "Nothing was generated." in result.output and client.calls == []
    result = compare(workspace, input="y\n")
    assert result.exit_code == 0, result.output
    assert len(client.calls) == 9


def test_a_daily_limit_pauses_with_how_to_continue(workspace, monkeypatch, fake_images, bootstrapped, jpeg):
    bootstrapped(workspace)
    (workspace / "config" / "settings.yaml").write_text("render:\n  concurrency: 1\n", encoding="utf-8")
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    outcomes = iter([jpeg, jpeg])
    use_images(monkeypatch, fake_images(lambda call: next(outcomes, daily)))
    result = compare(workspace, "--yes")
    assert result.exit_code == 2, result.output
    assert f"run `stickman compare -p {SOURCE}` after the daily reset" in result.output
    assert (workspace / "projects" / COMPARE / "export" / "compare.html").is_file()
