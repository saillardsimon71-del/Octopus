"""Tests hors-réseau du routage OmniRoute."""
from __future__ import annotations

from octopus import catalog, llm
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
    assert cat.provider("omniroute")["base_url"] == "http://127.0.0.1:20128/api/v1"
    assert cat.task("podalux.write_job")["candidates"]["zero_cost"][0] == "omniroute/auto-free"


def test_zero_cost_llm_uses_omniroute_without_paid_fallback(monkeypatch, providers_up):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_MODEL", "auto/free")
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    providers_up.discard("omniroute")

    captured = {}

    def fake(provider, request):
        captured["provider"] = provider
        captured["request"] = request
        return '{"ok": true}', Usage(prompt_tokens=10, completion_tokens=5, cache_hit_tokens=0)

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
    assert captured["provider"]["base_url"] == "http://127.0.0.1:20128/api/v1"


def test_zero_cost_does_not_pick_paid_model_when_free_route_is_down(monkeypatch, providers_up):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    providers_up.add("omniroute")
    # Les autres providers cloud sont également indisponibles dans ce scénario.
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
