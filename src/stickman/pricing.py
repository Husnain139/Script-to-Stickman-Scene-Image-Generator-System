"""Cost estimates from config/pricing.yaml (spec §9.6), and the neuron price (spec §9.7)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stickman.config_files import load_config_file
from stickman.settings import ConfigError

USD_PER_NEURON = 0.011 / 1000
TILE_PIXELS = 512 * 512
MP_PIXELS = 1024 * 1024

Size = tuple[int, int]

# Which prices each formula needs. Tiles and megapixels are area-based (M0, docs/m0-findings.md).
_FORMULAS: dict[str, tuple[str, ...]] = {
    "tiles": ("out_tile", "in_tile"),  # FLUX.2 [klein] 4B
    "megapixels": ("first_mp", "extra_mp", "in_mp"),  # FLUX.2 [klein] 9B
    "tile_steps": ("out_tile_step", "in_tile_step"),  # FLUX.2 [dev]
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImagePrice(_Model):
    kind: Literal["image"]
    out_tile: float | None = Field(None, ge=0)
    in_tile: float | None = Field(None, ge=0)
    first_mp: float | None = Field(None, ge=0)
    extra_mp: float | None = Field(None, ge=0)
    in_mp: float | None = Field(None, ge=0)
    out_tile_step: float | None = Field(None, ge=0)
    in_tile_step: float | None = Field(None, ge=0)
    supports_negative_prompt: bool = False
    supports_steps: bool = False

    @model_validator(mode="after")
    def _one_formula(self) -> ImagePrice:
        given = {name for names in _FORMULAS.values() for name in names if getattr(self, name) is not None}
        if not any(given == set(names) for names in _FORMULAS.values()):
            raise ValueError(
                "give the prices of exactly one formula: out_tile + in_tile, "
                "first_mp + extra_mp + in_mp, or out_tile_step + in_tile_step"
            )
        return self

    @property
    def formula(self) -> str:
        return next(name for name, names in _FORMULAS.items() if getattr(self, names[0]) is not None)


class LLMPrice(_Model):
    kind: Literal["llm"]
    in_per_m: float = Field(ge=0)
    out_per_m: float = Field(ge=0)


class PricingConfig(_Model):
    schema_version: Literal[1] = 1
    free_daily_usd: float = Field(ge=0)
    models: dict[str, Annotated[ImagePrice | LLMPrice, Field(discriminator="kind")]]

    def image(self, model: str) -> ImagePrice:
        price = self.models.get(model)
        if not isinstance(price, ImagePrice):
            raise ConfigError(f"config/pricing.yaml has no image price for {model}")
        return price

    def llm(self, model: str) -> LLMPrice | None:
        price = self.models.get(model)
        return price if isinstance(price, LLMPrice) else None

    def llm_estimate(self, model: str, prompt_chars: int, max_tokens: int) -> float:
        """The most a chat call can cost: its prompt (about 4 characters a token) plus max_tokens."""
        price = self.llm(model)
        return 0.0 if price is None else llm_cost_usd(price, prompt_chars // 4, max_tokens)

    def llm_cost(self, model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
        """A chat call's cost from its token counts, when the response had them."""
        price = self.llm(model)
        if price is None or input_tokens is None or output_tokens is None:
            return None
        return llm_cost_usd(price, input_tokens, output_tokens)

    @property
    def free_daily_neurons(self) -> float:
        return usd_neurons(self.free_daily_usd)


def _tiles(size: Size) -> float:
    return size[0] * size[1] / TILE_PIXELS


def _mp(size: Size) -> float:
    return size[0] * size[1] / MP_PIXELS


def image_cost_usd(price: ImagePrice, size: Size, refs: Sequence[Size] = (), *, steps: int = 1) -> float:
    """spec §9.6. `refs` are the reference images' sizes; `steps` matters only for FLUX.2 dev."""
    if price.formula == "tiles":
        return _tiles(size) * price.out_tile + sum(_tiles(ref) * price.in_tile for ref in refs)
    if price.formula == "megapixels":
        return (
            price.first_mp
            + max(0, math.ceil(_mp(size) - 1)) * price.extra_mp
            + sum(math.ceil(_mp(ref)) * price.in_mp for ref in refs)
        )
    return steps * (_tiles(size) * price.out_tile_step + sum(_tiles(ref) * price.in_tile_step for ref in refs))


def llm_cost_usd(price: LLMPrice, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * price.in_per_m + output_tokens * price.out_per_m) / 1_000_000


def neurons_usd(neurons: float) -> float:
    return neurons * USD_PER_NEURON


def usd_neurons(usd: float) -> float:
    return usd / USD_PER_NEURON


def format_usd(amount: float) -> str:
    """Dollars: 2 decimals, or 4 under 10 cents so that one image's cost doesn't read $0.00."""
    return f"${amount:.4f}" if 0 < abs(amount) < 0.1 else f"${amount:.2f}"


def load_pricing(workspace: Path) -> PricingConfig:
    return load_config_file(PricingConfig, workspace, "pricing.yaml")
