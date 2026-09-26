import asyncio
import io
import os
import random
import re

import pytest

from stickman.config_files import load_mascot, load_style, load_visual_rules
from stickman.meter import Meter
from stickman.plan.models import parse_plan
from stickman.pricing import load_pricing
from stickman.qc.fixes import BACKGROUND_FIX
from stickman.render import recovery, renderer, state
from stickman.render.images import history_files, image_stem
from stickman.render.jobs import JobBuilder, RenderContext
from stickman.render.recovery import recover
from stickman.render.renderer import Renderer
from stickman.render.state import StateStore, needs_work
from stickman.runlog import RunLog
from stickman.settings import QCSettings, RetrySettings, Settings

UNITS = 90
KILLS = 4
VISION = "@cf/qwen/qwen3.8-27b"


class Kill(BaseException):
    """The process being killed: nothing after it reaches the disk."""


class Killer:
    def __init__(self):
        self.count = 0
        self.at = None
        self.dead = False

    def arm(self, steps_from_now):
        self.dead = False
        self.at = None if steps_from_now is None else self.count + steps_from_now

    def tick(self):
        if self.dead:
            raise Kill()
        self.count += 1
        if self.at is not None and self.count >= self.at:
            self.dead = True
            raise Kill()


def killable_write(killer):
    def write(path, data):
        killer.tick()  # killed before this write
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)  # no fsync: this test is about the order of writes, not durability
        killer.tick()  # killed after the temp file, before the replace
        os.replace(tmp, path)

    return write


def synthetic_plan(units=UNITS):
    """About a 5-minute script: 3-second units, every fourth scene split in two."""
    scenes, start, made, number = [], 0.0, 0, 0
    while made < units:
        number += 1
        scene_id = f"{number:03d}"
        text = f"Line {number} of the synthetic script."
        split = number % 4 == 0 and units - made >= 2
        parts = [(f"{scene_id}a", "1 of 2"), (f"{scene_id}b", "2 of 2")] if split else [(scene_id, None)]
        unit_list = []
        for index, (unit_id, part) in enumerate(parts):
            unit_start = start + 3.0 * index
            unit_list.append({
                "id": unit_id, "part": part, "start": unit_start, "end": unit_start + 3.0,
                "source_text": text, "corrected_text": text, "visual_idea": f"Idea {unit_id}",
                "visual_type": "literal", "shot": "wide", "time_of_day": "day",
                "characters": [{"ref": "mascot", "action": "waving", "emotion": "happy"}],
                "setting": [], "props": [], "composition": "centred", "energy_marks": [],
                "image_prompt": f"prompt for {unit_id}",
            })
        end = start + 3.0 * len(parts)
        scenes.append({
            "id": scene_id, "lines": [number], "start": start, "end": end, "source_text": text,
            "corrected_text": text, "units": unit_list,
            "split": {"status": "split", "cut_after_word": 3, "candidates": [3]} if split else {"status": "none"},
        })
        made += len(parts)
        start = end
    return parse_plan({
        "schema_version": 1, "project": "synthetic", "aspect": "16:9", "style_version": 1,
        "image_model": "@cf/black-forest-labs/flux-2-klein-4b", "duration_end": start, "pace_wps": 2.5,
        "cast": [{"id": "mascot"}], "corrections": [], "merge_check": [], "scenes": scenes,
    })


async def no_sleep(seconds):
    return None


def run_once(workspace, project, plan, client):
    """What `stickman resume` does: recover, then take up every unit that needs work (state.needs_work)."""
    ctx = RenderContext(workspace, Settings(), load_mascot(workspace), (), load_pricing(workspace),
                        load_style(workspace), load_visual_rules(workspace))
    builder = JobBuilder(ctx, plan)
    store = StateStore.load(project)
    recover(store, builder.expected())
    todo = [unit for unit in plan.units() if needs_work(store.unit(unit.id))]
    render = Renderer(client, store, Meter(project=project.name), builder, qc=QCSettings(), vision_model=VISION,
                      retry=RetrySettings(), concurrency=4, log=RunLog(None), sleep=no_sleep)
    asyncio.run(render.run(builder.jobs(todo)))


def test_the_synthetic_plan_is_about_five_minutes():
    plan = synthetic_plan()
    assert len(plan.units()) == UNITS
    assert 240 <= plan.duration_end <= 300


def random_kills(rng):
    return [rng.randint(1, 400) for _ in range(KILLS)]


def kills_in_recovery(rng):
    """One kill mid-run, then kills at the next runs' first writes: recover()'s own writes
    (_restore_current_images, then store.save()), each run killed one write step further in."""
    return [rng.randint(5, 120), 1, 2, 3]


def retried_units(plan):
    """Every fifth unit's first image has a filled background, so it fails QC and gets one retry."""
    return {unit.id for index, unit in enumerate(plan.units()) if index % 5 == 0}


def to_jpeg(image):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


@pytest.mark.parametrize("schedule", [random_kills, kills_in_recovery], ids=["random", "in_recovery"])
@pytest.mark.parametrize("seed", range(6))
def test_resume_after_kills_finishes_with_no_lost_or_duplicate_images(
    tmp_path, monkeypatch, fake_images, jpeg, drawings, vision, seed, schedule
):
    rng = random.Random(seed)
    killer = Killer()
    for module in (state, renderer):  # the current copies are written by state.write_current_copy
        monkeypatch.setattr(module, "safe_write", killable_write(killer))
    project = tmp_path / "projects" / "2026-09-25_synthetic"
    project.mkdir(parents=True)
    plan = synthetic_plan()
    retried = retried_units(plan)
    filled = to_jpeg(drawings.filled_background())

    def image(call):
        killer.tick()  # killed while the request is out
        unit_id = re.search(r"prompt for (\w+)", call["prompt"]).group(1)
        return filled if unit_id in retried and BACKGROUND_FIX not in call["prompt"] else jpeg

    def check(model, messages):
        killer.tick()  # killed while the vision call is out
        return vision.passing(model, messages)

    client = fake_images(image, chat=check)
    arms = schedule(rng)
    kills = 0
    while True:
        killer.arm(arms[kills] if kills < len(arms) else None)
        try:
            run_once(tmp_path, project, plan, client)
            break
        except Kill:
            kills += 1
    assert kills >= 1

    saved = StateStore.load(project).state
    history = history_files(project)
    for unit in plan.units():
        entry = saved.units[unit.id]
        want = [1, 2] if unit.id in retried else [1]
        assert entry.status == "generated", (unit.id, entry.status)
        assert [version.v for version in entry.versions] == want, unit.id
        assert entry.current_version == want[-1], unit.id
        assert all(version.qc is not None for version in entry.versions), unit.id
        assert entry.versions[-1].qc.passed, unit.id
        if unit.id in retried:
            first, second = entry.versions
            assert first.qc.reason == "background_filled", unit.id
            assert (second.retry_of, second.retry_reason) == (1, "background_filled"), unit.id
        assert sorted(history[unit.id]) == want, unit.id
        current = project / "images" / f"{image_stem(unit.id, unit.start)}.png"
        assert current.read_bytes() == history[unit.id][want[-1]].read_bytes(), unit.id
    assert set(history) == {unit.id for unit in plan.units()}
    assert len(list((project / "images").glob("*.png"))) == UNITS
    assert not list(project.rglob("*.tmp"))
