"""Tests hors-réseau du routage OmniRoute."""
from __future__ import annotations

from octopus import catalog, journal, llm
from octopus.pricing import Usage


def test_catalog_injects_omniroute_free_model(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.delenv("OMNIROUTE_BASE_URL", raising=False)
    monkeypatch.setenv("OMNIROUTE_MODEL", "auto/free")
    cat = catalog.load()
    model = cat.model("omniroute/auto-free")
    assert model["api_model"] == "auto/free"
    assert model["cost_class"] == "free_quota"
    assert model["provider"] == "omniroute"
    assert cat.provider("omniroute")["base_url"] == "http://127.0.0.1:20128/v1"
    assert cat.task("podalux.write_job")["candidates"]["zero_cost"][0] == "omniroute/auto-free"
    assert cat.task("web.inspect_page")["candidates"]["zero_cost"][0] == "omniroute/auto-free"


def test_zero_cost_llm_uses_omniroute_without_paid_fallback(monkeypatch, providers_up):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_MODEL", "auto/free")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    providers_up.discard("omniroute")

    captured = {}

    def fake(provider, request):
        captured["provider"] = provider
        captured["request"] = request
        return llm.TransportResult(
            text='{"ok": true}', usage=Usage(prompt_tokens=10, completion_tokens=5, cache_hit_tokens=0),
            requested_model="auto/free", resolved_model="qwen/qwen3-32b", resolved_provider="groq",
            request_id="req-free", provider_cost_usd=0.0)

    monkeypatch.setattr(llm, "_transport_override", fake)
    result = llm.complete(
        "podalux.write_job",
        [{"role": "user", "content": "écris un job vidéo"}],
        agent="test",
        business="podalux",
        max_tokens=50,
        json_mode=True,
        profile="zero_cost",
    )
    assert result.provider == "omniroute"
    assert result.model == "omniroute/auto-free"
    assert captured["request"]["model"] == "auto/free"
    assert captured["provider"]["base_url"] == "http://127.0.0.1:20128/v1"


def test_zero_cost_does_not_pick_paid_model_when_free_route_is_down(monkeypatch, providers_up):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    providers_up.add("omniroute")
    providers_up.update({"groq", "gemini"})
    monkeypatch.setattr(llm, "provider_status",
                        lambda name, provider: (False, "down") if name in providers_up else (True, "up"))
    try:
        llm.complete(
            "podalux.write_job",
            [{"role": "user", "content": "test"}],
            agent="test", business="podalux", max_tokens=20, profile="zero_cost",
        )
    except llm.NoEligibleModel as exc:
        assert all(item["model"] not in {"deepseek/flash", "deepseek/v4-pro"} for item in exc.considered)
    else:
        raise AssertionError("zero_cost ne doit pas retomber sur DeepSeek payant")


def test_resolved_omniroute_route_is_exposed_and_journaled(monkeypatch, providers_up):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_MODEL", "auto/free")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    providers_up.discard("omniroute")
    monkeypatch.setattr(llm, "_transport_override", lambda provider, request: llm.TransportResult(
        text='{"ok": true}',
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        requested_model="auto/free",
        resolved_model="qwen/qwen3-32b",
        resolved_provider="groq",
        request_id="req-123",
        provider_cost_usd=0.0,
    ))

    result = llm.complete("podalux.write_job", [{"role": "user", "content": "test"}], profile="zero_cost")

    assert (result.requested_model, result.resolved_model, result.resolved_provider, result.request_id) == (
        "auto/free", "qwen/qwen3-32b", "groq", "req-123")
    (row,) = journal.query("SELECT * FROM llm_calls")
    assert (row["requested_model"], row["resolved_model"], row["resolved_provider"], row["request_id"]) == (
        "auto/free", "qwen/qwen3-32b", "groq", "req-123")
    assert row["provider_cost_usd"] == 0.0


def test_zero_cost_omniroute_requires_free_only_pool_attestation(monkeypatch, providers_up, transport):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    providers_up.discard("omniroute")

    try:
        llm.complete("podalux.write_job", [{"role": "user", "content": "test"}], profile="zero_cost")
    except llm.NoEligibleModel as exc:
        omni = next(item for item in exc.considered if item["model"] == "omniroute/auto-free")
        assert "attestation free_only absente" in omni["reason"]
    else:
        raise AssertionError("zero_cost doit refuser un pool OmniRoute non attesté")
    assert transport.calls == []


def test_zero_cost_blocks_paid_route_resolved_by_omniroute(monkeypatch, providers_up):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    providers_up.discard("omniroute")
    monkeypatch.setattr(llm, "_transport_override", lambda provider, request: llm.TransportResult(
        text='{"ok": true}', usage=Usage(prompt_tokens=10, completion_tokens=5),
        requested_model=request["model"], resolved_model="gpt-5", resolved_provider="openai",
        request_id="req-paid", provider_cost_usd=0.01))

    try:
        llm.complete("podalux.write_job", [{"role": "user", "content": "test"}], profile="zero_cost")
    except llm.NoEligibleModel:
        pass
    else:
        raise AssertionError("zero_cost doit bloquer une route résolue payante")
    (row,) = journal.query("SELECT * FROM llm_calls")
    assert row["status"] == "blocked" and row["provider_cost_usd"] == 0.01
    assert row["resolved_provider"] == "openai" and "coût résolu" in row["error"]


def test_zero_cost_blocks_unresolved_omniroute_response(monkeypatch, providers_up, transport):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    providers_up.discard("omniroute")
    transport.reply()

    try:
        llm.complete("podalux.write_job", [{"role": "user", "content": "test"}], profile="zero_cost")
    except llm.NoEligibleModel:
        pass
    else:
        raise AssertionError("zero_cost doit bloquer une route OmniRoute non résolue")
    (row,) = journal.query("SELECT * FROM llm_calls")
    assert row["status"] == "blocked" and "identité résolue absente" in row["error"]
