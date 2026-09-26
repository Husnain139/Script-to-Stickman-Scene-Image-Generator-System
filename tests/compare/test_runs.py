import asyncio
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from stickman.cf.errors import CFError, ErrorCategory
from stickman.compare.runs import prepare_run, render_runs
from stickman.compare.setup import ComparePick, CompareSetup, compare_dir, create_compare, default_runs, load_compare
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.plan.models import parse_plan
from stickman.plan.picking import pick_compare_units
from stickman.plan.store import to_document, write_plan
from stickman.render.jobs import RenderContext
from stickman.render.renderer import RunControl, StopReason
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))


async def no_sleep(seconds):
    return None


def make_compare(tmp_path, plan_data, built_prompts, bootstrapped, *, built=True):
    bootstrapped(tmp_path)
    source = tmp_path / "projects" / "2026-09-25_demo"
    source.mkdir(parents=True)
    data = built_prompts(plan_data) if built else plan_data
    write_plan(source / "plan.yaml", to_document(parse_plan(data)), expected_hash=None)
    plan = parse_plan(plan_data)
    seeds = iter(range(7000, 7100))
    picks = [ComparePick(category=p.category, unit=p.unit_id, filled=p.filled, seed=next(seeds))
             for p in pick_compare_units(plan, {"mascot": 1, "caveman_group": 3})]
    setup = CompareSetup(source=source.name, created=datetime(2026, 9, 26, 9, 0, tzinfo=PK), picks=picks,
                         runs=default_runs(Settings(), "16:9"))
    folder = compare_dir(tmp_path, source, date(2026, 9, 26))
    create_compare(folder, source, setup)
    return folder


def prepare_all(tmp_path, folder):
    ctx = RenderContext.load(tmp_path, Settings())
    setup, plan = load_compare(folder, library_ids=ctx.library_ids)
    return [prepare_run(folder, setup, plan, run, ctx) for run in setup.runs]


def render(tmp_path, prepared, client):
    meter = Meter(project="cmp", ledger=Ledger(tmp_path / "ledger.jsonl"))
    return asyncio.run(render_runs(client, prepared, meter, secrets=(), control=RunControl(5), sleep=no_sleep))


def statuses(folder, run_id):
    data = json.loads((folder / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
    return {unit: entry["status"] for unit, entry in data["units"].items()}


def test_each_run_renders_the_picked_units_in_its_own_folder(tmp_path, plan_data, built_prompts, bootstrapped, fake_images):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    client = fake_images()
    assert render(tmp_path, prepare_all(tmp_path, folder), client).stop is None
    for run_id in ("klein-4b-refs", "klein-4b-no-refs", "klein-4b-small-refs"):
        assert set(statuses(folder, run_id).values()) == {"generated"}
    assert Counter((c["width"], c["height"]) for c in client.calls) == {(1920, 1088): 6, (1280, 720): 3}
    assert Counter(len(c["input_images"]) for c in client.calls) == {2: 6, 0: 3}  # anchor + mascot sheet, or none


def test_each_runs_prompts_say_what_its_reference_images_are(tmp_path, plan_data, built_prompts, bootstrapped, fake_images):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    frozen = (folder / "source_plan.yaml").read_bytes()
    client = fake_images()
    render(tmp_path, prepare_all(tmp_path, folder), client)
    with_refs = [c["prompt"] for c in client.calls if c["input_images"]]
    without = [c["prompt"] for c in client.calls if not c["input_images"]]
    assert all("Reference images: image 0 shows" in p and "Image 1 shows Everyman:" in p for p in with_refs)
    assert not any("Reference images" in p for p in without)
    assert (folder / "source_plan.yaml").read_bytes() == frozen  # the frozen plan is never written


def test_there_are_no_qc_retries(tmp_path, plan_data, built_prompts, bootstrapped, fake_images, vision):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    client = fake_images(chat=lambda model, messages: vision.reply(has_text=True, character_count=vision.figures(messages)))
    render(tmp_path, prepare_all(tmp_path, folder), client)
    assert len(client.calls) == 9
    assert set(statuses(folder, "klein-4b-refs").values()) == {"needs_review"}


def test_a_paused_comparison_continues_where_it_stopped(tmp_path, plan_data, built_prompts, bootstrapped, fake_images, jpeg):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    outcomes = iter([jpeg] * 4 + [daily] * 20)
    first = fake_images(lambda call: next(outcomes))
    assert render(tmp_path, prepare_all(tmp_path, folder), first).stop is StopReason.DAILY_LIMIT
    later = fake_images()
    again = prepare_all(tmp_path, folder)
    assert render(tmp_path, again, later).stop is None
    total = sum(len(p.store.state.units[u].versions) for p in again for u in p.store.state.units)
    assert total == 9  # no lost and no duplicate images
    assert all(set(statuses(folder, run_id).values()) == {"generated"}
               for run_id in ("klein-4b-refs", "klein-4b-no-refs", "klein-4b-small-refs"))


def test_each_run_sends_a_picked_unit_with_its_picks_seed(tmp_path, plan_data, built_prompts, bootstrapped, fake_images):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped)
    client = fake_images()
    render(tmp_path, prepare_all(tmp_path, folder), client)
    setup, _ = load_compare(folder, library_ids=set())
    seeds = {pick.unit: pick.seed for pick in setup.picks}
    sent = {}
    for call in client.calls:
        unit = next(u for u in seeds if f"Idea {u}." in call["prompt"])
        sent.setdefault(unit, []).append(call["seed"])
    assert sent == {unit: [seed] * 3 for unit, seed in seeds.items()}


def test_hand_edited_prompts_are_noted_for_each_run_that_sends_references(
    tmp_path, plan_data, built_prompts, bootstrapped
):
    folder = make_compare(tmp_path, plan_data, built_prompts, bootstrapped, built=False)
    notes = {run.run.id: run.notes for run in prepare_all(tmp_path, folder)}
    assert notes["klein-4b-refs"] == ["hand-edited prompts sent as they are: 001, 002a, 002b"]
    assert notes["klein-4b-small-refs"] == ["hand-edited prompts sent as they are: 001, 002a, 002b"]
    assert notes["klein-4b-no-refs"] == []
