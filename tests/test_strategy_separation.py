"""Stratégie économique distincte des capacités disponibles. Hors réseau, sans provider réel."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import octopus
from agents import runtime
from octopus import actions, economy, journal, strategy, supervisor, tasks
from octopus import strategy_separation as separation

BUSINESS = "octopus"
PHONE = "Le téléphone est le canal le plus intéressant pour encaisser."
EMAIL = "L'email est un canal disponible mais moins pertinent."
SEARCH_CHANNEL = "Chercher des demandes publiques est un canal moins pertinent."


def inventory(*, present=(), executable=(), temporary=(), denied=(), human=()):
    return separation.Inventory(
        present=set(present), executable=set(executable), temporarily_unavailable=set(temporary),
        permission_denied=set(denied), human_required=set(human))


def phone_email(*, phone_present=False, email_present=True, email_executable=False):
    present = {"search"}
    executable = {"search"}
    human = set()
    if phone_present:
        present.add("phone_call")
        executable.add("phone_call")
    if email_present:
        present.add("email:send")
        if email_executable:
            executable.add("email:send")
        else:
            human.add("email:send")
    return inventory(present=present, executable=executable, human=human)


def proposals(*, phone_rank=2, email_rank=1, phone_available=True, email_capability="email:send"):
    return [
        {"statement": PHONE, "economic_justification": "Meilleure chance d'argent client encaissé.",
         "economic_criteria": ["cash_received", "margin"], "economic_rank": phone_rank,
         "required_capabilities": [{"id": "phone_call", "available": phone_available}],
         "expected_signal": "Paiement client après appel",
         "stop_criterion": "Aucun paiement après la série prévue"},
        {"statement": EMAIL, "economic_justification": "Canal déjà possible, conversion attendue plus faible.",
         "economic_criteria": ["growth"], "economic_rank": email_rank,
         "required_capabilities": [email_capability],
         "expected_signal": "Réponse", "stop_criterion": "Aucune réponse"},
    ]


def test_better_strategy_is_retained_when_no_compatible_tool_exists():
    before = set(runtime.TOOLS)
    assessed = separation.assess(proposals(), phone_email())
    assert assessed["retained"]["statement"] == PHONE
    assert assessed["retained"]["executability"] == "missing_capability"
    assert assessed["retained"]["missing_capabilities"] == ["phone_call"]
    assert assessed["retained"]["strategic_state"] == "retained"
    assert assessed["retained"]["may_execute"] is False
    assert "phone_call" not in runtime.TOOLS
    assert set(runtime.TOOLS) == before


def test_availability_flip_does_not_change_the_economic_winner():
    winners = {
        separation.assess(proposals(), phone_email(phone_present=phone, email_present=email))["retained"]["statement"]
        for phone in (False, True) for email in (False, True)
    }
    assert winners == {PHONE}


def test_tool_biased_rank_cannot_override_the_economic_order():
    # Le modèle classe l'email devant parce que l'outil existe. Le critère économique prime.
    assessed = separation.assess(proposals(phone_rank=2, email_rank=1), phone_email(email_executable=True))
    assert assessed["retained"]["statement"] == PHONE
    assert assessed["considered"][0]["executability"] == "missing_capability"
    email = next(item for item in assessed["considered"] if item["statement"] == EMAIL)
    assert email["strategic_state"] == "candidate"
    assert email["executability"] == "executable"


def test_same_criterion_rank_is_honored_and_still_ignores_tools():
    same = [
        {"statement": PHONE, "economic_criteria": ["cash_received"], "economic_rank": 2,
         "required_capabilities": ["phone_call"]},
        {"statement": EMAIL, "economic_criteria": ["cash_received"], "economic_rank": 1,
         "required_capabilities": ["email:send"]},
    ]
    missing_phone = separation.assess(same, phone_email())
    missing_email = separation.assess(same, phone_email(phone_present=True, email_present=False))
    assert missing_phone["retained"]["statement"] == EMAIL
    assert missing_email["retained"]["statement"] == EMAIL
    assert missing_phone["retained"]["executability"] == "human_required"
    assert missing_email["retained"]["executability"] == "missing_capability"


def test_non_executable_strategy_is_not_economically_invalidated():
    assessed = separation.assess(proposals(), phone_email())
    assert assessed["retained"]["strategic_state"] != "invalidated"
    assert assessed["retained"]["economically_invalidated"] is False
    assert "échec" not in assessed["retained"]["executability"]


def test_proposal_cap_keeps_twelve_and_drops_the_rest():
    raw = [{"statement": f"Canal distinct {index}", "economic_criteria": ["growth"]} for index in range(13)]
    raw.insert(3, "pas un objet")
    assert len(separation.normalize_proposals(raw)) == 12


def test_registered_executor_needs_explicit_system_authorization():
    facts = separation.build_inventory(
        present_tools={"email:send", "search"}, allowed_execution={"email:send", "search"},
        executors={"email:send"})
    assert "email:send" in facts.present
    assert "email:send" not in facts.human_required
    assert "email:send" in facts.executable
    assert separation.classify_executability(["email:send"], facts)["executability"] == "executable"
    denied = separation.build_inventory(present_tools={"search"}, allowed_execution={"search"}, executors={"email:send"})
    assert separation.classify_executability(["email:send"], denied)["executability"] == "human_required"
    assert "search" in facts.executable


def test_temporary_probe_does_not_hide_or_widen_a_permission():
    facts = separation.build_inventory(
        present_tools={"browser_click", "browser_navigate", "search"},
        allowed_execution={"browser_navigate", "search"},
        temporarily_unavailable={"browser_click", "browser_navigate"})
    denied = separation.classify_executability(["browser_click"], facts)
    temporary = separation.classify_executability(["browser_navigate"], facts)
    assert denied["executability"] == "permission_denied"
    assert denied["permission_state"] == "denied"
    assert "browser_click" not in facts.executable
    assert temporary["executability"] == "temporarily_unavailable"
    assert temporary["missing_capabilities"] == []
    assert "browser_navigate" in facts.present


def test_missing_capability_is_distinct_from_refused_permission_and_human_gate():
    facts = separation.build_inventory(
        present_tools={"browser_click", "search"}, allowed_execution={"search"},
        executors={"email:send"}, temporarily_unavailable={"search"})
    missing = separation.classify_executability(["phone_call"], facts)
    denied = separation.classify_executability(["browser_click"], facts)
    human = separation.classify_executability(["email:send"], facts)
    temporary = separation.classify_executability(["search"], facts)
    assert missing["executability"] == "missing_capability"
    assert missing["permission_state"] == "none"
    assert missing["missing_capabilities"] == ["phone_call"]
    assert denied == {"executability": "permission_denied", "missing_capabilities": [],
                      "temporarily_unavailable": [], "permission_state": "denied", "may_execute": False}
    assert human["executability"] == "human_required" and human["missing_capabilities"] == []
    assert human["permission_state"] == "human_required"
    assert temporary["executability"] == "temporarily_unavailable"
    assert temporary["missing_capabilities"] == []
    assert "search" in facts.present


def test_forbidden_strategy_stays_relevant_and_is_not_executed():
    assessed = separation.assess(
        [{"statement": "Publier une offre reste économiquement intéressant.",
          "economic_criteria": ["cash_received"], "economic_rank": 1,
          "required_capabilities": ["browser_click"]}],
        separation.build_inventory(present_tools={"browser_click", "search"}, allowed_execution={"search"}))
    assert assessed["retained"]["strategic_state"] == "retained"
    assert assessed["retained"]["executability"] == "permission_denied"
    assert assessed["retained"]["economically_invalidated"] is False
    triggered = []
    execution = separation.dispatch_if_authorized(assessed, triggered.append)
    assert execution["authorized"] is False and execution["triggered"] is False and triggered == []


def test_human_permission_does_not_make_the_strategy_bad():
    assessed = separation.assess(
        [{"statement": EMAIL, "economic_justification": "Pertinent si un humain ouvre l'envoi.",
          "economic_criteria": ["margin"], "required_capabilities": ["email:send"]}],
        phone_email())
    assert assessed["retained"]["executability"] == "human_required"
    assert assessed["retained"]["strategic_state"] == "retained"
    assert assessed["retained"]["permission_state"] == "human_required"
    assert assessed["retained"]["missing_capabilities"] == []
    assert separation.authorize_execution(assessed)["authorized"] is False


def test_only_the_retained_executable_strategy_can_trigger_an_action():
    assessed = separation.assess(
        [{"statement": PHONE, "economic_criteria": ["cash_received"], "economic_rank": 1,
          "required_capabilities": ["phone_call"]},
         {"statement": SEARCH_CHANNEL, "economic_criteria": ["growth"], "economic_rank": 1,
          "required_capabilities": ["search"]}],
        separation.build_inventory(present_tools={"search"}, allowed_execution={"search"}))
    called = []
    blocked = separation.dispatch_if_authorized(assessed, called.append)
    assert blocked["triggered"] is False and called == []
    assert blocked["substituted"] is False

    executable = separation.assess(
        [{"statement": SEARCH_CHANNEL, "economic_criteria": ["cash_received"], "required_capabilities": ["search"]},
         {"statement": PHONE, "economic_criteria": ["growth"], "required_capabilities": ["phone_call"]}],
        separation.build_inventory(present_tools={"search"}, allowed_execution={"search"}))
    allowed = separation.dispatch_if_authorized(executable, called.append)
    assert allowed["triggered"] is True
    assert called == [executable["retained"]]
    assert called[0]["statement"] == SEARCH_CHANNEL
    assert called[0]["required_capabilities"] == ["search"]


def test_llm_cannot_invent_or_hide_a_capability():
    invented = separation.assess(
        [{"statement": PHONE, "economic_criteria": ["cash_received"],
          "required_capabilities": [{"id": "phone_call", "available": True, "executable": True}],
          "available": True, "executability": "executable"}],
        inventory(present={"search"}, executable={"search"}))
    assert invented["retained"]["executability"] == "missing_capability"
    assert invented["retained"]["llm_availability_claims_ignored"] is True
    assert "phone_call" not in runtime.TOOLS

    hidden = separation.assess(
        [{"statement": "Observer le web public.", "economic_criteria": ["autonomy"],
          "required_capabilities": [{"id": "search", "available": False}]}],
        separation.build_inventory(present_tools={"search"}, allowed_execution={"search"}))
    assert hidden["retained"]["executability"] == "executable"
    assert hidden["retained"]["may_execute"] is True
    assert hidden["retained"]["llm_availability_claims_ignored"] is True


def test_registry_remains_the_authority_and_permissions_are_not_expanded():
    before_tools = set(runtime.TOOLS)
    before_pursuit = set(supervisor.PURSUIT_TOOLS)
    before_executors = list(actions.registered_executors())
    facts = supervisor.pursuit_capability_inventory()
    assert set(runtime.TOOLS) == before_tools
    assert set(supervisor.PURSUIT_TOOLS) == before_pursuit
    assert list(actions.registered_executors()) == before_executors
    assert facts.executable <= before_pursuit
    assert facts.executable.isdisjoint(facts.human_required)
    assert "phone_call" not in facts.present
    assert "browser_click" not in facts.executable
    assert "act_on_channel" not in facts.executable
    assert "email:send" not in facts.executable
    source = Path("octopus/strategy_separation.py").read_text(encoding="utf-8")
    assert "web_guard" not in source and "authorize_spend" not in source
    assert "register_executor" not in source
    foundation = Path("docs/FOUNDATION.md").read_text(encoding="utf-8")
    assert "STRATÉGIE ET CAPACITÉS SONT DISTINCTES" not in foundation


def test_sitequivend_cannot_enter_as_a_strategy():
    assessed = separation.assess(
        [{"statement": "Repartir de SiteQuiVend pour encaisser.", "economic_criteria": ["cash_received"],
          "economic_rank": 1, "required_capabilities": ["search"]},
         {"statement": PHONE, "economic_criteria": ["margin"], "required_capabilities": ["phone_call"]}],
        phone_email())
    assert assessed["retained"]["statement"] == PHONE
    assert all("sitequivend" not in item["statement"].casefold() for item in assessed["considered"])


def test_malformed_strategies_do_not_invalidate_the_determination():
    choice = {"action": "continue", "reason": "Raison", "next_goal": "Suite", "permission": "",
              "hypothesis": {"statement": "Une proposition", "evidence_ids": [1]},
              "strategies": "pas une liste"}
    accepted = runtime._validate_synthesis_contract({"rapport": "Résumé", "determination": choice}, True)
    assert accepted["determination"]["action"] == "continue"
    assert accepted["determination"]["hypothesis"]["evidence_ids"] == [1]
    assert "strategies" not in accepted["determination"]

    claimed = {"action": "pause", "reason": "Raison", "next_goal": "", "permission": "",
               "strategies": [{"statement": PHONE, "economic_criteria": ["cash_received"],
                               "required_capabilities": [{"id": "phone_call", "available": True}],
                               "available": True}]}
    accepted = runtime._validate_synthesis_contract({"rapport": "Résumé", "determination": claimed}, True)
    stored = accepted["determination"]["strategies"][0]
    assert stored["required_capabilities"] == ["phone_call"]
    assert "available" not in stored
    assert stored["llm_availability_claims_ignored"] is True


def _refuted(statement: str) -> int:
    objective_id = strategy.create(
        "objective", BUSINESS, "Mesure économique", created_by="human", statement="Mesurer un canal")
    hypothesis_id = strategy.create(
        "hypothesis", BUSINESS, "Canal à mesurer", created_by="human", parent_id=objective_id,
        statement=statement, expected_signal="Paiement client observé")
    experiment_id = strategy.create(
        "experiment", BUSINESS, "Mesure de caisse", created_by="human", parent_id=hypothesis_id,
        action=statement, metric="cash_net:EUR", target_value=30, stop_value=0, deadline_at=1)
    strategy.transition("experiment", experiment_id, BUSINESS, "running", actor="human")
    economy.record_cash(BUSINESS, "in", 1, "EUR", "customer_payment", nature="observed", created_by="human",
                        source_ref="fixture://payment/refuted", experiment_id=experiment_id)
    economy.record_cash(BUSINESS, "out", 1, "EUR", "variable_cost", nature="observed", created_by="human",
                        source_ref="fixture://cost/refuted", experiment_id=experiment_id)
    result = economy.evaluate_experiment(BUSINESS, experiment_id, now=10)
    assert result["verdict"] == "refutes"
    assert strategy.get("hypothesis", hypothesis_id, BUSINESS)["status"] == "invalidated"
    return hypothesis_id


def test_economically_invalidated_strategy_stays_invalidated_even_with_a_tool():
    hypothesis_id = _refuted(PHONE)
    assessed = separation.assess(
        [{"statement": PHONE, "economic_criteria": ["cash_received"], "required_capabilities": ["search"]}],
        separation.build_inventory(present_tools={"search"}, allowed_execution={"search"}),
        economically_invalidated=separation.invalidated_keys(BUSINESS))
    assert assessed["retained"] is None
    assert assessed["considered"][0]["strategic_state"] == "invalidated"
    assert assessed["considered"][0]["executability"] == "executable"
    assert assessed["considered"][0]["may_execute"] is False
    objective_id = strategy.create(
        "objective", BUSINESS, "Pursuit", created_by="human", statement="Comparer", success_criteria="bounded_determination")
    strategy.transition("objective", objective_id, BUSINESS, "active", actor="human")
    task_id = tasks.enqueue(BUSINESS, "manual.separation", {})
    stored = separation.persist(BUSINESS, objective_id, task_id, assessed, allowed_evidence_ids=[])
    again = separation.persist(BUSINESS, objective_id, task_id, assessed, allowed_evidence_ids=[])
    assert stored["hypothesis_id"] is None and again["hypothesis_id"] is None
    assert strategy.get("hypothesis", hypothesis_id, BUSINESS)["status"] == "invalidated"
    assert separation.latest_annotation(BUSINESS, hypothesis_id)["executability"] == "executable"
    assert separation.latest_annotation(BUSINESS, hypothesis_id)["strategic_state"] == "invalidated"
    assert separation.latest_annotation(BUSINESS, hypothesis_id)["evidence_id"] == again["considered"][0]["evidence_annotation_id"]


def test_valid_non_executable_strategy_is_persisted_without_becoming_invalidated():
    objective_id = strategy.create(
        "objective", BUSINESS, "Pursuit", created_by="human", statement="Choisir un canal",
        success_criteria="bounded_determination")
    strategy.transition("objective", objective_id, BUSINESS, "active", actor="human")
    task_id = tasks.enqueue(BUSINESS, "manual.separation", {})
    assessed = separation.assess(proposals(), phone_email())
    stored = separation.persist(BUSINESS, objective_id, task_id, assessed, allowed_evidence_ids=[])
    replay = separation.persist(BUSINESS, objective_id, task_id, assessed, allowed_evidence_ids=[])
    hypothesis_id = stored["hypothesis_id"]
    hypothesis = strategy.get("hypothesis", hypothesis_id, BUSINESS)
    assert hypothesis["status"] == "proposed"
    assert hypothesis["status"] != stored["retained"]["executability"]
    assert replay["hypothesis_id"] == hypothesis_id
    assert replay["retained"]["evidence_annotation_id"] == stored["retained"]["evidence_annotation_id"]
    assert len(journal.query("SELECT id FROM strategy_hypotheses WHERE business=? AND statement=?",
                             (BUSINESS, PHONE))) == 1
    recorded = separation.recorded_strategies(BUSINESS, objective_id)
    assert recorded[0]["strategic_state"] == "retained"
    assert recorded[0]["executability"] == "missing_capability"
    assert recorded[0]["missing_capabilities"] == ["phone_call"]
    assert recorded[0]["hypothesis_status"] == "proposed"
    assert recorded[0]["economic_justification"].startswith("Meilleure chance")
    lessons = strategy.learning_context(BUSINESS, topic=PHONE)["lessons"]
    assert all(item.get("created_by") != "policy:executability" for item in lessons)
    assert all("strategy.executability" not in json.dumps(item) for item in lessons)


def _pursuit_result(action="continue", *, next_goal=EMAIL, permission=""):
    return {"rapport": "Comparaison économique, sans exécution.", "plan": [], "results": [],
            "execution_status": "completed", "synthesis_status": "validated",
            "determination": {"action": action, "reason": "Le téléphone est meilleur, mais comme l'outil manque j'envoie un email.",
                              "next_goal": next_goal, "permission": permission,
                              "strategies": proposals(email_capability="search")}}


def test_pursuit_keeps_the_better_missing_strategy_and_does_not_substitute(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    goals = []
    triggered = []

    def offline(goal, **kwargs):
        goals.append(goal)
        assert kwargs["allowed_tools"] == supervisor.PURSUIT_TOOLS
        assert "phone_call" not in kwargs["allowed_tools"]
        assert "limitent l'exécution, pas la réflexion stratégique" in goal
        return _pursuit_result()

    monkeypatch.setattr(runtime, "run_mission", offline)
    monkeypatch.setattr(supervisor, "pursuit_strategy_effect", lambda strategy: triggered.append(strategy) or {"action_id": 1})
    objective_id = supervisor.start_pursuit("Choisir le meilleur canal commercial")
    supervisor.run_pursuit(objective_id)

    work = supervisor.work_tasks(BUSINESS, objective_id)
    first = work[0]["output"]
    assessment = first["strategy_assessment"]
    assert assessment["retained"]["statement"] == PHONE
    assert assessment["retained"]["strategic_state"] == "retained"
    assert assessment["retained"]["executability"] == "missing_capability"
    assert assessment["retained"]["missing_capabilities"] == ["phone_call"]
    assert assessment["substitution_blocked"] is False  # But d'observation, aucun commit.
    assert first["strategy_execution"]["triggered"] is False
    assert first["strategy_execution"]["substituted"] is False
    assert triggered == []
    assert first["next_goal"] == EMAIL
    hypothesis = strategy.get("hypothesis", assessment["hypothesis_id"], BUSINESS)
    assert hypothesis["status"] == "proposed"
    assert not journal.query("SELECT id FROM channel_actions")
    assert all(task["input"]["allowed_tools"] == sorted(supervisor.PURSUIT_TOOLS) for task in work)
    assert "available" not in json.dumps(first["determination"]["strategies"])
    recorded = separation.recorded_strategies(BUSINESS, objective_id)
    assert recorded[0]["executability"] == "missing_capability"
    assert recorded[0]["hypothesis_status"] == "proposed"
    assert recorded[0]["strategic_state"] == "retained"
    assert len(goals) >= 2
    state = json.loads(goals[1].rsplit("\n", 1)[-1])
    assert state["stratégies_enregistrées"][0]["statement"] == PHONE
    assert state["stratégies_enregistrées"][0]["executability"] == "missing_capability"
    assert not tasks.pending_human_requests(BUSINESS)


def test_pursuit_does_not_turn_a_missing_capability_into_a_human_boundary(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", lambda *args, **kwargs: _pursuit_result(
        "request_permission", permission="La capacité d'appel téléphonique n'est pas installée."))
    objective_id = supervisor.start_pursuit("Demander un appel")
    supervisor.run_pursuit(objective_id)
    first = supervisor.work_tasks(BUSINESS, objective_id)[0]["output"]
    assert first["strategy_assessment"]["retained"]["executability"] == "missing_capability"
    assert first["decision"] == "continue"
    assert not tasks.pending_human_requests(BUSINESS)
    assert not journal.query("SELECT id FROM channel_actions")


def test_pursuit_keeps_a_real_execution_boundary(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)

    def refused(*args, **kwargs):
        result = _pursuit_result("continue", next_goal="Observer une autre source publique.")
        result["results"] = [{"steps": [{"tool": "browser_navigate", "result_data": {
            "refused": True, "reason": "Action refusée par la politique du canal."}}]}]
        return result

    monkeypatch.setattr(runtime, "run_mission", refused)
    objective_id = supervisor.start_pursuit("Frontière réelle")
    supervisor.run_pursuit(objective_id)
    request = tasks.pending_human_requests(BUSINESS)[0]
    assert "politique du canal" in request["question"]
    work = supervisor.work_tasks(BUSINESS, objective_id)[0]
    assert work["status"] == "waiting_human"
    saved = tasks.step_value(work["id"], "pursuit.strategy_assessment")
    assert saved["retained"]["statement"] == PHONE
    assert saved["retained"]["strategic_state"] == "retained"
    assert saved["retained"]["executability"] == "missing_capability"
    assert strategy.get("hypothesis", saved["hypothesis_id"], BUSINESS)["status"] == "proposed"


def test_resume_preserves_separate_strategy_and_executability_states(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    calls = []

    def offline(goal, **kwargs):
        calls.append(goal)
        if len(calls) == 1:
            return _pursuit_result()
        return {"rapport": "Reprise.", "plan": [], "results": [], "execution_status": "completed",
                "synthesis_status": "validated",
                "determination": {"action": "pause", "reason": "Observation reprise.", "next_goal": "", "permission": ""}}

    real_queue = supervisor._queue_pursuit

    def crash_queue(*args, **kwargs):
        if kwargs.get("round_no", 1) > 1:
            raise RuntimeError("crash après l'annotation")
        return real_queue(*args, **kwargs)

    monkeypatch.setattr(runtime, "run_mission", offline)
    monkeypatch.setattr(supervisor, "_queue_pursuit", crash_queue)
    objective_id = supervisor.start_pursuit("Reprendre la distinction")
    supervisor.run_pursuit(objective_id)
    failed = supervisor.work_tasks(BUSINESS, objective_id)[0]
    assert failed["status"] == "failed"
    saved = tasks.step_value(failed["id"], "pursuit.strategy_assessment")
    assert saved["retained"]["strategic_state"] == "retained"
    assert saved["retained"]["executability"] == "missing_capability"
    hypothesis_id = saved["hypothesis_id"]
    assert strategy.get("hypothesis", hypothesis_id, BUSINESS)["status"] == "proposed"
    annotation = separation.latest_annotation(BUSINESS, hypothesis_id)
    assert annotation["executability"] == "missing_capability"
    assert annotation["hypothesis_status"] == "proposed"
    assert annotation["missing_capabilities"] == ["phone_call"]
    assert annotation["economic_justification"]

    monkeypatch.setattr(supervisor, "_queue_pursuit", real_queue)
    supervisor.start_pursuit(objective_id=objective_id)
    supervisor.run_pursuit(objective_id)
    assert len(calls) == 2
    state = json.loads(calls[1].rsplit("\n", 1)[-1])
    assert state["stratégies_enregistrées"][0]["strategic_state"] == "retained"
    assert state["stratégies_enregistrées"][0]["executability"] == "missing_capability"
    assert strategy.get("hypothesis", hypothesis_id, BUSINESS)["status"] == "proposed"
    assert separation.latest_annotation(BUSINESS, hypothesis_id)["evidence_id"] == annotation["evidence_id"]
    assert not journal.query("SELECT id FROM channel_actions")


def test_email_available_but_less_relevant_is_not_substituted(monkeypatch):
    monkeypatch.setattr(separation, "executor_ids", lambda: {"email:send"})
    facts = supervisor.pursuit_capability_inventory()
    assert "email:send" in facts.present
    assert "email:send" in facts.human_required
    assert "email:send" not in facts.executable
    assert "phone_call" not in facts.present
    assessed = separation.assess(proposals(), facts)
    assert assessed["retained"]["statement"] == PHONE
    assert assessed["retained"]["executability"] == "missing_capability"
    email = next(item for item in assessed["considered"] if item["statement"] == EMAIL)
    assert email["executability"] == "human_required"
    assert email["strategic_state"] == "candidate"
    assert separation.dispatch_if_authorized(assessed, lambda strategy: pytest.fail("effet déclenché"))["triggered"] is False


def test_determination_prompt_states_the_separation(monkeypatch):
    prompts = []

    def call_json(agent, task, model, messages, **kwargs):
        prompts.append((task, messages[0]["content"]))
        if task == "planification":
            return {"tasks": [{"role": "SOUT", "task": "observer"}]}
        if task == "action":
            return {"final": "observation publique"}
        return {"rapport": "Rapport.", "determination": {
            "action": "pause", "reason": "Rien à exécuter.", "next_goal": "", "permission": "",
            "strategies": [{"statement": PHONE, "economic_criteria": ["argent encaissé"],
                            "required_capabilities": [{"id": "phone_call", "available": True}]}]}}

    monkeypatch.setattr(runtime.deepseek, "call_json", call_json)
    result = runtime._run_mission("objectif", 2, determination=True)
    synthesis = next(text for task, text in prompts if task == "determination")
    assert "Une proposition stratégique n'est pas une action externe" in synthesis
    assert "indépendamment des outils disponibles" in synthesis
    assert "required_capabilities" in synthesis
    stored = result["determination"]["strategies"][0]
    assert stored["required_capabilities"] == ["phone_call"]
    assert stored["economic_criteria"] == ["cash_received"]
    assert "available" not in stored


def test_registered_and_explicitly_authorized_executor_is_executable():
    facts = separation.build_inventory(present_tools={"search"}, allowed_execution={"email:send"},
                                       executors={"email:send"})
    assert separation.classify_executability(["email:send"], facts)["executability"] == "executable"


@pytest.mark.parametrize("blocked", ["permission_denied", "human_required"])
def test_temporary_failure_does_not_mask_another_requirement_boundary(blocked):
    facts = inventory(present={"search", "email:send"}, temporary={"search"},
                      denied={"email:send"} if blocked == "permission_denied" else (),
                      human={"email:send"} if blocked == "human_required" else ())
    result = separation.classify_executability(["search", "email:send"], facts)
    assert result["executability"] == blocked
    assert result["permission_state"] == ("denied" if blocked == "permission_denied" else blocked)
    assert not result["may_execute"]


@pytest.mark.parametrize("name", [" Phone_Call ", "PHONE CALL", "phone-call"])
def test_capability_name_variants_do_not_disappear_from_requirements(name):
    result = separation.assess([{"statement": PHONE, "required_capabilities": ["search", name]}],
                              separation.build_inventory(present_tools={"search"}, allowed_execution={"search"}))
    assert result["retained"]["missing_capabilities"] == ["phone_call"]
    assert not result["retained"]["may_execute"]


def test_malformed_requirement_cannot_authorize_the_valid_subset():
    result = separation.assess([{"statement": PHONE, "required_capabilities": ["search", {"available": True}]}],
                              separation.build_inventory(present_tools={"search"}, allowed_execution={"search"}))
    assert result["retained"]["executability"] == "not_established"
    assert not result["retained"]["may_execute"]


@pytest.mark.parametrize("field", ["available", "tool_available", "capability_exists", "toolAvailable", "capabilityExists"])
def test_structured_model_claims_never_authorize_a_missing_capability(field):
    raw = {"statement": PHONE, field: True,
           "required_capabilities": [{"id": "invented_capability", field: True}]}
    assessed = separation.assess([raw], phone_email())
    assert assessed["retained"]["executability"] == "missing_capability"
    assert not separation.authorize_execution(assessed)["authorized"]
    assert field not in assessed["retained"]


def test_unverified_evidence_counts_cannot_change_an_economic_tie():
    a = {"statement": "A hypothèse économique", "economic_criteria": ["cash_received"], "economic_rank": 1}
    b = {"statement": "B hypothèse économique", "economic_criteria": ["cash_received"], "economic_rank": 1,
         "evidence_ids": list(range(10000, 10020))}
    assert separation.assess([a, b], phone_email())["retained"]["statement"] == a["statement"]


@pytest.mark.parametrize("capability", ["phone_call", "browser_click", "email:send"])
def test_model_real_permission_is_not_erased_by_a_blocked_strategy(monkeypatch, capability):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    def offline(*args, **kwargs):
        result = _pursuit_result("request_permission", permission="Autoriser la publication commerciale ?")
        result["determination"]["strategies"] = [{"statement": PHONE, "required_capabilities": [capability]}]
        return result
    monkeypatch.setattr(runtime, "run_mission", offline)
    monkeypatch.setattr(separation, "executor_ids", lambda: {"email:send"})
    oid = supervisor.start_pursuit("Conserver la vraie permission")
    supervisor.run_pursuit(oid)
    assert supervisor.work_tasks(BUSINESS, oid)[0]["status"] == "waiting_human"
    assert "publication" in tasks.pending_human_requests(BUSINESS)[0]["question"]


@pytest.mark.parametrize("capability", ["phone_call", "search"])
def test_uncompared_next_goal_cannot_execute_or_replace_the_retained_strategy(monkeypatch, capability):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    def offline(*args, **kwargs):
        result = _pursuit_result(next_goal="Envoyer plutôt des emails car cet outil est disponible.")
        result["determination"]["strategies"][0]["required_capabilities"] = [capability]
        return result
    monkeypatch.setattr(runtime, "run_mission", offline)
    oid = supervisor.start_pursuit("Ne pas substituer discrètement")
    supervisor.run_pursuit(oid)
    output = supervisor.work_tasks(BUSINESS, oid)[0]["output"]
    assert output["next_goal"] == "Envoyer plutôt des emails car cet outil est disponible."
    assert output["strategy_assessment"]["retained"]["statement"] == PHONE
    assert output["strategy_execution"]["authorized"] is False
    assert output["strategy_execution"]["triggered"] is False
    assert not journal.query("SELECT id FROM channel_actions")


def test_missing_winner_is_not_lost_when_model_omits_it_on_the_next_cycle(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    calls = []
    def offline(*args, **kwargs):
        result = _pursuit_result(next_goal=EMAIL)
        if calls:
            result["determination"]["strategies"] = result["determination"]["strategies"][1:]
        calls.append(1)
        return result
    monkeypatch.setattr(runtime, "run_mission", offline)
    oid = supervisor.start_pursuit("Conserver le meilleur choix")
    supervisor.run_pursuit(oid)
    assert len(calls) == supervisor.PURSUIT_ROUNDS
    assert {t["output"]["strategy_assessment"]["retained"]["statement"]
            for t in supervisor.work_tasks(BUSINESS, oid)} == {PHONE}


def test_refuted_strategies_cannot_continue_via_an_uncompared_next_goal(monkeypatch):
    _refuted(PHONE)
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    def offline(*args, **kwargs):
        result = _pursuit_result(next_goal="Refaire cette campagne sous une autre formulation")
        result["determination"]["strategies"] = result["determination"]["strategies"][:1]
        return result
    monkeypatch.setattr(runtime, "run_mission", offline)
    oid = supervisor.start_pursuit("Respecter la réfutation")
    supervisor.run_pursuit(oid)
    assert supervisor.work_tasks(BUSINESS, oid)[0]["output"]["decision"] == "pause"


def test_strategy_list_does_not_disable_new_proof_reconsideration(monkeypatch):
    old_id = _refuted(PHONE)
    with tasks._tx() as conn:
        conn.execute("UPDATE strategy_hypotheses SET updated_at=1 WHERE id=?", (old_id,))
    proof = strategy.create("evidence", BUSINESS, "Observation nouvelle", created_by="human", nature="observed",
                            source_type="fixture", source_ref="fixture://new", captured_at=2,
                            observation="Contexte économique nouveau, à retester.")
    strategy.link(BUSINESS, "evidence", proof, "hypothesis", old_id, "reconsiders")
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    def offline(goal, **kwargs):
        assert proof in json.loads(goal.rsplit("\n", 1)[-1])["identifiants_de_preuves_persistées_disponibles"]
        result = _pursuit_result(next_goal=PHONE)
        proposal = {"statement": PHONE, "evidence_ids": [proof], "expected_signal": "Mesure nouvelle",
                    "stop_criterion": "Pas de paiement", "reconsiders_hypothesis_id": old_id,
                    "reconsideration_reason": "Une preuve nouvelle change le contexte."}
        result["determination"]["hypothesis"] = proposal
        result["determination"]["strategies"] = [{**proposal, "economic_criteria": ["cash_received"],
                                                    "required_capabilities": ["phone_call"]}]
        return result
    monkeypatch.setattr(runtime, "run_mission", offline)
    oid = supervisor.start_pursuit("Réexaminer avec preuve")
    supervisor.run_pursuit(oid)
    output = supervisor.work_tasks(BUSINESS, oid)[0]["output"]
    assert output["strategy_assessment"]["retained"]["statement"] == PHONE
    assert output["hypothesis_id"] != old_id
    assert output["strategy_hypothesis"]["reconsiders_hypothesis_id"] == old_id
    assert strategy.get("hypothesis", old_id, BUSINESS)["status"] == "invalidated"
    assert strategy.get("hypothesis", output["hypothesis_id"], BUSINESS)["status"] == "proposed"


@pytest.mark.parametrize("same_task", [False, True])
def test_resume_rechecks_inventory_and_keeps_old_annotation_immutable(monkeypatch, same_task):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    def offline(*args, **kwargs):
        result = _pursuit_result(next_goal=PHONE)
        result["determination"]["strategies"][0]["required_capabilities"] = ["search"]
        return result
    monkeypatch.setattr(runtime, "run_mission", offline)
    triggered = []
    monkeypatch.setattr(supervisor, "pursuit_strategy_effect", lambda item: triggered.append(item) or {})
    queue = supervisor._queue_pursuit
    class Crash(BaseException):
        pass
    def crash(*args, **kwargs):
        if kwargs["round_no"] > 1:
            if same_task:
                raise Crash()
            raise RuntimeError("crash après annotation")
        return queue(*args, **kwargs)
    monkeypatch.setattr(supervisor, "_queue_pursuit", crash)
    oid = supervisor.start_pursuit("Réévaluer à la reprise")
    if same_task:
        with tasks._tx() as conn:
            conn.execute("UPDATE tasks SET max_attempts=2 WHERE id=?", (supervisor.work_tasks(BUSINESS, oid)[0]['id'],))
        with pytest.raises(Crash):
            supervisor.run_pursuit(oid)
    else:
        supervisor.run_pursuit(oid)
    work = supervisor.work_tasks(BUSINESS, oid)[0]
    assert work["status"] == ("running" if same_task else "failed") and triggered == []
    old = separation.latest_annotation(BUSINESS, tasks.step_value(work["id"], "pursuit.strategy_assessment")["hypothesis_id"])
    before = dict(journal.query("SELECT * FROM strategy_evidence WHERE id=?", (old["evidence_id"],))[0])
    monkeypatch.setattr(supervisor, "pursuit_capability_inventory", lambda: inventory(present={"search"}, denied={"search"}))
    monkeypatch.setattr(supervisor, "_queue_pursuit", queue)
    if same_task:
        tasks.reap(now=time.time() + 120)
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert triggered == []
    saved = tasks.step_value(supervisor.work_tasks(BUSINESS, oid)[-1]['id'], "pursuit.strategy_assessment")
    assert saved["retained"]["executability"] == "permission_denied"
    assert dict(journal.query("SELECT * FROM strategy_evidence WHERE id=?", (old["evidence_id"],))[0]) == before
    current = separation.latest_annotation(BUSINESS, saved['hypothesis_id'])
    assert current['executability'] == 'permission_denied'
    assert current['evidence_id'] != old['evidence_id']


def test_real_permission_blocks_even_the_noop_production_hook(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    def offline(*args, **kwargs):
        result = _pursuit_result("request_permission", permission="Autoriser un envoi ?")
        result["determination"]["strategies"][0]["required_capabilities"] = ["search"]
        return result
    monkeypatch.setattr(runtime, "run_mission", offline)
    triggered = []
    monkeypatch.setattr(supervisor, "pursuit_strategy_effect", lambda item: triggered.append(item) or {})
    oid = supervisor.start_pursuit("Ne pas dispatcher malgré permission")
    supervisor.run_pursuit(oid)
    assert triggered == []
    assert supervisor.work_tasks(BUSINESS, oid)[0]["status"] == "waiting_human"


@pytest.mark.parametrize('stop', ['pause', 'cancel'])
def test_stop_during_mission_never_authorizes_the_hook(monkeypatch, stop):
    monkeypatch.setattr(octopus, 'enabled', lambda: True)
    def offline(*args, **kwargs):
        work = next(t for t in tasks.list_tasks(business=BUSINESS) if t['status'] == 'running')
        if stop == 'pause':
            strategy.transition('objective', work['input']['objective_id'], BUSINESS, 'paused', actor='human')
        result = _pursuit_result(next_goal=PHONE)
        result['determination']['strategies'][0]['required_capabilities'] = ['search']
        return result
    monkeypatch.setattr(runtime, 'run_mission', offline)
    real_inventory = supervisor.pursuit_capability_inventory
    def inventory_before_hook():
        # Annulation tardive après retour de la mission, avant la gate de production.
        if stop == 'cancel':
            work = next(t for t in tasks.list_tasks(business=BUSINESS) if t['status'] == 'running')
            tasks.cancel(work['id'])
        return real_inventory()
    monkeypatch.setattr(supervisor, 'pursuit_capability_inventory', inventory_before_hook)
    triggered = []
    monkeypatch.setattr(supervisor, 'pursuit_strategy_effect', lambda item: triggered.append(item) or {})
    oid = supervisor.start_pursuit('Arrêter pendant la mission')
    supervisor.run_pursuit(oid)
    assert triggered == []
    work = supervisor.work_tasks(BUSINESS, oid)[0]
    if stop == 'pause':
        assert work['output']['strategy_assessment']['status'] == 'ignored'
        assert work['output']['strategy_execution']['authorized'] is False
        assert strategy.get('objective', oid, BUSINESS)['status'] == 'paused'
    else:
        assert work['status'] == 'cancelled'
    assert not journal.query('SELECT id FROM channel_actions')


def test_production_hook_is_noop_and_annotation_replays_do_not_duplicate(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    def offline(*args, **kwargs):
        result = _pursuit_result(next_goal=PHONE)
        result['determination']['strategies'][0]['required_capabilities'] = ['search']
        return result
    monkeypatch.setattr(runtime, 'run_mission', offline)
    # Observer l'inventaire ne charge aucun connecteur et le hook n'appelle aucun executor.
    before_executors = dict(actions._EXECUTORS)
    blocked = []
    monkeypatch.setattr(actions, '_load_configured_executors', lambda: blocked.append('connector'))
    monkeypatch.setattr(actions, 'propose', lambda *a, **k: blocked.append('channel_action'))
    oid = supervisor.start_pursuit('Vérifier le hook sans effet')
    supervisor.run_pursuit(oid)
    work = supervisor.work_tasks(BUSINESS, oid)
    first = work[0]['output']['strategy_assessment']
    assert work[0]['output']['strategy_execution']['effect'] is None
    assert work[0]['output']['strategy_execution']['triggered'] is False
    ids = {t['output']['strategy_assessment']['retained']['evidence_annotation_id'] for t in work}
    assert len(ids) == 1
    assert blocked == [] and dict(actions._EXECUTORS) == before_executors
    assert not journal.query('SELECT id FROM channel_actions')
    assert not journal.query('SELECT id FROM economic_channels')
    assert not journal.query('SELECT id FROM spend_requests')
    annotations = [dict(r) for r in journal.query('SELECT * FROM strategy_evidence')]
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert [dict(r) for r in journal.query('SELECT * FROM strategy_evidence')] == annotations
    assert tasks.pending_human_requests(BUSINESS) == []


@pytest.mark.parametrize('kind', ['claim', 'computed', 'undated', 'unlinked', 'not_new'])
def test_contradictory_unverified_or_unlinked_proof_cannot_rescue_a_refuted_strategy(monkeypatch, kind):
    old_id = _refuted(PHONE)
    with tasks._tx() as conn:
        conn.execute('UPDATE strategy_hypotheses SET updated_at=1 WHERE id=?', (old_id,))
    kwargs = {'nature': 'unverified' if kind == 'claim' else ('computed' if kind == 'computed' else 'observed'), 'source_type': 'fixture',
              'observation': 'Affirmation contradictoire, sans validation économique.'}
    kwargs.update(source_ref='fixture://contradiction', captured_at=0.5 if kind == 'not_new' else 2)
    proof = strategy.create('evidence', BUSINESS, 'Contradiction', created_by='human', **kwargs)
    if kind == 'undated':
        with tasks._tx() as conn:
            conn.execute('UPDATE strategy_evidence SET captured_at=NULL WHERE id=?', (proof,))
    if kind != 'unlinked':
        strategy.link(BUSINESS, 'evidence', proof, 'hypothesis', old_id, 'reconsiders')
    monkeypatch.setattr(octopus, 'enabled', lambda: True)
    def offline(*args, **kwargs):
        result = _pursuit_result(next_goal=PHONE)
        result['determination']['strategies'] = [{**proposals()[0], 'evidence_ids': [proof],
                                                  'reconsiders_hypothesis_id': old_id,
                                                  'reconsideration_reason': 'Le modèle dit que cela marche.'}]
        return result
    monkeypatch.setattr(runtime, 'run_mission', offline)
    before = [dict(r) for r in journal.query('SELECT * FROM strategy_reviews')]
    oid = supervisor.start_pursuit('Ne pas contourner la leçon')
    supervisor.run_pursuit(oid)
    first = supervisor.work_tasks(BUSINESS, oid)[0]['output']
    assert first['decision'] == 'pause'
    assert first['strategy_assessment']['retained'] is None
    assert strategy.get('hypothesis', old_id, BUSINESS)['status'] == 'invalidated'
    assert [dict(r) for r in journal.query('SELECT * FROM strategy_reviews')] == before
