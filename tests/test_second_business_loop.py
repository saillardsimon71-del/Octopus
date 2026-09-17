"""Critère d'acceptation multi-business : un deuxième business parcourt toute la boucle stratégique
(objectif -> hypothèse -> expérience -> mission ORBIT -> preuve -> résultat -> décision -> revue)
sans chemin de code propre à Podalux. Réseau et LLM simulés."""
from __future__ import annotations

import json

from agents import task_handlers  # noqa: F401  (enregistre orbit.mission)
from agents.gui.workspaces import WorkspaceRegistry
from octopus import builtin_handlers, journal, strategy, tasks, worker  # noqa: F401

BUSINESS = "atelier_test"
QUIET = dict(log=lambda s: None)
REPLY = json.dumps({"tasks": [{"role": "SOUT", "task": "trouver 3 ateliers"}], "final": "3 pistes trouvées",
                    "rapport": "Trois ateliers intéressés d'après les pages consultées"})


def test_second_business_runs_the_whole_strategic_loop(transport, tmp_path):
    # 1-2. créé et sélectionné comme workspace, sans offre Podalux
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    registry.upsert(BUSINESS, "Atelier test", "Business de démonstration")
    registry.set_current(BUSINESS)
    assert registry.current().id == BUSINESS and registry.offers_for(BUSINESS) == []

    # 3-5. objectif, hypothèse, expérience
    objective = strategy.create("objective", BUSINESS, "Premier client atelier", created_by="human",
                                statement="Signer un premier atelier partenaire")
    strategy.transition("objective", objective, BUSINESS, "active", actor="human")
    hypothesis = strategy.create("hypothesis", BUSINESS, "Les ateliers veulent des vidéos", created_by="human",
                                 parent_id=objective, statement="Un atelier accepte un essai gratuit",
                                 stop_criterion="0 réponse sur 10 contacts")
    strategy.transition("hypothesis", hypothesis, BUSINESS, "testing", actor="human")
    experiment = strategy.create("experiment", BUSINESS, "Liste de prospects", created_by="human",
                                 parent_id=hypothesis, action="Identifier 3 ateliers (sans les contacter)",
                                 budget_limit=0.5, budget_currency="USD")
    strategy.transition("experiment", experiment, BUSINESS, "running", actor="human")

    # 6. mission ORBIT par la file existante, rattachée à l'expérience
    transport.reply(REPLY)
    task_id = worker.enqueue(BUSINESS, "orbit.mission", {"goal": "Préparer la liste", "experiment_id": experiment,
                                                         "max_steps": 1})
    done = worker.run_one("w", kinds=["orbit.mission"], **QUIET)
    assert done["id"] == task_id and done["status"] == "done", done["error"]
    output = done["output"]
    assert output["business"] == BUSINESS and output["rapport_nature"] == "inferred"
    auto_evidence = output["strategy"].pop("evidence_id")
    assert output["strategy"] == {"objective_id": objective, "hypothesis_id": hypothesis, "experiment_id": experiment}
    recorded = strategy.get("evidence", auto_evidence, BUSINESS)
    assert recorded["nature"] == "inferred" and recorded["source_ref"] == f"task#{task_id}"
    assert recorded["observation"] == output["rapport"] and recorded["origin_task_id"] == task_id
    prompt = json.dumps(transport.calls[0][1]["messages"], ensure_ascii=False)
    assert "Identifier 3 ateliers" in prompt and "Signer un premier atelier partenaire" in prompt
    assert {r["business"] for r in journal.query("SELECT business FROM runs")} == {BUSINESS}
    assert {r["business"] for r in journal.query("SELECT business FROM llm_calls")} == {BUSINESS}
    assert ("experiment", "task") in {(l["from_type"], l["to_type"])
                                      for l in strategy.links(BUSINESS, "experiment", experiment)}

    # 7. preuves : le rapport enregistré automatiquement (inférence) + une donnée fournie par l'humain
    assert ("evidence", "experiment") in {(l["from_type"], l["to_type"])
                                          for l in strategy.links(BUSINESS, "evidence", auto_evidence)}
    evidence = strategy.create("evidence", BUSINESS, "Retour d'un atelier", created_by="human",
                               nature="unverified", source_type="human", observation="Un atelier a répondu")
    strategy.link(BUSINESS, "evidence", evidence, "experiment", experiment, "informs")

    # 8. résultat de l'expérience et décision candidate, approuvée par l'humain
    strategy.transition("experiment", experiment, BUSINESS, "completed", actor="human", outcome="inconclusive",
                        actual_result="Liste préparée ; aucun contact réel effectué")
    decision = strategy.create("decision", BUSINESS, "Contacter les ateliers", created_by="orbit",
                               origin_task_id=task_id, decision="Préparer un message d'essai gratuit",
                               rationale="Preuve faible (inférence du modèle) : validation humaine requise")
    strategy.link(BUSINESS, "decision", decision, "evidence", evidence, "considers")
    strategy.transition("decision", decision, BUSINESS, "approved", actor="human")

    # 9. revue planifiée par la file, exécutée sans LLM
    calls_before = len(transport.calls)
    review, review_task = strategy.schedule_review(BUSINESS, due_in_s=0, created_by="human")
    ran = worker.run_one("w", kinds=["strategy.review"], **QUIET)
    assert ran["id"] == review_task and ran["status"] == "done"
    assert len(transport.calls) == calls_before
    row = strategy.get("review", review, BUSINESS)
    assert row["status"] == "done" and "expériences : completed 1" in row["evidence_summary"]

    # Isolation : rien n'a fui vers Podalux ni vers le pseudo-business global
    for kind in strategy.KINDS:
        assert strategy.list_items(kind, "podalux") == []
    assert {e["business"] for e in tasks.events() if e["type"].startswith("strategy.")} == {BUSINESS}
