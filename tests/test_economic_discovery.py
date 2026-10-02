"""Permissions de raisonnement et transitions : modèles scriptés, aucune mesure d'intelligence."""
import copy
import json

import pytest

from agents import deepseek, runtime
from octopus import actions, economy, journal, strategy, supervisor, tasks
from octopus import strategy_separation as separation

BUSINESS = "octopus"
A = "Service de diagnostic personnalisé vendu aux professionnels"
B = "Actifs éditoriaux financés par affiliation"
C = "Produit de données vendu par abonnement"


def proposal(statement, rank=1, capabilities=None, justification="Intérêt économique à vérifier"):
    return {"statement": statement, "economic_criteria": ["cash_received", "margin"],
            "economic_rank": rank, "economic_justification": justification,
            "required_capabilities": capabilities if capabilities is not None else ["phone_call"],
            "expected_signal": "Paiement client observé", "stop_criterion": "Test sans résultat concluant"}


def result(proposals, goal, *, action="continue", reason="Comparer avant de se fixer", permission=""):
    return {"rapport": reason, "plan": [], "results": [], "execution_status": "completed",
            "synthesis_status": "validated", "determination": {
                "action": action, "reason": reason, "next_goal": goal, "permission": permission,
                "strategies": proposals}}


def scripted(monkeypatch, outputs):
    scripts = iter(copy.deepcopy(outputs))
    inputs = []

    def run(goal, **kwargs):
        inputs.append((goal, kwargs))
        assert kwargs["allowed_tools"] == supervisor.PURSUIT_TOOLS
        return next(scripts, copy.deepcopy(outputs[-1]))

    monkeypatch.setattr(runtime, "run_mission", run)
    monkeypatch.setattr(supervisor, "pursuit_strategy_effect", lambda *_: pytest.fail("Commit en découverte"))
    return inputs


def no_effects():
    for table in ("channel_actions", "spend_requests", "ledger_entries"):
        assert not journal.query(f"SELECT id FROM {table}")
    assert tasks.pending_human_requests(BUSINESS) == []


def test_a_distinct_families_are_compared_without_forcing_first(monkeypatch):
    goal = "Comparer demande, acheteurs et coûts des trois familles indépendantes"
    scripted(monkeypatch, [result([proposal(A), proposal(B, 2), proposal(C, 3)], goal),
                           result([], "", action="pause")])
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    work = supervisor.work_tasks(BUSINESS, oid)
    assert work[0]["output"]["next_goal"] == goal
    assert work[1]["input"]["goal"] == goal
    assert {item["statement"] for item in separation.recorded_strategies(BUSINESS, oid)} == {A, B, C}
    assert len(work[0]["output"]["strategy_assessment"]["considered"]) == 3
    assert all(not item["output"]["strategy_execution"]["triggered"] for item in work)
    no_effects()


def test_b_proactive_demo_missing_email_remains_economic_option(monkeypatch):
    demo = proposal(A, capabilities=["email_send"], justification=(
        "Maquette personnalisée avant demande ; livraison par email faisable en principe ; "
        "achat, coûts et taux de conversion à vérifier"))
    scripted(monkeypatch, [result([demo, proposal(B, 2, ["search"])], "Observer des besoins professionnels"),
                           result([], "", action="pause")])
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assessed = supervisor.work_tasks(BUSINESS, oid)[0]["output"]["strategy_assessment"]
    assert assessed["retained"]["statement"] == A
    assert assessed["retained"]["economic_rank"] == 1
    assert assessed["retained"]["executability"] == "missing_capability"
    assert assessed["retained"]["may_execute"] is False
    assert "faisable en principe" in assessed["retained"]["economic_justification"]
    assert "email_send" not in runtime.TOOLS
    no_effects()


def test_c_mass_small_unit_revenue_is_not_rejected_or_booked(monkeypatch):
    rationale = ("Hypothèse : faible revenu unitaire × volume automatisé. Coûts marginaux IA, "
                 "maintenance, acquisition et distribution inconnus ; aucune rentabilité démontrée.")
    scripted(monkeypatch, [result([proposal(B, justification=rationale)], "", action="pause")])
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    output = supervisor.work_tasks(BUSINESS, oid)[0]["output"]
    assert output["decision"] == "pause"
    assert output["strategy_assessment"]["retained"]["economic_justification"] == rationale
    assert output["economic_result"] is None
    assert economy.cash_summary(BUSINESS) == {}
    no_effects()


def test_d_restart_keeps_exhausted_a_and_actually_observes_b(monkeypatch, original_runtime):
    exhausted = "A : information marginale faible ; achat inconnu, aucune réfutation formelle"
    inputs = scripted(monkeypatch, [result([proposal(A)], "", action="pause", reason=exhausted)])
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    old = supervisor.work_tasks(BUSINESS, oid)[0]
    assert old["output"]["decision"] == "pause"
    aid = old["output"]["strategy_assessment"]["hypothesis_id"]
    visited = []
    captured = []
    goal = "Observer les acheteurs de B, indépendante de A"

    def llm(agent, stage, model, messages, **kwargs):
        captured.append((stage, messages))
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": goal}]}
        if stage == "action":
            if not visited:
                return {"tool": "search", "args": {"query": "acheteurs actifs éditoriaux"}}
            return {"final": "Observation B sourcée, intérêt commercial encore inconnu"}
        return {"rapport": "B observée ; pas d'achat établi", "determination": {
            "action": "pause", "reason": "Observation conservée", "next_goal": "", "permission": "",
            "strategies": [proposal(B, 2)]}}

    def search(args):
        visited.append(args["query"])
        return "https://fixture.example/b — demande à vérifier, donnée simulée"

    # Reprise réelle du supervisor/runtime, seul le LLM et l'observation sont simulés.
    monkeypatch.setattr(runtime, "run_mission", original_runtime)
    monkeypatch.setattr(deepseek, "call_json", llm)
    monkeypatch.setitem(runtime.TOOLS, "search", {**runtime.TOOLS["search"], "fn": search})
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert visited == ["acheteurs actifs éditoriaux"]
    planner_text = json.dumps(captured[0][1], ensure_ascii=False)
    assert exhausted in planner_text and A in planner_text
    assert strategy.get("hypothesis", aid, BUSINESS)["status"] == "proposed"
    assert {s["statement"] for s in separation.recorded_strategies(BUSINESS, oid)} == {A, B}
    assert len(inputs) == 1
    no_effects()


@pytest.fixture
def original_runtime():
    return runtime.run_mission


def test_e_execution_cannot_replace_better_missing_a_with_available_b():
    assessed = separation.assess([proposal(A), proposal(B, 2, ["search"])],
                                  supervisor.pursuit_capability_inventory())
    called = []
    execution = separation.dispatch_if_authorized(assessed, lambda item: called.append(item))
    assert assessed["retained"]["statement"] == A
    assert assessed["considered"][1]["executability"] == "executable"
    assert execution["authorized"] is False and execution["substituted"] is False
    assert called == []


def test_f_new_observed_evidence_and_comparison_can_retain_b(monkeypatch):
    scripted(monkeypatch, [result([proposal(A), proposal(B, 2)], "", action="pause")])
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    prior = separation.recorded_strategies(BUSINESS, oid)
    bid = next(s["hypothesis_id"] for s in prior if s["statement"] == B)
    eid = strategy.create("experiment", BUSINESS, "Tester B", created_by="human", parent_id=bid,
                          action="Observer une demande", metric="customer_acceptance", target_value=1,
                          deadline_at=1)
    strategy.transition("experiment", eid, BUSINESS, "running", actor="human")
    proof = strategy.create("evidence", BUSINESS, "Demande acheteur simulée", created_by="human",
                            nature="observed", source_type="fixture", source_ref="fixture://buyer/b",
                            captured_at=2, experiment_id=eid,
                            observation="Acheteur demande une offre ; pas un encaissement")
    strategy.link(BUSINESS, "evidence", proof, "experiment", eid, "observes")
    assert economy.evaluate_experiment(BUSINESS, eid, now=10)["verdict"] == "inconclusive"
    updated_b = proposal(B, justification="Nouvelle demande sourcée améliore B ; paiement inconnu")
    updated_b["evidence_ids"] = [proof]
    scripted(monkeypatch, [result([proposal(A, 2), updated_b], "", action="pause")])
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    final = supervisor.work_tasks(BUSINESS, oid)[-1]["output"]
    assert final["strategy_assessment"]["retained"]["statement"] == B
    assert proof in final["strategy_assessment"]["retained"]["evidence_ids"]
    assert final["economic_result"] is None
    no_effects()


def test_g_new_informative_observation_on_a_does_not_force_pivot(monkeypatch):
    goal = "Lire l'appel d'offres public nouvellement trouvé pour A"
    scripted(monkeypatch, [result([proposal(A)], goal), result([], "", action="pause")])
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert supervisor.work_tasks(BUSINESS, oid)[0]["output"]["next_goal"] == goal
    assert supervisor.work_tasks(BUSINESS, oid)[1]["input"]["goal"] == goal
    no_effects()


@pytest.mark.parametrize("means", ["email_send", "file_link_delivery", "drive_upload", "platform_delivery", "message_send"])
def test_h_conceptual_delivery_does_not_claim_executor_or_permission(means):
    assessed = separation.assess([proposal("Livrer un produit numérique", capabilities=[means],
                                         justification="Livraison faisable en principe, disponibilité inconnue")],
                                  supervisor.pursuit_capability_inventory())
    assert assessed["retained"]["strategic_state"] == "retained"
    assert assessed["retained"]["executability"] == "missing_capability"
    assert assessed["retained"]["may_execute"] is False
    assert means not in runtime.TOOLS
    assert not separation.authorize_execution(assessed)["authorized"]


def test_current_options_are_not_crowded_out_by_twelve_old_retained(monkeypatch):
    oid = supervisor.start_pursuit()
    history = [{**proposal(f"Ancienne option {i}"), "strategic_state": "retained"} for i in range(12)]
    monkeypatch.setattr(separation, "recorded_strategies", lambda *_: history)
    choices = separation.pursuit_proposals(BUSINESS, oid, {"strategies": [proposal(B)]}, observation_only=True)
    assert choices[0]["statement"] == B
    assert len(choices) == 12
    legacy = separation.pursuit_proposals(BUSINESS, oid, {"strategies": [proposal(B)]})
    assert len(legacy) == 12 and all(item["statement"] != B for item in legacy)


def test_refuted_history_alone_does_not_close_independent_discovery():
    assessed = separation.assess([proposal(A)], supervisor.pursuit_capability_inventory(),
                                  economically_invalidated={strategy._hypothesis_key(A)})
    goal = "Identifier des acheteurs dans une autre famille économique"
    adjusted = separation.apply_pursuit_choice(
        {"strategies": [], "next_goal": goal}, "continue", "Chercher ailleurs", None, assessed,
        execution_boundary=False, rounds_left=True, observation_only=True)
    assert adjusted["action"] == "continue" and adjusted["next_goal"] == goal
    assert assessed["considered"][0]["strategic_state"] == "invalidated"
    assert not separation.authorize_execution(assessed)["authorized"]


def test_discovery_prompt_states_finality_without_a_comparison_procedure():
    from pursuit_prompt_capture import capture
    goal = capture()["goal"]
    assert "performance économique réelle" in goal
    assert "limitent l'exécution, pas la réflexion stratégique" in goal
    assert "argent client réellement encaissé" in goal
    assert "Compare plusieurs" not in goal and "Considère délai" not in goal


def test_executable_option_still_cannot_dispatch_during_discovery(monkeypatch):
    before_executors = list(actions.registered_executors())
    scripted(monkeypatch, [result([proposal(A, capabilities=["search"])], "Observer la demande de A"),
                           result([], "", action="pause")])
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    first = supervisor.work_tasks(BUSINESS, oid)[0]["output"]
    assert first["strategy_assessment"]["retained"]["executability"] == "executable"
    assert first["strategy_execution"]["authorized"] is False
    assert first["strategy_execution"]["triggered"] is False
    assert list(actions.registered_executors()) == before_executors
    no_effects()
