"""Apprentissage pursuit relié au journal stratégique, hors réseau et sans provider réel."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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


def test_second_experiment_can_resolve_an_inconclusive_hypothesis():
    history = _experience()
    old = strategy.create("hypothesis", BUSINESS, "Sans mesure", created_by="human",
                          parent_id=history["objective_id"], statement="Hypothèse à réexaminer")
    strategy.transition("hypothesis", old, BUSINESS, "testing", actor="human")
    strategy.transition("hypothesis", old, BUSINESS, "inconclusive", actor="human")
    exp = strategy.create("experiment", BUSINESS, "Nouvelle mesure", created_by="human", parent_id=old,
                          action="Mesurer la livraison", metric="delivery", target_value=1)
    strategy.transition("experiment", exp, BUSINESS, "running", actor="human")
    strategy.create("evidence", BUSINESS, "Livraison constatée", created_by="human", nature="observed",
                    source_type="fixture", source_ref="fixture://delivery", captured_at=time.time(),
                    observation="Livraison", metric="delivery", value=1, experiment_id=exp)
    result = economy.evaluate_experiment(BUSINESS, exp)
    assert result["lesson_recorded"] and result["verdict"] == "supports"
    assert strategy.get("hypothesis", old, BUSINESS)["status"] == "validated"


def test_partial_evaluation_is_recovered_even_if_live_metric_now_becomes_pending():
    history = _experience()
    exp = history["experiment_id"]
    evaluation_id = history["result"]["evidence_id"]
    proof = strategy.get("evidence", evaluation_id, BUSINESS)
    # État d'une ancienne interruption après clôture, avant création de la review.
    with tasks._tx() as conn:
        conn.execute("DELETE FROM strategy_links WHERE from_type='review' OR to_type='review'")
        conn.execute("DELETE FROM strategy_reviews")
        conn.execute("DELETE FROM ledger_entries WHERE experiment_id=?", (exp,))
    result = economy.evaluate_experiment(BUSINESS, exp)
    assert result["evidence_id"] == evaluation_id and result["verdict"] == "supports"
    assert result["lesson_recorded"]
    assert strategy.get("evidence", evaluation_id, BUSINESS) == proof


def test_concurrent_evaluations_converge_to_one_complete_chain(monkeypatch):
    history = _experience()
    exp = history["experiment_id"]
    with tasks._tx() as conn:
        conn.execute("DELETE FROM strategy_links WHERE from_type IN ('review','decision','evidence') "
                     "OR to_type IN ('review','decision','evidence')")
        conn.execute("DELETE FROM strategy_reviews")
        conn.execute("DELETE FROM strategy_decisions")
        conn.execute("DELETE FROM strategy_evidence WHERE source_type='economy.evaluate'")
        conn.execute("UPDATE strategy_experiments SET status='running',outcome=NULL WHERE id=?", (exp,))
    original = strategy.create
    collision = Barrier(2)

    def delayed_create(kind, *args, **kwargs):
        # Force la course ancienne check/create; un appel dans une transaction partagée
        # possède déjà le verrou SQLite et ne doit pas attendre l'autre écrivain.
        if kind == "evidence" and kwargs.get("source_type") == "economy.evaluate" and kwargs.get("_conn") is None:
            collision.wait(timeout=5)
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(strategy, "create", delayed_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(economy.evaluate_experiment, BUSINESS, exp) for _ in range(2)]
        results = [future.result() for future in futures]
    assert results[0]["evidence_id"] == results[1]["evidence_id"]
    assert results[0]["decision_id"] == results[1]["decision_id"]
    assert results[0]["review_id"] == results[1]["review_id"]
    assert len(strategy.list_items("review", BUSINESS)) == 1
    assert len(strategy.list_items("decision", BUSINESS)) == 1


def test_evaluation_failure_rolls_back_the_entire_chain(monkeypatch):
    history = _experience()
    exp = history["experiment_id"]
    with tasks._tx() as conn:
        conn.execute("DELETE FROM strategy_links WHERE from_type IN ('review','decision','evidence') "
                     "OR to_type IN ('review','decision','evidence')")
        conn.execute("DELETE FROM strategy_reviews")
        conn.execute("DELETE FROM strategy_decisions")
        conn.execute("DELETE FROM strategy_evidence WHERE source_type='economy.evaluate'")
        conn.execute("UPDATE strategy_experiments SET status='running',outcome=NULL WHERE id=?", (exp,))
    original = strategy.link

    def crash(business, from_type, *args, **kwargs):
        if from_type == "review":
            raise RuntimeError("fixture interruption avant liens de review")
        return original(business, from_type, *args, **kwargs)

    monkeypatch.setattr(strategy, "link", crash)
    with pytest.raises(RuntimeError, match="fixture interruption"):
        economy.evaluate_experiment(BUSINESS, exp)
    assert strategy.get("experiment", exp, BUSINESS)["status"] == "running"
    assert not strategy.list_items("review", BUSINESS)
    assert not strategy.list_items("decision", BUSINESS)
    assert not journal.query("SELECT id FROM strategy_evidence WHERE source_type='economy.evaluate'")
    monkeypatch.setattr(strategy, "link", original)
    results = [economy.evaluate_experiment(BUSINESS, exp) for _ in range(3)]
    assert len({result["review_id"] for result in results}) == 1


def test_anti_repetition_does_not_block_a_goal_to_avoid_the_old_action():
    history = _experience(outcome="zero_margin")
    old_action = strategy.get("experiment", history["experiment_id"], BUSINESS)["action"]
    assert strategy.repeated_invalidated_strategy(BUSINESS, "Ne pas " + old_action + "; comparer une autre offre") is None
    assert strategy.repeated_invalidated_strategy(BUSINESS, old_action.upper() + " !!!")["hypothesis_id"] == history["hypothesis_id"]


def test_learning_context_uses_the_evaluation_decision_not_a_later_free_claim():
    history = _experience()
    other = strategy.create("decision", BUSINESS, "Interprétation libre", created_by="agent:fixture",
                            decision="FREE_CLAIM_IS_MARKET_PROOF", rationale="Sans observation")
    strategy.link(BUSINESS, "decision", other, "evidence", history["result"]["evidence_id"], "considers")
    lesson = strategy.learning_context(BUSINESS)["lessons"][0]
    assert lesson["lesson_status"] == "persisted"
    assert lesson["decision"]["id"] == history["result"]["decision_id"]


def test_pursuit_context_is_bounded_with_large_historical_observations(monkeypatch):
    history = _experience()
    strategy.create("evidence", BUSINESS, "Observation longue", created_by="human", nature="observed",
                    source_type="fixture", source_ref="fixture://large", captured_at=time.time(),
                    observation="OBSERVATION " * 50000, experiment_id=history["experiment_id"])
    captured = []
    def offline(goal, **kwargs):
        captured.append(_state_from_goal(goal))
        return _runtime_result()
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", offline)
    pursuit = supervisor.start_pursuit("Mesurer les paiements clients")
    supervisor.run_pursuit(pursuit)
    assert len(json.dumps(captured[0], ensure_ascii=False)) <= 64000


def test_pursuit_does_not_inject_unverified_claims_as_historical_proofs(monkeypatch):
    history = _experience(unverified_claim=True)
    captured = []
    def offline(goal, **kwargs):
        captured.append(_state_from_goal(goal))
        return _runtime_result()
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", offline)
    supervisor.run_pursuit(supervisor.start_pursuit("Comparer les paiements"))
    assert "LLM_ONLY_MARKET_SUCCESS" not in json.dumps(captured[0], ensure_ascii=False)
    assert history["unverified_id"] not in captured[0]["identifiants_de_preuves_persistées_disponibles"]


def test_concurrent_hypothesis_proposals_are_idempotent(monkeypatch):
    history = _experience()
    objective = strategy.create("objective", BUSINESS, "Pursuit", created_by="human", statement="Mesurer")
    strategy.transition("objective", objective, BUSINESS, "active", actor="human")
    work = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {"pursuit": True})
    evidence_id = history["result"]["evidence_id"]
    proposal = {"statement": "Une nouvelle offre peut produire des paiements répétés",
                "expected_signal": "Paiement", "stop_criterion": "Aucune vente", "evidence_ids": [evidence_id]}
    original = strategy.create
    collision = Barrier(2)
    def delayed_create(kind, *args, **kwargs):
        if kind == "hypothesis" and kwargs.get("_conn") is None:
            collision.wait(timeout=5)
        return original(kind, *args, **kwargs)
    monkeypatch.setattr(strategy, "create", delayed_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(strategy.propose_pursuit_hypothesis, BUSINESS, objective, work, proposal,
                               available_evidence_ids=[evidence_id]) for _ in range(2)]
        results = [future.result() for future in futures]
    assert results[0]["hypothesis_id"] == results[1]["hypothesis_id"]
    assert len(strategy.list_items("hypothesis", BUSINESS, parent_id=objective)) == 1


def test_anti_repetition_handles_underscore_punctuation_and_short_statements():
    history = _experience(outcome="zero_margin")
    old = strategy.get("hypothesis", history["hypothesis_id"], BUSINESS)
    assert strategy.repeated_invalidated_strategy(BUSINESS, old["statement"].replace(" ", "_"))
    with tasks._tx() as conn:
        conn.execute("UPDATE strategy_hypotheses SET statement='Ça se vend' WHERE id=?", (old["id"],))
    assert strategy.repeated_invalidated_strategy(BUSINESS, "  ÇA  SE VEND!!!  ")


def test_reconsideration_retains_the_complete_reason():
    history = _experience(outcome="zero_margin")
    old = strategy.get("hypothesis", history["hypothesis_id"], BUSINESS)
    with tasks._tx() as conn:
        conn.execute("UPDATE strategy_hypotheses SET updated_at=1 WHERE id=?", (old["id"],))
    proof = strategy.create("evidence", BUSINESS, "Nouvelle mesure", created_by="human", nature="observed",
                            source_type="fixture", source_ref="fixture://new", captured_at=2, observation="Mesure")
    strategy.link(BUSINESS, "evidence", proof, "hypothesis", old["id"], "reconsiders")
    obj = strategy.create("objective", BUSINESS, "Réexaminer", created_by="human", statement="Réexaminer")
    strategy.transition("objective", obj, BUSINESS, "active", actor="human")
    work = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {})
    reason = "Contexte à expliquer. " * 12 + "LA NOUVELLE MESURE INDÉPENDANTE CHANGE LE PRIX."
    result = strategy.propose_pursuit_hypothesis(
        BUSINESS, obj, work, {"statement": old["statement"], "expected_signal": "Paiement",
                             "stop_criterion": "Aucun paiement", "evidence_ids": [proof],
                             "reconsiders_hypothesis_id": old["id"], "reconsideration_reason": reason},
        available_evidence_ids=[proof])
    assert reason in strategy.get("hypothesis", result["hypothesis_id"], BUSINESS)["summary"]


@pytest.mark.parametrize("kind", ["old", "future", "unlinked", "unverified", "missing_reason"])
def test_reconsideration_refuses_unusable_or_undated_new_proof(kind):
    history = _experience(outcome="zero_margin")
    old = strategy.get("hypothesis", history["hypothesis_id"], BUSINESS)
    at = old["updated_at"] - 1 if kind == "old" else time.time() + (86400 if kind == "future" else 0)
    proof = strategy.create("evidence", BUSINESS, "Mesure candidate", created_by="human",
                            nature="unverified" if kind == "unverified" else "observed",
                            source_type="fixture", source_ref="fixture://new", captured_at=at, observation="Mesure")
    if kind != "unlinked":
        strategy.link(BUSINESS, "evidence", proof, "hypothesis", old["id"], "reconsiders")
    obj = strategy.create("objective", BUSINESS, "Réexaminer", created_by="human", statement="Réexaminer")
    strategy.transition("objective", obj, BUSINESS, "active", actor="human")
    work = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {})
    result = strategy.propose_pursuit_hypothesis(
        BUSINESS, obj, work, {"statement": old["statement"], "expected_signal": "Paiement",
                             "stop_criterion": "Aucun paiement", "evidence_ids": [proof],
                             "reconsiders_hypothesis_id": old["id"],
                             "reconsideration_reason": "" if kind == "missing_reason" else "Nouvelle mesure"},
        available_evidence_ids=[proof])
    assert result["status"] == "ignored"
    assert not strategy.list_items("hypothesis", BUSINESS, parent_id=obj)


def test_lexical_selection_is_stable_for_ties_and_has_no_semantic_inference():
    histories = [_experience(theme="offres clients indépendants") for _ in range(3)]
    with tasks._tx() as conn:
        conn.execute("UPDATE strategy_experiments SET updated_at=1")
    first = strategy.learning_context(BUSINESS, limit=2, topic="offres clients indépendants")
    assert first == strategy.learning_context(BUSINESS, limit=2, topic="offres clients indépendants")
    # Synonymes et flexions ne sont pas rapprochés : limite explicite du classement lexical.
    assert not strategy._learning_terms("achats réguliers") & strategy._learning_terms("paiements récurrents")
    assert not strategy._learning_terms("paiement") & strategy._learning_terms("paiements")
    assert strategy.repeated_invalidated_strategy(BUSINESS, "Une reformulation indépendante") is None
    assert len(first["lessons"]) == 2


def test_large_evaluated_corpus_stays_bounded_in_pursuit(monkeypatch):
    history = _experience()
    hypothesis = strategy.create("hypothesis", BUSINESS, "Corpus", created_by="human",
                                 parent_id=history["objective_id"], statement="Mesure de corpus")
    exp = strategy.create("experiment", BUSINESS, "Mesure", created_by="human", parent_id=hypothesis,
                          action="Mesurer", metric="delivery", target_value=1)
    strategy.transition("experiment", exp, BUSINESS, "running", actor="human")
    for index in range(70):
        strategy.create("evidence", BUSINESS, f"Observation {index}", created_by="human", nature="observed",
                        source_type="fixture", source_ref=f"fixture://large/{index}", captured_at=time.time(),
                        observation="OBSERVATION " * 150, experiment_id=exp, metric="delivery", value=1)
    economy.evaluate_experiment(BUSINESS, exp)
    captured = []
    def offline(goal, **kwargs):
        captured.append(_state_from_goal(goal))
        return _runtime_result()
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", offline)
    supervisor.run_pursuit(supervisor.start_pursuit("Mesurer le corpus"))
    assert len(json.dumps(captured[0], ensure_ascii=False)) <= 64000


def test_retracted_source_excludes_a_lesson_without_mutating_the_snapshot():
    history = _experience()
    hypothesis = strategy.create("hypothesis", BUSINESS, "Livraison", created_by="human",
                                 parent_id=history["objective_id"], statement="La livraison sera attestée")
    exp = strategy.create("experiment", BUSINESS, "Livraison", created_by="human", parent_id=hypothesis,
                          action="Vérifier la livraison", metric="delivery", target_value=1)
    strategy.transition("experiment", exp, BUSINESS, "running", actor="human")
    source = strategy.create("evidence", BUSINESS, "Livraison", created_by="human", nature="observed",
                             source_type="fixture", source_ref="fixture://delivery", captured_at=time.time(),
                             observation="Livraison", experiment_id=exp, metric="delivery", value=1)
    result = economy.evaluate_experiment(BUSINESS, exp)
    proof = strategy.get("evidence", result["evidence_id"], BUSINESS)
    strategy.transition("evidence", source, BUSINESS, "retracted", actor="human")
    assert all(item["experiment"]["id"] != exp for item in strategy.learning_context(BUSINESS)["lessons"])
    assert strategy.get("evidence", proof["id"], BUSINESS) == proof
