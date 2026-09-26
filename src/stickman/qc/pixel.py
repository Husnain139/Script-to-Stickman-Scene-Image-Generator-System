"""Pixel checks: free, on a 480 px-tall copy of the image (spec §11.1)."""

from __future__ import annotations

from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict

from stickman.settings import QCSettings

QC_HEIGHT = 480
INK_LUM = 128  # ink: luminance below this (spec §11.1 min_ink_fraction)
COLOUR_VALUE = 0.3  # a colour pixel is also brighter than this (HSV value, 0-1)

PixelReason = Literal["safety_filtered", "empty", "background_filled", "style"]


class PixelResult(BaseModel):
    """What the pixel checks measured (rounded to 4 places), and the first failure or None."""

    model_config = ConfigDict(extra="forbid")

    reason: PixelReason | None
    lum_std: float
    lum_mean: float
    ink_fraction: float
    lap_var: float
    white_fraction: float
    colour_fraction: float
    black_fraction: float
    black_blob_fraction: float | None = None  # measured only when all black pixels together are over the limit


def qc_copy(image: Image.Image) -> Image.Image:
    """An RGB copy QC_HEIGHT pixels tall, with the same aspect ratio."""
    rgb = image.convert("RGB")
    width = max(1, round(rgb.width * QC_HEIGHT / rgb.height))
    return rgb.resize((width, QC_HEIGHT), Image.Resampling.LANCZOS)


def largest_component(mask: np.ndarray) -> int:
    """The pixel count of the largest 8-connected area of True pixels."""
    height, width = mask.shape
    flat = mask.ravel().tolist()
    seen = bytearray(len(flat))
    best = 0
    for start, value in enumerate(flat):
        if not value or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        size = 0
        while stack:
            index = stack.pop()
            size += 1
            y, x = divmod(index, width)
            for ny in (y - 1, y, y + 1):
                if ny < 0 or ny >= height:
                    continue
                row = ny * width
                for nx in (x - 1, x, x + 1):
                    if 0 <= nx < width:
                        other = row + nx
                        if flat[other] and not seen[other]:
                            seen[other] = 1
                            stack.append(other)
        best = max(best, size)
    return best


def pixel_check(image: Image.Image, qc: QCSettings) -> PixelResult:
    small = qc_copy(image)
    lum = np.asarray(small.convert("L"), dtype=np.float64)
    hsv = np.asarray(small.convert("HSV"), dtype=np.float64) / 255.0
    total = lum.size
    laplacian = lum[:-2, 1:-1] + lum[2:, 1:-1] + lum[1:-1, :-2] + lum[1:-1, 2:] - 4 * lum[1:-1, 1:-1]
    black = lum < qc.black_lum
    measured = {
        "lum_std": float(lum.std()),
        "lum_mean": float(lum.mean()),
        "ink_fraction": float((lum < INK_LUM).sum() / total),
        "lap_var": float(laplacian.var()) if laplacian.size else 0.0,
        "white_fraction": float((lum >= qc.near_white_lum).sum() / total),
        "colour_fraction": float(((hsv[..., 1] > qc.color_sat) & (hsv[..., 2] > COLOUR_VALUE)).sum() / total),
        "black_fraction": float(black.sum() / total),
    }
    reason, blob = _first_failure(measured, black, qc)
    rounded = {key: round(value, 4) for key, value in measured.items()}
    return PixelResult(reason=reason, black_blob_fraction=None if blob is None else round(blob, 4), **rounded)


def _first_failure(
    m: dict[str, float], black: np.ndarray, qc: QCSettings
) -> tuple[PixelReason | None, float | None]:
    """spec §11.1: the checks run in this order, and the first match decides. In step 4 a filled
    background outranks colour, as in the reason order of §11.3."""
    if m["lum_std"] < qc.uniform_std_max:
        return ("empty" if m["lum_mean"] >= qc.near_white_lum else "safety_filtered"), None
    if m["ink_fraction"] < qc.min_ink_fraction:
        return "empty", None
    if m["lap_var"] < qc.blur_lap_var_min:
        return "safety_filtered", None
    if m["white_fraction"] < qc.min_white_fraction:
        return "background_filled", None
    blob = None
    if m["black_fraction"] > qc.max_black_blob_fraction:  # otherwise no single black area can be over it
        blob = largest_component(black) / black.size
        if blob > qc.max_black_blob_fraction:
            return "background_filled", blob
    if m["colour_fraction"] > qc.max_color_fraction:
        return "style", blob
    return None, blob
