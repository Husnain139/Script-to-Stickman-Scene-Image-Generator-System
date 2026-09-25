import json
from datetime import date
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from stickman import cli
from stickman.cf.errors import CFError, ErrorCategory
from stickman.plan.store import load_plan, update_unit, write_plan

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
    logs = "".join(p.read_text(encoding="utf-8") for p in (folder(workspace) / "logs").glob("run-*.jsonl"))
    assert logs and "tok-secret" not in logs


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
