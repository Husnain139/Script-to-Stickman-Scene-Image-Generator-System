"""Opt-in checks against the real Cloudflare API: uv run pytest -m live -s. They cost a little."""

import asyncio
from pathlib import Path

import pytest

from stickman.cf.client import CloudflareClient
from stickman.ledger import LEDGER_FILE, Ledger
from stickman.meter import Meter
from stickman.pricing import image_cost_usd, load_pricing
from stickman.render.images import decode_image
from stickman.settings import load_config

pytestmark = pytest.mark.live
ROOT = Path(__file__).parent.parent
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
SIZE = (1920, 1088)


def test_one_klein_4b_image():
    """About 208 neurons: one 1920x1088 image with no reference images. Like every billable call, it
    goes through the meter, so it adds an entry to the workspace's ledger.jsonl (spec §9.7)."""
    cfg = load_config(ROOT)
    pricing = load_pricing(ROOT)
    meter = Meter(project="live-smoke", ledger=Ledger(ROOT / LEDGER_FILE), pricing=pricing)

    async def go():
        async with CloudflareClient(
            cfg.secrets.cf_account_id,
            cfg.secrets.cf_api_token.get_secret_value(),
            plan=cfg.settings.account.plan,
            timeout_s=cfg.settings.render.timeout_s,
        ) as client:
            return await meter.run(
                lambda: client.generate_image(
                    KLEIN_4B,
                    prompt="Clean black ink line art of one stickman waving, on a plain white background. No text.",
                    width=SIZE[0],
                    height=SIZE[1],
                    seed=12345,
                ),
                kind="image",
                model=KLEIN_4B,
                estimate_usd=image_cost_usd(pricing.image(KLEIN_4B), SIZE),
            )

    result = asyncio.run(go()).result
    print(f"neurons={result.neurons} request_id={result.request_id}")
    assert decode_image(result.image_bytes).size == SIZE
    assert result.neurons is not None and 150 < result.neurons < 260
    assert result.request_id
