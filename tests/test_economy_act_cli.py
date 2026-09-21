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


def test_outcome_cli_reports_unknowns_without_llm(capsys, transport):
    from octopus import strategy
    from octopus.__main__ import main

    objective = strategy.create("objective", B, "O", created_by="human", statement="Premier client")
    hypothesis = strategy.create("hypothesis", B, "H", created_by="human", parent_id=objective, statement="À tester")
    experiment = strategy.create("experiment", B, "E", created_by="human", parent_id=hypothesis, action="Pilote")
    assert main(["economy", "outcome", B, str(experiment)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["delivery"]["status"] == "unknown"
    assert report["cash_by_currency"] == {}
    assert main(["economy", "outcome", B, "999999"]) == 2
    assert "introuvable" in capsys.readouterr().out
    assert transport.calls == []


def test_economic_cli_does_not_import_media_or_development(tmp_path):
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "-c", "from octopus.__main__ import main; import sys; "
                             f"main(['economy', 'status', '{B}']); "
                             "assert 'octopus.media.handlers' not in sys.modules; "
                             "assert 'octopus.dev_worker' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_supervised_cli_roundtrip_persists_and_keeps_reports_read_only():
    """Fixtures uniquement : commandes distinctes/reprises, aucune action commerciale réelle."""
    import re
    import subprocess
    import sys
    import time

    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "octopus", *args],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def added(*args):
        return re.search(r"#(\d+)", cli(*args)).group(1)

    objective = added("strategy", "add", "objective", B, "Fixture O", "--by", "human", "--set", "statement=Test")
    hypothesis = added("strategy", "add", "hypothesis", B, "Fixture H", "--by", "human", "--parent", objective,
                       "--set", "statement=Test")
    experiment = added("strategy", "add", "experiment", B, "Fixture E", "--by", "human", "--parent", hypothesis,
                       "--set", "action=Test hors ligne", "--set", "metric=customer_acceptance",
                       "--set", "target_value=1", "--set", "budget_limit=10", "--set", "budget_currency=EUR")
    cli("strategy", "move", "experiment", experiment, B, "running", "--by", "human")

    def observe(metric, value):
        return added("strategy", "add", "evidence", B, "Fixture " + metric, "--by", "human",
                     "--set", "nature=observed", "--set", "source_type=test_fixture",
                     "--set", "source_ref=fixture#" + metric, "--set", f"captured_at={time.time()}",
                     "--set", "experiment_id=" + experiment, "--set", "metric=" + metric,
                     "--set", f"value={value}", "--set", "observation=Fixture, pas un résultat commercial")

    before = json.loads(cli("economy", "outcome", B, experiment))
    assert before["delivery"]["status"] == "unknown" and before["human_minutes"]["observed_total"] is None
    proof = observe("delivery", 1)
    observe("human_minutes:production", 35)
    observe("customer_acceptance", 1)
    for direction, amount, category in (("in", "99", "customer_payment"), ("out", "7", "variable_cost")):
        cli("economy", "cash", B, direction, amount, "EUR", category, "--source", "fixture#" + category,
            "--experiment", experiment)
    # Réutilisation des liens pour la provenance, sans démarrer le dev-worker.
    from octopus import tasks
    task = str(tasks.enqueue(B, "development.task", {"goal": "Fixture, non exécutée"}))
    cli("strategy", "link", B, "evidence", proof, "task", task, "motivates")
    cli("strategy", "link", B, "experiment", experiment, "task", task, "improves")
    events_before = journal.query("SELECT COUNT(*) AS n FROM events")[0]["n"]
    report = json.loads(cli("economy", "outcome", B, experiment))
    assert journal.query("SELECT COUNT(*) AS n FROM events")[0]["n"] == events_before
    assert report["technical_completion"]["status"] == "unknown"  # improves n'exécute pas le lot
    assert report["delivery"]["status"] == "delivered" and report["customer_acceptance"]["status"] == "accepted"
    assert report["human_minutes"]["observed_total"] == 35
    assert report["contribution_by_currency"]["EUR"]["recorded_contribution"] == 92
    assert report["cash_evidence"][0]["source_ref"] == "fixture#customer_payment"
    cycle = json.loads(cli("economy", "cycle", B))
    assert cycle["evaluated"][0]["verdict"] == "supports"
    report = json.loads(cli("economy", "outcome", B, experiment))
    assert report["experiment_status"] == "completed" and report["customer_use"]["status"] == "unknown"
    assert "proposed" in cli("strategy", "list", "decision", B)
