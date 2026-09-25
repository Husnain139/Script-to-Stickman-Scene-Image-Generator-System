import json

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, update_unit, write_plan
from stickman.render.state import StateStore

runner = CliRunner()
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


@pytest.fixture
def workspace(tmp_path, monkeypatch, plan_data):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(cli, "_wait", no_wait)
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    folder = tmp_path / "projects" / FOLDER
    folder.mkdir(parents=True)
    write_plan(folder / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    return tmp_path


def use_images(monkeypatch, client):
    monkeypatch.setattr(cli, "build_client", lambda cfg: client)
    return client


def generate(workspace, *args, command="generate"):
    return runner.invoke(cli.app, [command, "-w", str(workspace), *args])


def project(workspace):
    return workspace / "projects" / FOLDER


def statuses(workspace):
    data = json.loads((project(workspace) / "state.json").read_text(encoding="utf-8"))
    return {unit: entry["status"] for unit, entry in data["units"].items()}


def only_001(outcome, jpeg):
    return lambda call: outcome if call["prompt"] == "prompt for 001" else jpeg


def test_generate_makes_every_units_image_and_prints_the_summary(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"Project: {FOLDER}"
    assert f"Generating 3 unit(s) on {KLEIN_4B}" in result.output
    assert "so about 48 more image(s) fit" in result.output
    assert "Run finished · 3 units · done 3 · needs_review 0 · failed 0 · stale 0 · skipped 0" in result.output
    assert statuses(workspace) == {"001": "generated", "002a": "generated", "002b": "generated"}
    assert sorted(p.name for p in (project(workspace) / "images").glob("*.png")) == [
        "001_00-00.0.png", "002a_00-04.0.png", "002b_00-07.0.png"]
    entries = [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["kind"], e["billing"], e["project"]) for e in entries] == [("image", "billed", FOLDER)] * 3
    assert list((project(workspace) / "logs").glob("run-*.jsonl"))
    assert not (project(workspace) / ".lock").exists()


def test_a_daily_limit_pauses_and_resume_continues(workspace, monkeypatch, fake_images, jpeg):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    use_images(monkeypatch, fake_images(lambda call: jpeg if call["prompt"] == "prompt for 001" else daily))
    result = generate(workspace)
    assert result.exit_code == 2, result.output
    assert "Run paused (daily limit) · 3 units · done 1" in result.output
    assert "run `stickman resume` after the daily reset (00:00 UTC" in result.output
    resumed = use_images(monkeypatch, fake_images())
    result = generate(workspace, command="resume")
    assert result.exit_code == 0, result.output
    assert "done 3" in result.output
    assert sorted(call["prompt"] for call in resumed.calls) == ["prompt for 002a", "prompt for 002b"]


def test_limit_caps_one_run(workspace, monkeypatch, fake_images):
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace, "--limit", "1")
    assert result.exit_code == 0, result.output
    assert "done 1 · needs_review 0 · failed 0 · stale 0 · skipped 2" in result.output
    assert [call["prompt"] for call in client.calls] == ["prompt for 001"]


def test_the_weekly_budget_stops_the_run_and_force_goes_on(workspace, monkeypatch, fake_images):
    (workspace / "config").mkdir()
    (workspace / "config" / "settings.yaml").write_text("budget:\n  weekly_usd: 0.001\n", encoding="utf-8")
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 2, result.output
    assert "Weekly budget reached" in result.output
    assert "stickman resume --force" in result.output
    assert client.calls == []
    result = generate(workspace, "--force", command="resume")
    assert result.exit_code == 0, result.output
    assert "continuing because of --force" in result.output
    assert "done 3" in result.output


def test_five_temporary_errors_in_a_row_pause_the_run(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images(lambda call: CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)))
    result = generate(workspace)
    assert result.exit_code == 2, result.output
    assert cli.OUTAGE_MESSAGE in result.output


def test_a_rejected_token_exits_3(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images(lambda call: CFError(ErrorCategory.AUTH, "Authentication error", status=401)))
    result = generate(workspace)
    assert result.exit_code == 3, result.output
    assert "Cloudflare rejected the token" in result.output


def test_a_second_process_is_refused(workspace, monkeypatch, fake_images):
    monkeypatch.setattr("stickman.render.lock.pid_alive", lambda pid: True)
    (project(workspace) / ".lock").write_text("4242", encoding="ascii")
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 1, result.output
    assert "another stickman process (PID 4242)" in result.output
    lock = (project(workspace) / ".lock").resolve()
    assert f"If no stickman is running, delete `{lock}`." in result.output
    assert client.calls == []
    assert (project(workspace) / ".lock").read_text(encoding="ascii") == "4242"


def test_a_unit_left_generating_by_a_killed_run_is_generated_again(workspace, monkeypatch, fake_images):
    StateStore.load(project(workspace)).set_status("001", "generating")
    use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Reset 1 unit(s) a stopped run left generating: 001" in result.output
    assert statuses(workspace)["001"] == "generated"


def test_an_edited_unit_becomes_stale_and_is_not_regenerated(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    assert generate(workspace).exit_code == 0
    path = project(workspace) / "plan.yaml"
    loaded = load_plan(path)
    update_unit(loaded.doc, "001", {"props": ["a small campfire"]})
    write_plan(path, loaded.doc, expected_hash=loaded.hash)
    client = use_images(monkeypatch, fake_images())
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Stale, the plan changed after the image was made (not regenerated automatically): 001" in result.output
    assert "Nothing to generate" in result.output
    assert client.calls == []
    assert statuses(workspace)["001"] == "stale"


def test_errors_that_echo_the_token_are_masked(workspace, monkeypatch, fake_images, jpeg):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "invalid token tok-secret", status=400)
    use_images(monkeypatch, fake_images(only_001(rejected, jpeg)))
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Failed: 001 (bad_request: invalid token ***)" in result.output
    assert "tok-secret" not in result.output
    assert "tok-secret" not in (project(workspace) / "state.json").read_text(encoding="utf-8")


def test_generate_without_a_planned_project_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "console", Console(width=300))
    result = generate(tmp_path)
    assert result.exit_code == 1
    assert result.output.splitlines()[0] == "Project: (none found)"


def test_errors_that_echo_the_account_id_are_masked(workspace, monkeypatch, fake_images, jpeg):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "Could not route to /client/v4/accounts/acc123/ai/run", status=400)
    use_images(monkeypatch, fake_images(only_001(rejected, jpeg)))
    result = generate(workspace)
    assert result.exit_code == 0, result.output
    assert "Failed: 001 (bad_request: Could not route to /client/v4/accounts/***/ai/run)" in result.output
    assert "acc123" not in result.output
    assert "acc123" not in (project(workspace) / "state.json").read_text(encoding="utf-8")
    logs = "".join(p.read_text(encoding="utf-8") for p in (project(workspace) / "logs").glob("run-*.jsonl"))
    assert "accounts/***/ai/run" in logs
    assert "acc123" not in logs


def test_ctrl_c_stops_the_run_keeps_finished_images_and_releases_the_lock(workspace, monkeypatch, fake_images, jpeg):
    (workspace / "config").mkdir()
    (workspace / "config" / "settings.yaml").write_text("render:\n  concurrency: 1\n", encoding="utf-8")
    use_images(monkeypatch, fake_images([jpeg, KeyboardInterrupt()]))  # Ctrl+C during the second request
    result = generate(workspace)
    assert result.exit_code == 130, result.output
    assert "Stopped. Finished images are kept; run `stickman resume` to continue." in result.output
    assert not (project(workspace) / ".lock").exists()
    assert statuses(workspace)["001"] == "generated"
