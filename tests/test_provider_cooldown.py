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

    def __init__(self):
        super().__init__("429 rate limit reached")
        self.response = type("Response", (), {
            "status_code": 429,
            "headers": {"retry-after": "60"},
        })()


def _omni(monkeypatch) -> None:
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")


def _omni_ok(request, payload='{"ok": "omni"}'):
    return llm.TransportResult(
        text=payload,
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        requested_model=request["model"],
        resolved_model="free/test-model",
        resolved_provider="free-provider",
        request_id="req-test",
        provider_cost_usd=0.0,
    )


def test_dead_gateway_fails_once_then_skips_second_omniroute_model(
        transport, providers_up, monkeypatch):
    """Cas H3 #66 : devworker meurt, auto-free du même gateway est sauté."""
    _omni(monkeypatch)
    seen = []

    def handler(provider, request):
        seen.append(request["model"])
        if request["model"] == "groq/openai/gpt-oss-120b":
            return APIConnectionError("Connection error.")
        if request["model"] == "auto/best-free":
            pytest.fail("le deuxième modèle OmniRoute doit être sauté")
        if request["model"] == "kilo-auto/free":
            return ('{"ok": "kilo"}', Usage(prompt_tokens=10, completion_tokens=5))
        pytest.fail(f"modèle inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert result.model == "kilo/auto-free"
    assert seen == ["groq/openai/gpt-oss-120b", "kilo-auto/free"]
    assert "omniroute" in llm._provider_cooldowns
    skipped = next(
        item for item in result.justification["considered"]
        if item["model"] == "omniroute/auto-free"
    )
    assert "provider injoignable" in skipped["reason"]
    assert "cooldown" in skipped["reason"]


def test_active_provider_cooldown_skips_omniroute_on_next_completion(
        transport, providers_up, monkeypatch):
    _omni(monkeypatch)
    llm._provider_cooldowns["omniroute"] = (
        time.monotonic() + 60,
        "provider injoignable (APIConnectionError); cooldown 60s",
    )

    def handler(provider, request):
        assert request["model"] == "kilo-auto/free"
        return ('{"ok": "kilo"}', Usage(prompt_tokens=10, completion_tokens=5))

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )
    assert result.model == "kilo/auto-free"
    assert transport.models == ["kilo-auto/free"]


def test_provider_recovers_after_cooldown_expiry(transport, providers_up, monkeypatch):
    _omni(monkeypatch)
    llm._provider_cooldowns["omniroute"] = (time.monotonic() - 1, "expired")

    def handler(provider, request):
        assert request["model"] == "groq/openai/gpt-oss-120b"
        return _omni_ok(request)

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert result.model == "omniroute/devworker-groq"
    assert "omniroute" not in llm._provider_cooldowns


def test_429_stays_model_level_and_second_omniroute_route_can_run(
        transport, providers_up, monkeypatch):
    _omni(monkeypatch)

    def handler(provider, request):
        if request["model"] == "groq/openai/gpt-oss-120b":
            return _FakeRateLimit()
        if request["model"] == "auto/best-free":
            return _omni_ok(request, '{"ok": "auto"}')
        pytest.fail(f"modèle inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert result.model == "omniroute/auto-free"
    assert transport.models == ["groq/openai/gpt-oss-120b", "auto/best-free"]
    assert "omniroute/devworker-groq" in llm._rate_limit_cooldowns
    assert "omniroute" not in llm._provider_cooldowns


def test_invalid_json_does_not_poison_provider(
        transport, providers_up, monkeypatch):
    _omni(monkeypatch)
    devworker_calls = 0

    def handler(provider, request):
        nonlocal devworker_calls
        if request["model"] == "groq/openai/gpt-oss-120b":
            devworker_calls += 1
            return _omni_ok(request, "not-json")
        if request["model"] == "auto/best-free":
            return _omni_ok(request, '{"ok": "auto"}')
        pytest.fail(f"modèle inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete(
        "agent.react_step", MSG, profile="flash_fallback",
        json_mode=True, validate=llm.parse_json,
    )

    assert devworker_calls >= 1
    assert result.model == "omniroute/auto-free"
    assert "omniroute" not in llm._provider_cooldowns


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
    assert llm._set_provider_cooldown("omniroute", exc) is None
    assert "omniroute" not in llm._provider_cooldowns


def test_auth_error_does_not_set_provider_cooldown():
    exc = AuthenticationError("401 invalid_api_key")
    assert llm._set_provider_cooldown("omniroute", exc) is None
    assert "omniroute" not in llm._provider_cooldowns
