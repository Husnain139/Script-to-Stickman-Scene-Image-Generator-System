"""The review server (spec §12): FastAPI bound to 127.0.0.1, serving the page, its API, server-sent events and
the images it shows. Requests must name 127.0.0.1 (or localhost) as their Host, which defeats DNS rebinding,
and every change must carry X-Stickman: 1, which another site's page can't send, since CORS is never enabled."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from stickman.bootstrap.store import BOOTSTRAP_DIR
from stickman.config_files import load_mascot, load_style
from stickman.plan.models import Plan, PlanValidationError
from stickman.plan.store import file_hash, load_plan
from stickman.render.jobs import RenderContext
from stickman.render.references import FORBIDDEN_DIR
from stickman.render.state import StateError
from stickman.review.access import Busy, ProjectAccess
from stickman.review.actions import (
    EXTRAS_LATER,
    ActionError,
    approve_plan,
    approve_remaining,
    approve_sheet,
    approve_tests,
    approve_unit,
    edit_prompt,
    select_version,
)
from stickman.review.events import EventHub, event_stream
from stickman.review.jobs import Jobs
from stickman.review.view import empty_view, project_view
from stickman.review.watch import PlanWatcher
from stickman.runlog import mask
from stickman.settings import ConfigError, Settings

STATIC = Path(__file__).parent / "static"
CHANGES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
NO_CACHE = {"Cache-Control": "no-cache"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateBody(_Body):
    candidate: int = Field(ge=1)


class VersionBody(_Body):
    v: int = Field(ge=1)


class PromptBody(_Body):
    prompt: str
    plan_hash: str


class HintBody(_Body):
    hint: str = ""


class PlanSource:
    """plan.yaml as the page sees it: while the file is invalid, the last valid plan stays on the page, with
    the file's errors beside it (spec §12.4)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._plan: Plan | None = None

    def load(self, library_ids: Iterable[str]) -> tuple[Plan | None, str | None, list[str]]:
        try:
            loaded = load_plan(self.path, library_ids=set(library_ids))
        except PlanValidationError as exc:
            try:
                current = file_hash(self.path.read_bytes())
            except OSError:
                current = None
            return self._plan, current, list(exc.errors)
        except OSError as exc:
            return self._plan, None, [f"can't read plan.yaml: {exc.strerror or exc}"]
        self._plan = loaded.plan
        return loaded.plan, loaded.hash, []


def create_app(
    workspace: Path,
    project_dir: Path,
    settings: Settings,
    *,
    client_factory: Callable[[], Any],
    secrets: Sequence[str] = (),
    watch: bool = True,
    allowed_hosts: Iterable[str] | None = None,
) -> FastAPI:
    port = settings.review.port
    hosts = set(allowed_hosts) if allowed_hosts is not None else {f"127.0.0.1:{port}", f"localhost:{port}"}
    hub = EventHub()
    access = ProjectAccess(project_dir)
    watcher = PlanWatcher(project_dir, hub)
    jobs = Jobs(workspace, project_dir, settings, access, hub, client_factory=client_factory, secrets=secrets,
                on_plan_written=watcher.wrote)
    source = PlanSource(project_dir / "plan.yaml")
    plan_path = project_dir / "plan.yaml"
    roots = [project_dir / "images", workspace / BOOTSTRAP_DIR, workspace / "library" / "style", workspace / "library" / "mascot"]
    forbidden = (workspace / FORBIDDEN_DIR).resolve()

    def masked(text: str) -> str:
        return mask(text, secrets)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task = asyncio.create_task(watcher.run(stop)) if watch else None
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await jobs.wait()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub, app.state.access, app.state.jobs, app.state.watcher = hub, access, jobs, watcher

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Any:
        if request.headers.get("host") not in hosts:
            return JSONResponse({"error": "The review page answers only on 127.0.0.1."}, status_code=400)
        if request.method in CHANGES and request.headers.get("x-stickman") != "1":
            return JSONResponse({"error": "Changes need the X-Stickman: 1 header."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ActionError)
    async def action_error(request: Request, exc: ActionError) -> JSONResponse:
        return JSONResponse({"error": masked(exc.message)}, status_code=exc.status)

    @app.exception_handler(Busy)
    async def busy(request: Request, exc: Busy) -> JSONResponse:
        return JSONResponse({"error": masked(str(exc))}, status_code=409)

    def view() -> dict[str, Any]:
        try:
            ctx = RenderContext.load(workspace, settings)
        except ConfigError as exc:
            return empty_view(project_dir, [masked(str(exc))])
        plan, plan_hash, errors = source.load(ctx.library_ids)
        try:
            state = access.read()
        except StateError as exc:
            return empty_view(project_dir, [masked(str(exc))])
        data = project_view(
            workspace=workspace, project_dir=project_dir, ctx=ctx, plan=plan, plan_hash=plan_hash, errors=errors,
            state=state, now=datetime.now().astimezone(), job=access.job.as_dict() if access.job else None,
        )
        data["problems"] = [masked(problem) for problem in data["problems"]]
        for unit in data["units"]:
            if unit["error"]:
                unit["error"] = masked(unit["error"])
        return data

    def unit_ids() -> set[str]:
        return {unit["id"] for unit in view()["units"]}

    def changed() -> dict[str, Any]:
        hub.publish("state", {})
        return view()

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers=NO_CACHE)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/project")
    async def get_project() -> dict[str, Any]:
        return view()

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(event_stream(hub, request.is_disconnected), media_type="text/event-stream",
                                 headers=NO_CACHE)

    @app.post("/api/plan/approve")
    async def plan_approve() -> dict[str, Any]:
        errors = view()["errors"]
        with access.state() as store:
            approve_plan(store, errors)
        return changed()

    @app.post("/api/tests/approve")
    async def tests_approve() -> dict[str, Any]:
        with access.state() as store:
            approve_tests(store)
        return changed()

    @app.post("/api/sheets/{char_id}/approve")
    async def sheet_approve(char_id: str, body: CandidateBody) -> dict[str, Any]:
        try:
            style, mascot = load_style(workspace), load_mascot(workspace)
        except ConfigError as exc:
            raise ActionError(409, str(exc)) from None
        approve_sheet(workspace, char_id, body.candidate, settings=settings, style=style, mascot=mascot)
        return changed()

    @app.post("/api/sheets/{char_id}/regenerate", status_code=202)
    async def sheet_more(char_id: str) -> dict[str, Any]:
        if char_id not in ("anchor", "mascot"):
            raise ActionError(400, EXTRAS_LATER)
        return {"job": jobs.more_candidates(char_id).as_dict()}  # type: ignore[arg-type]

    @app.post("/api/units/approve-remaining")
    async def units_approve_remaining() -> dict[str, Any]:
        statuses = {unit["id"]: unit["status"] for unit in view()["units"]}
        with access.state() as store:
            approved = approve_remaining(store, statuses, busy=access.busy_unit)
        return {**changed(), "approved": approved}

    @app.post("/api/units/{unit_id}/approve")
    async def unit_approve(unit_id: str) -> dict[str, Any]:
        ids = unit_ids()
        with access.state() as store:
            approve_unit(store, unit_id, unit_ids=ids, busy=access.busy_unit)
        return changed()

    @app.post("/api/units/{unit_id}/select-version")
    async def unit_select(unit_id: str, body: VersionBody) -> dict[str, Any]:
        ids = unit_ids()
        with access.state() as store:
            select_version(store, unit_id, body.v, unit_ids=ids, busy=access.busy_unit)
        return changed()

    @app.post("/api/units/{unit_id}/regenerate", status_code=202)
    async def unit_regenerate(unit_id: str) -> dict[str, Any]:
        if unit_id not in unit_ids():
            raise ActionError(404, f"No unit {unit_id} in this plan.")
        return {"job": jobs.regenerate(unit_id).as_dict()}

    @app.put("/api/units/{unit_id}/prompt")
    async def unit_prompt(unit_id: str, body: PromptBody) -> dict[str, Any]:
        try:
            library_ids = RenderContext.load(workspace, settings).library_ids
        except ConfigError as exc:
            raise ActionError(409, str(exc)) from None
        written = edit_prompt(plan_path, unit_id, body.prompt, body.plan_hash, library_ids=library_ids)
        watcher.wrote(written)
        hub.publish("plan", {"hash": written})
        return view()

    @app.post("/api/units/{unit_id}/replan", status_code=202)
    async def unit_replan(unit_id: str, body: HintBody) -> dict[str, Any]:
        if unit_id not in unit_ids():
            raise ActionError(404, f"No unit {unit_id} in this plan.")
        return {"job": jobs.replan(unit_id, body.hint).as_dict()}

    @app.get("/files/{path:path}")
    async def files(path: str) -> FileResponse:
        """Only PNGs under the project's images, bootstrap's candidates and the approved library images; never
        style_refs/ (spec §2.2), and never anything outside those folders."""
        target = (workspace / path).resolve()
        allowed = (
            target.suffix.lower() == ".png"
            and target.is_file()
            and not target.is_relative_to(forbidden)
            and any(target.is_relative_to(root.resolve()) for root in roots)
        )
        if not allowed:
            raise ActionError(404, "Not found.")
        return FileResponse(target, media_type="image/png", headers=NO_CACHE)

    return app
