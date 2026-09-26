import json
from collections import Counter

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import load_mascot

runner = CliRunner()
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(cli, "_wait", no_wait)
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    return tmp_path


def use_images(monkeypatch, client):
    monkeypatch.setattr(cli, "build_client", lambda cfg: client)
    return client


def bootstrap(workspace, *args):
    return runner.invoke(cli.app, ["bootstrap", "-w", str(workspace), *args])


def ledger(workspace):
    return [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_the_first_run_makes_four_anchor_candidates_and_waits_for_approval(workspace, monkeypatch, fake_images):
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 2, result.output
    assert result.output.splitlines()[0] == "Bootstrap: style v1"
    assert f"Style anchor: making 4 candidate(s) on {KLEIN_9B} at 1024x768, each checked by" in result.output
    assert "Style anchor candidates, best first:" in result.output
    assert "library/_bootstrap/v1/anchor/c1.png" in result.output
    assert "stickman bootstrap --approve-anchor <N>" in result.output
    assert len(client.calls) == 4 and all(call["input_images"] == [] for call in client.calls)
    assert Counter(e["kind"] for e in ledger(workspace)) == {"anchor": 4, "vision": 4}
    assert {e["project"] for e in ledger(workspace)} == {"bootstrap"}
    assert list((workspace / "library/_bootstrap/v1/logs").glob("run-*.jsonl"))
    assert not (workspace / "library/_bootstrap/v1/.lock").exists()


def test_a_second_run_adds_nothing_until_more_are_asked_for(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 2 and client.calls == []
    result = bootstrap(workspace, "--candidates", "6")
    assert len(client.calls) == 2 and "anchor/c6.png" in result.output


def test_the_whole_bootstrap_anchor_then_mascot(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    result = bootstrap(workspace, "--approve-anchor", "2")
    assert result.exit_code == 0, result.output
    assert ("Approved style anchor candidate c2: library/style/anchor_v1.png, library/style/anchor_v1_ref.png"
            in result.output)
    anchor = (workspace / "library/style/anchor_v1_ref.png").read_bytes()
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 2, result.output
    assert f"Mascot sheet: making 3 candidate(s) on {KLEIN_9B} at 768x1024" in result.output
    assert all(call["input_images"] == [anchor] for call in client.calls)
    assert Counter(e["kind"] for e in ledger(workspace))["sheet"] == 3
    result = bootstrap(workspace, "--approve-mascot", "1")
    assert result.exit_code == 0, result.output
    assert "Bootstrap is complete" in result.output
    assert load_mascot(workspace).seed is not None
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 0 and "Bootstrap is complete" in result.output and client.calls == []


def test_approving_needs_no_credentials(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    (workspace / ".env").unlink()
    result = bootstrap(workspace, "--approve-anchor", "1")
    assert result.exit_code == 0, result.output


def test_approving_an_unknown_candidate_exits_1(workspace):
    result = bootstrap(workspace, "--approve-anchor", "9")
    assert result.exit_code == 1
    assert "no anchor candidate 9 (candidates: none yet)" in result.output


def test_one_approval_at_a_time(workspace):
    result = bootstrap(workspace, "--approve-anchor", "1", "--approve-mascot", "1")
    assert result.exit_code == 1 and "one candidate at a time" in result.output


def test_a_failed_candidate_can_be_approved_with_a_warning(workspace, monkeypatch, fake_images, vision):
    use_images(monkeypatch, fake_images(chat=lambda model, messages: vision.reply(has_text=True, character_count=2)))
    bootstrap(workspace)
    result = bootstrap(workspace, "--approve-anchor", "1")
    assert result.exit_code == 0, result.output
    assert "c1 failed QC (text); approved anyway." in result.output


def test_a_daily_limit_pauses_with_how_to_continue(workspace, monkeypatch, fake_images, jpeg):
    (workspace / "config").mkdir()
    (workspace / "config" / "settings.yaml").write_text("render:\n  concurrency: 1\n", encoding="utf-8")
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    use_images(monkeypatch, fake_images([jpeg, daily]))
    result = bootstrap(workspace)
    assert result.exit_code == 2, result.output
    assert "run `stickman bootstrap` after the daily reset" in result.output
    assert "anchor/c1.png" in result.output
    client = use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    assert len(client.calls) == 3


def test_an_anchor_copied_into_style_refs_is_never_sent(workspace, monkeypatch, fake_images):
    use_images(monkeypatch, fake_images())
    bootstrap(workspace)
    bootstrap(workspace, "--approve-anchor", "1")
    stock = workspace / "style_refs" / "copy.png"
    stock.parent.mkdir()
    stock.write_bytes((workspace / "library/style/anchor_v1_ref.png").read_bytes())
    client = use_images(monkeypatch, fake_images())
    result = bootstrap(workspace)
    assert result.exit_code == 3 and "never sent to any API" in result.output
    assert client.calls == []


def test_no_secret_reaches_any_bootstrap_file(workspace, monkeypatch, fake_images):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "bad request for account acc123 with tok-secret", status=400)
    use_images(monkeypatch, fake_images(lambda call: rejected))
    result = bootstrap(workspace, "--candidates", "1")
    assert "tok-secret" not in result.output and "acc123" not in result.output
    for path in (workspace / "library").rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"tok-secret" not in data and b"acc123" not in data
