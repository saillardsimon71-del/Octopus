"""Tests hors-réseau du routage OmniRoute."""
from __future__ import annotations

import pytest

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


def test_devworker_uses_only_dedicated_omniroute_routes(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")

    cat = catalog.load()
    candidates = cat.task("development.step")["candidates"]

    expected = [
        "omniroute/devworker-gemini",
        "omniroute/devworker-groq",
        "omniroute/devworker-cloudflare",
    ]
    assert candidates["zero_cost"] == expected
    assert candidates["low_cost"] == expected
    cloudflare = cat.model("omniroute/devworker-cloudflare")
    assert cloudflare["api_model"] == "octopus-free-devworker-cloudflare"
    assert cloudflare["json_schema_mode"] == "tool_call"


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


def test_litellm_headers_resolve_real_provider_identity(monkeypatch):
    import sys
    import types
    from types import SimpleNamespace

    from octopus import llm

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        usage=None,
        model="octopus-free-devworker",
    )

    raw_response = SimpleNamespace(
        headers={
            "x-litellm-model-name": "groq/openai/gpt-oss-120b",
            "x-litellm-model-group": "octopus-free-devworker",
            "x-litellm-response-cost": "2.325e-05",
        },
        request_id="req-litellm",
        parse=lambda: response,
    )

    create = lambda **kwargs: raw_response
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=create)
            )
        )
    )

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda **kwargs: fake_client
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr(llm, "_transport_override", None)
    llm._clients.clear()

    result = llm._transport(
        {
            "base_url": "http://127.0.0.1:4000/v1",
            "api_key_env": None,
        },
        {
            "model": "octopus-free-devworker",
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 20,
        },
    )

    assert result.resolved_model == "groq/openai/gpt-oss-120b"
    assert result.resolved_provider == "groq"
    assert result.provider_cost_usd is None
    assert result.request_id == "req-litellm"


def install_tool_response(monkeypatch, name, arguments):
    import sys
    import types
    from types import SimpleNamespace

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=None,
            tool_calls=[SimpleNamespace(function=SimpleNamespace(
                name=name,
                arguments=arguments,
            ))],
        ))],
        usage=None,
        model="octopus-free-devworker-groq",
    )
    raw_response = SimpleNamespace(headers={}, request_id="req-tool", parse=lambda: response)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        with_raw_response=SimpleNamespace(create=lambda **kwargs: raw_response),
    )))
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda **kwargs: fake_client
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr(llm, "_transport_override", None)
    llm._clients.clear()


@pytest.mark.parametrize(("name", "arguments", "expected"), [
    ("read", '{"path":"octopus/dev_worker.py"}', {
        "action": "read", "path": "octopus/dev_worker.py", "query": None, "patch": None, "message": None,
    }),
    ("search", '{"query":"needle"}', {
        "action": "search", "path": None, "query": "needle", "patch": None, "message": None,
    }),
    ("search", '{"query":"needle","path":null}', {
        "action": "search", "path": None, "query": "needle", "patch": None, "message": None,
    }),
    ("patch", '{"patch":"--- a/x\\n+++ b/x\\n"}', {
        "action": "patch", "path": None, "query": None, "patch": "--- a/x\n+++ b/x\n", "message": None,
    }),
    ("test", '{}', {
        "action": "test", "path": None, "query": None, "patch": None, "message": None,
    }),
    ("commit", '{"message":"fix: safe cleanup"}', {
        "action": "commit", "path": None, "query": None, "patch": None, "message": "fix: safe cleanup",
    }),
])
def test_transport_normalizes_declarative_devworker_tool_calls(monkeypatch, name, arguments, expected):
    from octopus import dev_worker

    install_tool_response(monkeypatch, name, arguments)
    result = llm._transport(
        {"base_url": "http://127.0.0.1:4000/v1", "api_key_env": None},
        {
            "model": "octopus-free-devworker-groq",
            "messages": [{"role": "user", "content": "next"}],
            "max_tokens": 20,
            "tools": dev_worker.DEV_ACTION_TOOLS,
            "tool_choice": "required",
        },
    )

    assert dev_worker._parse_action(result.text) == expected


def test_transport_refuses_unknown_declarative_tool(monkeypatch):
    from octopus import dev_worker

    install_tool_response(monkeypatch, "delete", '{}')
    with pytest.raises(ValueError, match="inconnu"):
        llm._transport(
            {"base_url": "http://127.0.0.1:4000/v1", "api_key_env": None},
            {
                "model": "octopus-free-devworker-groq",
                "messages": [{"role": "user", "content": "next"}],
                "max_tokens": 20,
                "tools": dev_worker.DEV_ACTION_TOOLS,
                "tool_choice": "required",
            },
        )


@pytest.mark.parametrize(("name", "arguments"), [
    ("search", '{"path":null}'),
    ("read", '{"path":7}'),
    ("test", '{"path":"x"}'),
    ("read", '{'),
])
def test_transport_refuses_invalid_declarative_tool_arguments(monkeypatch, name, arguments):
    from octopus import dev_worker

    install_tool_response(monkeypatch, name, arguments)
    with pytest.raises(ValueError):
        llm._transport(
            {"base_url": "http://127.0.0.1:4000/v1", "api_key_env": None},
            {
                "model": "octopus-free-devworker-groq",
                "messages": [{"role": "user", "content": "next"}],
                "max_tokens": 20,
                "tools": dev_worker.DEV_ACTION_TOOLS,
                "tool_choice": "required",
            },
        )


def test_transport_never_executes_declarative_provider_tool(monkeypatch):
    from octopus import dev_worker

    executed = False

    def forbidden(*args, **kwargs):
        nonlocal executed
        executed = True
        raise AssertionError("provider tool must remain declarative")

    monkeypatch.setattr(dev_worker, "_tool", forbidden)
    install_tool_response(monkeypatch, "search", '{"query":"needle"}')
    result = llm._transport(
        {"base_url": "http://127.0.0.1:4000/v1", "api_key_env": None},
        {
            "model": "octopus-free-devworker-groq",
            "messages": [{"role": "user", "content": "next"}],
            "max_tokens": 20,
            "tools": dev_worker.DEV_ACTION_TOOLS,
            "tool_choice": "required",
        },
    )

    assert executed is False
    assert dev_worker._parse_action(result.text)["action"] == "search"
