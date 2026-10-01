from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from octopus import journal, llm
from octopus.pricing import Usage


MSG = [{"role": "user", "content": "Réponds en JSON."}]


def _free(request, provider_cost=0.0, resolved=None):
    return llm.TransportResult(
        text='{"final":"ok"}', usage=Usage(prompt_tokens=20, completion_tokens=8),
        requested_model=request["model"], resolved_model=resolved or request["model"],
        resolved_provider="FreeHost", provider_cost_usd=provider_cost,
    )


def test_openrouter_direct_enforces_zero_price_and_observed_cost(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    transport.handler = lambda provider, request: _free(request)

    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True)

    assert result.model == "openrouter/dots-3-free"
    assert result.provider_cost_usd == 0
    assert transport.calls[0][1]["extra_body"]["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    row = journal.query("SELECT * FROM llm_calls ORDER BY id DESC LIMIT 1")[0]
    assert row["resolved_provider"] == "FreeHost" and row["provider_cost_usd"] == 0


@pytest.mark.parametrize("cost,resolved", [(None, None), (0.01, None), (0.0, "paid/model")])
def test_openrouter_unattested_or_paid_response_is_blocked(transport, providers_up, monkeypatch,
                                                            cost, resolved):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def handler(provider, request):
        if request["model"] == "dots-studio/dots-3-note-preview:free":
            return _free(request, cost, resolved)
        return _free(request)

    transport.handler = handler
    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True)

    assert result.model == "deepseek/flash"
    rows = journal.query("SELECT status FROM llm_calls ORDER BY id")
    assert [row["status"] for row in rows] == ["blocked", "ok"]
    assert transport.models == ["dots-studio/dots-3-note-preview:free", "deepseek-flash"]


def test_openrouter_account_429_cools_sibling_route_and_survives_restart(transport, providers_up,
                                                                          monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class RateLimit(Exception):
        status_code = 429
        body = {"code": 429, "metadata": {"limit_source": "account"}}
        response = type("Response", (), {"status_code": 429, "headers": {"retry-after": "60"}})()

    transport.handler = lambda provider, request: RateLimit("429 rate limit")
    with pytest.raises(RateLimit):
        llm.complete("octopus.json", MSG, profile="bench", pin_model="openrouter/qwen3.8-27b-free")
    assert "quota OpenRouter" in llm._provider_cooldown_reason("openrouter")
    llm._provider_cooldowns.clear()
    llm._rate_limit_cooldowns.clear()
    assert "429" in llm._rate_limit_cooldown_reason("openrouter/qwen3.8-27b-free")
    assert "429" in llm._provider_cooldown_reason("openrouter")


def test_retry_after_http_date_is_respected():
    until = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=75))

    class RateLimit(Exception):
        status_code = 429
        response = type("Response", (), {"status_code": 429, "headers": {"retry-after": until}})()

    assert 70 < llm._rate_limit_delay(RateLimit("429 rate limit")) < 80


def test_benchmark_reputation_selects_successful_free_route(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    with journal.run("octopus", "bench") as ctx:
        for model, passed in (("omniroute/devworker-groq", 0),
                              ("openrouter/dots-3-free", 1)):
            for repeat in range(2):
                journal.record_bench_result({"ts": time.time(), "bench_run_id": ctx.id,
                                             "suite": "octopus.octopus_evals", "task": "octopus.json",
                                             "item": "strict", "model": model, "repeat": repeat,
                                             "passed": passed, "score": float(passed)})
    transport.handler = lambda provider, request: _free(request)

    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True)

    assert result.model == "openrouter/dots-3-free"
    assert transport.models == ["dots-studio/dots-3-note-preview:free"]
