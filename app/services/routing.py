"""Cost-aware model routing — prefer small/cheap models unless overridden."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class ModelChoice:
    provider: str
    model: str
    cost_tier: str  # economy | standard | premium
    reason: str


# Relative cost ranking (lower = cheaper). Used for preference routing.
MODEL_CATALOG: dict[str, dict[str, int]] = {
    "mock": {
        "mock-1": 0,
    },
    "digitalocean": {
        # Prefer small Llama / economy instruct models for batch work
        "llama3.2-3b-instruct": 1,
        "llama3-8b-instruct": 2,
        "meta-llama/Meta-Llama-3.1-8B-Instruct": 2,
        "llama3.3-70b-instruct": 8,
        "deepseek-v3.2": 6,
    },
    "openai": {
        "gpt-4o-mini": 2,
        "gpt-4o": 7,
        "gpt-4.1-mini": 2,
        "gpt-4.1": 7,
    },
    "anthropic": {
        "claude-3-5-haiku-latest": 2,
        "claude-3-5-sonnet-latest": 6,
        "claude-sonnet-4-20250514": 7,
    },
    "openai_compatible": {
        "default": 3,
    },
}

COST_TIER_MAX_RANK = {
    "economy": 2,
    "standard": 5,
    "premium": 99,
}


def _cheapest_for_provider(provider: str, max_rank: int) -> str | None:
    catalog = MODEL_CATALOG.get(provider) or {}
    eligible = [(m, r) for m, r in catalog.items() if r <= max_rank]
    if not eligible:
        return None
    eligible.sort(key=lambda x: (x[1], x[0]))
    return eligible[0][0]


def resolve_model(
    *,
    provider: str | None,
    model: str | None,
    cost_preference: str | None,
    settings: Settings,
) -> ModelChoice:
    """Resolve provider/model with cost preference.

    Rules:
    - Explicit model wins (client override).
    - Else pick cheapest catalog model for provider within cost_preference tier.
    - Defaults come from settings (DEFAULT_PROVIDER / DEFAULT_MODEL / DEFAULT_COST_PREFERENCE).
    """
    pref = (cost_preference or settings.default_cost_preference or "economy").lower().strip()
    if pref not in COST_TIER_MAX_RANK:
        pref = "economy"

    resolved_provider = (provider or settings.default_provider or "mock").strip()
    max_rank = COST_TIER_MAX_RANK[pref]

    if model and model.strip():
        return ModelChoice(
            provider=resolved_provider,
            model=model.strip(),
            cost_tier=pref,
            reason="explicit_model_override",
        )

    # Prefer configured default model if it fits the cost tier
    default_model = (settings.default_model or "").strip()
    if default_model:
        rank = (MODEL_CATALOG.get(resolved_provider) or {}).get(default_model)
        if rank is None or rank <= max_rank:
            return ModelChoice(
                provider=resolved_provider,
                model=default_model,
                cost_tier=pref,
                reason="settings_default_model",
            )

    cheapest = _cheapest_for_provider(resolved_provider, max_rank)
    if cheapest:
        return ModelChoice(
            provider=resolved_provider,
            model=cheapest,
            cost_tier=pref,
            reason=f"cost_preference_{pref}",
        )

    # Fallback: any catalog model, or a sensible economy default for DO
    catalog = MODEL_CATALOG.get(resolved_provider) or {}
    if catalog:
        model_name = min(catalog.items(), key=lambda x: x[1])[0]
        return ModelChoice(
            provider=resolved_provider,
            model=model_name,
            cost_tier=pref,
            reason="cheapest_available",
        )

    fallback = settings.default_model or "llama3.2-3b-instruct"
    return ModelChoice(
        provider=resolved_provider,
        model=fallback,
        cost_tier=pref,
        reason="fallback_default",
    )
