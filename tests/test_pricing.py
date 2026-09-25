import pytest
from pydantic import ValidationError

from stickman.pricing import (
    ImagePrice,
    LLMPrice,
    format_usd,
    image_cost_usd,
    llm_cost_usd,
    load_pricing,
    neurons_usd,
    usd_neurons,
)
from stickman.settings import ConfigError

KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
GPT_OSS = "@cf/openai/gpt-oss-120b"


def test_the_default_pricing_loads_when_there_is_no_config_file(tmp_path):
    pricing = load_pricing(tmp_path)
    assert pricing.free_daily_usd == 0.11
    assert pricing.free_daily_neurons == pytest.approx(10_000)
    assert pricing.image(KLEIN_4B).formula == "tiles"
    assert pricing.image(KLEIN_9B).formula == "megapixels"
    assert pricing.image("@cf/black-forest-labs/flux-2-dev").supports_steps is True
    assert pricing.llm(GPT_OSS) == LLMPrice(kind="llm", in_per_m=0.35, out_per_m=0.75)


def test_the_klein_4b_estimate_matches_the_measured_cost(tmp_path):
    usd = image_cost_usd(load_pricing(tmp_path).image(KLEIN_4B), (1920, 1088))
    assert usd_neurons(usd) == pytest.approx(207.59, rel=0.01)  # measured in M0


def test_the_klein_9b_estimate_matches_the_measured_cost(tmp_path):
    usd = image_cost_usd(load_pricing(tmp_path).image(KLEIN_9B), (1920, 1088))
    assert usd_neurons(usd) == pytest.approx(1541, rel=0.01)  # measured in M0


def test_klein_4b_adds_each_reference_images_tiles():
    price = ImagePrice(kind="image", out_tile=0.000287, in_tile=0.000059)
    usd = image_cost_usd(price, (1024, 1024), [(512, 512), (512, 384)])
    assert usd == pytest.approx(4 * 0.000287 + 1 * 0.000059 + 0.75 * 0.000059)


def test_klein_9b_rounds_megapixels_up():
    price = ImagePrice(kind="image", first_mp=0.015, extra_mp=0.002, in_mp=0.002)
    assert image_cost_usd(price, (1024, 1024)) == pytest.approx(0.015)
    assert image_cost_usd(price, (1025, 1024)) == pytest.approx(0.017)
    assert image_cost_usd(price, (1024, 1024), [(512, 512)]) == pytest.approx(0.017)


def test_dev_multiplies_by_the_steps():
    price = ImagePrice(kind="image", out_tile_step=0.00041, in_tile_step=0.00021, supports_steps=True)
    usd = image_cost_usd(price, (1024, 1024), [(512, 512)], steps=25)
    assert usd == pytest.approx(25 * (4 * 0.00041 + 1 * 0.00021))


@pytest.mark.parametrize(
    "fields",
    [
        {"out_tile": 0.1},
        {"out_tile": 0.1, "in_tile": 0.1, "first_mp": 0.1, "extra_mp": 0.1, "in_mp": 0.1},
        {},
    ],
)
def test_an_image_price_needs_exactly_one_formula(fields):
    with pytest.raises(ValidationError):
        ImagePrice(kind="image", **fields)


def test_llm_cost_and_estimate(tmp_path):
    price = LLMPrice(kind="llm", in_per_m=0.35, out_per_m=0.75)
    assert llm_cost_usd(price, 1_000_000, 2_000_000) == pytest.approx(1.85)
    pricing = load_pricing(tmp_path)
    assert pricing.llm_estimate(GPT_OSS, 4000, 1000) == pytest.approx((1000 * 0.35 + 1000 * 0.75) / 1e6)
    assert pricing.llm_cost(GPT_OSS, 1000, 1000) == pytest.approx(0.0011)
    assert pricing.llm_cost(GPT_OSS, None, 1000) is None
    assert pricing.llm_estimate("@cf/unknown/model", 4000, 1000) == 0.0


def test_neuron_prices():
    assert neurons_usd(1000) == pytest.approx(0.011)
    assert usd_neurons(0.011) == pytest.approx(1000)


def test_an_unpriced_image_model_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="no image price"):
        load_pricing(tmp_path).image("@cf/unknown/model")


def test_a_bad_pricing_file_names_the_file(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pricing.yaml").write_text(
        "schema_version: 1\nfree_daily_usd: 0.11\nmodels:\n  x: {kind: image, out_tile: 1}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="pricing.yaml"):
        load_pricing(tmp_path)


@pytest.mark.parametrize(("amount", "text"), [(0.74, "$0.74"), (0.0023, "$0.0023"), (0.0, "$0.00"), (15.0, "$15.00")])
def test_dollars_keep_small_amounts_readable(amount, text):
    assert format_usd(amount) == text
