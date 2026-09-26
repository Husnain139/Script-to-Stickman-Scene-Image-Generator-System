from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from stickman.qc.pixel import QC_HEIGHT, largest_component, pixel_check, qc_copy
from stickman.settings import QCSettings

REAL = Path(__file__).parent.parent / "fixtures" / "qc"


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("clean", None),
        ("shoes_and_tie", None),  # small solid black areas pass (spec §11.1 max_black_blob_fraction)
        ("with_text", None),  # text is line art too: the vision check catches it (§11.2)
        ("colour", "style"),
        ("filled_background", "background_filled"),
        ("dark_with_detail", "background_filled"),  # dark, but with line detail: not safety_filtered
        ("big_black_blob", "background_filled"),
        ("all_black", "safety_filtered"),
        ("grey", "safety_filtered"),
        ("blurred", "safety_filtered"),
        ("white", "empty"),  # blank white is never safety_filtered
        ("cream", "empty"),
        ("almost_empty", "empty"),
    ],
)
def test_each_fixture_drawing_gets_its_result(drawings, name, reason):
    assert pixel_check(getattr(drawings, name)(), QCSettings()).reason == reason


@pytest.mark.parametrize("name", ["klein4b_cavemen.png", "klein4b_fire_night.png", "klein4b_clock.png"])
def test_real_klein_images_pass(name):
    result = pixel_check(Image.open(REAL / name), QCSettings())
    assert result.reason is None, result


def test_a_jpeg_of_the_clean_drawing_passes(jpeg):
    from stickman.render.images import decode_image

    assert pixel_check(decode_image(jpeg), QCSettings()).reason is None


def test_the_checks_run_on_a_copy_480_px_tall(drawings):
    small = qc_copy(drawings.clean())
    assert (small.height, small.width, small.mode) == (QC_HEIGHT, 847, "RGB")
    assert qc_copy(Image.new("L", (1088, 1920), 255)).size == (272, QC_HEIGHT)


def test_what_was_measured_is_kept_rounded(drawings):
    result = pixel_check(drawings.clean(), QCSettings())
    assert result.white_fraction > 0.9 and result.ink_fraction > 0.005 and result.colour_fraction == 0
    assert result.lap_var > 15 and result.lum_std > 6
    assert all(round(value, 4) == value for value in result.model_dump().values() if isinstance(value, float))


def test_the_largest_black_area_is_measured_only_when_all_black_pixels_are_over_the_limit(drawings):
    assert pixel_check(drawings.shoes_and_tie(), QCSettings()).black_blob_fraction is None
    blob = pixel_check(drawings.big_black_blob(), QCSettings())
    assert blob.black_blob_fraction == pytest.approx(0.109, abs=0.01)


@pytest.mark.parametrize(("box", "fraction", "reason"), [
    ((40, 40, 220, 185), 0.05, None),  # the live check's clean dense line art measured 0.0436 and 0.0495
    ((40, 40, 240, 222), 0.07, "background_filled"),
])
def test_a_black_area_passes_up_to_six_percent_of_the_image(drawings, box, fraction, reason):
    image = drawings.clean()
    ImageDraw.Draw(image).rectangle(box, fill=(0, 0, 0))
    result = pixel_check(image, QCSettings())
    assert result.black_blob_fraction == pytest.approx(fraction, abs=0.005)
    assert result.reason == reason


def test_a_filled_background_outranks_colour(drawings):
    image = drawings.colour()  # the red sun is in the top half
    image.paste((120, 120, 120), (0, image.height // 2, image.width, image.height))
    assert pixel_check(image, QCSettings()).reason == "background_filled"


def test_the_thresholds_come_from_settings(drawings):
    assert pixel_check(drawings.clean(), QCSettings(min_white_fraction=0.99)).reason == "background_filled"
    assert pixel_check(drawings.colour(), QCSettings(max_color_fraction=0.5)).reason is None


def test_diagonal_pixels_join_one_area():
    mask = np.zeros((5, 5), dtype=bool)
    for i in range(5):
        mask[i, i] = True
    mask[0, 4] = True
    assert largest_component(mask) == 5
    assert largest_component(np.zeros((3, 3), dtype=bool)) == 0
