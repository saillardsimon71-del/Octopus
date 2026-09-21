from __future__ import annotations

import json

from agents import runtime
from octopus import actions, economy, journal


B = "phase2_cli_test"


def _setup_channel(monkeypatch):
    monkeypatch.setattr(actions, "_EXECUTORS", {})
    channel = economy.add_channel(B, "marketplace", "Canal test", created_by="human")
    economy.update_channel(B, channel, actor="human", status="active", access="act")
    actions.register_executor(
        "marketplace",
        "message",
        lambda c, p: {
            "observation": "message accepté",
            "source_ref": "https://example.test/messages/1",
            "metric": "messages",
            "value": 1,
        },
        cost_class="local",
        requires_idempotency=True,
    )
    return channel


def test_economy_act_cli_executes_with_required_idempotency(monkeypatch, capsys):
    from octopus.__main__ import main

    channel = _setup_channel(monkeypatch)
    payload = json.dumps({"text": "bonjour"})

    assert main([
        "economy", "act", B, str(channel), "message",
        "--payload", payload,
        "--idempotency-key", "cli-msg-1",
    ]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "executed"

    assert main([
        "economy", "act", B, str(channel), "message",
        "--payload", payload,
        "--idempotency-key", "cli-msg-1",
    ]) == 0
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["duplicate"] is True


def test_agent_tool_passes_idempotency_key(monkeypatch):
    channel = _setup_channel(monkeypatch)
    args = {
        "channel_id": channel,
        "action": "message",
        "payload": {"text": "bonjour"},
        "idempotency_key": "agent-msg-1",
    }

    with journal.run(B, "agent"):
        first = runtime.TOOLS["act_on_channel"]["fn"](args)
        second = runtime.TOOLS["act_on_channel"]["fn"](args)

    assert first["status"] == "executed"
    assert second["duplicate"] is True
