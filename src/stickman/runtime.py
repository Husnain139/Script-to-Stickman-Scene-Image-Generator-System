"""What the CLI and the review server share for talking to Cloudflare: the client, the secrets that
logs and messages mask, and the typical cost of a vision check."""

from __future__ import annotations

from stickman.cf.client import CloudflareClient
from stickman.pricing import PricingConfig, llm_cost_usd
from stickman.qc.vision import TYPICAL_TOKENS
from stickman.settings import AppConfig, ConfigError, Settings


def secrets_of(cfg: AppConfig) -> tuple[str, ...]:
    """What run logs, state.json and messages built from Cloudflare errors mask: the token and the account
    id (Cloudflare's routing errors echo the request path, which holds the account id)."""
    if cfg.secrets is None:
        return ()
    return (cfg.secrets.cf_api_token.get_secret_value(), cfg.secrets.cf_account_id)


def build_client(cfg: AppConfig) -> CloudflareClient:
    if cfg.secrets is None:
        raise ConfigError("Cloudflare credentials are not loaded (CF_ACCOUNT_ID and CF_API_TOKEN in .env)")
    return CloudflareClient(
        cfg.secrets.cf_account_id,
        cfg.secrets.cf_api_token.get_secret_value(),
        plan=cfg.settings.account.plan,
        timeout_s=cfg.settings.render.timeout_s,
    )


def check_usd(settings: Settings, pricing: PricingConfig) -> float:
    """What a typical vision check costs (qwen's tokens, measured in M4)."""
    if not settings.qc.vision:
        return 0.0
    price = pricing.llm(settings.llm.vision_model)
    return 0.0 if price is None else llm_cost_usd(price, *TYPICAL_TOKENS)
