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
    # Sans mesure, aucune réfutation du marché ne doit entrer dans les apprentissages.
    assert learning["experiment_id"] == e and learning["outcome"] == "inconclusive" and learning["action"] == "annonce"


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



def test_generic_agent_prompt_forbids_invented_observed_facts():
    with journal.run(B, "agent"):
        system, _, _ = runtime.build_prompts("LEDGER", "résume l'état économique")
    assert "RÈGLE DE PREUVE" in system
    assert "n'apparaît pas dans un résultat d'outil" in system
    assert "inconnue" in system


def test_agent_keeps_tool_result_context_for_mission_synthesis(monkeypatch):
    payload = {"overview": {"available": 4}, "resources": [{"key": "site_sitequivend", "detail": "x" * 500}]}
    actions = iter([
        {"tool": "resources_status", "args": {}},
        {"final": "terminé"},
    ])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))
    monkeypatch.setitem(runtime.TOOLS["resources_status"], "fn", lambda args: payload)
    result = runtime.run_agent("LEDGER", "observe seulement", max_steps=2, business=B)
    assert len(result["steps"][0]["result"]) > 200
    assert "site_sitequivend" in result["steps"][0]["result"]


def test_orbit_mission_can_return_compact_search_browse_trace(monkeypatch):
    from agents import task_handlers  # noqa: F401

    monkeypatch.setattr(runtime, "run_mission", lambda *args, **kwargs: {
        "plan": [{"role": "FORGE", "task": "collecter"}],
        "results": [{
            "role": "FORGE",
            "task": "collecter",
            "final": "fini",
            "steps": [
                {"step": 1, "tool": "search", "result": "titre\nhttps://example.com/source"},
                {"step": 2, "tool": "remember", "result": "memo"},
                {"step": 3, "tool": "browse", "result": '{"url":"https://example.com/source","texte":"preuve"}'},
            ],
        }],
        "rapport": "rapport",
        "synthesis_status": "validated",
    })

    task_id = worker.enqueue(B, "orbit.mission", {"goal": "tester", "trace_tools": True})
    done = worker.run_one("w", kinds=["orbit.mission"], log=lambda s: None)

    assert done["status"] == "done"
    assert done["output"]["plan_trace"] == [{"role": "FORGE", "task": "collecter"}]
    assert done["output"]["tool_trace"] == [
        {"role": "FORGE", "step": 1, "tool": "search", "args": {}, "result": "titre\nhttps://example.com/source"},
        {"role": "FORGE", "step": 3, "tool": "browse", "args": {},
         "result": '{"url":"https://example.com/source","texte":"preuve"}'},
    ]


    assert done["output"]["trace_summary"] == {
        "totals": {"search": 1, "browse": 1},
        "browse_search_ratio": 1.0,
        "by_role": {"FORGE": {"search": 1, "browse": 1}},
        "max_steps_roles": [],
        "search_queries": [],
        "repeated_search_queries": 0,
        "browse_urls": [],
        "browsed_from_search": [],
        "cross_role_browsed_from_search": [],
        "lockstep_forced_browses": 0,
    }
    assert done["output"]["subtask_trace"] == [
        {"role": "FORGE", "task": "collecter", "final": "fini", "steps": 3}
    ]


def test_orbit_mission_trace_is_opt_in(monkeypatch):
    from agents import task_handlers  # noqa: F401

    monkeypatch.setattr(runtime, "run_mission", lambda *args, **kwargs: {
        "plan": [],
        "results": [{"role": "FORGE", "steps": [{"step": 1, "tool": "search", "result": "x"}]}],
        "rapport": "rapport",
        "synthesis_status": "validated",
    })

    worker.enqueue(B, "orbit.mission", {"goal": "tester"})
    done = worker.run_one("w", kinds=["orbit.mission"], log=lambda s: None)

    assert "plan_trace" not in done["output"]
    assert "tool_trace" not in done["output"]
    assert "trace_summary" not in done["output"]
    assert "subtask_trace" not in done["output"]


def test_degraded_orbit_mission_preserves_results_without_strategy_evidence(monkeypatch):
    from agents import task_handlers  # noqa: F401

    objective_id = strategy.create(
        "objective", B, "Tester une mission", created_by="human", statement="Tester une mission économique"
    )
    monkeypatch.setattr(runtime, "run_mission", lambda *args, **kwargs: {
        "plan": [{"role": "SOUT", "task": "observer"}],
        "results": [{"role": "SOUT", "task": "observer", "final": "preuve brute", "steps": []}],
        "rapport": "Synthèse LLM indisponible. Les résultats bruts sont conservés.",
        "synthesis_status": "degraded",
        "synthesis_error": "NoEligibleModel: test",
    })

    task_id = worker.enqueue(B, "orbit.mission", {"goal": "tester", "objective_id": objective_id})
    done = worker.run_one("w", kinds=["orbit.mission"], log=lambda s: None)

    assert done["status"] == "done_degraded"
    assert done["output"]["synthesis_status"] == "degraded"
    assert done["output"]["rapport_nature"] == "unavailable"
    assert done["output"]["results"][0]["final"] == "preuve brute"
    assert "evidence_id" not in done["output"]["strategy"]
    assert not [row for row in strategy.list_items("evidence", B) if row.get("origin_task_id") == task_id]


def test_mission_trace_summary_counts_equivalent_searches_and_cross_role_reuse():
    from agents.task_handlers import _mission_trace_summary

    summary = _mission_trace_summary([
        {
            "role": "SOUT",
            "final": "sources",
            "steps": [
                {
                    "tool": "search",
                    "args": {"query": "Retards de paiement PME France"},
                    # Runtime stocke le retour string de search via json.dumps : les sauts de ligne
                    # sont donc échappés dans la trace réelle H2.
                    "result": json.dumps("Source A\nhttps://example.com/preuve\nRésumé", ensure_ascii=False),
                },
                {
                    "tool": "search",
                    "args": {"query": "France PME retards paiements"},
                    "result": "Source B\nhttps://example.com/autre",
                },
            ],
        },
        {
            "role": "CONVERT",
            "final": "exploité",
            "steps": [
                {
                    "tool": "browse",
                    "args": {"url": "https://example.com/preuve"},
                    "result": "preuve ouverte",
                },
            ],
        },
    ])

    assert summary["repeated_search_queries"] == 1
    assert summary["browsed_from_search"] == ["https://example.com/preuve"]
    assert summary["cross_role_browsed_from_search"] == ["https://example.com/preuve"]


def test_mission_trace_summary_counts_forced_lockstep_browse():
    from agents.task_handlers import _mission_trace_summary

    summary = _mission_trace_summary([
        {
            "role": "SOUT",
            "final": "fini",
            "steps": [
                {
                    "tool": "search",
                    "args": {"query": "preuve PME"},
                    "result": json.dumps("Titre\nhttps://example.com/preuve", ensure_ascii=False),
                },
                {
                    "tool": "browse",
                    "args": {"url": "https://example.com/preuve"},
                    "result": '{"url":"https://example.com/preuve","texte":"preuve"}',
                    "lockstep_forced": True,
                },
            ],
        },
    ])

    assert summary["lockstep_forced_browses"] == 1
    assert summary["browsed_from_search"] == ["https://example.com/preuve"]


def test_trace_summary_prefers_pre_truncation_search_urls():
    from agents.task_handlers import _mission_trace_summary

    summary = _mission_trace_summary([
        {
            "role": "SOUT",
            "final": "fini",
            "steps": [
                {
                    "tool": "search",
                    "args": {"query": "preuve PME"},
                    # Reproduit H2 : JSON tronqué donc impossible à reparsing.
                    "result": '"Titre\\nhttps://example.com/preuve\\n' + ("x" * 1400),
                    "result_urls": ["https://example.com/preuve"],
                },
                {
                    "tool": "browse",
                    "args": {"url": "https://example.com/preuve"},
                    "result": '{"url":"https://example.com/preuve","texte":"tronqué',
                    "browse_meta": {
                        "url": "https://example.com/preuve",
                        "text_chars": 900,
                        "blocked": False,
                        "vision_error": False,
                    },
                    "lockstep_forced": True,
                },
            ],
        },
    ])

    assert summary["browsed_from_search"] == ["https://example.com/preuve"]
    assert summary["lockstep_forced_browses"] == 1


def test_objective_result_counts_metadata_even_when_result_is_truncated():
    from agents.task_handlers import _mission_objective_result

    results = [{
        "role": "SOUT",
        "steps": [{
            "tool": "browse",
            "args": {"url": "https://example.com/long"},
            "result": '{"url":"https://example.com/long","texte":"' + ("x" * 1450),
            "browse_meta": {
                "url": "https://example.com/long",
                "text_chars": 1500,
                "blocked": False,
                "vision_error": False,
            },
        }],
    }]

    outcome = _mission_objective_result(
        results,
        {"metric": "usable_browse_count", "gte": 1},
    )

    assert outcome["observed"] == 1
    assert outcome["success"] is True
    assert outcome["usable_browse_urls"] == ["https://example.com/long"]


def test_objective_result_counts_only_usable_non_blocked_browses():
    from agents.task_handlers import _mission_objective_result

    good_text = "Article factuel exploitable. " + ("x" * 160)
    results = [
        {
            "role": "SOUT",
            "steps": [
                {
                    "tool": "browse",
                    "args": {"url": "https://example.com/good"},
                    "result": json.dumps({
                        "url": "https://example.com/good",
                        "texte": good_text,
                    }, ensure_ascii=False),
                },
                {
                    "tool": "browse",
                    "args": {"url": "https://example.com/cloudflare"},
                    "result": json.dumps({
                        "url": "https://example.com/cloudflare",
                        "texte": "Performing security verification. Verify you are not a bot." + ("x" * 160),
                    }, ensure_ascii=False),
                },
                {
                    "tool": "browse",
                    "args": {"url": "https://example.com/fail"},
                    "result": "erreur : timeout",
                },
            ],
        },
    ]

    outcome = _mission_objective_result(
        results,
        {"metric": "usable_browse_count", "gte": 2},
    )

    assert outcome["observed"] == 1
    assert outcome["target"] == 2
    assert outcome["success"] is False
    assert outcome["usable_browse_urls"] == ["https://example.com/good"]
    assert outcome["scope"] == "technical_proxy"


def test_orbit_mission_passes_lockstep_and_reports_objective_result(monkeypatch):
    from agents import task_handlers  # noqa: F401

    captured = {}

    def fake_run_mission(*args, **kwargs):
        captured.update(kwargs)
        return {
            "plan": [{"role": "SOUT", "task": "collecter"}],
            "results": [{
                "role": "SOUT",
                "task": "collecter",
                "final": "fini",
                "steps": [{
                    "step": 1,
                    "tool": "browse",
                    "args": {"url": "https://example.com/preuve"},
                    "result": json.dumps({
                        "url": "https://example.com/preuve",
                        "texte": "preuve substantielle " + ("x" * 180),
                    }, ensure_ascii=False),
                    "lockstep_forced": True,
                }],
            }],
            "rapport": "rapport",
            "synthesis_status": "validated",
        }

    monkeypatch.setattr(runtime, "run_mission", fake_run_mission)

    worker.enqueue(B, "orbit.mission", {
        "goal": "tester",
        "trace_tools": True,
        "search_browse_lockstep": True,
        "success_criterion": {"metric": "usable_browse_count", "gte": 1},
    })
    done = worker.run_one("w", kinds=["orbit.mission"], log=lambda s: None)

    assert captured["search_browse_lockstep"] is True
    assert captured["search_browse_selector"] == "first"
    assert done["output"]["experiment_flags"] == {
        "search_browse_lockstep": True,
        "search_browse_selector": "first",
    }
    assert done["output"]["objective_result"]["success"] is True
    assert done["output"]["objective_result"]["observed"] == 1
    assert done["output"]["trace_summary"]["lockstep_forced_browses"] == 1
    assert done["output"]["tool_trace"][0]["lockstep_forced"] is True


def test_orbit_mission_business_signal_focus_reports_semantic_success(monkeypatch):
    from agents import task_handlers  # noqa: F401

    captured = {}
    signal = {
        "signal_type": "job_demand",
        "buyer": "entreprise de plomberie multi-sites",
        "pain": "saisie manuelle de devis et relances",
        "money_signal": "recrutement dédié à cette tâche",
        "evidence_url": "https://example.com/job",
        "evidence_summary": "offre d'emploi pour gérer devis et relances",
        "test_channel": "prospection directe d'entreprises similaires",
        "test_offer": "automatisation légère devis + relances",
        "next_test": "contacter 5 entreprises comparables",
    }

    def fake_run_mission(*args, **kwargs):
        captured.update(kwargs)
        return {
            "plan": [{"role": "SOUT", "task": "trouver des signaux"}],
            "results": [],
            "rapport": "signal business",
            "synthesis_status": "validated",
            "business_signals": [signal],
            "business_signal_rejections": [{"signal": {}, "reasons": ["missing_buyer"]}],
        }

    monkeypatch.setattr(runtime, "run_mission", fake_run_mission)

    worker.enqueue(B, "orbit.mission", {
        "goal": "chercher une opportunité testable",
        "business_signal_focus": True,
        "business_signal_target": 1,
    })
    done = worker.run_one("w", kinds=["orbit.mission"], log=lambda s: None)

    assert captured["business_signal_focus"] is True
    assert captured["business_signal_target"] == 1
    assert done["output"]["business_signals"] == [signal]
    assert done["output"]["business_signal_result"] == {
        "metric": "qualified_business_signal_count",
        "observed": 1,
        "target": 1,
        "success": True,
        "rejected": 1,
        "scope": "semantic_gate",
        "note": (
            "Signal qualifié = acheteur + douleur + signal monétaire/urgence + source ouverte "
            "+ canal + offre testable + prochain test."
        ),
    }
    assert done["output"]["experiment_flags"] == {
        "business_signal_focus": True,
        "business_signal_target": 1,
    }


def test_trace_summary_exposes_lockstep_selector_rank():
    from agents.task_handlers import _mission_trace_summary

    summary = _mission_trace_summary([{
        "role": "SOUT",
        "final": "fini",
        "steps": [{
            "tool": "browse",
            "args": {"url": "https://www.insee.fr/preuve"},
            "result": "preuve",
            "lockstep_forced": True,
            "lockstep_selection": {
                "url": "https://www.insee.fr/preuve",
                "selector": "evidence_relevance",
                "rank": 3,
                "score": 11,
            },
        }],
    }])

    assert summary["lockstep_forced_browses"] == 1
    assert summary["lockstep_selected_ranks"] == [3]
    assert summary["lockstep_selector_counts"] == {"evidence_relevance": 1}


def test_mission_llm_summary_counts_provider_failures(monkeypatch):
    from agents.task_handlers import _mission_llm_summary

    rows = [
        {"status": "error", "error": "RateLimitError: 429 rate limit", "model": "m1", "provider": "groq", "task": "agent.react_step"},
        {"status": "invalid", "error": "ValueError: objet JSON introuvable", "model": "m2", "provider": "kilo", "task": "agent.plan"},
        {"status": "error", "error": "APIStatusError: 504 gateway timeout", "model": "m3", "provider": "route", "task": "agent.synthesize"},
        {"status": "ok", "error": None, "model": "m4", "provider": "route", "task": "agent.react_step"},
    ]
    monkeypatch.setattr(journal, "query", lambda sql, params=(): rows)

    summary = _mission_llm_summary(42)

    assert summary["calls"] == 4
    assert summary["by_status"] == {"error": 2, "invalid": 1, "ok": 1}
    assert summary["non_ok"] == 3
    assert summary["http_errors"] == {"429": 1, "504": 1}
    assert len(summary["errors"]) == 3
    assert all(len(item["error"]) <= 300 for item in summary["errors"])
