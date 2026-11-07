"""Model prices: what one call costs, and where that number came from.

2026-11 report: a run used 1,930,172 input / 203,253 output tokens and the
cost panel said ``0.0000`` USD against a 10 USD budget. The gateway priced
every call at the operator's ``POLISCOPE_MODEL_PRICE_*`` variables, which no
deployment sets, and a researcher-supplied endpoint never received a price at
all -- so the budget could never be spent and the panel reported a cost of
zero that was simply false.

Resolution order (first hit wins):

1. **Configured** -- the operator's ``POLISCOPE_MODEL_PRICE_*`` variables, for
   the deployment's own vendor account.
2. **List** -- the vendor's published price for a model name this module
   knows. Today that is the DeepSeek family, the only vendor this deployment
   has verified end to end. The *standard (peak)* tier is used on purpose:
   DeepSeek halves prices off-peak, so the list figure is an upper bound,
   never an understatement of what the budget has spent.
3. **Unknown** -- ``None``. The cost of an unknown model is not zero, it is
   unknown, and the read side reports it as such (CLAUDE.md 7) instead of
   inventing a price for an arbitrary vendor or relay.

Every price carries its ``source`` so the UI can say "estimated from the
DeepSeek list price" rather than presenting an estimate as a bill.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from packages.models.endpoint_config import DEFAULT_MODEL_NAME

PRICE_INPUT_ENV = "POLISCOPE_MODEL_PRICE_INPUT_PER_1M_USD"
PRICE_OUTPUT_ENV = "POLISCOPE_MODEL_PRICE_OUTPUT_PER_1M_USD"
PRICE_CACHED_INPUT_ENV = "POLISCOPE_MODEL_PRICE_CACHED_INPUT_PER_1M_USD"

_PER_MILLION = Decimal(1_000_000)

PriceSource = Literal["configured", "list"]


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per one million tokens. ``cached_input`` is the cache-hit rate;
    ``None`` bills cached prompt tokens at the ordinary input rate."""

    input_per_1m_usd: Decimal
    output_per_1m_usd: Decimal
    cached_input_per_1m_usd: Decimal | None = None
    source: PriceSource = "configured"
    vendor: str | None = None

    def cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> Decimal:
        cached = max(0, min(cached_input_tokens, input_tokens))
        cached_rate = (
            self.input_per_1m_usd
            if self.cached_input_per_1m_usd is None
            else self.cached_input_per_1m_usd
        )
        return (
            Decimal(input_tokens - cached) * self.input_per_1m_usd
            + Decimal(cached) * cached_rate
            + Decimal(output_tokens) * self.output_per_1m_usd
        ) / _PER_MILLION

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "vendor": self.vendor,
            "input_per_1m_usd": float(self.input_per_1m_usd),
            "output_per_1m_usd": float(self.output_per_1m_usd),
            "cached_input_per_1m_usd": (
                None
                if self.cached_input_per_1m_usd is None
                else float(self.cached_input_per_1m_usd)
            ),
        }


# DeepSeek list prices, standard (peak) tier, USD per 1M tokens.
# Source: https://api-docs.deepseek.com/quick_start/pricing (checked
# 2026-11-07). ``deepseek-flash`` is the current Flash name; the legacy
# ``deepseek-v4-flash`` is still accepted and billed at the Flash price.
DEEPSEEK_FLASH_PRICE = ModelPrice(
    input_per_1m_usd=Decimal("0.30"),
    output_per_1m_usd=Decimal("1.20"),
    cached_input_per_1m_usd=Decimal("0.006"),
    source="list",
    vendor="DeepSeek",
)
DEEPSEEK_PRO_PRICE = ModelPrice(
    input_per_1m_usd=Decimal("1.32"),
    output_per_1m_usd=Decimal("3.96"),
    cached_input_per_1m_usd=Decimal("0.044"),
    source="list",
    vendor="DeepSeek",
)


def list_price(model_name: str | None) -> ModelPrice | None:
    """The published price for a known model name, else ``None``.

    Matching is by family, not exact string: relays re-label models
    (``deepseek/deepseek-flash``, ``DeepSeek-V4-Flash``), and every Flash or
    Pro variant DeepSeek still serves bills at that family's price.
    """
    if not model_name:
        return None
    name = model_name.strip().lower().rsplit("/", 1)[-1]
    if "deepseek" not in name:
        return None
    if "flash" in name:
        return DEEPSEEK_FLASH_PRICE
    if "pro" in name:
        return DEEPSEEK_PRO_PRICE
    return None


def configured_price(environ: Mapping[str, str]) -> ModelPrice | None:
    """The operator's explicit price, or ``None`` when neither rate is set."""
    raw_input = environ.get(PRICE_INPUT_ENV)
    raw_output = environ.get(PRICE_OUTPUT_ENV)
    if not raw_input and not raw_output:
        return None
    raw_cached = environ.get(PRICE_CACHED_INPUT_ENV)
    return ModelPrice(
        input_per_1m_usd=Decimal(raw_input or "0"),
        output_per_1m_usd=Decimal(raw_output or "0"),
        cached_input_per_1m_usd=Decimal(raw_cached) if raw_cached else None,
        source="configured",
    )


def resolve_price(
    model_name: str | None,
    configured: ModelPrice | None = None,
) -> ModelPrice | None:
    """Configured price first, then the list price, else unknown."""
    return configured or list_price(model_name)


def task_price(
    model_config: Mapping[str, object] | None,
    environ: Mapping[str, str],
) -> tuple[str, ModelPrice | None]:
    """The (model name, price) a task's calls are billed at.

    Mirrors the worker's gateway choice (apps/worker/jobs.py): a task with its
    own endpoint runs its one model name at that model's list price -- an
    operator price describes the operator's vendor account, not the
    researcher's -- while a task on the deployment default runs
    ``POLISCOPE_MODEL_NAME`` at the operator's price, else its list price.
    """
    deployment_model = environ.get("POLISCOPE_MODEL_NAME") or DEFAULT_MODEL_NAME
    config = model_config or {}
    if config.get("base_url") and config.get("api_key"):
        name = str(config.get("model_name") or deployment_model)
        return name, list_price(name)
    return deployment_model, resolve_price(
        deployment_model, configured_price(environ)
    )


def cached_prompt_tokens(usage: Mapping[str, Any]) -> int:
    """Cache-hit prompt tokens from a chat-completions ``usage`` block.

    DeepSeek reports ``prompt_cache_hit_tokens``; the OpenAI dialect (and
    relays that pass it through) reports ``prompt_tokens_details.cached_tokens``.
    Absent both, nothing was cached as far as we can tell.
    """
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            hit = details.get("cached_tokens")
    try:
        return max(0, int(hit or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "DEEPSEEK_FLASH_PRICE",
    "DEEPSEEK_PRO_PRICE",
    "PRICE_CACHED_INPUT_ENV",
    "PRICE_INPUT_ENV",
    "PRICE_OUTPUT_ENV",
    "ModelPrice",
    "PriceSource",
    "cached_prompt_tokens",
    "configured_price",
    "list_price",
    "resolve_price",
    "task_price",
]
