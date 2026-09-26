import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from stickman.bootstrap.store import BootstrapStore, Candidate
from stickman.ledger import Ledger, LedgerEntry
from stickman.library import anchor_ref_path
from stickman.plan.models import parse_plan
from stickman.plan.store import to_document, write_plan
from stickman.pricing import image_cost_usd
from stickman.qc.decide import decide
from stickman.qc.pixel import PixelResult
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.references import ReferenceFiles
from stickman.render.state import ProjectState, StateStore, Version
from stickman.review.view import file_url, gallery_order, project_view
from stickman.runtime import check_usd
from stickman.settings import Settings

PK = timezone(timedelta(hours=5))
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=PK)
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"


def qc(passed=True, reason=None):
    pixel = PixelResult(reason=None if passed else reason, lum_std=30.0, lum_mean=250.0, ink_fraction=0.02,
                        lap_var=900.0, white_fraction=0.97, colour_fraction=0.0, black_fraction=0.01)
    return decide(pixel, expected_figures=1, min_idea_score=3)


def version(unit, v=1, fingerprint="sha256:x", **changes):
    data = dict(v=v, file=f"images/_history/{unit}_v{v}.png", seed=40 + v, model=KLEIN_4B, width=1920, height=1088,
                fingerprint=fingerprint, prompt_sent="p", qc=qc(), est_cost_usd=0.0023, latency_s=12.0,
                created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
    return Version(**{**data, **changes})


class Setup:
    def __init__(self, tmp_path, plan_data):
        self.workspace = tmp_path
        self.project = tmp_path / "projects" / FOLDER
        self.project.mkdir(parents=True)
        write_plan(self.project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
        self.plan = parse_plan(plan_data)
        self.ctx = RenderContext.load(tmp_path, Settings())
        self.builder = JobBuilder(self.ctx, self.plan)
        self.store = StateStore.load(self.project)

    def fingerprint(self, unit_id):
        return self.builder.fingerprint(next(u for u in self.plan.units() if u.id == unit_id))

    def view(self, errors=(), plan="same", state=None, job=None):
        return project_view(workspace=self.workspace, project_dir=self.project, ctx=self.ctx,
                            plan=self.plan if plan == "same" else plan, plan_hash="sha256:h", errors=list(errors),
                            state=state or self.store.state, now=NOW, job=job)


def units_by_id(view):
    return {unit["id"]: unit for unit in view["units"]}


def test_a_file_url_is_relative_to_the_workspace(tmp_path):
    assert file_url(tmp_path, tmp_path / "projects" / "p" / "images" / "a.png") == "/files/projects/p/images/a.png"


def test_units_carry_their_status_versions_and_image_urls(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    run.store.add_version("001", version("001", fingerprint=run.fingerprint("001")), status="generated")
    unit = units_by_id(run.view())["001"]
    assert (unit["status"], unit["current_version"], unit["stem"]) == ("generated", 1, "001_00-00.0")
    assert unit["versions"][0]["url"] == f"/files/projects/{FOLDER}/images/_history/001_v1.png"
    assert unit["versions"][0]["qc"] == {"passed": True, "reason": None, "score": None, "notes": "", "text_seen": ""}
    assert unit["visual_idea"] == "Idea 001" and unit["split"] == "none"
    assert units_by_id(run.view())["002a"]["split"] == "split"


def test_a_unit_whose_fields_changed_shows_stale_and_nothing_is_written(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    run.store.add_version("001", version("001", fingerprint="sha256:old"), status="generated")
    before = (run.project / "state.json").read_bytes()
    assert units_by_id(run.view())["001"]["status"] == "stale"
    assert (run.project / "state.json").read_bytes() == before


def test_the_side_by_side_pair_is_shown_only_when_it_differs_from_the_current_version(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    fp = run.fingerprint("001")
    run.store.add_version("001", version("001", fingerprint=fp), status="generated")
    run.store.begin_regeneration("001")
    assert units_by_id(run.view())["001"]["compare_with"] is None  # the new image hasn't come yet
    run.store.add_version("001", version("001", v=2, fingerprint=fp), status="generated")
    assert units_by_id(run.view())["001"]["compare_with"] == 1


def test_a_needs_review_unit_says_why(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    run.store.add_version("001", version("001", fingerprint=run.fingerprint("001"), qc=qc(False, "empty")),
                          status="needs_review")
    unit = units_by_id(run.view())["001"]
    assert (unit["status"], unit["review_reason"]) == ("needs_review", "empty")


def test_the_gallery_shows_flagged_units_first_then_time_order():
    units = [{"id": "001", "status": "generated"}, {"id": "002", "status": "failed"}, {"id": "003", "status": "stale"},
             {"id": "004", "status": "needs_review"}, {"id": "005", "status": "planned"}, {"id": "006", "status": "needs_review"}]
    assert gallery_order(units) == ["004", "006", "003", "002", "001", "005"]


def test_the_estimate_counts_images_checks_and_extras_sheets(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    estimate = run.view()["estimate"]
    settings, pricing = run.ctx.settings, run.ctx.pricing
    per_image = image_cost_usd(pricing.image(KLEIN_4B), (1920, 1088))
    sheets = 2 * image_cost_usd(pricing.image(KLEIN_4B), (1024, 768))  # the caveman group has 3 figures
    assert estimate["units"] == 3
    assert estimate["images_usd"] == pytest.approx(3 * per_image * 1.25)
    assert estimate["checks_usd"] == pytest.approx(3 * check_usd(settings, pricing) * 1.25)
    assert estimate["sheets_usd"] == pytest.approx(sheets)
    assert estimate["llm_usd"] == 0.0
    assert estimate["minutes"] == pytest.approx(round(3 * 1.25 * 10 / 4 / 60, 1))
    assert estimate["free_note"] == "Up to $0.11 of today's usage may be covered by the free daily allocation."


def test_an_invalid_plan_keeps_showing_the_last_one_with_its_errors(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    view = run.view(errors=["scenes[0].units[0].shot: Input should be 'wide', 'medium' or 'close-up'"])
    assert view["errors"] and len(view["units"]) == 3
    empty = run.view(plan=None, errors=["invalid YAML"])
    assert empty["units"] == [] and empty["estimate"] is None


def test_corrections_merge_checks_and_cast_are_listed(tmp_path, plan_data):
    view = Setup(tmp_path, plan_data).view()
    assert view["corrections"] == [{"scene": "001", "from": "90 at night", "to": "9 at night", "reason": "clock time"}]
    assert [member["id"] for member in view["cast"]] == ["mascot", "caveman_group"]
    assert view["cast"][1]["figures"] == 3 and view["cast"][1]["sheet"] is False


def test_the_budget_shows_the_weeks_spend(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    Ledger(tmp_path / "ledger.jsonl").append(LedgerEntry(ts=NOW, project=FOLDER, kind="image", model=KLEIN_4B,
                                                        est_usd=13.0, billing="billed"))
    budget = run.view()["budget"]
    assert budget["week_usd"] == pytest.approx(13.0) and budget["weekly_usd"] == 15.0 and budget["warn"] is True


def test_bootstrap_candidates_are_listed_best_first_and_old_anchor_ones_are_marked(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    ref = anchor_ref_path(tmp_path, 1)
    ref.parent.mkdir(parents=True)
    Image.new("RGB", (512, 384), "white").save(ref, format="PNG")
    store = BootstrapStore.load(tmp_path, 1)
    for n, refs in ((1, ["library/style/anchor_v1_ref.png#sha256:old"]), (2, None)):
        store.add("mascot", Candidate(n=n, file=f"library/_bootstrap/v1/mascot/c{n}.png", seed=n, model=KLEIN_9B,
                                      width=768, height=1024, prompt_sent="p", refs=refs or [], est_cost_usd=0.017,
                                      latency_s=4.0, created=NOW, qc=qc()))
    label = ReferenceFiles(tmp_path, ref_max_side=512).load(ref).label
    store.state.mascot.candidates[1].refs = [label]
    store.save()
    boot = run.view()["bootstrap"]
    assert boot["style_version"] == 1 and boot["anchor_done"] is False
    marks = {c["n"]: c["previous_anchor"] for c in boot["mascot"]["candidates"]}
    assert marks == {1: True, 2: False}
    assert boot["mascot"]["candidates"][0]["url"].startswith("/files/library/_bootstrap/v1/mascot/c")


def test_the_job_and_approvals_are_passed_through(tmp_path, plan_data):
    run = Setup(tmp_path, plan_data)
    state = ProjectState(plan_approved=True, test_units=["001"])
    view = run.view(state=state, job={"kind": "regenerate", "unit": "001", "message": ""})
    assert view["approvals"] == {"plan": True, "sheets": False, "tests": False}
    assert view["test_units"] == ["001"] and view["job"]["unit"] == "001"
    json.dumps(view)  # everything is JSON-ready
