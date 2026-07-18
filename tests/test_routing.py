"""Unit tests for cost-aware model routing."""

from __future__ import annotations

from app.core.config import Settings
from app.services.routing import resolve_model


def _settings(**kwargs) -> Settings:
    defaults = {
        "MOCK_PROVIDER": True,
        "DEFAULT_PROVIDER": "digitalocean",
        "DEFAULT_MODEL": "",
        "DEFAULT_COST_PREFERENCE": "economy",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_resolve_model_economy_picks_cheap_model():
    choice = resolve_model(
        provider="digitalocean",
        model=None,
        cost_preference="economy",
        settings=_settings(),
    )
    assert choice.provider == "digitalocean"
    assert choice.model == "openai-gpt-oss-20b"
    assert choice.cost_tier == "economy"
    assert "cost_preference" in choice.reason or choice.reason == "cheapest_available"


def test_resolve_model_explicit_override_wins():
    choice = resolve_model(
        provider="digitalocean",
        model="llama3.3-70b-instruct",
        cost_preference="economy",
        settings=_settings(),
    )
    assert choice.model == "llama3.3-70b-instruct"
    assert choice.reason == "explicit_model_override"
    assert choice.cost_tier == "economy"


def test_resolve_model_invalid_preference_falls_back_to_economy():
    choice = resolve_model(
        provider="digitalocean",
        model=None,
        cost_preference="not-a-real-tier",
        settings=_settings(DEFAULT_COST_PREFERENCE="premium"),
    )
    assert choice.cost_tier == "economy"
    assert choice.model == "openai-gpt-oss-20b"


def test_resolve_model_settings_default_when_fits_tier():
    choice = resolve_model(
        provider="digitalocean",
        model=None,
        cost_preference="economy",
        settings=_settings(DEFAULT_MODEL="openai-gpt-oss-120b"),
    )
    assert choice.model == "openai-gpt-oss-120b"
    assert choice.reason == "settings_default_model"


def test_resolve_model_uses_settings_provider_default():
    choice = resolve_model(
        provider=None,
        model=None,
        cost_preference="economy",
        settings=_settings(DEFAULT_PROVIDER="mock", DEFAULT_MODEL="mock-1"),
    )
    assert choice.provider == "mock"
    assert choice.model == "mock-1"
