import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stickman.plan.models import parse_plan
from stickman.plan.store import load_plan, to_document, write_plan
from stickman.qc.decide import decide
from stickman.qc.pixel import pixel_check
from stickman.render.images import encode_png
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.state import StateStore, Version
from stickman.review.actions import PLAN_CHANGED
from stickman.review.app import create_app
from stickman.settings import QCSettings, Settings

PK = timezone(timedelta(hours=5))
FOLDER = "2026-09-25_demo"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
WRITE = {"X-Stickman": "1"}


@pytest.fixture
def site(tmp_path, plan_data, drawings, fake_images):
    """A project with an image for 001 and 002a (002b has none), and a TestClient over its review app."""
    project = tmp_path / "projects" / FOLDER
    project.mkdir(parents=True)
    write_plan(project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    plan = parse_plan(plan_data)
    builder = JobBuilder(RenderContext.load(tmp_path, Settings()), plan)
    store = StateStore.load(project)
    image = drawings.clean()
    good = decide(pixel_check(image, QCSettings()), expected_figures=1, min_idea_score=3)
    for unit in plan.units()[:2]:
        record = Version(v=1, file=f"images/_history/{unit.id}_v1.png", seed=1, model=KLEIN_4B, width=960,
                         height=544, fingerprint=builder.fingerprint(unit), prompt_sent=unit.image_prompt, qc=good,
                         est_cost_usd=0.002, latency_s=1.0, created=datetime(2026, 9, 26, 10, 0, tzinfo=PK))
        (project / "images" / "_history").mkdir(parents=True, exist_ok=True)
        (project / record.file).write_bytes(encode_png(image, record.model_dump(mode="json")))
        store.add_version(unit.id, record, status="generated")
    images = fake_images()
    app = create_app(tmp_path, project, Settings(), client_factory=lambda: images, secrets=("tok-secret",),
                     watch=False, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        client.project = project
        client.images = images
        yield client


def state(site):
    return StateStore.load(site.project).state


def wait_for_job(site, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        view = site.get("/api/project").json()
        if view["job"] is None:
            return view
        time.sleep(0.02)
    raise AssertionError("the job didn't finish")


def test_the_page_and_its_data_are_served(site):
    assert site.get("/").status_code == 200
    view = site.get("/api/project").json()
    assert view["project"] == FOLDER and [u["id"] for u in view["units"]] == ["001", "002a", "002b"]
    assert view["plan_hash"].startswith("sha256:") and view["errors"] == []


def test_only_127_0_0_1_hosts_are_answered(tmp_path, plan_data, fake_images):
    project = tmp_path / "p"
    project.mkdir()
    write_plan(project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    app = create_app(tmp_path, project, Settings(), client_factory=lambda: fake_images(), watch=False)
    with TestClient(app) as client:
        assert client.get("/api/project").status_code == 400  # Host: testserver
        assert client.get("/api/project", headers={"Host": "127.0.0.1:8765"}).status_code == 200
        assert client.get("/api/project", headers={"Host": "localhost:8765"}).status_code == 200
        assert client.get("/api/project", headers={"Host": "evil.example:8765"}).status_code == 400


def test_a_change_without_the_same_origin_header_is_refused(site):
    response = site.post("/api/units/001/approve")
    assert response.status_code == 403
    assert state(site).units["001"].status == "generated"


def test_approving_and_approving_the_rest(site):
    view = site.post("/api/units/001/approve", headers=WRITE).json()
    assert next(u for u in view["units"] if u["id"] == "001")["status"] == "approved"
    response = site.post("/api/units/approve-remaining", headers=WRITE)
    assert response.json()["approved"] == ["002a"]
    assert next(u for u in response.json()["units"] if u["id"] == "002b")["status"] == "planned"


def test_approving_a_unit_without_an_image_is_409_and_an_unknown_unit_404(site):
    assert site.post("/api/units/002b/approve", headers=WRITE).status_code == 409
    response = site.post("/api/units/999/approve", headers=WRITE)
    assert response.status_code == 404 and response.json() == {"error": "No unit 999 in this plan."}


def test_the_plan_and_tests_approvals(site):
    assert site.post("/api/plan/approve", headers=WRITE).json()["approvals"]["plan"] is True
    response = site.post("/api/tests/approve", headers=WRITE)
    assert response.status_code == 409 and "M7" in response.json()["error"]


def test_a_prompt_edit_locks_the_prompt_and_is_ignored_by_the_watcher(site):
    view = site.get("/api/project").json()
    response = site.put("/api/units/002a/prompt", headers=WRITE,
                        json={"prompt": "My own prompt.", "plan_hash": view["plan_hash"]})
    assert response.status_code == 200
    unit = next(u for u in response.json()["units"] if u["id"] == "002a")
    assert (unit["image_prompt"], unit["prompt_locked"]) == ("My own prompt.", True)
    assert site.app.state.watcher.changed([site.project / "plan.yaml"]) == []  # the server's own write


def test_a_prompt_edit_after_the_file_changed_gives_409_and_changes_nothing(site):
    old_hash = site.get("/api/project").json()["plan_hash"]
    path = site.project / "plan.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# edited in the editor\n", encoding="utf-8")
    before = path.read_bytes()
    response = site.put("/api/units/002a/prompt", headers=WRITE, json={"prompt": "x", "plan_hash": old_hash})
    assert (response.status_code, response.json()) == (409, {"error": PLAN_CHANGED})
    assert path.read_bytes() == before


def test_an_invalid_plan_shows_its_errors_and_keeps_the_last_units(site):
    site.get("/api/project")
    path = site.project / "plan.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("shot: wide", "shot: sideways", 1), encoding="utf-8")
    view = site.get("/api/project").json()
    assert view["errors"] and "shot" in view["errors"][0]
    assert len(view["units"]) == 3
    assert site.post("/api/plan/approve", headers=WRITE).status_code == 409


def test_regenerating_runs_in_the_background_and_shows_both_images(site):
    response = site.post("/api/units/001/regenerate", headers=WRITE)
    assert response.status_code == 202 and response.json()["job"]["unit"] == "001"
    view = wait_for_job(site)
    unit = next(u for u in view["units"] if u["id"] == "001")
    assert (unit["current_version"], unit["compare_with"]) == (2, 1)
    copy = site.project / "images" / f"{unit['stem']}.png"
    assert copy.read_bytes() == (site.project / "images" / "_history" / "001_v2.png").read_bytes()
    chosen = site.post("/api/units/001/select-version", headers=WRITE, json={"v": 1}).json()
    unit = next(u for u in chosen["units"] if u["id"] == "001")
    assert (unit["current_version"], unit["compare_with"]) == (1, None)
    assert copy.read_bytes() == (site.project / "images" / "_history" / "001_v1.png").read_bytes()


def test_a_cli_run_holding_the_lock_makes_changes_wait(site):
    (site.project / ".lock").write_text(str(os.getppid()), encoding="ascii")
    response = site.post("/api/units/001/approve", headers=WRITE)
    assert response.status_code == 409 and "another stickman process" in response.json()["error"]
    assert site.post("/api/units/001/regenerate", headers=WRITE).status_code == 409


def test_extras_sheets_are_for_m7(site):
    response = site.post("/api/sheets/caveman_group/approve", headers=WRITE, json={"candidate": 1})
    assert response.status_code == 400 and "M7" in response.json()["error"]


def test_files_serves_project_images_and_nothing_else(site, tmp_path):
    assert site.get(f"/files/projects/{FOLDER}/images/_history/001_v1.png").headers["content-type"] == "image/png"
    stock = tmp_path / "style_refs" / "a.png"
    stock.parent.mkdir()
    Image.new("RGB", (8, 8)).save(stock, format="PNG")
    (tmp_path / ".env").write_text("CF_API_TOKEN=tok-secret\n", encoding="utf-8")
    for path in ("style_refs/a.png", ".env", f"projects/{FOLDER}/plan.yaml", f"projects/{FOLDER}/state.json",
                 "../outside.png", f"projects/{FOLDER}/images/../../../style_refs/a.png"):
        assert site.get(f"/files/{path}").status_code == 404, path


def test_no_secret_reaches_the_page(site):
    site.post("/api/units/001/approve", headers=WRITE)
    assert "tok-secret" not in site.get("/api/project").text


@pytest.mark.parametrize("url", [
    "/files///host/share/x.png",
    "/files/%5C%5Chost%5Cs%5Cx.png",
    "/files/C:/Windows/x.png",
    "/files/..%2F..%2Fx.png",
    f"/files/projects/{FOLDER}/images/%00x.png",
    f"/files/projects/{FOLDER}/images/..%2F..%2F..%2Fstyle_refs%2Fa.png",
])
def test_files_refuses_unc_absolute_and_parent_paths_without_touching_the_disk(site, monkeypatch, url):
    import pathlib

    touched = []

    def fake(name, value):
        def call(self, *args, **kwargs):  # records the call and never reaches the disk (a UNC path would)
            touched.append((name, str(self)))
            if isinstance(value, Exception):
                raise value
            return self if value is None else value
        return call

    with monkeypatch.context() as patch:
        for name, value in (("resolve", None), ("is_file", False), ("exists", False), ("stat", OSError("no"))):
            patch.setattr(pathlib.Path, name, fake(name, value))
        status = site.get(url).status_code
    assert status == 404, url
    assert touched == [], touched


SECURITY_HEADERS = {
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


@pytest.mark.parametrize("url", ["/", "/api/project", "/files/nothing.png", "/static/app.js"])
def test_every_response_forbids_framing_and_sniffing(site, url):
    response = site.get(url)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, (url, name)


def test_stopping_the_server_during_a_job_says_what_it_waits_for(tmp_path, plan_data, fake_images, jpeg, capsys):
    import asyncio

    project = tmp_path / "projects" / FOLDER
    project.mkdir(parents=True)
    write_plan(project / "plan.yaml", to_document(parse_plan(plan_data)), expected_hash=None)
    images = fake_images(lambda call: asyncio.sleep(0.3, result=jpeg))
    app = create_app(tmp_path, project, Settings(), client_factory=lambda: images, watch=False,
                     allowed_hosts={"testserver"})
    with TestClient(app) as client:
        assert client.post("/api/units/001/regenerate", headers=WRITE).status_code == 202
    err = capsys.readouterr().err
    assert "Waiting for the page's regeneration of 001 to finish (Ctrl+C again to abandon it)" in err
    assert StateStore.load(project).unit("001").versions  # the job finished before the server stopped
