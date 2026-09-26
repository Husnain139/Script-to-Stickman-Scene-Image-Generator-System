import itertools
from datetime import date

import pytest
from PIL import Image

from stickman.config_files import MascotConfig, load_mascot, load_style, load_visual_rules
from stickman.library import LibraryCharacter
from stickman.plan.models import parse_plan
from stickman.pricing import image_cost_usd, load_pricing
from stickman.qc.fixes import retry_prompt
from stickman.qc.vision import ExpectedPicture
from stickman.render.jobs import JobBuilder, JobError, RenderContext, random_seed
from stickman.render.recovery import ExpectedUnit
from stickman.settings import ConfigError, Settings

KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
DEV = "@cf/black-forest-labs/flux-2-dev"


def context(workspace, mascot=None, library=()):
    return RenderContext(workspace, Settings(), mascot or load_mascot(workspace), tuple(library), load_pricing(workspace),
                         load_style(workspace), load_visual_rules(workspace))


def builder(workspace, plan_data, **kwargs):
    return JobBuilder(context(workspace, **kwargs), parse_plan(plan_data), seeds=itertools.count(1000).__next__)


def png(path, size=(512, 384)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format="PNG")


def test_a_job_is_the_units_prompt_at_the_projects_size_and_model(tmp_path, plan_data):
    jobs = builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units())
    first = jobs[0]
    assert (first.unit_id, first.stem, first.prompt, first.model) == ("001", "001_00-00.0", "prompt for 001", KLEIN_4B)
    assert (first.width, first.height, first.steps, first.references) == (1920, 1088, None, ())
    assert [job.seed for job in jobs] == [1000, 1001, 1002]
    assert first.estimate_usd == pytest.approx(image_cost_usd(load_pricing(tmp_path).image(KLEIN_4B), (1920, 1088)))


def test_a_vertical_project_uses_the_vertical_size(tmp_path, plan_data):
    plan_data["aspect"] = "9:16"
    assert builder(tmp_path, plan_data).size == (1088, 1920)


def test_a_pinned_seed_is_used(tmp_path, plan_data):
    plan_data["scenes"][0]["units"][0]["seed"] = 42
    assert builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units()[:1])[0].seed == 42


def test_steps_are_sent_only_to_models_that_accept_them(tmp_path, plan_data):
    plan_data["image_model"] = DEV
    assert builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units()[:1])[0].steps == 25


def test_an_unpriced_model_is_a_config_error(tmp_path, plan_data):
    plan_data["image_model"] = "@cf/unknown/model"
    with pytest.raises(ConfigError, match="no image price"):
        builder(tmp_path, plan_data)


def test_units_without_a_prompt_are_refused(tmp_path, plan_data):
    plan_data["scenes"][1]["units"][0]["image_prompt"] = "  "
    with pytest.raises(JobError, match="002a"):
        builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units())


def test_references_come_in_slot_order_and_are_priced(tmp_path, plan_data):
    png(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    png(tmp_path / "library" / "mascot" / "ref_v1.png", (384, 512))
    png(tmp_path / "library" / "characters" / "cavemen_v1" / "ref.png")
    mascot = MascotConfig(name="Everyman", identity="a stickman", sheet="library/mascot/sheet_v1.png",
                          ref="library/mascot/ref_v1.png", seed=7, style_version=1)
    cavemen = LibraryCharacter(id="cavemen_v1", name="Caveman group", figures=3, description="three cavemen",
                               style_version=1, model=KLEIN_4B, sheet="sheet.png", ref="ref.png",
                               approved=date(2026, 9, 22))
    plan_data["cast"][1]["library_ref"] = "cavemen_v1"
    plan_data["scenes"][0]["units"][0]["characters"].append({"ref": "caveman_group", "action": "sitting", "emotion": "calm"})
    [job] = builder(tmp_path, plan_data, mascot=mascot, library=[cavemen]).jobs(parse_plan(plan_data).units()[:1])
    assert [ref.path for ref in job.references] == [
        "library/style/anchor_v1_ref.png", "library/mascot/ref_v1.png", "library/characters/cavemen_v1/ref.png"]
    price = load_pricing(tmp_path).image(KLEIN_4B)
    assert job.estimate_usd == pytest.approx(image_cost_usd(price, (1920, 1088), [(512, 384), (384, 512), (512, 384)]))


def test_expected_gives_each_units_image_name_and_fingerprint(tmp_path, plan_data):
    build = builder(tmp_path, plan_data)
    plan = parse_plan(plan_data)
    expected = build.expected()
    assert list(expected) == ["001", "002a", "002b"]
    assert expected["002b"] == ExpectedUnit("002b_00-07.0", build.fingerprint(plan.units()[2]))


def test_a_changed_reference_file_changes_the_fingerprint(tmp_path, plan_data):
    anchor = tmp_path / "library" / "style" / "anchor_v1_ref.png"
    png(anchor)
    unit = parse_plan(plan_data).units()[0]
    before = builder(tmp_path, plan_data).fingerprint(unit)
    png(anchor, (500, 384))
    assert builder(tmp_path, plan_data).fingerprint(unit) != before


def test_random_seeds_fit_a_signed_32_bit_integer():
    assert all(0 <= random_seed() < 2**31 for _ in range(200))


MASCOT_WITH_SHEET = MascotConfig(name="Everyman", identity="a stickman with three hair strokes",
                                 sheet="library/mascot/sheet_v1.png", ref="library/mascot/ref_v1.png", seed=7,
                                 style_version=1)


def test_a_job_knows_its_unit_and_what_qc_expects(tmp_path, plan_data):
    job = builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units())[0]
    assert job.unit.id == "001" and job.unit.image_prompt == "prompt for 001"
    assert job.expected == ExpectedPicture("Idea 001", 1, "Everyman: 1", 1)
    assert job.vision_reference is None  # no mascot sheet before bootstrap (M5)
    assert job.fixes.strict_clause == load_style(tmp_path).strict_clause
    assert (job.fixes.props, job.fixes.locked, job.fixes.mascot_in_image_1) == ((), False, False)
    assert job.fixes.identity == load_mascot(tmp_path).identity


def test_expected_figures_are_a_range_over_the_units_characters(tmp_path, plan_data):
    # Changed after the M4 live check: a group may show 1 up to its full size.
    plan_data["scenes"][1]["units"][0]["characters"].append(
        {"ref": "caveman_group", "action": "sitting", "emotion": "calm"})
    job = builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units())[1]
    assert job.expected == ExpectedPicture("Idea 002a", 4, "Everyman: 1, Caveman group: 1-3", 2)


def test_a_repeated_reference_counts_once(tmp_path, plan_data):
    # 008a in the live check listed one group three times, and expected 9 figures.
    plan_data["scenes"][1]["units"][1]["characters"] = [
        {"ref": "caveman_group", "action": action, "emotion": "calm"} for action in ("sitting", "standing", "pointing")]
    job = builder(tmp_path, plan_data).jobs(parse_plan(plan_data).units())[2]
    assert job.expected == ExpectedPicture("Idea 002b", 3, "Caveman group: 1-3", 1)


def test_the_mascot_sheet_goes_with_the_vision_check_of_mascot_units_once_it_exists(tmp_path, plan_data):
    png(tmp_path / "library/mascot/ref_v1.png", (384, 512))
    plan_data["scenes"][1]["units"][1]["characters"] = [{"ref": "caveman_group", "action": "sitting", "emotion": "calm"}]
    jobs = builder(tmp_path, plan_data, mascot=MASCOT_WITH_SHEET).jobs(parse_plan(plan_data).units())
    assert jobs[0].vision_reference.path == "library/mascot/ref_v1.png"
    assert jobs[2].vision_reference is None  # 002b has no mascot
    unapproved = MASCOT_WITH_SHEET.model_copy(update={"seed": None})
    assert builder(tmp_path, plan_data, mascot=unapproved).jobs(parse_plan(plan_data).units())[0].vision_reference is None


def test_a_mascot_reference_in_style_refs_is_never_sent_to_the_vision_check(tmp_path, plan_data):
    """No anchor, so the image request sends no references: only the vision check would read it."""
    png(tmp_path / "style_refs" / "mascot.png", (384, 512))
    mascot = MASCOT_WITH_SHEET.model_copy(update={"ref": "style_refs/mascot.png"})
    jobs_builder = builder(tmp_path, plan_data, mascot=mascot)
    assert jobs_builder.references(parse_plan(plan_data).units()[0]) == ()
    with pytest.raises(ConfigError, match="never sent to any API"):
        jobs_builder.jobs(parse_plan(plan_data).units())


def test_the_mascot_is_in_image_1_when_the_anchor_and_its_sheet_are_sent(tmp_path, plan_data):
    png(tmp_path / "library/style/anchor_v1_ref.png")
    png(tmp_path / "library/mascot/ref_v1.png", (384, 512))
    job = builder(tmp_path, plan_data, mascot=MASCOT_WITH_SHEET).jobs(parse_plan(plan_data).units())[0]
    assert job.fixes.mascot_in_image_1 is True


def test_a_retry_has_the_fixed_prompt_and_a_new_seed(tmp_path, plan_data):
    plan_data["scenes"][0]["units"][0]["seed"] = 5  # pinned: a retry still uses a new seed (spec §7.5)
    plan_data["scenes"][0]["units"][0]["props"] = ["wall clock"]
    jobs_builder = builder(tmp_path, plan_data)
    job = jobs_builder.jobs(parse_plan(plan_data).units())[0]
    assert job.seed == 5
    retry = jobs_builder.retry(job, ["text", "anatomy"])
    assert retry.seed == 1002  # the next seed after the two unpinned units' 1000 and 1001
    assert retry.prompt == retry_prompt("prompt for 001", ["text", "anatomy"], job.fixes)
    assert (retry.unit, retry.fingerprint, retry.references, retry.stem) == (job.unit, job.fingerprint, job.references, job.stem)


def test_a_job_for_one_unit_refuses_an_empty_prompt(tmp_path, plan_data):
    jobs_builder = builder(tmp_path, plan_data)
    unit = parse_plan(plan_data).units()[0].model_copy(update={"image_prompt": "  "})
    with pytest.raises(JobError, match="no image_prompt for 001"):
        jobs_builder.job(unit)
