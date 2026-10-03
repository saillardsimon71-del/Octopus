"""Offline transport checks: tools stay declarative and validated."""
import pytest
from octopus import llm


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
        model="fixture/text-gamma:free",
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
        {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
        {
            "model": "fixture/text-gamma:free",
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
            {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
            {
                "model": "fixture/text-gamma:free",
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
            {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
            {
                "model": "fixture/text-gamma:free",
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
        {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
        {
            "model": "fixture/text-gamma:free",
            "messages": [{"role": "user", "content": "next"}],
            "max_tokens": 20,
            "tools": dev_worker.DEV_ACTION_TOOLS,
            "tool_choice": "required",
        },
    )

    assert executed is False
    assert dev_worker._parse_action(result.text)["action"] == "search"
