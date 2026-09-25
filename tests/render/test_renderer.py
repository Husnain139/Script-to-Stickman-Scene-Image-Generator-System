import asyncio
import itertools
import json

import pytest
from PIL import Image

from stickman.budget import Budget
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import MascotConfig, load_mascot
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.plan.models import parse_plan
from stickman.pricing import load_pricing
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.renderer import Renderer, StopReason
from stickman.render.state import StateStore
from stickman.runlog import RunLog
from stickman.settings import BudgetSettings, RetrySettings, Settings

FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
IMAGE_USD = 207.59 * 0.011 / 1000
TRANSIENT = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)


async def no_sleep(seconds):
    return None


async def slow(data, seconds=0.01):
    await asyncio.sleep(seconds)
    return data


class Run:
    """A renderer over plan_data's three units: 001, 002a and 002b."""

    def __init__(self, workspace, plan_data, client, *, mascot=None, budget=None, concurrency=4, retry=None):
        self.project = workspace / "projects" / FOLDER
        self.project.mkdir(parents=True, exist_ok=True)
        ctx = RenderContext(workspace, Settings(), mascot or load_mascot(workspace), (), load_pricing(workspace))
        plan = parse_plan(plan_data)
        self.jobs = JobBuilder(ctx, plan, seeds=itertools.count(1000).__next__).jobs(plan.units())
        self.store = StateStore.load(self.project)
        self.ledger = Ledger(workspace / "ledger.jsonl")
        self.meter = Meter(project=FOLDER, ledger=self.ledger, budget=budget, pricing=ctx.pricing)
        self.log_path = self.project / "logs" / "run.jsonl"
        self.client = client
        self.renderer = Renderer(client, self.store, self.meter, retry=retry or RetrySettings(), concurrency=concurrency,
                                 log=RunLog(self.log_path, secrets=("tok-secret",)), sleep=no_sleep)

    def go(self, jobs=None):
        return asyncio.run(self.renderer.run(self.jobs if jobs is None else jobs))

    def statuses(self):
        return {job.unit_id: self.store.unit(job.unit_id).status for job in self.jobs}

    def log(self):
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]


def test_every_unit_gets_one_version_its_history_file_and_its_current_image(tmp_path, plan_data, fake_images):
    run = Run(tmp_path, plan_data, fake_images())
    assert run.go().stop is None
    assert run.statuses() == {"001": "generated", "002a": "generated", "002b": "generated"}
    unit = run.store.unit("002a")
    [version] = unit.versions
    assert (unit.current_version, version.v, version.file) == (1, 1, "images/_history/002a_v1.png")
    assert (version.seed, version.model, version.width, version.height) == (1001, KLEIN_4B, 64, 36)
    assert (version.prompt_sent, version.refs, version.fingerprint) == ("prompt for 002a", [], run.jobs[1].fingerprint)
    assert version.est_cost_usd == pytest.approx(IMAGE_USD)
    history = (run.project / version.file).read_bytes()
    assert history.startswith(b"\x89PNG")
    assert (run.project / "images" / "002a_00-04.0.png").read_bytes() == history
    assert StateStore.load(run.project).state.units["002a"].versions == unit.versions
    entries = list(run.ledger.entries())
    assert sorted(e.unit for e in entries) == ["001", "002a", "002b"]
    assert {(e.kind, e.billing, e.neurons) for e in entries} == {("image", "billed", 207.59)}


def test_requests_carry_the_job_and_at_most_concurrency_run_at_once(tmp_path, plan_data, fake_images, jpeg):
    client = fake_images(lambda call: slow(jpeg))
    Run(tmp_path, plan_data, client, concurrency=2).go()
    assert client.max_active == 2
    first = next(call for call in client.calls if call["prompt"] == "prompt for 001")
    assert (first["model"], first["width"], first["height"], first["seed"], first["steps"], first["input_images"]) == (
        KLEIN_4B, 1920, 1088, 1000, None, [])


def test_generating_is_saved_before_the_request(tmp_path, plan_data, fake_images, jpeg):
    seen = []

    def outcome(call):
        state = json.loads((tmp_path / "projects" / FOLDER / "state.json").read_text(encoding="utf-8"))
        seen.append(state["units"]["001"]["status"])
        return jpeg

    run = Run(tmp_path, plan_data, fake_images(outcome))
    run.go(run.jobs[:1])
    assert seen == ["generating"]


def test_each_api_call_is_logged(tmp_path, plan_data, fake_images, jpeg):
    run = Run(tmp_path, plan_data, fake_images([TRANSIENT, jpeg]))
    run.go(run.jobs[:1])
    first, second = run.log()
    assert (first["ok"], first["error"], first["status"], first["billing"]) == (False, "transient", 502, "not_billed")
    assert (second["ok"], second["billing"], second["neurons"], second["request_id"]) == (True, "billed", 207.59, "req-2")
    assert all(e["kind"] == "image" and e["unit"] == "001" and e["seed"] == 1000 and e["prompt"] == "prompt for 001"
               for e in (first, second))
    assert run.statuses()["001"] == "generated"


def test_a_daily_limit_stops_new_requests_and_lets_running_ones_finish(tmp_path, plan_data, fake_images, jpeg):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    client = fake_images(lambda call: daily if call["prompt"] == "prompt for 001" else slow(jpeg))
    run = Run(tmp_path, plan_data, client, concurrency=2)
    assert run.go().stop is StopReason.DAILY_LIMIT
    assert run.statuses() == {"001": "planned", "002a": "generated", "002b": "planned"}
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "prompt for 002a"]


def test_a_rejected_token_stops_the_run(tmp_path, plan_data, fake_images):
    client = fake_images([CFError(ErrorCategory.AUTH, "Authentication error", status=401)])
    run = Run(tmp_path, plan_data, client, concurrency=1)
    assert run.go().stop is StopReason.AUTH
    assert set(run.statuses().values()) == {"planned"}
    assert len(client.calls) == 1


@pytest.mark.parametrize("category", [ErrorCategory.BAD_REQUEST, ErrorCategory.REFUSED])
def test_a_rejected_request_fails_only_its_unit(tmp_path, plan_data, fake_images, jpeg, category):
    rejected = CFError(category, "no: tok-secret", status=400)
    run = Run(tmp_path, plan_data, fake_images(lambda call: rejected if call["prompt"] == "prompt for 001" else jpeg))
    assert run.go().stop is None
    assert run.statuses() == {"001": "failed", "002a": "generated", "002b": "generated"}
    assert run.store.unit("001").error == f"{category}: no: ***"


def test_five_temporary_errors_in_a_row_pause_the_run(tmp_path, plan_data, fake_images):
    client = fake_images(lambda call: TRANSIENT)
    run = Run(tmp_path, plan_data, client, concurrency=1, retry=RetrySettings(transient_max=3, circuit_breaker=5))
    assert run.go().stop is StopReason.CIRCUIT_BREAKER
    assert run.statuses() == {"001": "failed", "002a": "planned", "002b": "planned"}
    assert len(client.calls) == 5  # 001: 1 try + 3 retries; 002a: the 5th error in a row
    assert run.store.unit("001").error == "transient: bad gateway"


def test_refusals_and_bad_requests_never_trip_the_breaker(tmp_path, plan_data, fake_images):
    errors = itertools.cycle([CFError(ErrorCategory.BAD_REQUEST, "invalid", status=400),
                              CFError(ErrorCategory.REFUSED, "flagged", status=400)])
    run = Run(tmp_path, plan_data, fake_images(lambda call: next(errors)), concurrency=1,
              retry=RetrySettings(circuit_breaker=1))
    assert run.go().stop is None
    assert set(run.statuses().values()) == {"failed"}


def test_a_success_resets_the_breaker(tmp_path, plan_data, fake_images, jpeg):
    client = fake_images([TRANSIENT, TRANSIENT, jpeg, TRANSIENT, TRANSIENT, jpeg, jpeg])
    run = Run(tmp_path, plan_data, client, concurrency=1, retry=RetrySettings(transient_max=3, circuit_breaker=3))
    assert run.go().stop is None
    assert set(run.statuses().values()) == {"generated"}


def test_the_weekly_budget_stops_new_requests(tmp_path, plan_data, fake_images):
    client = fake_images()
    run = Run(tmp_path, plan_data, client, concurrency=1, budget=Budget(BudgetSettings(weekly_usd=0.003), spent=0.0))
    result = run.go()
    assert result.stop is StopReason.BUDGET
    assert "over the weekly budget" in result.detail
    assert run.statuses() == {"001": "generated", "002a": "planned", "002b": "planned"}
    assert len(client.calls) == 1


def test_force_goes_past_the_budget_with_one_warning(tmp_path, plan_data, fake_images):
    warnings = []
    budget = Budget(BudgetSettings(weekly_usd=0.003), spent=0.0, force=True, warn=warnings.append)
    run = Run(tmp_path, plan_data, fake_images(), concurrency=1, budget=budget)
    assert run.go().stop is None
    assert set(run.statuses().values()) == {"generated"}
    assert len(warnings) == 1 and "--force" in warnings[0]


def test_timeouts_are_recorded_as_possibly_billed(tmp_path, plan_data, fake_images):
    timeout = CFError(ErrorCategory.TRANSIENT, "timeout", possibly_billed=True)
    run = Run(tmp_path, plan_data, fake_images(lambda call: timeout), concurrency=1,
              retry=RetrySettings(transient_max=1, circuit_breaker=10))
    run.go(run.jobs[:1])
    assert [e.billing for e in run.ledger.entries()] == ["possibly_billed", "possibly_billed"]
    assert run.meter.possibly_billed == 2
    assert run.meter.run_usd == pytest.approx(2 * run.jobs[0].estimate_usd)
    assert run.statuses()["001"] == "failed"


def test_bytes_that_are_not_an_image_fail_the_unit(tmp_path, plan_data, fake_images, jpeg):
    run = Run(tmp_path, plan_data, fake_images(lambda call: b"<html>oops</html>" if call["prompt"] == "prompt for 001" else jpeg))
    run.go()
    assert run.statuses() == {"001": "failed", "002a": "generated", "002b": "generated"}
    assert run.store.unit("001").error.startswith("bad_image: ")


def test_reference_images_are_sent_in_slot_order_and_recorded(tmp_path, plan_data, fake_images):
    for relative, size in (("library/style/anchor_v1_ref.png", (512, 384)), ("library/mascot/ref_v1.png", (384, 512))):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, "white").save(path, format="PNG")
    mascot = MascotConfig(name="Everyman", identity="a stickman with three hair strokes", sheet="library/mascot/sheet_v1.png",
                          ref="library/mascot/ref_v1.png", seed=7, style_version=1)
    client = fake_images()
    run = Run(tmp_path, plan_data, client, mascot=mascot)
    run.go(run.jobs[:1])
    anchor = (tmp_path / "library/style/anchor_v1_ref.png").read_bytes()
    sheet = (tmp_path / "library/mascot/ref_v1.png").read_bytes()
    assert client.calls[0]["input_images"] == [anchor, sheet]
    [version] = run.store.unit("001").versions
    assert [ref.split("#")[0] for ref in version.refs] == ["library/style/anchor_v1_ref.png", "library/mascot/ref_v1.png"]
    assert all(ref.split("#")[1].startswith("sha256:") for ref in version.refs)
    assert run.log()[0]["refs"] == version.refs
