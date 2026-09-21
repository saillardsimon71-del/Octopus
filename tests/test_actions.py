"""Actions réelles génériques : bloquées et tracées tant que canal, accès, exécuteur ou dépense manquent."""
from __future__ import annotations

import pytest

from agents import runtime
from octopus import actions, economy, journal, strategy
from octopus.strategy import StrategyError

B = "atelier_test"


@pytest.fixture(autouse=True)
def no_executors(monkeypatch):
    monkeypatch.setattr(actions, "_EXECUTORS", {})
    monkeypatch.delenv("OCTOPUS_ENABLE_SMTP_EXECUTOR", raising=False)


def _channel():
    return economy.add_channel(B, "marketplace", "Place X", created_by="agent:SOUT", capabilities=["sell"])


def _experiment():
    o = strategy.create("objective", B, "o", created_by="human", statement="o")
    h = strategy.create("hypothesis", B, "h", created_by="orbit", parent_id=o, statement="h")
    e = strategy.create("experiment", B, "e", created_by="orbit", parent_id=h, action="a", metric="ventes",
                        target_value=1)
    strategy.transition("experiment", e, B, "running", actor="orbit")
    return e


def test_action_is_blocked_until_channel_is_active_opened_by_human_and_has_an_executor():
    channel = _channel()
    steps = []
    result = actions.propose(B, channel, "list_product", {"title": "Guide"}, requested_by="agent:GROWTH")
    steps.append(result["reason"])
    economy.update_channel(B, channel, actor="orbit", status="active")
    with pytest.raises(StrategyError):
        economy.update_channel(B, channel, actor="orbit", access="act")
    steps.append(actions.propose(B, channel, "list_product", {}, requested_by="agent:GROWTH")["reason"])
    economy.update_channel(B, channel, actor="human", access="act")
    steps.append(actions.propose(B, channel, "list_product", {}, requested_by="agent:GROWTH")["reason"])
    assert ["activation" in steps[0], "accès 'act'" in steps[1], "aucun exécuteur" in steps[2]] == [True, True, True]
    assert [a["status"] for a in actions.list_actions(B)] == ["blocked"] * 3


def test_executed_action_produces_observed_evidence_for_the_experiment():
    channel = _channel()
    economy.update_channel(B, channel, actor="human", status="active", access="act")
    experiment = _experiment()
    seen = []

    def list_product(channel_row, payload):
        seen.append((channel_row["name"], payload))
        return {"observation": "annonce en ligne", "source_ref": "https://place-x.example/annonce/42",
                "metric": "ventes", "value": 1}

    actions.register_executor("Marketplace", "list_product", list_product, cost_class="free_quota")
    result = actions.propose(B, channel, "LIST_PRODUCT", {"title": "Guide"}, requested_by="agent:GROWTH",
                             experiment_id=experiment, idempotency_key="exp1-list")
    assert result["status"] == "executed" and seen == [("Place X", {"title": "Guide"})]
    evidence = strategy.get("evidence", result["evidence_id"], B)
    assert evidence["nature"] == "observed" and evidence["experiment_id"] == experiment and evidence["value"] == 1
    assert actions.propose(B, channel, "list_product", {}, requested_by="agent:GROWTH",
                           idempotency_key="exp1-list")["duplicate"] is True
    assert economy.evaluate_experiment(B, experiment)["verdict"] == "supports"


def test_paid_action_needs_an_allowance_and_failures_release_the_spend():
    channel = _channel()
    economy.update_channel(B, channel, actor="human", status="active", access="act")
    actions.register_executor("marketplace", "boost", lambda c, p: (_ for _ in ()).throw(TimeoutError("API lente")),
                              cost_class="paid")
    undeclared = actions.propose(B, channel, "boost", {}, requested_by="agent:GROWTH")
    assert undeclared["status"] == "blocked" and "coût" in undeclared["reason"]
    blocked = actions.propose(B, channel, "boost", {}, requested_by="agent:GROWTH", spend_amount=5, spend_currency="EUR")
    assert blocked["status"] == "blocked" and "dépense" in blocked["reason"]
    economy.grant_allowance(B, 5, "EUR", granted_by="human", rationale="boost")
    failed = actions.propose(B, channel, "boost", {}, requested_by="agent:GROWTH", spend_amount=5, spend_currency="EUR")
    assert failed["status"] == "failed" and "TimeoutError" in failed["reason"]
    assert economy.authorize_spend(B, 5, "EUR", "réutilisable", requested_by="human")["status"] == "authorized"
    actions.register_executor("marketplace", "silent", lambda c, p: {"observation": "ok"}, cost_class="local")
    assert actions.propose(B, channel, "silent", {}, requested_by="agent:GROWTH")["status"] == "failed"


def test_executor_can_require_idempotency_before_irreversible_action():
    channel = _channel()
    economy.update_channel(B, channel, actor="human", status="active", access="act")
    calls = []
    actions.register_executor(
        "marketplace", "message", lambda c, p: calls.append(p) or {
            "observation": "message envoyé", "source_ref": "message:test"
        }, cost_class="local", requires_idempotency=True,
    )
    blocked = actions.propose(B, channel, "message", {"text": "bonjour"}, requested_by="human")
    assert blocked["status"] == "blocked" and "idempotency_key" in blocked["reason"] and calls == []
    done = actions.propose(
        B, channel, "message", {"text": "bonjour"}, requested_by="human", idempotency_key="msg-1"
    )
    assert done["status"] == "executed" and calls == [{"text": "bonjour"}]
    assert actions.propose(
        B, channel, "message", {"text": "autre"}, requested_by="human", idempotency_key="msg-1"
    )["duplicate"] is True


def test_agent_tool_and_status_expose_actions():
    channel = _channel()
    with journal.run(B, "agent"):
        result = runtime.TOOLS["act_on_channel"]["fn"]({"channel_id": str(channel), "action": "message",
                                                        "payload": "bonjour"})
    assert result["status"] == "blocked"
    status = economy.status(B)
    assert status["action_executors"] == [] and status["recent_actions"][0]["status"] == "blocked"
    with pytest.raises(StrategyError):
        actions.propose("podalux", channel, "message", {}, requested_by="orbit")
