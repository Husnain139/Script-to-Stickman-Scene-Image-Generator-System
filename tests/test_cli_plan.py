import json
from datetime import date
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.models import PlanValidationError
from stickman.plan.store import PlanChangedError, load_plan, update_unit, write_plan

runner = CliRunner()
SAMPLE = Path(__file__).parent / "fixtures" / "scripts" / "first-sleep.txt"
REPLANNED = {
    "id": "006a", "corrected_text": "Anthropologists studying the Ju/'hoansi in the Kalahari",
    "visual_idea": "An anthropologist sketches in a notebook", "visual_type": "literal", "shot": "close-up",
    "time_of_day": "day", "characters": [{"ref": "historian", "action": "sketching in a notebook", "emotion": "curious"}],
    "mood": None, "setting": [], "props": ["notebook with blank pages"],
    "composition": "notebook large in the foreground", "energy_marks": [], "softened": False, "softened_reason": None,
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "console", Console(width=300))  # no line wrapping in the captured output
    (tmp_path / ".env").write_text("CF_ACCOUNT_ID=acc123\nCF_API_TOKEN=tok-secret\n", encoding="utf-8")
    (tmp_path / "first-sleep.txt").write_bytes(SAMPLE.read_bytes())
    return tmp_path


def use_chat(monkeypatch, chat):
    monkeypatch.setattr(cli, "build_client", lambda cfg: chat)
    return chat


def new(workspace, *extra):
    return runner.invoke(cli.app, ["new", str(workspace / "first-sleep.txt"), "--aspect", "16:9", "-w", str(workspace), *extra])


def replan(workspace, *args):
    return runner.invoke(cli.app, ["replan", *args, "-w", str(workspace)])


def folder(workspace, slug="first-sleep"):
    return workspace / "projects" / f"{date.today().isoformat()}_{slug}"


def planned(workspace, monkeypatch, sample_chat):
    use_chat(monkeypatch, sample_chat)
    assert new(workspace).exit_code == 0
    return folder(workspace) / "plan.yaml"


def test_new_writes_a_valid_plan(workspace, monkeypatch, sample_chat):
    use_chat(monkeypatch, sample_chat)
    result = new(workspace)
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"Project: {folder(workspace).name}"
    assert "Planned 28 scenes -> 36 units (8 split)." in result.output
    plan = load_plan(folder(workspace) / "plan.yaml").plan
    assert (plan.project, plan.aspect, len(plan.units())) == ("first-sleep", "16:9", 36)
    assert (folder(workspace) / "script.txt").read_bytes() == SAMPLE.read_bytes()
    assert list((folder(workspace) / "logs").glob("run-*.jsonl"))


def test_the_run_log_masks_the_token(workspace, monkeypatch, fake_chat):
    use_chat(monkeypatch, fake_chat([CFError(ErrorCategory.BAD_REQUEST, "rejected request from tok-secret")]))
    assert new(workspace).exit_code == 1
    logs = "".join(p.read_text(encoding="utf-8") for p in (folder(workspace) / "logs").glob("run-*.jsonl"))
    assert "rejected request from ***" in logs
    assert "tok-secret" not in logs


def test_the_name_option_sets_the_project_folder(workspace, monkeypatch, sample_chat):
    use_chat(monkeypatch, sample_chat)
    assert new(workspace, "--name", "First Sleep v2").exit_code == 0
    assert (folder(workspace, "first-sleep-v2") / "plan.yaml").is_file()


def test_new_refuses_to_replace_an_existing_plan(workspace, monkeypatch, sample_chat):
    planned(workspace, monkeypatch, sample_chat)
    result = new(workspace)
    assert result.exit_code == 1
    assert "already has a plan.yaml" in result.output


def test_a_daily_limit_pauses_and_the_rerun_continues(workspace, monkeypatch, fake_chat, sample_reply):
    def limited(model, messages):
        if messages[0]["content"].startswith("You design one illustration"):
            raise CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation")
        return sample_reply(model, messages)

    use_chat(monkeypatch, fake_chat(limited))
    result = new(workspace)
    assert result.exit_code == 2
    assert "after the daily reset (00:00 UTC)" in result.output
    assert not (folder(workspace) / "plan.yaml").exists()
    resumed = use_chat(monkeypatch, fake_chat(sample_reply))
    assert new(workspace).exit_code == 0
    assert len(resumed.calls) == 5  # only the describe batches; analyse and cut came from the cache


DAY_1, DAY_2 = date(2026, 9, 23), date(2026, 9, 24)


def on_day(monkeypatch, day):
    monkeypatch.setattr(cli, "_today", lambda: day)


def daily_limit_at_describe(sample_reply):
    def limited(model, messages):
        if messages[0]["content"].startswith("You design one illustration"):
            raise CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation")
        return sample_reply(model, messages)

    return limited


def test_a_rerun_on_a_later_date_continues_in_the_unfinished_folder(workspace, monkeypatch, fake_chat, sample_reply):
    on_day(monkeypatch, DAY_1)
    use_chat(monkeypatch, fake_chat(daily_limit_at_describe(sample_reply)))
    assert new(workspace).exit_code == 2
    first = workspace / "projects" / "2026-09-23_first-sleep"
    assert not (first / "plan.yaml").exists()
    on_day(monkeypatch, DAY_2)
    resumed = use_chat(monkeypatch, fake_chat(sample_reply))
    result = new(workspace)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Project: 2026-09-23_first-sleep"
    assert lines[1].startswith("Planning continues in 2026-09-23_first-sleep")
    assert (first / "plan.yaml").is_file()
    assert not (workspace / "projects" / "2026-09-24_first-sleep").exists()
    systems = [call["messages"][0]["content"] for call in resumed.calls]
    assert len(systems) == 5  # the describe batches only: analyse and cut came from the cache
    assert all(system.startswith("You design one illustration") for system in systems)


def test_a_different_script_with_the_same_name_gets_a_new_folder(workspace, monkeypatch, fake_chat, sample_reply):
    on_day(monkeypatch, DAY_1)
    use_chat(monkeypatch, fake_chat(daily_limit_at_describe(sample_reply)))
    assert new(workspace).exit_code == 2
    script = workspace / "first-sleep.txt"
    script.write_text(script.read_text(encoding="utf-8").replace("Then we got fire", "Then we found fire"), encoding="utf-8")
    on_day(monkeypatch, DAY_2)
    use_chat(monkeypatch, fake_chat(daily_limit_at_describe(sample_reply)))
    result = new(workspace)
    assert result.output.splitlines()[0] == "Project: 2026-09-24_first-sleep"
    assert "Planning continues" not in result.output
    assert (workspace / "projects" / "2026-09-23_first-sleep" / "script.txt").read_bytes() == SAMPLE.read_bytes()


def test_a_planned_folder_is_refused_before_the_script_is_read(workspace, monkeypatch, sample_chat):
    planned(workspace, monkeypatch, sample_chat)

    def parse_script(text):
        raise AssertionError("the script was parsed")

    monkeypatch.setattr(cli, "parse_script", parse_script)
    result = new(workspace)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert result.output.splitlines()[0] == f"Project: {folder(workspace).name}"
    assert "already has a plan.yaml" in result.output


def test_a_planning_failure_exits_1_and_points_to_the_log(workspace, monkeypatch, fake_chat):
    use_chat(monkeypatch, fake_chat(lambda model, messages: "not json"))
    result = new(workspace)
    assert result.exit_code == 1
    assert "Planning failed" in result.output
    assert list((folder(workspace) / "logs").glob("run-*.jsonl"))


def test_a_bad_aspect_or_script_exits_1_without_calling_the_llm(workspace, monkeypatch, sample_chat):
    use_chat(monkeypatch, sample_chat)
    assert new(workspace, "--aspect", "4:3").exit_code == 1
    (workspace / "bad.txt").write_text("not a script line\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["new", str(workspace / "bad.txt"), "--aspect", "16:9", "-w", str(workspace)])
    assert result.exit_code == 1
    assert "Script error" in result.output
    assert sample_chat.calls == []


def test_missing_credentials_exit_3(workspace, monkeypatch, sample_chat):
    (workspace / ".env").unlink()
    use_chat(monkeypatch, sample_chat)
    assert new(workspace).exit_code == 3


def test_a_config_file_that_is_not_utf8_exits_3(workspace, monkeypatch, sample_chat):
    use_chat(monkeypatch, sample_chat)
    (workspace / "config").mkdir()
    rules = 'schema_version: 1\nrules: ["Café signs are blank."]\n'
    (workspace / "config" / "visual_rules.yaml").write_bytes(rules.encode("cp1252"))
    result = new(workspace)
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "visual_rules.yaml" in result.output
    assert sample_chat.calls == []


def test_replan_updates_one_unit_and_keeps_hand_edits(workspace, monkeypatch, sample_chat, fake_chat):
    path = planned(workspace, monkeypatch, sample_chat)
    text = path.read_text(encoding="utf-8")
    path.write_text("# my notes\n" + text.replace("visual_idea: Idea for 007", "visual_idea: My own idea for 007  # hand edit"), encoding="utf-8")
    chat = use_chat(monkeypatch, fake_chat([json.dumps({"units": [REPLANNED]})]))
    result = replan(workspace, "006a", "--hint", "show the notebook")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"Project: {folder(workspace).name}"
    out = path.read_text(encoding="utf-8")
    assert out.startswith("# my notes\n")
    assert "visual_idea: My own idea for 007  # hand edit" in out
    unit = next(u for u in load_plan(path).plan.units() if u.id == "006a")
    assert unit.visual_idea == "An anthropologist sketches in a notebook"
    assert "Scene: An anthropologist sketches in a notebook." in unit.image_prompt
    assert chat.calls[0]["messages"][1]["content"].endswith("HINT: show the notebook")


@pytest.mark.parametrize("unit_id", ["006a", "007"])
def test_replan_keeps_a_hand_edited_corrected_text(workspace, monkeypatch, sample_chat, fake_chat, unit_id):
    path = planned(workspace, monkeypatch, sample_chat)
    loaded = load_plan(path)
    update_unit(loaded.doc, unit_id, {"corrected_text": "My own caption"})
    write_plan(path, loaded.doc, expected_hash=loaded.hash)
    use_chat(monkeypatch, fake_chat([json.dumps({"units": [{**REPLANNED, "id": unit_id}]})]))
    result = replan(workspace, unit_id)
    assert result.exit_code == 0, result.output
    unit = next(u for u in load_plan(path).plan.units() if u.id == unit_id)
    assert (unit.corrected_text, unit.visual_idea) == ("My own caption", "An anthropologist sketches in a notebook")


def test_replan_refuses_when_the_plan_changed_meanwhile(workspace, monkeypatch, sample_chat, fake_chat):
    path = planned(workspace, monkeypatch, sample_chat)

    def edit_then_reply(model, messages):
        path.write_text(path.read_text(encoding="utf-8") + "# edited meanwhile\n", encoding="utf-8")
        return json.dumps({"units": [REPLANNED]})

    use_chat(monkeypatch, fake_chat(edit_then_reply))
    result = replan(workspace, "006a")
    assert result.exit_code == 1
    assert "changed on disk" in result.output
    assert path.read_text(encoding="utf-8").endswith("# edited meanwhile\n")


WRITE_ERRORS = [
    (PlanValidationError(["scenes[0].end: 1.0 must be after start 2.0"]), "plan.yaml was not written: the result failed validation"),
    (PermissionError(13, "Permission denied"), "Can't write "),
]


def failing_write(monkeypatch, error):
    def write_plan(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "write_plan", write_plan)


@pytest.mark.parametrize("error, message", WRITE_ERRORS)
def test_new_reports_a_failed_plan_write(workspace, monkeypatch, sample_chat, error, message):
    use_chat(monkeypatch, sample_chat)
    failing_write(monkeypatch, error)
    result = new(workspace)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # a message, not a traceback
    assert message in result.output and "plan.yaml" in result.output


@pytest.mark.parametrize(
    "error, message", [*WRITE_ERRORS, (PlanChangedError("plan.yaml changed"), "plan.yaml changed on disk")]
)
def test_replan_reports_a_failed_plan_write(workspace, monkeypatch, sample_chat, fake_chat, error, message):
    planned(workspace, monkeypatch, sample_chat)
    use_chat(monkeypatch, fake_chat([json.dumps({"units": [REPLANNED]})]))
    failing_write(monkeypatch, error)
    result = replan(workspace, "006a")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert message in result.output and "plan.yaml" in result.output


def test_replan_needs_a_project_and_a_known_unit(workspace, monkeypatch, sample_chat):
    no_project = replan(workspace, "006a")  # no projects yet
    assert no_project.exit_code == 1
    assert no_project.output.splitlines()[0] == "Project: (none found)"
    planned(workspace, monkeypatch, sample_chat)
    result = replan(workspace, "999")
    assert result.exit_code == 1
    assert "No unit '999'" in result.output


def test_replan_uses_the_named_project(workspace, monkeypatch, sample_chat, fake_chat):
    planned(workspace, monkeypatch, sample_chat)
    assert new(workspace, "--name", "second").exit_code == 0  # now the most recent project
    use_chat(monkeypatch, fake_chat([json.dumps({"units": [REPLANNED]})]))
    result = replan(workspace, "006a", "-p", folder(workspace).name)
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == f"Project: {folder(workspace).name}"


def test_new_records_its_planning_calls_in_the_ledger(workspace, monkeypatch, sample_chat):
    use_chat(monkeypatch, sample_chat)
    assert new(workspace).exit_code == 0
    entries = [json.loads(line) for line in (workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 7  # analyse, cut and 5 describe batches
    assert {(e["kind"], e["billing"], e["project"]) for e in entries} == {("llm", "billed", folder(workspace).name)}


def test_cloudflare_errors_on_the_console_mask_the_token(workspace, monkeypatch, fake_chat):
    use_chat(monkeypatch, fake_chat([CFError(ErrorCategory.BAD_REQUEST, "rejected request from tok-secret")]))
    result = new(workspace)
    assert result.exit_code == 1
    assert "rejected request from ***" in result.output
    assert "tok-secret" not in result.output
