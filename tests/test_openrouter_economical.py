from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from octopus import journal, llm
from browser_evidence import prove
from octopus.pricing import Usage


MSG = [{"role": "user", "content": "Réponds en JSON."}]


def _free(request, provider_cost=0.0, resolved=None):
    return llm.TransportResult(
        text='{"final":"ok"}', usage=Usage(prompt_tokens=20, completion_tokens=8),
        requested_model=request["model"], resolved_model=resolved or request["model"],
        resolved_provider="FreeHost", provider_cost_usd=provider_cost,
    )


def test_openrouter_direct_enforces_zero_price_and_observed_cost(transport, providers_up, monkeypatch):
    prove('agent.react_step', "openrouter/fixture/text-delta:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    transport.handler = lambda provider, request: _free(request)

    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True)

    assert result.model == "openrouter/fixture/text-delta:free"
    assert result.provider_cost_usd == 0
    assert transport.calls[0][1]["extra_body"]["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    row = journal.query("SELECT * FROM llm_calls ORDER BY id DESC LIMIT 1")[0]
    assert row["resolved_provider"] == "FreeHost" and row["provider_cost_usd"] == 0


def test_economical_openrouter_structured_400_uses_same_model_text_once(transport, providers_up, monkeypatch):
    prove('agent.react_step', "openrouter/fixture/text-delta:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class UnsupportedFormat(Exception):
        status_code = 400
        body = {"error": {"message": "json_object response_format is not supported"}}

    def handler(provider, request):
        if request["model"] == "fixture/text-delta:free" and "response_format" in request:
            return UnsupportedFormat("400")
        return _free(request)

    transport.handler = handler
    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)

    assert result.model == "openrouter/fixture/text-delta:free" and result.data == {"final": "ok"}
    assert transport.models == ["fixture/text-delta:free"] * 2
    assert "response_format" not in transport.calls[1][1]
    assert transport.calls[1][1]["extra_body"]["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    rows = journal.query("SELECT status, justification FROM llm_calls ORDER BY id")
    assert [row["status"] for row in rows] == ["error", "ok"]
    assert json.loads(rows[1]["justification"])["structured_method"] == "text"


def test_economical_caps_two_free_routes_and_three_requests(transport, providers_up, monkeypatch):
    prove('agent.react_step', "openrouter/fixture/text-delta:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    prove("agent.react_step", "openrouter/fixture/text-gamma:free")
    class UnsupportedFormat(Exception):
        status_code = 400
        body = {"error": {"message": "response_format is not supported"}}

    def handler(provider, request):
        if request["model"] == "deepseek-flash":
            return ('{"final":"ok"}', Usage(prompt_tokens=20, completion_tokens=8))
        return UnsupportedFormat("400")

    transport.handler = handler
    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)

    assert result.model == "deepseek/flash"
    assert transport.models == ["fixture/text-delta:free"] * 2 + ["fixture/text-gamma:free", "deepseek-flash"]


def test_429_with_structured_word_does_not_retry_same_route(transport, providers_up, monkeypatch):
    prove('agent.plan', "openrouter/fixture/text-delta:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class RateLimit(Exception):
        status_code = 429
        body = {"error": {"message": "response_format json_object rate limit"}}
        response = type("Response", (), {"status_code": 429, "headers": {"retry-after": "60"}})()

    def handler(provider, request):
        if request["model"].endswith(":free"):
            return RateLimit("429")
        return ('{"tasks":[]}', Usage(prompt_tokens=20, completion_tokens=8))

    transport.handler = handler
    result = llm.complete("agent.plan", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)

    assert result.model == "deepseek/flash"
    assert transport.models == ["fixture/text-delta:free", "deepseek-flash"]
    assert "429" in llm._rate_limit_cooldown_reason("openrouter/fixture/text-delta:free")


@pytest.mark.parametrize("cost,resolved", [(None, None), (0.01, None), (0.0, "paid/model")])
def test_openrouter_unattested_or_paid_response_is_blocked(transport, providers_up, monkeypatch,
                                                            cost, resolved):
    prove('agent.react_step', "openrouter/fixture/text-delta:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def handler(provider, request):
        if request["model"] == "fixture/text-delta:free":
            return _free(request, cost, resolved)
        return _free(request)

    transport.handler = handler
    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True)

    assert result.model == "deepseek/flash"
    rows = journal.query("SELECT status FROM llm_calls ORDER BY id")
    assert [row["status"] for row in rows] == ["blocked", "ok"]
    assert transport.models == ["fixture/text-delta:free", "deepseek-flash"]


def test_openrouter_account_429_cools_sibling_route_and_survives_restart(transport, providers_up,
                                                                          monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class RateLimit(Exception):
        status_code = 429
        body = {"code": 429, "metadata": {"limit_source": "account"}}
        response = type("Response", (), {"status_code": 429, "headers": {"retry-after": "60"}})()

    transport.handler = lambda provider, request: RateLimit("429 rate limit")
    with pytest.raises(RateLimit):
        llm.complete("octopus.json", MSG, profile="bench", pin_model="openrouter/fixture/vision-beta:free")
    assert "quota OpenRouter" in llm._provider_cooldown_reason("openrouter")
    llm._provider_cooldowns.clear()
    llm._rate_limit_cooldowns.clear()
    assert "429" in llm._rate_limit_cooldown_reason("openrouter/fixture/vision-beta:free")
    assert "429" in llm._provider_cooldown_reason("openrouter")


def test_retry_after_http_date_is_respected():
    until = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=75))

    class RateLimit(Exception):
        status_code = 429
        response = type("Response", (), {"status_code": 429, "headers": {"retry-after": until}})()

    assert 70 < llm._rate_limit_delay(RateLimit("429 rate limit")) < 80


def test_benchmark_reputation_selects_successful_free_route(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    prove("octopus.json", "openrouter/fixture/text-gamma:free", passed=0, total=5)
    prove("octopus.json", "openrouter/fixture/text-delta:free")
    transport.handler = lambda provider, request: _free(request)

    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True)

    assert result.model == "openrouter/fixture/text-delta:free"
    assert transport.models == ["fixture/text-delta:free"]
