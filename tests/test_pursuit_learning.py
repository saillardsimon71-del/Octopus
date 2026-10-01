"""Apprentissage pursuit relié au journal stratégique, hors réseau et sans provider réel."""
from __future__ import annotations

import json
import time

import pytest

import octopus
from agents import runtime
from octopus import economy, journal, strategy, supervisor, tasks

BUSINESS = "octopus"


def _experience(*, outcome="supports", technical_done=True, llm_cost=0.031, unverified_claim=False,
                theme="paiements clients"):
    objective_id = strategy.create(
        "objective", BUSINESS, f"Mesurer {theme}", created_by="human",
        statement=f"Vérifier {theme} et les coûts associés")
    hypothesis_id = strategy.create(
        "hypothesis", BUSINESS, "Offre testable", created_by="human", parent_id=objective_id,
        statement=f"Des offres liées à {theme} peuvent être payées", expected_signal="Paiement client observé")
    experiment_id = strategy.create(
        "experiment", BUSINESS, "Test économique", created_by="human", parent_id=hypothesis_id,
        action=f"Présenter une offre testable liée à {theme}", metric="cash_net:EUR",
        target_value=30 if outcome == "supports" else 1, stop_value=0,
        deadline_at=(time.time() + 86400) if outcome == "supports" else 1)
    strategy.transition("experiment", experiment_id, BUSINESS, "running", actor="human")

    task_id = tasks.enqueue(BUSINESS, "manual.experiment_work", {"experiment_id": experiment_id})
    strategy.link(BUSINESS, "experiment", experiment_id, "task", task_id, "executed_by")
    with journal.run(BUSINESS, "task:manual.experiment_work") as run:
        tasks.set_run(task_id, run.id)
        if llm_cost:
            journal.record_llm_call({
                "ts": time.time(), "run_id": run.id, "root_run_id": run.root_id, "business": BUSINESS,
                "task": "fixture.analysis", "profile": "zero_cost", "model": "fixture-model",
                "provider": "fixture", "cost_class": "paid", "status": "ok", "cost_usd": llm_cost,
            })
    claimed = tasks.claim("fixture-worker", task_id=task_id)
    assert claimed is not None
    if technical_done:
        tasks.complete(task_id, claimed["lease_owner"], {"artifact_produced": True})
    else:
        tasks.fail(task_id, claimed["lease_owner"], "fixture timeout", final=True)

    unverified_id = None
    if unverified_claim:
        unverified_id = strategy.create(
            "evidence", BUSINESS, "Le modèle suppose une traction", created_by="agent:ORBIT",
            nature="unverified", source_type="llm_output", observation="LLM_ONLY_MARKET_SUCCESS",
            experiment_id=experiment_id)

    if outcome == "supports":
        economy.record_cash(BUSINESS, "in", 40, "EUR", "customer_payment", nature="observed",
                            created_by="human", source_ref="fixture://payment/1", experiment_id=experiment_id)
        economy.record_cash(BUSINESS, "out", 5, "EUR", "variable_cost", nature="observed",
                            created_by="human", source_ref="fixture://cost/1", experiment_id=experiment_id)
    else:
        economy.record_cash(BUSINESS, "in", 10, "EUR", "customer_payment", nature="observed",
                            created_by="human", source_ref="fixture://payment/zero-margin", experiment_id=experiment_id)
        economy.record_cash(BUSINESS, "out", 10, "EUR", "variable_cost", nature="observed",
                            created_by="human", source_ref="fixture://cost/zero-margin", experiment_id=experiment_id)

    result = economy.evaluate_experiment(BUSINESS, experiment_id, now=time.time() + 2)
    return {"objective_id": objective_id, "hypothesis_id": hypothesis_id, "experiment_id": experiment_id,
            "task_id": task_id, "unverified_id": unverified_id, "result": result}


def _runtime_result(action="pause", *, next_goal="", hypothesis=None):
    choice = {"action": action, "reason": "Décision fondée sur le contexte persisté.",
              "next_goal": next_goal, "permission": ""}
    if hypothesis is not None:
        choice["hypothesis"] = hypothesis
    return {"rapport": "Le résultat économique reste distinct de la production technique.",
            "plan": [], "results": [], "execution_status": "completed", "synthesis_status": "validated",
            "determination": choice}


def _state_from_goal(goal: str) -> dict:
    return json.loads(goal.rsplit("\n", 1)[-1])


def test_evaluated_economic_experience_persists_lesson_links_costs_and_reuses_it_in_pursuit(monkeypatch):
    history = _experience()
    result = history["result"]
    exp_id = history["experiment_id"]
    hyp_id = history["hypothesis_id"]
    assert result["verdict"] == "supports" and result["value"] == 35
    assert result["llm_cost_usd"] == pytest.approx(0.031)
    assert result["lesson_recorded"] is True
    assert strategy.get("experiment", exp_id, BUSINESS)["status"] == "completed"
    assert strategy.get("hypothesis", hyp_id, BUSINESS)["status"] == "validated"

    evaluation_id = result["evidence_id"]
    historical_proof = strategy.get("evidence", evaluation_id, BUSINESS)
    raw_evaluation = historical_proof["observation"]
    repeated_evaluation = economy.evaluate_experiment(BUSINESS, exp_id, apply=True)
    assert repeated_evaluation["evidence_id"] == evaluation_id
    assert repeated_evaluation["review_id"] == result["review_id"]
    assert strategy.get("evidence", evaluation_id, BUSINESS)["observation"] == raw_evaluation
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE business=? AND experiment_id=? "
                             "AND source_type='economy.evaluate' AND status='active'", (BUSINESS, exp_id))) == 1
    lesson_row = strategy.get("review", result["review_id"], BUSINESS)
    lesson = json.loads(lesson_row["evidence_summary"])
    assert lesson_row["status"] == "done" and lesson["schema"] == "octopus.experiment_learning.v1"
    assert lesson["metric_result"]["scope"] == "configured_metric_only"
    assert lesson["economic_result"]["customer_receipts_observed_by_currency"] == {"EUR": 40.0}
    assert lesson["economic_result"]["recorded_contribution_by_currency"] == {"EUR": 35.0}
    assert lesson["technical_result"]["status"] == "done"
    assert lesson["historical_costs"]["llm_cost_usd"] == pytest.approx(0.031)
    assert lesson["historical_costs"]["sunk_costs_are_not_a_decision_input"] is True

    links = {(item["from_type"], item["from_id"], item["to_type"], item["to_id"], item["relation"])
             for item in journal.query("SELECT * FROM strategy_links WHERE business=?", (BUSINESS,))}
    assert ("hypothesis", hyp_id, "experiment", exp_id, "tests") in links
    assert ("experiment", exp_id, "evidence", evaluation_id, "produced") in links
    assert ("evidence", evaluation_id, "decision", result["decision_id"], "informs") in links
    assert ("review", result["review_id"], "evidence", evaluation_id, "based_on") in links
    assert ("review", result["review_id"], "decision", result["decision_id"], "reviews_decision") in links
    assert ("decision", result["decision_id"], "review", result["review_id"], "reviewed_in") in links

    calls = []

    def offline_pursuit(goal, **kwargs):
        state = _state_from_goal(goal)
        calls.append(kwargs)
        assert kwargs["allowed_tools"] == supervisor.PURSUIT_TOOLS
        lesson_from_journal = state["expériences_antérieures"][0]
        assert lesson_from_journal["experiment"]["id"] == exp_id
        assert lesson_from_journal["result"]["verdict"] == "supports"
        assert lesson_from_journal["costs"]["llm_usd_at_evaluation"] == pytest.approx(0.031)
        assert lesson_from_journal["result"]["cash_by_currency"]["EUR"]["net_observed"] == 35
        if len(calls) == 1:
            assert lesson_from_journal["lesson_status"] == "persisted"
            assert "marge" in lesson_from_journal["lesson"].lower()
            assert "SiteQuiVend" in goal and "ne sont ni preuve de marché" in goal
            assert "ils ne motivent ni poursuite, ni abandon" in goal
            return _runtime_result(
                "continue", next_goal="Vérifier l'acceptation client et la répétabilité avant de reproduire.",
                hypothesis={"statement": "Une offre utile peut produire des paiements répétés",
                            "evidence_ids": [evaluation_id], "expected_signal": "Paiements clients observés",
                            "stop_criterion": "Aucun paiement répété après la mesure prévue"})
        return _runtime_result()

    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", offline_pursuit)
    objective_id = supervisor.start_pursuit("Étudier une voie économique à partir des résultats persistés")
    supervisor.run_pursuit(objective_id)

    work = supervisor.work_tasks(BUSINESS, objective_id)
    assert work[0]["output"]["next_goal"].startswith("Vérifier l'acceptation")
    assert work[0]["output"]["hypothesis_id"]
    assert work[1]["input"]["goal"] == work[0]["output"]["next_goal"]
    proposed = strategy.get("hypothesis", work[0]["output"]["hypothesis_id"], BUSINESS)
    assert proposed["status"] == "proposed" and proposed["created_by"] == "octopus:pursuit"
    proposal_links = strategy.links(BUSINESS, "hypothesis", proposed["id"])
    assert any(row["to_type"] == "evidence" and row["to_id"] == evaluation_id and row["relation"] == "considers"
               for row in proposal_links)
    assert any(row["to_type"] == "experiment" and row["to_id"] == exp_id and row["relation"] == "learns_from"
               for row in proposal_links)
    assert strategy.get("evidence", evaluation_id, BUSINESS)["observation"] == raw_evaluation
    assert len(journal.query("SELECT id FROM strategy_reviews WHERE business=? AND summary=?",
                             (BUSINESS, f"Leçon d'expérience #{exp_id}"))) == 1


def test_learning_context_surfaces_an_older_topically_relevant_experience(monkeypatch):
    older_relevant = _experience(theme="paiements clients récurrents")
    for index in range(8):
        _experience(theme=f"production de vidéos verticales {index}", llm_cost=0)

    context = strategy.learning_context(BUSINESS, limit=3, topic="paiements clients récurrents")
    assert context["lessons"][0]["experiment"]["id"] == older_relevant["experiment_id"]
    assert context["lessons"][0]["hypothesis"]["statement"] == \
        strategy.get("hypothesis", older_relevant["hypothesis_id"], BUSINESS)["statement"]
    received = []

    def offline_pursuit(goal, **kwargs):
        received.append(_state_from_goal(goal))
        return _runtime_result()

    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", offline_pursuit)
    objective_id = supervisor.start_pursuit("Étudier les paiements clients récurrents")
    supervisor.run_pursuit(objective_id)
    assert received[0]["expériences_antérieures"][0]["experiment"]["id"] == older_relevant["experiment_id"]


def test_prior_experiment_costs_are_attributed_once_across_nested_runs():
    history = _experience(llm_cost=0)
    experiment_id = history["experiment_id"]
    parent_task = tasks.enqueue(BUSINESS, "fixture.parent", {})
    child_task = tasks.enqueue(BUSINESS, "fixture.child", {})
    for task_id in (parent_task, child_task):
        strategy.link(BUSINESS, "experiment", experiment_id, "task", task_id, "executed_by")

    base = {"ts": time.time(), "business": BUSINESS, "task": "fixture.call", "profile": "zero_cost",
            "model": "fixture-model", "provider": "fixture", "cost_class": "paid", "status": "ok"}
    with journal.run(BUSINESS, "fixture.parent") as parent:
        tasks.set_run(parent_task, parent.id)
        journal.record_llm_call({**base, "run_id": parent.id, "root_run_id": parent.root_id, "cost_usd": 0.01})
        with journal.run(BUSINESS, "fixture.child") as child:
            tasks.set_run(child_task, child.id)
            journal.record_llm_call({**base, "run_id": child.id, "root_run_id": child.root_id, "cost_usd": 0.02})

    assert economy.llm_cost_usd(BUSINESS, experiment_id=experiment_id) == pytest.approx(0.03)


def test_technical_completion_and_zero_margin_do_not_become_economic_success():
    history = _experience(outcome="zero_margin", technical_done=True, llm_cost=0)
    result = history["result"]
    lesson = json.loads(strategy.get("review", result["review_id"], BUSINESS)["evidence_summary"])

    assert result["verdict"] == "refutes" and result["value"] == 0
    assert result["outcomes"]["technical_completion"]["status"] == "done"
    assert lesson["technical_result"]["status"] == "done"
    assert lesson["metric_result"]["value"] == 0
    assert lesson["economic_result"]["customer_receipts_observed_by_currency"] == {"EUR": 10.0}
    assert lesson["economic_result"]["customer_refunds_observed_by_currency"] == {"EUR": 0}
    assert lesson["economic_result"]["variable_cost_observed_by_currency"] == {"EUR": 10.0}
    assert lesson["economic_result"]["recorded_contribution_by_currency"] == {"EUR": 0.0}
    assert lesson["economic_result"]["status"] == "customer_receipts_observed"
    assert strategy.get("hypothesis", history["hypothesis_id"], BUSINESS)["status"] == "invalidated"
    assert strategy.get("experiment", history["experiment_id"], BUSINESS)["outcome"] == "refutes"
    assert "succès économique" not in lesson["lesson"].lower()


def test_llm_only_or_unlinked_lesson_and_unverified_hypothesis_reference_are_ignored():
    history = _experience(unverified_claim=True)
    experiment_id = history["experiment_id"]
    objective_id = history["objective_id"]
    evaluated = strategy.learning_context(BUSINESS)
    persisted_lesson = json.loads(strategy.get("review", history["result"]["review_id"], BUSINESS)["evidence_summary"])
    assert any(item["id"] == history["unverified_id"]
               for item in persisted_lesson["unverified_claims_not_used_as_proof"])
    assert history["unverified_id"] not in evaluated["available_evidence_ids"]

    bogus_review = strategy.create(
        "review", BUSINESS, f"Leçon d'expérience #{experiment_id}", created_by="policy:evaluate",
        evidence_summary=json.dumps({"schema": "octopus.experiment_learning.v1", "experiment_id": experiment_id,
                                     "evaluation_evidence_id": history["result"]["evidence_id"],
                                     "lesson": "LLM_ONLY_MARKET_SUCCESS"}))
    strategy.transition("review", bogus_review, BUSINESS, "done", actor="policy:evaluate")
    strategy.link(BUSINESS, "review", bogus_review, "experiment", experiment_id, "reviews")
    strategy.link(BUSINESS, "review", bogus_review, "evidence", history["result"]["evidence_id"], "based_on")

    unverified_id = history["unverified_id"]
    objective_pursuit = strategy.create("objective", BUSINESS, "Cycle", created_by="human",
                                        statement="Comparer des pistes")
    strategy.transition("objective", objective_pursuit, BUSINESS, "active", actor="human")
    work = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {"pursuit": True})
    rejected = strategy.propose_pursuit_hypothesis(
        BUSINESS, objective_pursuit, work,
        {"statement": "Une sortie LLM prouve le marché", "evidence_ids": [unverified_id],
         "expected_signal": "Paiement vérifiable", "stop_criterion": "Aucun paiement observé"},
        available_evidence_ids=evaluated["available_evidence_ids"])

    context = strategy.learning_context(BUSINESS)
    assert context["lessons"] and context["lessons"][0]["lesson_status"] == "persisted"
    lesson_context = context["lessons"][0]
    assert "LLM_ONLY_MARKET_SUCCESS" not in lesson_context["lesson"]
    assert "LLM_ONLY_MARKET_SUCCESS" in json.dumps(lesson_context["unverified_claims_not_used_as_proof"])
    assert history["unverified_id"] not in lesson_context["evidence_ids"]
    assert rejected["status"] == "ignored"
    assert not strategy.list_items("hypothesis", BUSINESS, parent_id=objective_pursuit)


def test_pause_crash_and_resume_preserve_experience_and_historical_evidence(monkeypatch):
    history = _experience()
    evidence_id = history["result"]["evidence_id"]
    before = strategy.get("evidence", evidence_id, BUSINESS)
    calls = []

    class Crash(BaseException):
        pass

    def offline_pursuit(goal, **kwargs):
        calls.append(_state_from_goal(goal))
        if len(calls) == 1:
            assert calls[-1]["expériences_antérieures"][0]["experiment"]["id"] == history["experiment_id"]
            raise Crash("simulated process stop")
        assert calls[-1]["expériences_antérieures"][0]["costs"]["llm_usd_at_evaluation"] == pytest.approx(0.031)
        return _runtime_result()

    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", offline_pursuit)
    objective_id = supervisor.start_pursuit("Reprendre un cycle après interruption")
    with pytest.raises(Crash):
        supervisor.run_pursuit(objective_id)
    work = supervisor.work_tasks(BUSINESS, objective_id)[0]
    assert work["status"] == "running"

    supervisor.pause_pursuit(objective_id)
    tasks.reap(now=time.time() + 120)
    assert tasks.get(work["id"])["status"] == "cancelled"
    assert strategy.get("evidence", evidence_id, BUSINESS) == before

    supervisor.start_pursuit(objective_id=objective_id)
    supervisor.run_pursuit(objective_id)
    assert len(calls) == 2
    assert calls[1]["expériences_antérieures"][0]["review_id"] == history["result"]["review_id"]
    assert strategy.get("evidence", evidence_id, BUSINESS) == before
    assert len(journal.query("SELECT id FROM strategy_reviews WHERE business=? AND summary=?",
                             (BUSINESS, f"Leçon d'expérience #{history['experiment_id']}"))) == 1


def test_invalidated_hypothesis_is_blocked_but_new_explicit_proof_allows_reconsideration(monkeypatch):
    history = _experience(outcome="zero_margin", technical_done=False, llm_cost=0)
    old_id = history["hypothesis_id"]
    old = strategy.get("hypothesis", old_id, BUSINESS)
    assert old["status"] == "invalidated"
    evaluation_id = history["result"]["evidence_id"]

    monkeypatch.setattr(octopus, "enabled", lambda: True)
    calls = []

    def repeated(goal, **kwargs):
        state = _state_from_goal(goal)
        calls.append(state)
        assert state["hypothèses_invalidées_à_ne_pas_répéter_sans_preuve_nouvelle"][0]["hypothesis_id"] == old_id
        return _runtime_result(
            "continue", next_goal=old["statement"],
            hypothesis={"statement": old["statement"], "evidence_ids": [evaluation_id]})

    monkeypatch.setattr(runtime, "run_mission", repeated)
    pursuit_id = supervisor.start_pursuit("Comparer une autre hypothèse")
    supervisor.run_pursuit(pursuit_id)
    first = supervisor.work_tasks(BUSINESS, pursuit_id)[0]
    assert first["output"]["decision"] == "pause"
    assert first["output"]["strategy_hypothesis"]["status"] == "blocked_repetition"
    assert "déjà invalidée" in first["output"]["reason"]
    assert not strategy.list_items("hypothesis", BUSINESS, parent_id=pursuit_id)

    # Nouvelle preuve réellement datée et explicitement reliée à l'hypothèse invalidée.
    with tasks._tx() as conn:
        conn.execute("UPDATE strategy_hypotheses SET updated_at=1 WHERE id=?", (old_id,))
    new_evidence_id = strategy.create(
        "evidence", BUSINESS, "Observation nouvelle liée à la reconsidération", created_by="human",
        nature="observed", source_type="manual_fixture", source_ref="fixture://new-observation",
        captured_at=2, observation="Une nouvelle observation indépendante modifie le contexte.")
    strategy.link(BUSINESS, "evidence", new_evidence_id, "hypothesis", old_id, "reconsiders")

    def reconsider(goal, **kwargs):
        state = _state_from_goal(goal)
        assert new_evidence_id in state["identifiants_de_preuves_persistées_disponibles"]
        return _runtime_result(
            "continue", next_goal=old["statement"],
            hypothesis={"statement": old["statement"], "evidence_ids": [new_evidence_id],
                        "reconsiders_hypothesis_id": old_id,
                        "reconsideration_reason": "Une preuve observée nouvelle et datée change le contexte.",
                        "expected_signal": "Mesure distincte à tester", "stop_criterion": "Critère préalable atteint"})

    monkeypatch.setattr(runtime, "run_mission", reconsider)
    second_pursuit_id = supervisor.start_pursuit("Réexaminer à partir d'une preuve nouvelle")
    supervisor.run_pursuit(second_pursuit_id)
    first = supervisor.work_tasks(BUSINESS, second_pursuit_id)[0]
    new_hypothesis_id = first["output"]["hypothesis_id"]
    new_hypothesis = strategy.get("hypothesis", new_hypothesis_id, BUSINESS)
    assert first["output"]["decision"] == "continue"
    assert new_hypothesis["status"] == "proposed" and new_hypothesis["statement"] == old["statement"]
    assert new_hypothesis["summary"].find(f"reconsidère #{old_id}") >= 0
    links = strategy.links(BUSINESS, "hypothesis", new_hypothesis_id)
    assert any(row["to_type"] == "hypothesis" and row["to_id"] == old_id and row["relation"] == "reconsiders"
               for row in links)
    assert any(row["to_type"] == "evidence" and row["to_id"] == new_evidence_id and row["relation"] == "considers"
               for row in links)
    decision_id = first["output"]["decision_id"]
    assert any(row["to_type"] == "hypothesis" and row["to_id"] == new_hypothesis_id and row["relation"] == "advances"
               for row in strategy.links(BUSINESS, "decision", decision_id))
    assert not tasks.pending_human_requests(BUSINESS)


def test_determination_contract_discards_malformed_optional_hypothesis_only():
    good_decision = {"action": "continue", "reason": "Raison", "next_goal": "Suite", "permission": "",
                     "hypothesis": {"statement": "Une proposition", "evidence_ids": [1]}}
    accepted = runtime._validate_synthesis_contract({"rapport": "Résumé", "determination": good_decision}, True)
    assert accepted["determination"]["hypothesis"]["evidence_ids"] == [1]

    bad_decision = {"action": "continue", "reason": "Raison", "next_goal": "Suite", "permission": "",
                    "hypothesis": {"statement": "Hypothèse", "evidence_ids": [True]}}
    accepted = runtime._validate_synthesis_contract({"rapport": "Résumé", "determination": bad_decision}, True)
    assert "hypothesis" not in accepted["determination"]
    assert accepted["determination"]["action"] == "continue"
