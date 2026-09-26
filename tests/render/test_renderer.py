import asyncio
import io
import itertools
import json
import re
from collections import Counter

import pytest
from PIL import Image

from stickman.budget import Budget
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import MascotConfig, load_mascot, load_style, load_visual_rules
from stickman.ledger import Ledger
from stickman.meter import Meter
from stickman.plan.llm import StageRunner
from stickman.plan.models import parse_plan
from stickman.plan.planner import load_planning_context
from stickman.plan.store import to_document, write_plan
from stickman.pricing import load_pricing
from stickman.render.images import image_stem
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.recovery import ExpectedUnit, recover
from stickman.render.renderer import GuardedChat, Renderer, RunControl, StopReason
from stickman.render.rewrite import SOFTEN_PREFIX, PlanRewriter, RewriteFailed
from stickman.render.state import StateStore, needs_work
from stickman.render.summary import review_reason
from stickman.runlog import RunLog
from stickman.settings import BudgetSettings, LLMSettings, QCSettings, RetrySettings, Settings

FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
VISION = "@cf/qwen/qwen3.8-27b"
IMAGE_USD = 207.59 * 0.011 / 1000
TRANSIENT = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)


async def no_sleep(seconds):
    return None


async def slow(data, seconds=0.01):
    await asyncio.sleep(seconds)
    return data


class Run:
    """A renderer over plan_data's three units: 001, 002a and 002b."""

    def __init__(self, workspace, plan_data, client, *, mascot=None, budget=None, concurrency=4, retry=None,
                 qc=None, rewriter=None, control=None):
        self.project = workspace / "projects" / FOLDER
        self.project.mkdir(parents=True, exist_ok=True)
        ctx = RenderContext(workspace, Settings(), mascot or load_mascot(workspace), (), load_pricing(workspace),
                            load_style(workspace), load_visual_rules(workspace))
        plan = parse_plan(plan_data)
        self.builder = JobBuilder(ctx, plan, seeds=itertools.count(1000).__next__)
        self.jobs = self.builder.jobs(plan.units())
        self.store = StateStore.load(self.project)
        self.ledger = Ledger(workspace / "ledger.jsonl")
        self.meter = Meter(project=FOLDER, ledger=self.ledger, budget=budget, pricing=ctx.pricing)
        self.log_path = self.project / "logs" / "run.jsonl"
        self.client = client
        self.qc = qc or QCSettings()
        self.retry = retry or RetrySettings()
        self.concurrency = concurrency
        self.rewriter = rewriter
        self.renderer = self.make_renderer(client, control)

    def make_renderer(self, client, control=None):
        return Renderer(client, self.store, self.meter, self.builder, qc=self.qc, vision_model=VISION,
                        retry=self.retry, concurrency=self.concurrency,
                        log=RunLog(self.log_path, secrets=("tok-secret",)), rewriter=self.rewriter, sleep=no_sleep,
                        control=control)

    def go(self, jobs=None):
        return asyncio.run(self.renderer.run(self.jobs if jobs is None else jobs))

    def statuses(self):
        return {job.unit_id: self.store.unit(job.unit_id).status for job in self.jobs}

    def log(self):
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]


def jpeg_of(image):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def prompt_text(messages):
    return messages[0]["content"][0]["text"]


def scripted(vision, per_unit):
    """Vision replies per unit, one per check in order (a dict of report changes each); then passing."""
    left = {unit: list(replies) for unit, replies in per_unit.items()}

    def reply(model, messages):
        unit = re.search(r"Expected: Idea (\w+)\.", prompt_text(messages)).group(1)
        changes = left.get(unit, [])
        extra = changes.pop(0) if changes else {}
        return vision.reply(**{"character_count": vision.figures(messages), **extra})

    return reply


class FakeRewriter:
    """Plays PlanRewriter: `saved` is what plan.yaml holds after each unit's rewrite, written only once
    the renderer's check of the new unit passed."""

    def __init__(self, plan_data):
        self.units = {unit.id: unit for unit in parse_plan(plan_data).units()}
        self.calls = []
        self.saved = {}

    async def soften(self, unit_id, notes, check=None):
        self.calls.append(("soften", unit_id, notes))
        return self.write(self.units[unit_id].model_copy(update={
            "softened": True, "softened_reason": SOFTEN_PREFIX + "symbolic", "image_prompt": f"softened prompt for {unit_id}"}),
            check)

    async def redesign(self, unit_id, notes, check=None):
        self.calls.append(("redesign", unit_id, notes))
        return self.write(self.units[unit_id].model_copy(update={"image_prompt": f"redesigned prompt for {unit_id}"}), check)

    def write(self, unit, check):
        if check is not None:
            check(unit)
        self.saved[unit.id] = unit
        return unit


def test_every_unit_gets_one_version_its_history_file_and_its_current_image(tmp_path, plan_data, fake_images):
    run = Run(tmp_path, plan_data, fake_images())
    assert run.go().stop is None
    assert run.statuses() == {"001": "generated", "002a": "generated", "002b": "generated"}
    unit = run.store.unit("002a")
    [version] = unit.versions
    assert (unit.current_version, version.v, version.file) == (1, 1, "images/_history/002a_v1.png")
    assert (version.seed, version.model, version.width, version.height) == (1001, KLEIN_4B, 960, 544)
    assert (version.prompt_sent, version.refs, version.fingerprint) == ("prompt for 002a", [], run.jobs[1].fingerprint)
    assert version.est_cost_usd == pytest.approx(IMAGE_USD)
    history = (run.project / version.file).read_bytes()
    assert history.startswith(b"\x89PNG")
    assert (run.project / "images" / "002a_00-04.0.png").read_bytes() == history
    assert StateStore.load(run.project).state.units["002a"].versions == unit.versions
    assert version.qc.passed and version.qc.reason is None
    entries = list(run.ledger.entries())
    assert Counter(e.kind for e in entries) == {"image": 3, "vision": 3}
    assert {(e.kind, e.billing, e.neurons) for e in entries} == {("image", "billed", 207.59), ("vision", "billed", 110.0)}
    assert sorted(e.unit for e in entries if e.kind == "image") == ["001", "002a", "002b"]


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
    first, second = [e for e in run.log() if e["kind"] == "image"]
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
    # 002a's image request was already out, so it finishes and its image is kept; its check is a new request.
    assert run.statuses() == {"001": "planned", "002a": "planned", "002b": "planned"}
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "prompt for 002a"]
    assert [v.qc for v in run.store.unit("002a").versions] == [None]


def test_a_rejected_token_stops_the_run(tmp_path, plan_data, fake_images):
    client = fake_images([CFError(ErrorCategory.AUTH, "Authentication error", status=401)])
    run = Run(tmp_path, plan_data, client, concurrency=1)
    assert run.go().stop is StopReason.AUTH
    assert set(run.statuses().values()) == {"planned"}
    assert len(client.calls) == 1


@pytest.mark.parametrize("category", [ErrorCategory.BAD_REQUEST])
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
    assert set(run.statuses().values()) <= {"failed", "needs_review"}  # no rewriter, so a refused unit goes to review


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
    # 001's image fits the budget, but its check's reservation doesn't: the image is kept unchecked.
    assert run.statuses() == {"001": "planned", "002a": "planned", "002b": "planned"}
    assert [v.qc for v in run.store.unit("001").versions] == [None]
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


def test_an_unreadable_image_is_made_again_next_run(tmp_path, plan_data, fake_images):
    run = Run(tmp_path, plan_data, fake_images(), qc=QCSettings(vision=False))
    run.go(run.jobs[:1])
    unit = run.store.unit("001")
    unit.versions[0].qc = None  # it still needs its check
    run.store.save()
    (run.project / unit.versions[0].file).write_bytes(b"not a png any more")
    run.qc = QCSettings()
    asyncio.run(run.make_renderer(fake_images()).run(run.jobs[:1]))
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("failed", None)
    assert unit.error.startswith("bad_image: can't read images/_history/001_v1.png")
    assert not (run.project / "images" / "001_00-00.0.png").exists()
    later = fake_images()
    asyncio.run(run.make_renderer(later).run(run.jobs[:1]))
    assert len(later.calls) == 1  # a new image, not the same failing check
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("generated", 2)


def test_an_os_error_elsewhere_in_the_check_keeps_the_current_version(tmp_path, plan_data, fake_images, monkeypatch):
    run = Run(tmp_path, plan_data, fake_images(), qc=QCSettings(vision=False))
    run.go(run.jobs[:1])
    unit = run.store.unit("001")
    unit.versions[0].qc = None  # it still needs its check; its file reads fine
    run.store.save()

    def disk_full(image, settings):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("stickman.render.calls.pixel_check", disk_full)
    with pytest.raises(OSError, match="No space left"):
        asyncio.run(run.make_renderer(fake_images()).run(run.jobs[:1]))
    unit = StateStore.load(run.project).unit("001")
    assert unit.current_version == 1 and [v.v for v in unit.versions] == [1]
    assert (run.project / "images" / "001_00-00.0.png").exists()


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
    [check] = client.chat_calls
    assert len(check["messages"][0]["content"]) == 3
    assert "and the reference character (the second image)" in prompt_text(check["messages"])


def test_a_token_across_a_cut_is_masked_before_the_message_is_shortened(tmp_path, plan_data, fake_images, jpeg):
    # The token crosses character 200 (ERROR_CHARS, state.json) and again character 300 (the run log).
    message = "x" * 195 + "tok-secret" + "y" * 90 + "tok-secret" + "z" * 20
    rejected = CFError(ErrorCategory.BAD_REQUEST, message, status=400)
    run = Run(tmp_path, plan_data, fake_images(lambda call: rejected if call["prompt"] == "prompt for 001" else jpeg))
    run.go()
    assert run.store.unit("001").error.startswith("bad_request: " + "x" * 195 + "***")
    assert "tok-s" not in (run.project / "state.json").read_text(encoding="utf-8")
    assert "tok-s" not in run.log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("category", [ErrorCategory.DAILY_LIMIT, ErrorCategory.AUTH])
def test_the_stop_detail_is_masked(tmp_path, plan_data, fake_images, category):
    run = Run(tmp_path, plan_data, fake_images([CFError(category, "refused for tok-secret", status=429)]), concurrency=1)
    result = run.go()
    assert result.stop is not None
    assert result.detail == "refused for ***"


def test_the_vision_check_sees_the_image_and_what_the_unit_should_show(tmp_path, plan_data, fake_images):
    client = fake_images()
    run = Run(tmp_path, plan_data, client)
    run.go(run.jobs[:1])
    [call] = client.chat_calls
    assert (call["model"], call["max_tokens"]) == (VISION, 2048)
    text, image = call["messages"][0]["content"]
    assert "Expected: Idea 001.\nExpected figures: 1 (Everyman: 1).\n" in text["text"]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    [entry] = [e for e in run.log() if e["kind"] == "vision"]
    assert (entry["ok"], entry["attempt"], entry["images"], entry["neurons"], entry["billing"]) == (True, 1, 1, 110.0, "billed")
    assert "base64" not in json.dumps(entry)
    unit = run.store.unit("001")
    assert unit.versions[0].qc.vision.matches_visual_idea == 4


def test_a_group_unit_passes_with_one_member_of_the_group(tmp_path, plan_data, fake_images, vision):
    plan_data["scenes"][1]["units"][1]["characters"] = [{"ref": "caveman_group", "action": "sitting", "emotion": "calm"}]
    client = fake_images(chat=scripted(vision, {"002b": [{"character_count": 1}]}))
    run = Run(tmp_path, plan_data, client)
    run.go(run.jobs[2:])
    [call] = client.chat_calls
    assert "\nExpected figures: 1-3 (Caveman group: 1-3).\n" in prompt_text(call["messages"])
    [version] = run.store.unit("002b").versions
    assert (version.qc.passed, version.qc.expected_figures, version.qc.expected_min_figures) == (True, 3, 1)


def test_a_text_failure_is_retried_with_the_strict_clause_first_and_a_new_seed(tmp_path, plan_data, fake_images, vision):
    client = fake_images(chat=scripted(vision, {"001": [{"has_text": True, "text_seen": "ZZZ"}]}))
    run = Run(tmp_path, plan_data, client)
    run.go(run.jobs[:1])
    first, second = client.calls
    strict = load_style(tmp_path).strict_clause.strip()
    assert (first["prompt"], first["seed"]) == ("prompt for 001", 1000)
    assert second["prompt"].startswith(strict + "\n\nprompt for 001") and second["seed"] == 1003
    unit = run.store.unit("001")
    v1, v2 = unit.versions
    assert (v1.qc.reason, v2.retry_of, v2.retry_reason, v2.qc.passed) == ("text", 1, "text", True)
    assert (unit.status, unit.current_version, unit.error) == ("generated", 2, None)
    current = (run.project / "images" / "001_00-00.0.png").read_bytes()
    assert current == (run.project / v2.file).read_bytes()


def test_after_qc_max_retries_the_best_version_is_kept_for_review(tmp_path, plan_data, fake_images, vision):
    replies = [{"has_text": True, "matches_visual_idea": 3}, {"has_text": True, "matches_visual_idea": 5},
               {"has_text": True, "matches_visual_idea": 4}]
    client = fake_images(chat=scripted(vision, {"001": replies}))
    run = Run(tmp_path, plan_data, client)
    run.go(run.jobs[:1])
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version, len(unit.versions), len(client.calls)) == ("needs_review", 2, 3, 3)
    assert client.calls[2]["prompt"].count(load_style(tmp_path).strict_clause.strip()) == 1  # once, however many text retries
    assert review_reason(unit) == "text"
    assert (run.project / "images" / "001_00-00.0.png").read_bytes() == (run.project / unit.versions[1].file).read_bytes()


def test_an_empty_image_skips_the_vision_check_and_gets_only_a_new_seed(tmp_path, plan_data, fake_images, drawings, jpeg):
    client = fake_images([jpeg_of(drawings.white()), jpeg])
    run = Run(tmp_path, plan_data, client)
    run.go(run.jobs[:1])
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "prompt for 001"]
    assert client.calls[0]["seed"] != client.calls[1]["seed"]
    assert len(client.chat_calls) == 1  # only the second image was worth a vision call
    v1 = run.store.unit("001").versions[0]
    assert (v1.qc.reason, v1.qc.vision) == ("empty", None)
    assert run.renderer.softened == [] and run.store.unit("001").status == "generated"


def test_a_dark_image_is_softened_once_then_left_for_review(tmp_path, plan_data, fake_images, drawings):
    black = jpeg_of(drawings.all_black())
    rewriter = FakeRewriter(plan_data)
    client = fake_images(lambda call: black)
    run = Run(tmp_path, plan_data, client, rewriter=rewriter)
    run.go(run.jobs[:1])
    assert rewriter.calls == [("soften", "001", "")]
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "softened prompt for 001"]
    unit = run.store.unit("001")
    v1, v2 = unit.versions
    assert (v2.retry_of, v2.retry_reason) == (1, "safety_filtered")
    assert v1.fingerprint != v2.fingerprint  # the soften changed the unit
    assert (unit.status, unit.current_version) == ("needs_review", 2)
    assert run.renderer.softened == ["001"]


def test_a_softened_image_that_comes_out_clean_passes(tmp_path, plan_data, fake_images, drawings, jpeg):
    client = fake_images([jpeg_of(drawings.all_black()), jpeg])
    run = Run(tmp_path, plan_data, client, rewriter=FakeRewriter(plan_data))
    run.go(run.jobs[:1])
    assert (run.store.unit("001").status, run.store.unit("001").current_version) == ("generated", 2)


def test_a_refused_request_is_softened_and_a_second_refusal_is_left_for_review(tmp_path, plan_data, fake_images):
    refused = CFError(ErrorCategory.REFUSED, "flagged by the safety system tok-secret", status=400)
    rewriter = FakeRewriter(plan_data)
    client = fake_images([refused, refused])
    run = Run(tmp_path, plan_data, client, rewriter=rewriter)
    assert run.go(run.jobs[:1]).stop is None
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "softened prompt for 001"]
    unit = run.store.unit("001")
    assert (unit.status, unit.versions, unit.error) == ("needs_review", [], "refused: flagged by the safety system ***")
    assert review_reason(unit) == "safety_filtered"


def test_a_locked_unit_is_never_rewritten(tmp_path, plan_data, fake_images, drawings):
    plan_data["scenes"][0]["units"][0]["prompt_locked"] = True
    rewriter = FakeRewriter(plan_data)
    client = fake_images(lambda call: jpeg_of(drawings.all_black()))
    run = Run(tmp_path, plan_data, client, rewriter=rewriter)
    run.go(run.jobs[:1])
    assert rewriter.calls == [] and len(client.calls) == 1
    assert run.store.unit("001").status == "needs_review"


def test_a_weak_idea_gets_a_new_seed_then_a_redesign(tmp_path, plan_data, fake_images, vision):
    weak = [{"matches_visual_idea": 2, "notes": "the fire is missing"}, {"matches_visual_idea": 2, "notes": "still no fire"}]
    rewriter = FakeRewriter(plan_data)
    client = fake_images(chat=scripted(vision, {"001": weak}))
    run = Run(tmp_path, plan_data, client, rewriter=rewriter)
    run.go(run.jobs[:1])
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "prompt for 001", "redesigned prompt for 001"]
    assert rewriter.calls == [("redesign", "001", "still no fire")]
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("generated", 3)


def test_a_checker_that_never_answers_in_json_leaves_the_unit_for_review(tmp_path, plan_data, fake_images):
    client = fake_images(chat=lambda model, messages: "It looks fine to me.")
    run = Run(tmp_path, plan_data, client)
    run.go(run.jobs[:1])
    assert (len(client.calls), len(client.chat_calls)) == (1, 2)
    retry = client.chat_calls[1]["messages"]
    assert retry[1] == {"role": "assistant", "content": "It looks fine to me."}
    assert "the reply contains no JSON object" in retry[2]["content"]
    unit = run.store.unit("001")
    qc = unit.versions[0].qc
    assert (unit.status, qc.reason, qc.vision_error) == ("needs_review", "vision_error",
                                                        "invalid reply: the reply contains no JSON object")


def test_a_rejected_check_is_a_vision_error_not_a_failed_unit(tmp_path, plan_data, fake_images):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "image too large", status=400)
    run = Run(tmp_path, plan_data, fake_images(chat=lambda model, messages: rejected))
    run.go(run.jobs[:1])
    assert run.store.unit("001").versions[0].qc.vision_error == "bad_request: image too large"
    assert run.store.unit("001").status == "needs_review"


def test_the_daily_limit_during_a_check_stops_the_run_and_the_next_run_checks_the_same_image(tmp_path, plan_data, fake_images):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    run = Run(tmp_path, plan_data, fake_images(chat=lambda model, messages: daily), concurrency=1)
    assert run.go(run.jobs[:1]).stop is StopReason.DAILY_LIMIT
    unit = run.store.unit("001")
    assert (unit.status, [v.qc for v in unit.versions]) == ("planned", [None])
    later = fake_images()
    asyncio.run(run.make_renderer(later).run(run.jobs[:1]))
    assert (later.calls, len(later.chat_calls)) == ([], 1)
    assert (run.store.unit("001").status, run.store.unit("001").current_version) == ("generated", 1)


def test_a_stop_mid_chain_continues_the_chain_next_run(tmp_path, plan_data, fake_images, vision, jpeg):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    client = fake_images([jpeg, daily], chat=scripted(vision, {"001": [{"has_text": True}]}))
    run = Run(tmp_path, plan_data, client, concurrency=1)
    assert run.go(run.jobs[:1]).stop is StopReason.DAILY_LIMIT
    assert run.store.unit("001").status == "planned"
    later = fake_images()
    asyncio.run(run.make_renderer(later).run(run.jobs[:1]))
    [retry] = later.calls
    assert retry["prompt"].startswith(load_style(tmp_path).strict_clause.strip())
    unit = run.store.unit("001")
    assert ([v.v for v in unit.versions], unit.versions[1].retry_of, unit.status) == ([1, 2], 1, "generated")


def test_temporary_errors_of_checks_count_toward_the_breaker(tmp_path, plan_data, fake_images):
    transient = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)
    run = Run(tmp_path, plan_data, fake_images(chat=lambda model, messages: transient), concurrency=1,
              retry=RetrySettings(transient_max=3, circuit_breaker=4))
    assert run.go().stop is StopReason.CIRCUIT_BREAKER


def test_a_failed_rewrite_leaves_the_unit_for_review_with_the_reason(tmp_path, plan_data, fake_images, drawings):
    class Broken(FakeRewriter):
        async def soften(self, unit_id, notes, check=None):
            raise RewriteFailed("plan.yaml changed on disk during the rewrite, so nothing was written")

    run = Run(tmp_path, plan_data, fake_images(lambda call: jpeg_of(drawings.all_black())), rewriter=Broken(plan_data))
    run.go(run.jobs[:1])
    unit = run.store.unit("001")
    assert unit.status == "needs_review" and unit.error.startswith("rewrite failed: plan.yaml changed on disk")


def test_an_image_made_before_qc_is_checked_without_a_new_request(tmp_path, plan_data, fake_images):
    run = Run(tmp_path, plan_data, fake_images(), qc=QCSettings(vision=False))
    run.go(run.jobs[:1])
    run.store.unit("001").versions[0].qc = None  # as M3 left it
    run.store.save()
    run.qc = QCSettings()  # the next run has the vision check on
    later = fake_images()
    asyncio.run(run.make_renderer(later).run(run.jobs[:1]))
    assert later.calls == [] and len(later.chat_calls) == 1
    assert run.store.unit("001").versions[0].qc.passed


def test_with_the_vision_check_off_only_the_pixel_checks_run(tmp_path, plan_data, fake_images):
    client = fake_images()
    run = Run(tmp_path, plan_data, client, qc=QCSettings(vision=False))
    run.go()
    assert client.chat_calls == [] and set(run.statuses().values()) == {"generated"}
    assert run.store.unit("001").versions[0].qc.vision is None


# --- The final review's fix wave (I1-I4, M2, M8) ---

UNREACHABLE = [CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502),
               CFError(ErrorCategory.RATE_LIMITED, "too many requests", status=429)]


@pytest.mark.parametrize("error", UNREACHABLE, ids=["transient", "rate_limited"])
def test_a_checker_that_cannot_be_reached_is_asked_again_next_run_about_the_same_image(
        tmp_path, plan_data, fake_images, error):
    client = fake_images(chat=lambda model, messages: error)
    run = Run(tmp_path, plan_data, client, concurrency=1)
    assert run.go(run.jobs[:1]).stop is None
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version, [v.qc for v in unit.versions]) == ("failed", 1, [None])
    assert unit.error == f"{error.category}: {error.message}"
    assert needs_work(unit) and len(client.calls) == 1
    later = fake_images()
    asyncio.run(run.make_renderer(later).run(run.jobs[:1]))
    assert (later.calls, len(later.chat_calls)) == ([], 1)
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version, unit.error, unit.versions[0].qc.passed) == ("generated", 1, None, True)


def test_a_vision_outage_trips_the_breaker_even_while_images_succeed(tmp_path, plan_data, fake_images):
    client = fake_images(chat=lambda model, messages: TRANSIENT)
    run = Run(tmp_path, plan_data, client, concurrency=1, retry=RetrySettings(transient_max=3, circuit_breaker=5))
    assert run.go().stop is StopReason.CIRCUIT_BREAKER
    # 001: 4 checks fail; 002a's image succeeds (images count on their own), then its check is the 5th in a row.
    assert (len(client.calls), len(client.chat_calls)) == (2, 5)
    assert run.statuses() == {"001": "failed", "002a": "planned", "002b": "planned"}
    assert [v.qc for v in run.store.unit("002a").versions] == [None]  # checked next run, not made again


def test_the_breaker_counts_each_kind_of_call_on_its_own():
    control = RunControl(3)
    for kind in ("vision", "vision", "image", "llm"):
        control.transient(kind)
    control.success("image")
    control.success("llm")
    assert control.reason is None
    control.transient("vision")
    assert control.reason is StopReason.CIRCUIT_BREAKER and "3 temporary errors in a row" in control.detail


def test_a_refusal_after_the_soften_leaves_no_old_design_current_and_is_not_stale_next_run(
        tmp_path, plan_data, fake_images, drawings):
    refused = CFError(ErrorCategory.REFUSED, "flagged tok-secret", status=400)
    rewriter = FakeRewriter(plan_data)
    run = Run(tmp_path, plan_data, fake_images([jpeg_of(drawings.all_black()), refused]), rewriter=rewriter)
    run.go(run.jobs[:1])
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version, [v.v for v in unit.versions]) == ("needs_review", None, [1])
    assert (unit.error, review_reason(unit)) == ("refused: flagged ***", "safety_filtered")
    assert (run.project / unit.versions[0].file).is_file()  # the old image stays in _history
    assert not (run.project / "images" / "001_00-00.0.png").exists()
    recover(run.store, {"001": ExpectedUnit(image_stem("001", 0.0), run.builder.fingerprint(rewriter.saved["001"]))})
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("needs_review", None)
    assert not (run.project / "images" / "001_00-00.0.png").exists()


@pytest.mark.parametrize(("outcomes", "error"), [
    ([CFError(ErrorCategory.BAD_REQUEST, "invalid tok-secret", status=400)], "bad_request: invalid ***"),
    ([TRANSIENT] * 4, "transient: bad gateway"),
    ([b"<html>oops</html>"], "bad_image: "),
], ids=["bad_request", "transient", "bad_bytes"])
def test_an_error_on_the_softened_image_fails_the_unit_and_the_next_run_renders_the_new_design(
        tmp_path, plan_data, fake_images, drawings, outcomes, error):
    rewriter = FakeRewriter(plan_data)
    run = Run(tmp_path, plan_data, fake_images([jpeg_of(drawings.all_black()), *outcomes]), rewriter=rewriter)
    run.go(run.jobs[:1])
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version, [v.v for v in unit.versions]) == ("failed", None, [1])
    assert unit.error.startswith(error)
    plan_now = rewriter.saved["001"]
    recover(run.store, {"001": ExpectedUnit(image_stem("001", 0.0), run.builder.fingerprint(plan_now))})
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("failed", None) and needs_work(unit)
    later = fake_images()
    asyncio.run(run.make_renderer(later).run([run.builder.job(plan_now)]))
    [call] = later.calls
    assert call["prompt"] == "softened prompt for 001"
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version, unit.versions[1].retry_of) == ("generated", 2, None)


def test_a_rewrite_whose_request_cannot_be_built_is_never_written(tmp_path, plan_data, fake_images, drawings):
    class NoPrompt(FakeRewriter):
        async def soften(self, unit_id, notes, check=None):
            self.calls.append(("soften", unit_id, notes))
            return self.write(self.units[unit_id].model_copy(update={
                "softened": True, "softened_reason": SOFTEN_PREFIX + "symbolic", "image_prompt": " "}), check)

    rewriter = NoPrompt(plan_data)
    run = Run(tmp_path, plan_data, fake_images(lambda call: jpeg_of(drawings.all_black())), rewriter=rewriter)
    run.go(run.jobs[:1])
    assert rewriter.calls == [("soften", "001", "")] and rewriter.saved == {}
    unit = run.store.unit("001")
    assert (unit.status, unit.current_version) == ("needs_review", 1)  # plan.yaml still holds v1's design
    assert unit.error.startswith("rewrite failed: no image_prompt for 001")


def test_no_rewrite_starts_once_the_run_is_stopping(tmp_path, plan_data, fake_images, drawings):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    black = jpeg_of(drawings.all_black())
    rewriter = FakeRewriter(plan_data)
    client = fake_images(lambda call: slow(black, 0.05) if call["prompt"] == "prompt for 001" else daily)
    run = Run(tmp_path, plan_data, client, concurrency=2, rewriter=rewriter)
    assert run.go(run.jobs[:2]).stop is StopReason.DAILY_LIMIT
    assert rewriter.calls == [] and len(client.calls) == 2
    unit = run.store.unit("001")
    assert (unit.status, [v.qc.reason for v in unit.versions]) == ("planned", ["safety_filtered"])


def test_temporary_errors_of_a_rewrite_count_toward_the_breaker(tmp_path, plan_data, fake_images, fake_chat, drawings):
    path = tmp_path / "projects" / FOLDER / "plan.yaml"
    path.parent.mkdir(parents=True)
    write_plan(path, to_document(parse_plan(plan_data)), expected_hash=None)
    before = path.read_bytes()
    retry = RetrySettings(transient_max=3, circuit_breaker=3)
    control = RunControl(retry.circuit_breaker)
    chat = fake_chat(lambda model, messages: TRANSIENT)
    runner = StageRunner(GuardedChat(chat, control), LLMSettings(), retry, cache_dir=None, log=RunLog(None), sleep=no_sleep)
    rewriter = PlanRewriter(runner, load_planning_context(tmp_path, Settings()), path)
    run = Run(tmp_path, plan_data, fake_images(lambda call: jpeg_of(drawings.all_black())), retry=retry,
              rewriter=rewriter, control=control)
    result = run.go(run.jobs[:1])
    assert result.stop is StopReason.CIRCUIT_BREAKER and "llm" in result.detail
    assert len(chat.calls) == 3  # the third temporary error in a row trips it, and no 4th attempt starts
    assert path.read_bytes() == before
    assert run.store.unit("001").status == "planned"


def test_with_no_qc_retries_a_refusal_is_not_softened(tmp_path, plan_data, fake_images):
    refused = CFError(ErrorCategory.REFUSED, "flagged", status=400)
    rewriter = FakeRewriter(plan_data)
    client = fake_images([refused])
    run = Run(tmp_path, plan_data, client, rewriter=rewriter, retry=RetrySettings(qc_max=0))
    run.go(run.jobs[:1])
    assert rewriter.calls == [] and len(client.calls) == 1
    unit = run.store.unit("001")
    assert (unit.status, review_reason(unit)) == ("needs_review", "safety_filtered")


def test_a_blurred_image_is_softened_like_a_dark_one(tmp_path, plan_data, fake_images, drawings, jpeg):
    rewriter = FakeRewriter(plan_data)
    client = fake_images([jpeg_of(drawings.blurred()), jpeg])
    run = Run(tmp_path, plan_data, client, rewriter=rewriter)
    run.go(run.jobs[:1])
    assert rewriter.calls == [("soften", "001", "")]
    assert [call["prompt"] for call in client.calls] == ["prompt for 001", "softened prompt for 001"]
    unit = run.store.unit("001")
    v1, v2 = unit.versions
    assert (v1.qc.reason, v1.qc.vision, v2.retry_reason) == ("safety_filtered", None, "safety_filtered")
    assert (unit.status, unit.current_version) == ("generated", 2)
    assert run.renderer.softened == ["001"]


def test_a_rewrites_llm_calls_are_ledgered_under_its_unit(tmp_path, plan_data, fake_images, drawings, jpeg):
    from stickman.cf.client import LLMResult

    async def reply():
        return LLMResult(text="{}", input_tokens=1, output_tokens=1, raw={}, neurons=2.0)

    class MeteredRewriter(FakeRewriter):
        async def soften(self, unit_id, notes, check=None):
            await run.meter.run(reply, kind="llm", model="@cf/openai/gpt-oss-120b", estimate_usd=0.0)  # no unit given
            return await super().soften(unit_id, notes, check)

    run = Run(tmp_path, plan_data, fake_images([jpeg_of(drawings.all_black()), jpeg]), rewriter=MeteredRewriter(plan_data))
    run.go(run.jobs[:1])
    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["kind"], e["unit"]) for e in entries if e["kind"] == "llm"] == [("llm", "001")]
