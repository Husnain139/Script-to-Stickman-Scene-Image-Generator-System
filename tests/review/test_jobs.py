import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from PIL import Image

from stickman.bootstrap.store import BootstrapStore, Candidate
from stickman.cf.errors import CFError, ErrorCategory
from stickman.config_files import load_mascot, load_style
from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.render.images import encode_png
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.state import StateStore, Version
from stickman.review.access import Busy, ProjectAccess
from stickman.review.actions import ActionError, approve_sheet
from stickman.review.events import EventHub
from stickman.review.jobs import Jobs
from stickman.settings import ConfigError, QCSettings, Settings

PK = timezone(timedelta(hours=5))
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


async def no_sleep(seconds):
    return None


class Page:
    """A project with an approved image for every unit, and the page's jobs over it."""

    def __init__(self, tmp_path, plan_data, drawings, client, *, factory=None):
        self.workspace = tmp_path
        self.project = tmp_path / "projects" / FOLDER
        self.project.mkdir(parents=True)
        write_plan(self.project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
        plan = parse_plan(plan_data)
        builder = JobBuilder(RenderContext.load(tmp_path, Settings()), plan)
        store = StateStore.load(self.project)
        image = drawings.clean()
        good = decide(pixel_check(image, QCSettings()), expected_figures=1, min_idea_score=3)
        for unit in plan.units():
            record = Version(v=1, file=f"images/_history/{unit.id}_v1.png", seed=1, model=KLEIN_4B, width=960,
                             height=544, fingerprint=builder.fingerprint(unit), prompt_sent=unit.image_prompt,
                             qc=good, est_cost_usd=0.002, latency_s=1.0, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
            (self.project / "images" / "_history").mkdir(parents=True, exist_ok=True)
            (self.project / record.file).write_bytes(encode_png(image, record.model_dump(mode="json")))
            store.add_version(unit.id, record, status="generated")
            store.approve(unit.id)
        self.client = client
        self.hub = EventHub()
        self.access = ProjectAccess(self.project)
        self.written = []
        self.jobs = Jobs(tmp_path, self.project, Settings(), self.access, self.hub,
                         client_factory=factory or (lambda: client), secrets=("tok-secret",),
                         on_plan_written=self.written.append, sleep=no_sleep)

    def run(self, start):
        """Start a job inside a running loop and wait for it, collecting the events it published."""
        async def go():
            queue = self.hub.subscribe()
            info = start()
            await self.jobs.wait()
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
            return info, events

        return asyncio.run(go())

    def unit(self, unit_id):
        return StateStore.load(self.project).unit(unit_id)


def job_events(events):
    return [(data["kind"], data["state"]) for kind, data in events if kind == "job"]


def test_regenerating_makes_a_new_image_beside_the_old_one(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    info, events = page.run(lambda: page.jobs.regenerate("001"))
    unit = page.unit("001")
    assert (info.kind, info.unit) == ("regenerate", "001")
    assert [v.v for v in unit.versions] == [1, 2]
    assert (unit.current_version, unit.compare_with, unit.approved_version, unit.status) == (2, 1, None, "generated")
    assert job_events(events) == [("regenerate", "started"), ("regenerate", "progress"), ("regenerate", "finished")]
    assert page.access.job is None and not (page.project / ".lock").exists()
    entries = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["kind"] for e in entries} == {"image", "vision"} and {e["unit"] for e in entries} == {"001"}


def test_one_page_job_at_a_time(tmp_path, plan_data, drawings, fake_images, jpeg):
    gate = asyncio.Event()

    async def slow(call):
        await gate.wait()
        return jpeg

    page = Page(tmp_path, plan_data, drawings, fake_images(lambda call: slow(call)))

    async def go():
        page.jobs.regenerate("001")
        with pytest.raises(Busy, match="already regenerating 001"):
            page.jobs.regenerate("002a")
        gate.set()
        await page.jobs.wait()

    asyncio.run(go())
    assert page.unit("002a").versions[-1].v == 1


def test_a_cli_run_holding_the_lock_makes_the_page_wait(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    (page.project / ".lock").write_text(str(os.getppid()), encoding="ascii")  # a live process that isn't us
    with pytest.raises(Busy, match="another stickman process"):
        page.jobs.regenerate("001")
    assert page.unit("001").status == "approved"


def test_without_credentials_a_regeneration_fails_before_changing_anything(tmp_path, plan_data, drawings, fake_images):
    def no_credentials():
        raise ConfigError("Cloudflare credentials are not loaded (CF_ACCOUNT_ID and CF_API_TOKEN in .env)")

    page = Page(tmp_path, plan_data, drawings, fake_images(), factory=no_credentials)
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "failed"]
    assert "CF_API_TOKEN" in data["message"]
    unit = page.unit("001")
    assert (unit.status, unit.compare_with, unit.approved_version) == ("approved", None, 1)


def test_a_daily_limit_pauses_the_job_with_a_masked_message(tmp_path, plan_data, drawings, fake_images):
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation for tok-secret", status=429)
    page = Page(tmp_path, plan_data, drawings, fake_images([daily]))
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "paused"]
    assert "daily limit" in data["message"] and "tok-secret" not in data["message"]
    assert "No new image was made" in data["message"]
    unit = page.unit("001")  # nothing new, so it keeps its approved image, still approved
    assert (unit.status, unit.approved_version, unit.current_version, unit.compare_with) == ("approved", 1, 1, None)


def test_replanning_with_a_hint_writes_plan_yaml_and_records_the_hash(tmp_path, plan_data, drawings, fake_images, sample_reply):
    client = fake_images(chat=sample_reply)  # the planner's describe replies (see conftest)
    page = Page(tmp_path, plan_data, drawings, client)
    _, events = page.run(lambda: page.jobs.replan("001", "show the moon"))
    loaded = load_plan(page.project / "plan.yaml")
    unit = next(u for u in loaded.plan.units() if u.id == "001")
    assert unit.visual_idea == "Idea for 001"
    assert page.written == [loaded.hash]
    assert job_events(events)[-1] == ("replan", "finished")
    [call] = client.chat_calls
    assert "show the moon" in call["messages"][1]["content"]


def test_more_anchor_candidates_are_made_under_the_bootstrap_lock(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    _, events = page.run(lambda: page.jobs.more_candidates("anchor"))
    store = BootstrapStore.load(tmp_path, 1)
    assert [c.n for c in store.state.anchor.candidates] == [1, 2]
    assert job_events(events)[0] == ("candidates", "started") and job_events(events)[-1] == ("candidates", "finished")
    assert not (tmp_path / "library" / "_bootstrap" / "v1" / ".lock").exists()
    assert page.access.job is None


def test_more_candidates_for_a_step_bootstrap_isnt_on_fails(tmp_path, plan_data, drawings, fake_images):
    page = Page(tmp_path, plan_data, drawings, fake_images())
    _, events = page.run(lambda: page.jobs.more_candidates("mascot"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "failed"]
    assert "anchor" in data["message"] and page.client.calls == []


def test_page_actions_use_the_jobs_store_while_it_runs(tmp_path, plan_data, drawings, fake_images, jpeg):
    gate = asyncio.Event()

    async def slow(call):
        await gate.wait()
        return jpeg

    page = Page(tmp_path, plan_data, drawings, fake_images(lambda call: slow(call)))

    async def go():
        page.jobs.regenerate("001")
        await asyncio.sleep(0)
        assert page.access.busy_unit == "001"
        with page.access.state() as store:
            assert store is page.access.store
            store.state.plan_approved = True
            store.save()
        gate.set()
        await page.jobs.wait()

    asyncio.run(go())
    assert StateStore.load(page.project).state.plan_approved  # not lost when the job saved after it


def test_the_server_never_takes_a_lock_it_already_holds(tmp_path):
    access = ProjectAccess(tmp_path)
    folder = tmp_path / "library" / "_bootstrap" / "v1"
    folder.mkdir(parents=True)
    access.hold(folder)
    with pytest.raises(Busy):  # ProjectLock alone would call its own PID's lock stale and take it
        access.hold(folder)
    with pytest.raises(Busy):
        with access.locked(folder):
            pass
    assert (folder / ".lock").read_text(encoding="ascii") == str(os.getpid())
    access.drop(folder)
    assert not (folder / ".lock").exists()
    with access.locked(folder):
        assert (folder / ".lock").exists()
    assert not (folder / ".lock").exists()


def anchor_candidate(workspace):
    store = BootstrapStore.load(workspace, 1)
    record = Candidate(n=1, file="library/_bootstrap/v1/anchor/c1.png", seed=5,
                       model="@cf/black-forest-labs/flux-2-klein-9b", width=1024, height=768, prompt_sent="p",
                       est_cost_usd=0.015, latency_s=3.0, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    path = workspace / record.file
    path.parent.mkdir(parents=True)
    path.write_bytes(encode_png(Image.new("RGB", (1024, 768), "white"), record.model_dump(mode="json")))
    store.add("anchor", record)


def test_approving_a_sheet_while_its_candidates_are_made_is_refused_and_keeps_bootstraps_lock(
    tmp_path, plan_data, drawings, fake_images, jpeg
):
    gate = asyncio.Event()

    async def slow(call):
        await gate.wait()
        return jpeg

    page = Page(tmp_path, plan_data, drawings, fake_images(lambda call: slow(call)))
    anchor_candidate(tmp_path)
    lock = tmp_path / "library" / "_bootstrap" / "v1" / ".lock"
    kwargs = dict(settings=Settings(), style=load_style(tmp_path), mascot=load_mascot(tmp_path), access=page.access)

    async def go():
        page.jobs.more_candidates("anchor")
        for _ in range(200):  # until the job holds bootstrap's lock and waits for its images
            if page.client.calls:
                break
            await asyncio.sleep(0)
        assert lock.exists()
        with pytest.raises(ActionError) as error:
            approve_sheet(tmp_path, "anchor", 1, **kwargs)
        assert error.value.status == 409
        assert error.value.message == "the page is making anchor candidates; wait for it to finish"
        assert lock.exists()  # still the job's
        gate.set()
        await page.jobs.wait()

    asyncio.run(go())
    assert not lock.exists()
    assert BootstrapStore.load(tmp_path, 1).state.anchor.approved is None
    approve_sheet(tmp_path, "anchor", 1, **kwargs)
    store = BootstrapStore.load(tmp_path, 1)
    assert store.state.anchor.approved == 1 and [c.n for c in store.state.anchor.candidates] == [1, 2, 3]


def test_a_regeneration_whose_first_image_fails_keeps_the_approval(tmp_path, plan_data, drawings, fake_images):
    bad = CFError(ErrorCategory.BAD_REQUEST, "the prompt was rejected", status=400)
    page = Page(tmp_path, plan_data, drawings, fake_images([bad]))
    copy_before = (page.project / "images" / "_history" / "001_v1.png").read_bytes()
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "finished"]
    assert "001 is failed" in data["message"] and "the prompt was rejected" in data["message"]
    assert "No new image was made" in data["message"]
    unit = page.unit("001")
    assert (unit.status, unit.approved_version, unit.current_version, unit.compare_with) == ("approved", 1, 1, None)
    assert [v.v for v in unit.versions] == [1]
    assert (page.project / "images" / "_history" / "001_v1.png").read_bytes() == copy_before


def test_a_paused_regeneration_of_a_failed_unit_keeps_its_old_error_text(tmp_path, plan_data, drawings, fake_images):
    """N2: set_status("generating") clears the unit's error; a pause with no new image must restore it."""
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation for tok-secret", status=429)
    page = Page(tmp_path, plan_data, drawings, fake_images([daily]))
    store = StateStore.load(page.project)
    store.set_status("001", "failed", error="an older failure")
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    assert job_events(events)[-1] == ("regenerate", "paused")
    unit = page.unit("001")
    assert (unit.status, unit.error) == ("failed", "an older failure")


def test_a_paused_regeneration_of_a_needs_review_unit_keeps_its_review_note(tmp_path, plan_data, drawings, fake_images):
    """N2: the same loss happens to a needs_review unit's note, not only a failed one's error."""
    daily = CFError(ErrorCategory.DAILY_LIMIT, "daily free allocation for tok-secret", status=429)
    page = Page(tmp_path, plan_data, drawings, fake_images([daily]))
    store = StateStore.load(page.project)
    store.set_status("001", "needs_review", error="rewrite failed: something")
    unit = store.unit("001")
    unit.approved_version = None
    store.save()
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    assert job_events(events)[-1] == ("regenerate", "paused")
    unit = page.unit("001")
    assert (unit.status, unit.error) == ("needs_review", "rewrite failed: something")


def test_a_first_failure_with_no_image_stays_failed_instead_of_going_back_to_planned(
    tmp_path, plan_data, drawings, fake_images
):
    """N3: a unit that never had an image mustn't be silently restored to planned, hiding the failure."""
    bad = CFError(ErrorCategory.BAD_REQUEST, "the prompt was rejected", status=400)
    page = Page(tmp_path, plan_data, drawings, fake_images([bad]))
    (page.project / "images" / "_history" / "002b_v1.png").unlink()  # else recover() would adopt it back
    store = StateStore.load(page.project)
    unit = store.unit("002b")
    unit.status, unit.current_version, unit.approved_version, unit.versions, unit.error = "planned", None, None, [], None
    store.save()
    _, events = page.run(lambda: page.jobs.regenerate("002b"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "finished"]
    assert "002b is failed" in data["message"] and "the prompt was rejected" in data["message"]
    assert "keeps" not in data["message"]  # nothing to keep: it never had an image
    unit = page.unit("002b")
    assert "the prompt was rejected" in unit.error
    assert (unit.status, unit.current_version, unit.approved_version) == ("failed", None, None)


def test_an_error_building_the_meter_or_run_log_does_not_drop_the_approval(
    tmp_path, plan_data, drawings, fake_images, monkeypatch
):
    """N4: begin_regeneration must not run before something that can raise ahead of the render."""
    page = Page(tmp_path, plan_data, drawings, fake_images())

    def broken_meter(*args, **kwargs):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(page.jobs, "_meter", broken_meter)
    _, events = page.run(lambda: page.jobs.regenerate("001"))
    [(kind, data)] = [e for e in events if e[0] == "job" and e[1]["state"] == "failed"]
    assert "ledger unreadable" in data["message"]
    unit = page.unit("001")
    assert (unit.status, unit.approved_version, unit.current_version) == ("approved", 1, 1)
