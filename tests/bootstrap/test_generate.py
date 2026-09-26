import asyncio
import itertools
import json
from collections import Counter

import pytest
from PIL import Image

from stickman.bootstrap.generate import CandidateMaker, step_plan
from stickman.bootstrap.store import BootstrapStore
from stickman.budget import Budget
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import load_mascot, load_style
from stickman.ledger import Ledger
from stickman.library import anchor_ref_path
from stickman.meter import Meter
from stickman.pricing import load_pricing
from stickman.render.calls import Calls, RunControl, StopReason
from stickman.render.images import read_metadata
from stickman.render.references import ReferenceFiles
from stickman.runlog import RunLog
from stickman.settings import BudgetSettings, QCSettings, RetrySettings, Settings

KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
VISION = "@cf/qwen/qwen3.8-27b"
TRANSIENT = CFError(ErrorCategory.TRANSIENT, "bad gateway", status=502)


async def no_sleep(seconds):
    return None


class Setup:
    def __init__(self, tmp_path, client, *, retry=None, budget=None, concurrency=4, anchor=False):
        self.workspace = tmp_path
        if anchor:
            path = anchor_ref_path(tmp_path, 1)
            path.parent.mkdir(parents=True)
            Image.new("RGB", (512, 384), "white").save(path, format="PNG")
            self.anchor = path.read_bytes()
        self.client = client
        self.store = BootstrapStore.load(tmp_path, 1)
        retry = retry or RetrySettings()
        self.pricing = load_pricing(tmp_path)
        meter = Meter(project="bootstrap", ledger=Ledger(tmp_path / "ledger.jsonl"), budget=budget, pricing=self.pricing)
        self.calls = Calls(client, meter, RunLog(tmp_path / "run.jsonl"), RunControl(retry.circuit_breaker),
                           retry=retry, qc=QCSettings(), vision_model=VISION, sleep=no_sleep)
        self.maker = CandidateMaker(self.calls, self.store, concurrency=concurrency)

    def plan(self, step):
        return step_plan(step, workspace=self.workspace, settings=Settings(), style=load_style(self.workspace),
                         mascot=load_mascot(self.workspace), pricing=self.pricing,
                         files=ReferenceFiles(self.workspace, ref_max_side=512))

    def go(self, step, count):
        plan = self.plan(step)
        return asyncio.run(self.maker.run(plan, plan.jobs(self.store, count, seeds=itertools.count(100).__next__)))

    def ledger(self):
        return [json.loads(line) for line in (self.workspace / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_anchor_candidates_are_made_saved_and_checked(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images())
    assert run.go("anchor", 4).stop is None
    candidates = run.store.state.anchor.candidates
    assert [c.n for c in candidates] == [1, 2, 3, 4]
    assert all(c.qc is not None and c.qc.passed for c in candidates)
    assert [c.seed for c in candidates] == [100, 101, 102, 103]
    for c in candidates:
        assert read_metadata(tmp_path / c.file)["n"] == c.n
    assert {(call["model"], call["width"], call["height"]) for call in run.client.calls} == {(KLEIN_9B, 1024, 768)}
    assert all(call["input_images"] == [] for call in run.client.calls)
    assert all(call["prompt"].startswith(load_style(tmp_path).style_text.strip()) for call in run.client.calls)
    assert Counter(e["kind"] for e in run.ledger()) == {"anchor": 4, "vision": 4}
    assert sorted({e["unit"] for e in run.ledger()}) == ["anchor-c1", "anchor-c2", "anchor-c3", "anchor-c4"]
    assert BootstrapStore.load(tmp_path, 1).state.anchor.candidates == candidates


def test_mascot_candidates_are_made_with_the_anchor_in_slot_0(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images(), anchor=True)
    run.go("mascot", 3)
    assert {(call["width"], call["height"]) for call in run.client.calls} == {(768, 1024)}
    assert all(call["input_images"] == [run.anchor] for call in run.client.calls)
    assert all("Character sheet: a single full-body front view" in call["prompt"] for call in run.client.calls)
    assert Counter(e["kind"] for e in run.ledger()) == {"sheet": 3, "vision": 3}
    [first, *_] = run.store.state.mascot.candidates
    assert first.refs[0].startswith("library/style/anchor_v1_ref.png#sha256:")


def test_estimates_follow_the_klein_9b_formula(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images(), anchor=True)
    assert run.plan("anchor").estimate_usd == pytest.approx(0.015)
    assert run.plan("mascot").estimate_usd == pytest.approx(0.017)  # plus one reference image under 1 MP


def test_numbers_continue_after_earlier_candidates(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images())
    run.go("anchor", 2)
    run.go("anchor", 2)
    assert [c.n for c in run.store.state.anchor.candidates] == [1, 2, 3, 4]


def test_a_daily_limit_stops_new_candidates(tmp_path, fake_images, jpeg):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation", status=429)
    run = Setup(tmp_path, fake_images([jpeg, daily]), concurrency=1)
    result = run.go("anchor", 4)
    assert result.stop is StopReason.DAILY_LIMIT
    assert [c.n for c in run.store.state.anchor.candidates] == [1]
    assert len(run.client.calls) == 2


def test_a_rejected_candidate_is_skipped_and_the_others_go_on(tmp_path, fake_images, jpeg):
    rejected = CFError(ErrorCategory.BAD_REQUEST, "bad prompt tok", status=400)
    run = Setup(tmp_path, fake_images(lambda call: rejected if call["seed"] == 101 else jpeg))
    assert run.go("anchor", 3).stop is None
    assert [c.n for c in run.store.state.anchor.candidates] == [1, 3]
    assert run.maker.errors == {"anchor-c2": "bad_request: bad prompt tok"}


def test_a_checker_that_cannot_be_reached_is_asked_again_next_run(tmp_path, fake_images):
    run = Setup(tmp_path, fake_images(chat=lambda model, messages: TRANSIENT), retry=RetrySettings(transient_max=0))
    run.go("anchor", 2)
    assert [c.qc for c in run.store.state.anchor.candidates] == [None, None]
    assert run.maker.errors["anchor-c1"].startswith("transient: bad gateway")
    later = Setup(tmp_path, fake_images())
    assert later.go("anchor", 0).stop is None
    assert later.client.calls == [] and len(later.client.chat_calls) == 2
    assert all(c.qc.passed for c in later.store.state.anchor.candidates)


def test_the_weekly_budget_stops_before_any_call(tmp_path, fake_images):
    budget = Budget(BudgetSettings(weekly_usd=0.001), spent=0.0)
    run = Setup(tmp_path, fake_images(), budget=budget)
    assert run.go("anchor", 2).stop is StopReason.BUDGET
    assert run.client.calls == [] and run.store.state.anchor.candidates == []
