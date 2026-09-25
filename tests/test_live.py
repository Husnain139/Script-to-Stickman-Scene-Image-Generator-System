"""Opt-in checks against the real Cloudflare API: uv run pytest -m live. They cost a little."""

import asyncio
from pathlib import Path

import pytest

from stickman.cf.client import CloudflareClient
from stickman.render.images import decode_image
from stickman.settings import load_config

pytestmark = pytest.mark.live
ROOT = Path(__file__).parent.parent
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def test_one_klein_4b_image():
    """About 208 neurons: one 1920x1088 image with no reference images."""
    cfg = load_config(ROOT)

    async def go():
        async with CloudflareClient(
            cfg.secrets.cf_account_id,
            cfg.secrets.cf_api_token.get_secret_value(),
            plan=cfg.settings.account.plan,
            timeout_s=cfg.settings.render.timeout_s,
        ) as client:
            return await client.generate_image(
                KLEIN_4B,
                prompt="Clean black ink line art of one stickman waving, on a plain white background. No text.",
                width=1920,
                height=1088,
                seed=12345,
            )

    result = asyncio.run(go())
    assert decode_image(result.image_bytes).size == (1920, 1088)
    assert result.neurons is not None and 150 < result.neurons < 260
    assert result.request_id
