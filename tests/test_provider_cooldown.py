"""Régression H3 #66 : un gateway partagé mort ne doit échouer qu'une fois."""
from __future__ import annotations

import time

import pytest

from octopus import llm
from octopus.pricing import Usage


MSG = [{"role": "user", "content": "next"}]


class APIConnectionError(Exception):
    pass


class AuthenticationError(Exception):
    status_code = 401


class ReadTimeout(Exception):
    pass


class ConnectTimeout(Exception):
    pass


class _FakeRateLimit(Exception):
    status_code = 429
    body = {"metadata": {"limit_source": "upstream_provider_shared_pool"}}

    def __init__(self):
        super().__init__("429 rate limit reached")
        self.response = type("Response", (), {
            "status_code": 429,
            "headers": {"retry-after": "60"},
        })()


def _free_ok(request, payload='{"ok": "free"}'):
    return llm.TransportResult(
        text=payload,
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        requested_model=request["model"],
        resolved_model=request["model"],
        resolved_provider="free-provider",
        request_id="req-test",
        provider_cost_usd=0.0,
    )


def test_dead_gateway_fails_once_then_skips_second_openrouter_model(
        transport, providers_up, monkeypatch):
    """Une panne commune coupe les autres modèles du même fournisseur."""
    seen = []

    def handler(provider, request):
        seen.append(request["model"])
        if request["model"] == "fixture/text-delta:free":
            return APIConnectionError("Connection error.")
        if request["model"] == "fixture/text-gamma:free":
            pytest.fail("le deuxième modèle OpenRouter doit être sauté")
        if request["model"] == "deepseek-flash":
            return ('{"ok": "deepseek"}', Usage(prompt_tokens=10, completion_tokens=5))
        pytest.fail(f"modèle inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert result.model == "deepseek/flash"
    assert seen == ["fixture/text-delta:free", "deepseek-flash"]
    assert "openrouter" in llm._provider_cooldowns
    skipped = next(
        item for item in result.justification["considered"]
        if item["model"] == "openrouter/fixture/text-gamma:free"
    )
    assert "provider injoignable" in skipped["reason"]
    assert "cooldown" in skipped["reason"]


def test_active_provider_cooldown_skips_openrouter_on_next_completion(
        transport, providers_up, monkeypatch):
    llm._provider_cooldowns["openrouter"] = (
        time.monotonic() + 60,
        "provider injoignable (APIConnectionError); cooldown 60s",
    )

    def handler(provider, request):
        assert request["model"] == "deepseek-flash"
        return ('{"ok": "deepseek"}', Usage(prompt_tokens=10, completion_tokens=5))

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )
    assert result.model == "deepseek/flash"
    assert transport.models == ["deepseek-flash"]


def test_provider_recovers_after_cooldown_expiry(transport, providers_up, monkeypatch):
    llm._provider_cooldowns["openrouter"] = (time.monotonic() - 1, "expired")

    def handler(provider, request):
        assert request["model"] == "fixture/text-delta:free"
        return _free_ok(request)

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert result.model == "openrouter/fixture/text-delta:free"
    assert "openrouter" not in llm._provider_cooldowns


def test_429_stays_model_level_and_second_openrouter_route_can_run(
        transport, providers_up, monkeypatch):

    def handler(provider, request):
        if request["model"] == "fixture/text-delta:free":
            return _FakeRateLimit()
        if request["model"] == "fixture/text-gamma:free":
            return _free_ok(request, '{"ok": "auto"}')
        pytest.fail(f"modèle inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert result.model == "openrouter/fixture/text-gamma:free"
    assert transport.models == ["fixture/text-delta:free", "fixture/text-gamma:free"]
    assert "openrouter/fixture/text-delta:free" in llm._rate_limit_cooldowns
    assert "openrouter" not in llm._provider_cooldowns


def test_invalid_json_does_not_poison_provider(
        transport, providers_up, monkeypatch):
    devworker_calls = 0

    def handler(provider, request):
        nonlocal devworker_calls
        if request["model"] == "fixture/text-delta:free":
            devworker_calls += 1
            return _free_ok(request, "not-json")
        if request["model"] == "fixture/text-gamma:free":
            return _free_ok(request, '{"ok": "auto"}')
        pytest.fail(f"modèle inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert devworker_calls >= 1
    assert result.model == "openrouter/fixture/text-gamma:free"
    assert "openrouter" not in llm._provider_cooldowns


def test_connection_classifier_is_conservative():
    assert llm._is_provider_connection_error(APIConnectionError("Connection error"))
    assert llm._is_provider_connection_error(ConnectionError("connection refused"))
    assert llm._is_provider_connection_error(ConnectionRefusedError("refused"))
    assert llm._is_provider_connection_error(ConnectTimeout("connect timed out"))

    # Ces erreurs peuvent être modèle/applicatives : ne pas couper tout le provider.
    assert not llm._is_provider_connection_error(ReadTimeout("slow response"))
    assert not llm._is_provider_connection_error(TimeoutError("generic timeout"))
    assert not llm._is_provider_connection_error(_FakeRateLimit())
    assert not llm._is_provider_connection_error(AuthenticationError("invalid_api_key"))
    assert not llm._is_provider_connection_error(ValueError("invalid json"))


def test_connection_classifier_follows_wrapped_network_cause():
    inner = APIConnectionError("connection refused")
    try:
        raise RuntimeError("wrapper") from inner
    except RuntimeError as outer:
        assert llm._is_provider_connection_error(outer)


def test_structured_output_error_does_not_set_provider_cooldown():
    class FailedGeneration(Exception):
        body = {
            "error": {
                "message": "Failed to generate JSON",
                "failed_generation": "{broken",
            }
        }

    exc = FailedGeneration("400")
    assert llm._structured_method_error(exc) is True
    assert llm._set_provider_cooldown("openrouter", exc) is None
    assert "openrouter" not in llm._provider_cooldowns


def test_auth_error_does_not_set_provider_cooldown():
    exc = AuthenticationError("401 invalid_api_key")
    assert llm._set_provider_cooldown("openrouter", exc) is None
    assert "openrouter" not in llm._provider_cooldowns
