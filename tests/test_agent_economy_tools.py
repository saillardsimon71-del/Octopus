"""Les agents pilotent eux-mêmes la boucle économique, dans les limites posées par le code."""
from __future__ import annotations

import json

from agents import deepseek, runtime, web_guard
from octopus import builtin_handlers, economy, journal, strategy, worker  # noqa: F401

B = "atelier_test"


def _tool(tool, **args):
    return runtime.TOOLS[tool]["fn"](args)


def test_agent_observation_is_observed_only_if_the_source_was_consulted():
    with journal.run(B, "agent"), web_guard.session():
        invented = _tool("record_observation", summary="Marché", observation="1000 ventes/jour",
                         source_ref="https://invente.example/stats", metric="ventes", value=1000)
        web_guard.record("https://exemple.fr/prix", web_guard.PUBLIC, web_guard.current())
        seen = _tool("record_observation", summary="Prix", observation="Le concurrent vend 19 EUR",
                     source_ref="https://exemple.fr/prix", metric="prix_concurrent", value="19", unit="EUR")
    assert invented["nature"] == "unverified" and seen["nature"] == "observed"
    row = strategy.get("evidence", invented["evidence_id"], B)
    assert row["source_ref"] == "https://invente.example/stats" and row["captured_at"] is None
    assert strategy.get("evidence", seen["evidence_id"], B)["value"] == 19.0


def test_agent_designs_and_runs_an_experiment_but_cannot_spend_without_allowance():
    with journal.run(B, "agent"), web_guard.session():
        channel = _tool("register_channel", kind="marketplace", name="Place de marché X", capabilities="sell, observe")
        created = _tool("propose_experiment", objective="Premier cash", hypothesis="Un guide PDF à 9 EUR se vend",
                        action="Mettre en vente le guide", metric="cash_net:EUR", target_value="9", stop_value=0,
                        deadline_days=14, budget_limit=20, budget_currency="EUR", channel_id=str(channel["channel_id"]))
        _tool("start_experiment", experiment_id=created["experiment_id"])
        denied = _tool("request_spend", amount=5, currency="EUR", purpose="frais de mise en vente",
                       experiment_id=created["experiment_id"])
    assert denied["status"] == "denied"
    experiment = strategy.get("experiment", created["experiment_id"], B)
    assert experiment["status"] == "running" and experiment["created_by"] == "agent:RUNTIME"
    assert experiment["channel_id"] == channel["channel_id"] and experiment["deadline_at"]
    assert economy.channels(B)[0]["nature"] == "unverified"
    economy.grant_allowance(B, 10, "EUR", granted_by="human", rationale="test")
    with journal.run(B, "agent"):
        ok = _tool("request_spend", amount=5, currency="EUR", purpose="frais", experiment_id=created["experiment_id"])
    assert ok["status"] == "authorized"


def test_scripted_agent_closes_the_loop_and_the_engine_decides(monkeypatch):
    actions = iter([
        {"tool": "propose_experiment", "args": {"objective": "Encaisser", "hypothesis": "Service express vendable",
                                                "action": "Proposer le service", "metric": "cash_net:EUR",
                                                "target_value": 30}},
        {"tool": "start_experiment", "args": {"experiment_id": 1}},
        {"tool": "economy_status", "args": {}},
        {"final": "expérience lancée"},
    ])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))
    result = runtime.run_agent("ORBIT", "trouve un moyen d'encaisser", max_steps=6, business=B)
    assert result["final"] == "expérience lancée"
    assert [s["tool"] for s in result["steps"]] == ["propose_experiment", "start_experiment", "economy_status"]
    # Le monde réel répond (encaissement observé), le moteur tranche sans LLM ni humain.
    economy.record_cash(B, "in", 45, "EUR", "vente", nature="observed", created_by="human", source_ref="relevé",
                        experiment_id=1)
    task = worker.enqueue(B, "economy.cycle")
    done = worker.run_one("w", kinds=["economy.cycle"], log=lambda s: None)
    assert done["id"] == task and done["status"] == "done"
    assert done["output"]["evaluated"] == [{"experiment_id": 1, "verdict": "supports",
                                            "reason": "cash_net:EUR = 45.0 >= cible 30.0"}]
    assert strategy.get("experiment", 1, B)["outcome"] == "supports"
    assert strategy.list_items("decision", B, status="proposed")


def test_economy_cli_round_trip(capsys, tmp_path):
    from octopus.__main__ import main
    export = tmp_path / "ventes.csv"
    export.write_text("date,amount,currency\n2026-09-17,7.5,EUR\n", encoding="utf-8")
    assert main(["economy", "import", B, str(export)]) == 0
    assert main(["economy", "import", B, str(tmp_path / "absent.csv")]) == 2
    assert main(["economy", "channel", B, "website", "Boutique", "--capabilities", "sell,observe"]) == 0
    assert main(["economy", "cash", B, "in", "12.5", "eur", "vente", "--source", "export.csv"]) == 0
    assert main(["economy", "cash", B, "in", "100", "EUR", "vente annoncée"]) == 0
    assert main(["economy", "allow", B, "5", "EUR", "essai", "--days", "7"]) == 0
    assert main(["economy", "policy", B, "--share", "0.5", "--max", "50", "--currency", "EUR"]) == 0
    assert main(["economy", "cash", B, "in", "-3", "EUR", "x"]) == 2
    capsys.readouterr()
    assert main(["economy", "status", B]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["cash"]["by_currency"]["EUR"]["net_observed"] == 20.0
    assert status["cash"]["by_currency"]["EUR"]["in_unverified"] == 100
    assert main(["economy", "cycle", B]) == 0
    assert '"granted"' in capsys.readouterr().out
    assert main(["economy", "access", B, "1", "--status", "active", "--access", "act"]) == 0
    assert economy.channels(B)[0]["access"] == "act"
    assert main(["economy", "actions", B]) == 0


def test_status_exposes_learnings_from_concluded_experiments():
    o = strategy.create("objective", B, "o", created_by="human", statement="o")
    h = strategy.create("hypothesis", B, "h", created_by="orbit", parent_id=o, statement="h")
    e = strategy.create("experiment", B, "Vendre un guide", created_by="orbit", parent_id=h, action="annonce",
                        metric="cash_net:EUR", target_value=10, deadline_at=1.0)
    strategy.transition("experiment", e, B, "running", actor="orbit")
    economy.cycle(B)
    learning = economy.status(B)["learnings"][0]
    assert learning["experiment_id"] == e and learning["outcome"] == "refutes" and learning["action"] == "annonce"


def test_agent_opens_a_new_business_that_the_portfolio_cycle_then_manages(transport):
    with journal.run(B, "agent"):
        opened = _tool("open_business", business_id="Ateliers Réparation!", name="Réparation",
                       thesis="Des réparations à domicile réservées en ligne")
        again = _tool("open_business", business_id="ateliers_reparation", name="x", thesis="y")
    assert opened["status"] == "opened" and opened["business_id"] == "ateliers_reparation"
    assert again == {"business_id": "ateliers_reparation", "status": "exists"}
    strategy.create("objective", "autre", "Autre", created_by="human", statement="x")
    task = worker.enqueue("octopus", "economy.cycle", {"portfolio": True, "drive": True, "mission_budget_usd": 0.01})
    done = worker.run_one("w", kinds=["economy.cycle"], log=lambda s: None)
    assert done["id"] == task and done["status"] == "done"
    managed = {r["business"]: r["drive"]["status"] for r in done["output"]["portfolio"]}
    assert managed["ateliers_reparation"] == "mission_queued" and managed["autre"] == "mission_queued"
    queued = journal.query("SELECT business, budget_usd FROM tasks WHERE kind='orbit.mission'")
    assert all(r["budget_usd"] == 0.01 for r in queued)
    assert transport.calls == []
