"""Écarts de capacité et acquisition bornée.

Hors réseau, sans provider réel, sans achat, sans action externe et sans permission élargie :
les acquisitions démontrées passent par un double local explicitement enregistré.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import octopus
from agents import runtime
from octopus import actions, journal, resources, strategy, supervisor, tasks, worker
from octopus import capability_acquisition as acquisition
from octopus import strategy_separation as separation

BUSINESS = "octopus"
PHONE = "Le téléphone est le canal le plus intéressant pour encaisser."
EMAIL = "L'email est un canal disponible mais moins pertinent."
DUMMY = "Un écho local suffit pour éprouver le mécanisme d'acquisition."


def inventory(*, present=(), executable=(), temporary=(), denied=(), human=()):
    return separation.Inventory(present=set(present), executable=set(executable),
                                temporarily_unavailable=set(temporary), permission_denied=set(denied),
                                human_required=set(human))


def proposal(statement=PHONE, *, capabilities=("phone_call",), criteria=("cash_received",), rank=1,
             evidence=(), complete=True):
    item = {"statement": statement, "economic_justification": "Argent client encaissé attendu.",
            "economic_criteria": list(criteria), "economic_rank": rank,
            "required_capabilities": list(capabilities)}
    if evidence:
        item["evidence_ids"] = list(evidence)
    if not complete:
        item["requirements_complete"] = False
    return item


def persisted(proposals, facts, *, objective=None):
    """Objectif actif, tâche d'origine et annotation d'exécutabilité de #116 déjà persistée."""
    objective_id = strategy.create(
        "objective", BUSINESS, "Poursuite", created_by="human",
        statement=objective or "Comparer les canaux", success_criteria="bounded_determination")
    strategy.transition("objective", objective_id, BUSINESS, "active", actor="human")
    task_id = tasks.enqueue(BUSINESS, "manual.acquisition", {})
    assessed = separation.assess(proposals, facts)
    stored = separation.persist(BUSINESS, objective_id, task_id, assessed, allowed_evidence_ids=[])
    return objective_id, task_id, stored


def planned(proposals, facts, **kwargs):
    objective_id, task_id, stored = persisted(proposals, facts)
    record = acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts, **kwargs)
    return objective_id, task_id, stored, record


def local_double(capability="dummy_echo", *, executor=("dummy", "echo"), calls=None, register=True):
    """Constructeur local sûr : aucun service réel, aucun effet externe, aucune dépense."""
    calls = {"build": 0} if calls is None else calls

    def build(payload):
        calls["build"] += 1
        if register and executor not in actions.registered_executors():
            actions.register_executor(executor[0], executor[1],
                                      lambda channel, data: {"observation": "écho local",
                                                             "source_ref": "local://dummy_echo"},
                                      cost_class="local")
        return {"detail": "double local enregistré", "source_ref": "local://dummy_echo/builder"}

    builder = acquisition.register_builder(capability, build, complexity=1, risk="low",
                                           description="double local de test")
    return calls, builder


def ready(task_id):
    """Simule l'écoulement du délai de nouvelle tentative sans attendre."""
    with tasks._tx() as conn:
        conn.execute("UPDATE tasks SET not_before=0 WHERE id=?", (task_id,))


def run_task(task_id):
    worker.load_handlers(["octopus.builtin_handlers"])
    return worker.run_one(task_id=task_id, log=lambda *_: None)


@pytest.fixture(autouse=True)
def clean_registries():
    yield
    for name in list(acquisition.builders()):
        acquisition.unregister_builder(name)
    for key in [key for key in actions._EXECUTORS if key[0] in ("dummy", "broken")]:
        actions._EXECUTORS.pop(key, None)
    acquisition._TARGETS.clear()
    acquisition._TARGETS.update({k: dict(v) for k, v in acquisition.CAPABILITY_TARGETS.items()})
    acquisition._REQUIREMENTS.clear()
    acquisition._REQUIREMENTS.update({k: dict(v) for k, v in acquisition.CAPABILITY_REQUIREMENTS.items()})


class _args(dict):
    """Entrées de CLI minimales : aucun argument absent ne doit être deviné."""

    DEFAULTS = {"capability": None, "business": None, "allowed": None, "json": False, "probe": False,
                "objective": None, "hypothesis": None, "by": "human"}

    def __init__(self, capability_cmd, **values):
        super().__init__({**self.DEFAULTS, **values, "capability_cmd": capability_cmd})

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


# --- 1. capacité déjà disponible : aucun écart ------------------------------------------------------

def test_available_capability_produces_no_gap():
    facts = inventory(present={"search", "phone:call"}, executable={"search", "phone:call"})
    _, _, _, record = planned([proposal(capabilities=("phone_call",))], facts)
    assert record["status"] == "assessed"
    assert record["capability_gaps"] == []
    assert record["plans"] == []
    assert record["items"][0]["state"] == "available"
    assert record["items"][0]["requires_acquisition"] is False
    assert acquisition.render(record).endswith("aucun écart de capacité : rien à acquérir")


# --- 2. capacité absente : écart créé, stratégie conservée -------------------------------------------

def test_missing_capability_creates_a_gap_and_keeps_the_strategy():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, stored, record = planned([proposal()], facts)
    assert stored["retained"]["strategic_state"] == "retained"
    assert stored["retained"]["executability"] == "missing_capability"
    assert stored["retained"]["may_execute"] is False
    assert record["capability_gaps"] == ["phone_call"]
    gap = record["items"][0]
    assert gap["state"] == "missing" and gap["kind"] == "capability_gap"
    assert gap["requires_acquisition"] is True and gap["capable"] is False
    assert strategy.get("hypothesis", stored["hypothesis_id"], BUSINESS)["status"] == "proposed"


# --- 3. deux capacités requises, une seule manquante ------------------------------------------------

def test_only_the_truly_missing_capability_becomes_a_gap():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, _, record = planned([proposal(capabilities=("search", "phone_call"))], facts)
    assert record["capability_gaps"] == ["phone_call"]
    states = {item["capability"]: item["kind"] for item in record["items"]}
    assert states == {"search": "none", "phone_call": "capability_gap"}


# --- 4. indisponibilité temporaire : pas confondue avec une absence ----------------------------------

def test_temporary_outage_is_not_a_missing_capability():
    facts = inventory(present={"browser_navigate"}, executable=set(), temporary={"browser_navigate"})
    _, _, _, record = planned([proposal(capabilities=("browser_interaction",))], facts)
    assert record["capability_gaps"] == []
    item = record["items"][0]
    assert item["state"] == "temporarily_unavailable"
    assert item["kind"] == "outage" and item["requires_acquisition"] is False
    assert record["plans"] == []


# --- 5. permission refusée : jamais une acquisition technique ----------------------------------------

def test_permission_denied_is_not_converted_into_a_technical_acquisition():
    facts = inventory(present={"act_on_channel"}, executable=set(), denied={"act_on_channel"})
    _, _, _, record = planned([proposal(capabilities=("act_on_channel",))], facts)
    assert record["capability_gaps"] == []
    assert record["items"][0]["state"] == "permission_denied"
    assert record["items"][0]["kind"] == "permission"
    assert record["items"][0]["requires_acquisition"] is False
    assert record["plans"] == []


# --- 6. frontière humaine réelle conservée -----------------------------------------------------------

def test_human_boundary_is_preserved_and_not_turned_into_a_technical_gap():
    facts = inventory(present={"email:send"}, executable=set(), human={"email:send"})
    _, _, _, record = planned([proposal(capabilities=("email_send",))], facts)
    assert record["capability_gaps"] == []
    assert record["items"][0]["state"] == "human_required"
    assert record["items"][0]["kind"] == "human_boundary"
    assert record["items"][0]["requires_acquisition"] is False


def test_real_human_frontier_is_named_and_never_a_technical_repair():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, _, record = planned([proposal()], facts)
    plan = record["plans"][0]
    assert plan["decision"] == "human_required"
    assert plan["frontiers"] == ["login", "payment_method"]
    assert "frontière humaine réelle" in plan["reason"]
    option = next(item for item in plan["options"] if item["kind"] == "human_frontier")
    assert option["requires_human"] is True and option["local_safe"] is False
    for forbidden in ("timeout", "DNS", "endpoint", "JSON"):
        assert forbidden not in plan["reason"]


def test_declared_resource_need_becomes_a_human_frontier_option():
    resources.declare("phone_provider", "service", "Fournisseur téléphonique", created_by="human",
                      capabilities=["phone_call"], needs=["login", "payment_method"])
    facts = inventory(present={"search"}, executable={"search"})
    options = acquisition.acquisition_options("phone_call", facts)
    frontier = next(item for item in options if item["kind"] == "human_frontier")
    assert frontier["needs"] == ["login", "payment_method"]
    assert frontier["source"] in ("resources", "known_requirement")
    assert acquisition.resource_facts("phone_call")[0]["key"] == "phone_provider"


# --- 7. une déclaration de modèle ne prouve rien ------------------------------------------------------

@pytest.mark.parametrize("claim", [True, "available", {"available": True}])
def test_llm_availability_claim_is_ignored(claim):
    raw = {"statement": PHONE, "economic_criteria": ["cash_received"], "available": claim,
           "required_capabilities": [{"id": "phone_call", "available": claim}]}
    facts = inventory(present={"search"}, executable={"search"})
    assessed = separation.assess([raw], facts)
    assert assessed["retained"]["executability"] == "missing_capability"
    assert assessed["retained"]["required_capabilities"] == ["phone_call"]
    objective_id, task_id, stored = persisted([raw], facts)
    study = acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts)
    assert study["capability_gaps"] == ["phone_call"]
    assert study["items"][0]["available"] is False
    assert "available" not in json.dumps(study["items"][0]["resolution"])


def test_builder_claim_alone_never_validates_a_capability():
    calls, _ = local_double(register=False)  # le constructeur annonce sans rien enregistrer
    facts = inventory(present={"dummy_echo"}, executable={"dummy_echo"})
    objective_id, task_id, stored = persisted([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    # La capacité est retirée du registre réel : la déclaration du constructeur ne suffit pas.
    facts = inventory(present=set(), executable=set())
    record = acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts)
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    result = run_task(started["task_id"])
    assert calls["build"] == 1

    assert result["output"]["status"] == "failed"
    assert result["output"]["available"] is False
    assert acquisition.validate("dummy_echo")["capable"] is False
    assert acquisition.capability_state("dummy_echo", facts)["state"] == "missing"


def test_remembered_context_never_proves_a_capability():
    runtime._remember({"agent": "ORBIT", "key": "phone_call", "value": "disponible"})
    facts = inventory(present={"search"}, executable={"search"})
    state = acquisition.capability_state("phone_call", facts)
    assert state["available"] is False and state["capable"] is False
    assert acquisition.validate("phone_call")["capable"] is False
    recalculated = acquisition.system_inventory(allowed_execution={"search"})
    assert recalculated.present == separation.build_inventory(
        present_tools=set(runtime.TOOLS), allowed_execution={"search"},
        executors=separation.executor_ids(),
        temporarily_unavailable=separation.browser_unavailable_tools(set(runtime.TOOLS))).present


# --- 8. option peu coûteuse et réutilisable préférée --------------------------------------------------

def test_cheap_reusable_local_option_is_preferred():
    local_double()
    facts = inventory(present=set(), executable=set())
    proposals = [proposal(DUMMY, capabilities=("dummy_echo",)),
                 proposal("Un autre usage du même écho local.", capabilities=("dummy_echo",),
                          criteria=("margin",), rank=2)]
    _, _, _, record = planned(proposals, facts)
    plan = record["plans"][0]
    assert plan["value"]["reusability"] == 2
    assert plan["decision"] == "acquire"
    assert plan["chosen_option_id"] == "dummy_echo#local_build"
    assert plan["value"]["total"] - plan["evaluations"][0]["cost_score"] >= acquisition.ACQUIRE_THRESHOLD


# --- 9. acquisition coûteuse sans preuve économique ---------------------------------------------------

def test_expensive_acquisition_without_economic_proof_is_not_pursued():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, _, record = planned([proposal(capabilities=("payment_receive",), criteria=("growth",), rank=9)], facts)
    plan = record["plans"][0]
    assert plan["decision"] in ("reject", "human_required", "defer")
    assert plan["value"]["proof_count"] == 0
    chosen = next(item for item in plan["evaluations"] if item["option_id"] == plan["chosen_option_id"])
    assert chosen["cost_score"] > chosen["value_score"]
    assert acquisition.start_acquisition(
        BUSINESS, "payment_receive", record, requested_by="human")["status"] == "refused"


def test_unknown_requirements_are_deferred_and_never_invented():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, _, record = planned([proposal(capabilities=("quantum_billing",))], facts)
    plan = record["plans"][0]
    assert plan["decision"] == "defer"
    assert plan["options"][0]["kind"] == "none"
    assert "inconnu" in plan["reason"]
    assert plan["options"][0]["cost"]["financial"] is None


# --- 10. A meilleure avec écart reste retenue face à B exécutable --------------------------------------

def test_better_strategy_with_a_gap_stays_retained_next_to_an_executable_one():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, stored, record = planned(
        [proposal(PHONE, capabilities=("phone_call",), criteria=("cash_received",), rank=2),
         proposal(EMAIL, capabilities=("search",), criteria=("growth",), rank=1)], facts)
    assert stored["retained"]["statement"] == PHONE
    assert record["strategy_key"] == stored["retained"]["key"]
    assert record["capability_gaps"] == ["phone_call"]
    email = next(item for item in stored["considered"] if item["statement"] == EMAIL)
    assert email["strategic_state"] == "candidate" and email["executability"] == "executable"
    assert separation.authorize_execution(stored)["authorized"] is False


# --- 11. acquisition réussie via double local ----------------------------------------------------------

def test_local_double_acquisition_clears_the_gap_after_deterministic_validation():
    calls, _ = local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    assert record["capability_gaps"] == ["dummy_echo"]
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    assert started["status"] == "queued"
    assert acquisition.acquisition_state(BUSINESS, "dummy_echo", objective_id=objective_id,
                                         hypothesis_id=stored["hypothesis_id"])["state"] == "planned"
    result = run_task(started["task_id"])
    assert result["status"] == "done"
    assert result["output"]["status"] == "acquired"
    assert calls["build"] == 1
    validation = result["output"]["validation"]
    assert validation["capable"] is True
    assert validation["confirmed_by"] == ["executor_registered"]
    assert validation["source_ref"].startswith("actions.executor:")
    assert ("dummy", "echo") in actions.registered_executors()
    # Inventaire recalculé : l'écart disparaît seulement parce qu'une source réelle le confirme.
    refreshed = acquisition.system_inventory(allowed_execution={"dummy:echo"})
    assert acquisition.capability_state("dummy_echo", refreshed)["state"] == "available"
    assert acquisition.gaps(separation.assess([proposal(DUMMY, capabilities=("dummy_echo",))], refreshed),
                            refreshed)["capability_gaps"] == []
    state = acquisition.acquisition_state(BUSINESS, "dummy_echo", objective_id=objective_id,
                                          hypothesis_id=stored["hypothesis_id"])
    assert state["state"] == "acquired" and state["task_status"] == "done"
    proofs = journal.query("SELECT source_ref FROM strategy_evidence WHERE source_type=? AND created_by=?",
                           (acquisition.SOURCE_TYPE, acquisition.ACTOR))
    assert any("validation" in row["source_ref"] for row in proofs)
    assert not journal.query("SELECT id FROM channel_actions")


def test_capable_is_not_authorized():
    calls, _ = local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    assert run_task(started["task_id"])["output"]["status"] == "acquired"
    assert calls["build"] == 1
    allowed = acquisition.system_inventory(allowed_execution={"dummy:echo"})
    forbidden = acquisition.system_inventory(allowed_execution={"search"})
    assert acquisition.capability_state("dummy_echo", allowed)["available"] is True
    state = acquisition.capability_state("dummy_echo", forbidden)
    assert state["capable"] is True and state["authorized"] is False and state["available"] is False
    assert state["state"] in ("permission_denied", "human_required")
    assert separation.classify_executability(["dummy:echo"], forbidden)["may_execute"] is False


# --- 12. acquisition échouée --------------------------------------------------------------------------

def test_failed_acquisition_never_marks_the_capability_available():
    # Le constructeur enregistre un exécuteur qui ne correspond à aucune cible réelle de la capacité.
    calls, _ = local_double(executor=("broken", "echo"))
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    result = run_task(started["task_id"])
    assert calls["build"] == 1
    assert result["output"]["status"] == "failed"
    assert result["output"]["available"] is False
    assert acquisition.capability_state("dummy_echo", facts)["state"] == "missing"
    assert acquisition.acquisition_state(BUSINESS, "dummy_echo", objective_id=objective_id,
                                         hypothesis_id=stored["hypothesis_id"])["state"] == "failed"


# --- 13. reprise après crash au milieu d'une acquisition -----------------------------------------------

def test_crash_in_the_middle_of_an_acquisition_leaves_a_coherent_state_without_duplication(monkeypatch):
    calls, _ = local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    real_validate = acquisition.validate
    attempts = {"count": 0, "crashed": False}

    def crash_once_after_build(capability, **kwargs):
        attempts["count"] += 1
        if calls["build"] and not attempts["crashed"]:
            attempts["crashed"] = True
            raise RuntimeError("crash entre la construction et la validation")
        return real_validate(capability, **kwargs)

    monkeypatch.setattr(acquisition, "validate", crash_once_after_build)
    crashed = run_task(started["task_id"])
    assert crashed["status"] == "queued"  # nouvelle tentative différée, pas d'échec définitif
    assert attempts["crashed"] is True
    assert calls["build"] == 1
    state = acquisition.acquisition_state(BUSINESS, "dummy_echo", objective_id=objective_id,
                                          hypothesis_id=stored["hypothesis_id"])
    assert state["state"] == "validation_required"
    assert state["task_id"] == started["task_id"]
    ready(started["task_id"])
    resumed = run_task(started["task_id"])
    assert resumed["output"]["status"] == "acquired"
    assert calls["build"] == 1  # la construction mémoïsée n'est jamais rejouée
    assert attempts["count"] >= 3
    assert len(strategy.list_items("decision", BUSINESS)) == 1
    assert len(journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))) == 1


# --- 14. reprises répétées : idempotence ----------------------------------------------------------------

def test_repeated_resume_is_idempotent():
    calls, _ = local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    first = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                          objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    repeats = [acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                             objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
               for _ in range(3)]
    assert {item["task_id"] for item in repeats} == {first["task_id"]}
    assert len(journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))) == 1
    assert run_task(first["task_id"])["output"]["status"] == "acquired"
    after = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                          objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    assert after["status"] == "already_available"
    assert after["confirmed_by"] == ["executor_registered"]
    assert len(journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))) == 1
    assert calls["build"] == 1
    replays = [acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts)
               for _ in range(3)]
    assert {item["evidence_annotation_id"] for item in replays} == {record["evidence_annotation_id"]}
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_type=? AND created_by=? "
                             "AND source_ref NOT LIKE '%validation%'",
                             (acquisition.SOURCE_TYPE, acquisition.ACTOR))) == 1


# --- 15. capacité anciennement acquise redevenue indisponible -------------------------------------------

def test_a_previously_acquired_capability_that_disappears_is_recalculated():
    calls, _ = local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    assert run_task(started["task_id"])["output"]["status"] == "acquired"
    acquired_inventory = inventory(present={"dummy:echo"}, executable={"dummy:echo"})
    assert acquisition.capability_state("dummy_echo", acquired_inventory)["available"] is True
    actions._EXECUTORS.pop(("dummy", "echo"))  # l'exécuteur disparaît du registre réel
    lost = acquisition.system_inventory(allowed_execution={"dummy:echo"})
    state = acquisition.capability_state(
        "dummy_echo", lost,
        acquisition=acquisition.acquisition_state(BUSINESS, "dummy_echo", objective_id=objective_id,
                                                  hypothesis_id=stored["hypothesis_id"]))
    assert state["available"] is False
    assert state["stale_acquisition"] is True
    assert state["acquisition_state"] == "acquired"
    reopened = acquisition.gaps(separation.assess([proposal(DUMMY, capabilities=("dummy_echo",))], lost), lost)
    assert [item["capability"] for item in reopened["capability_gaps"]] == ["dummy_echo"]


def test_an_acquisition_in_progress_is_reported_without_rewriting_the_study():
    local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    assert started["status"] == "queued"
    again = acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts)
    # Le cycle de vie est visible, mais l'annotation persistée ne dépend que des registres réels.
    assert again["acquisition"]["dummy_echo"]["state"] == "planned"
    assert again["acquisition"]["dummy_echo"]["task_id"] == started["task_id"]
    assert again["items"][0]["acquisition_state"] == "none"
    assert again["capability_gaps"] == ["dummy_echo"]
    assert again["evidence_annotation_id"] == record["evidence_annotation_id"]
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_type=? AND created_by=?",
                             (acquisition.SOURCE_TYPE, acquisition.ACTOR))) == 1


def test_inventory_is_recalculated_and_never_read_from_memory():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, stored, record = planned([proposal()], facts)
    assert record["recalculated"] is True and record["executed"] is False
    annotation = acquisition.latest(BUSINESS, stored["hypothesis_id"])
    assert annotation["authority"] == "tool_registry"
    # Une annotation persistée ne rend jamais une capacité disponible.
    assert acquisition.capability_state("phone_call", facts, acquisition={"state": "acquired"})["available"] is False
    snapshot = acquisition.snapshot(["phone_call", "search"], inventory=facts)
    assert snapshot["recalculated"] is True
    assert {item["capability"]: item["state"] for item in snapshot["capabilities"]} == \
        {"phone_call": "missing", "search": "available"}


# --- 16. invalidation économique : ne pas poursuivre ----------------------------------------------------

def test_economically_invalidated_strategy_stops_the_acquisition():
    calls, _ = local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    assert record["plans"][0]["decision"] == "acquire"
    hypothesis_id = stored["hypothesis_id"]
    # La tâche d'acquisition est déjà en file lorsque la stratégie est économiquement invalidée.
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=hypothesis_id)
    assert started["status"] == "queued"
    strategy.transition("hypothesis", hypothesis_id, BUSINESS, "testing", actor="human")
    strategy.transition("hypothesis", hypothesis_id, BUSINESS, "invalidated", actor="human")
    result = run_task(started["task_id"])
    assert result["output"]["status"] == "not_needed"
    assert "invalidée" in result["output"]["reason"]
    assert calls["build"] == 0
    assert result["output"]["available"] is False
    refused = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=hypothesis_id)
    assert refused["status"] == "not_needed"
    assert calls["build"] == 0


def test_invalidated_value_never_justifies_an_acquisition():
    local_double()
    facts = inventory(present=set(), executable=set())
    value = acquisition.strategy_value({"economic_criteria": ["cash_received"], "economic_rank": 1,
                                        "evidence_ids": [1, 2], "economically_invalidated": True})
    option = acquisition.acquisition_options("dummy_echo", facts)[0]
    evaluation = acquisition.evaluate_option(option, value)
    assert evaluation["decision"] == "reject"
    assert "invalidée" in evaluation["reason"]


# --- 17. coûts déjà dépensés : jamais un motif de poursuite ----------------------------------------------

@pytest.mark.parametrize("sunk", [0.0, 12.5, 900.0])
def test_sunk_cost_never_pushes_an_acquisition_forward(sunk):
    local_double()
    facts = inventory(present=set(), executable=set())
    _, _, _, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts,
                              sunk_costs={"dummy_echo": sunk})
    plan = record["plans"][0]
    evaluation = next(item for item in plan["evaluations"] if item["option_id"] == plan["chosen_option_id"])
    assert plan["sunk_cost_usd"] == sunk and plan["sunk_cost_excluded"] is True
    assert evaluation["sunk_cost_excluded"] is True
    assert evaluation["net_score"] == round(evaluation["value_score"] - evaluation["cost_score"], 4)
    assert plan["decision"] == "acquire"
    assert "sunk" not in plan["reason"].lower()


def test_sunk_cost_does_not_revive_a_rejected_acquisition():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, _, cheap = planned([proposal(capabilities=("payment_receive",), criteria=("growth",), rank=9)], facts)
    _, _, _, funded = planned([proposal(capabilities=("payment_receive",), criteria=("growth",), rank=9)],
                              facts, sunk_costs={"payment_receive": 5000.0})
    assert cheap["plans"][0]["decision"] == funded["plans"][0]["decision"]
    assert funded["plans"][0]["sunk_cost_usd"] == 5000.0
    assert funded["plans"][0]["evaluations"][0]["net_score"] == cheap["plans"][0]["evaluations"][0]["net_score"]


# --- 18. aucune stratégie retenue : aucun écart inventé ---------------------------------------------------

def test_no_retained_strategy_invents_no_gap():
    facts = inventory(present={"search"}, executable={"search"})
    assert acquisition.gaps({"retained": None, "considered": []}, facts)["status"] == "no_retained_strategy"
    assessed = separation.assess([proposal(PHONE, capabilities=("phone_call",))], facts,
                                 economically_invalidated={strategy._hypothesis_key(PHONE)})
    assert assessed["retained"] is None
    objective_id, task_id, stored = persisted([proposal(PHONE, capabilities=("phone_call",))], facts)
    stored["retained"] = None
    stored["status"] = "no_retained"
    record = acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts)
    assert record["status"] == "no_retained_strategy"
    assert record["capability_gaps"] == [] and record["plans"] == []
    assert "evidence_annotation_id" not in record
    assert acquisition.recorded(BUSINESS, objective_id) == []


def test_incomplete_requirements_do_not_create_an_acquisition():
    facts = inventory(present={"search"}, executable={"search"})
    raw = {"statement": PHONE, "economic_criteria": ["cash_received"],
           "required_capabilities": ["search", {"available": True}]}
    objective_id, task_id, stored = persisted([raw], facts)
    assert stored["retained"]["executability"] == "not_established"
    record = acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts)
    assert record["capability_gaps"] == []
    assert record["items"][0]["kind"] == "requirements_incomplete"
    assert record["plans"] == []


# --- frontières humaines et sécurité -------------------------------------------------------------------

def test_human_request_is_opened_only_for_a_real_frontier():
    facts = inventory(present={"search"}, executable={"search"})
    _, _, _, record = planned([proposal()], facts)
    started = acquisition.start_acquisition(BUSINESS, "phone_call", record, requested_by="human")
    assert started["status"] == "refused"
    assert tasks.pending_human_requests(BUSINESS) == []

    acquisition.register_requirement("signed_contract", needs=("signature",), risk="low", complexity=1,
                                     human_minutes=5.0, financial=0.0, cost_class="local",
                                     description="contrat à faire signer")
    local_double("signed_contract", executor=("dummy", "sign"))
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, study = planned(
        [proposal("Faire signer un contrat d'abonnement.", capabilities=("signed_contract",))], facts)
    assert study["plans"][0]["decision"] == "human_required"
    forced = json.loads(json.dumps(study))
    forced["plans"][0]["decision"] = "acquire"
    forced["plans"][0]["options"][0]["local_safe"] = True
    forced["plans"][0]["options"][0]["executable_here"] = True
    started = acquisition.start_acquisition(BUSINESS, "signed_contract", forced, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    assert started["status"] == "refused"
    assert started["task_id"] is None
    assert tasks.pending_human_requests(BUSINESS) == []
    assert acquisition.acquisition_state(BUSINESS, "signed_contract", objective_id=objective_id,
                                         hypothesis_id=stored["hypothesis_id"])["state"] == "human_required"


def test_technical_failure_never_becomes_a_human_request():
    calls, _ = local_double(executor=("broken", "echo"))
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                            objective_id=objective_id, hypothesis_id=stored["hypothesis_id"])
    result = run_task(started["task_id"])
    assert result["output"]["status"] == "failed"
    assert tasks.pending_human_requests(BUSINESS) == []
    assert calls["build"] == 1


def test_paid_or_non_local_options_are_never_auto_acquired():
    acquisition.register_requirement("paid_capability", needs=(), risk="low", complexity=2,
                                     human_minutes=10.0, financial=15.0, currency="EUR",
                                     cost_class="paid", description="service payant")
    calls, _ = local_double("paid_capability", executor=("dummy", "paid"))
    facts = inventory(present=set(), executable=set())
    _, _, _, record = planned([proposal("Utiliser un service payant.", capabilities=("paid_capability",),
                                        criteria=("cash_received",), rank=1, evidence=[1, 2, 3])], facts)
    plan = record["plans"][0]
    assert plan["decision"] == "human_required"
    assert "enveloppe" in plan["reason"] or "autorisation humaine" in plan["reason"]
    assert acquisition.start_acquisition(BUSINESS, "paid_capability", record,
                                         requested_by="human")["status"] == "refused"
    assert calls["build"] == 0


def test_money_or_legal_frontier_cannot_be_replaced_by_a_local_double():
    calls, _ = local_double("payment_receive", executor=("dummy", "pay"))
    facts = inventory(present=set(), executable=set())
    _, _, _, record = planned([proposal("Encaisser directement par carte.", capabilities=("payment_receive",),
                                        criteria=("cash_received",), rank=1)], facts)
    plan = record["plans"][0]
    assert plan["decision"] == "human_required"
    build = next(item for item in plan["options"] if item["kind"] == "local_build")
    assert build["local_safe"] is False and build["executable_here"] is False
    assert "double local" in build["blocked_reason"]
    assert acquisition.start_acquisition(BUSINESS, "payment_receive", record,
                                         requested_by="human")["status"] == "refused"
    assert calls["build"] == 0


def test_module_never_widens_permissions_spends_or_touches_foundation():
    source = Path("octopus/capability_acquisition.py").read_text(encoding="utf-8")
    for forbidden in ("authorize_spend", "grant_allowance", "web_guard", "register_executor(",
                      "record_cash", "access=\"act\"", "OCTOPUS_WORKBENCH_READONLY"):
        assert forbidden not in source, forbidden
    foundation = Path("docs/FOUNDATION.md").read_text(encoding="utf-8")
    assert "CAPABILITY" not in foundation
    separation_source = Path("octopus/strategy_separation.py").read_text(encoding="utf-8")
    assert "capability_acquisition" not in separation_source
    assert acquisition.HUMAN_FRONTIERS == frozenset(resources.HUMAN_NEEDS) | {"permission", "physical_hardware"}


def test_builder_registration_is_bounded_and_validated():
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_builder("", lambda payload: {})
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_builder("dummy_echo", "pas appelable")
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_builder("dummy_echo", lambda payload: {}, risk="inconnu")
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_builder("dummy_echo", lambda payload: {}, complexity=9)
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_builder("dummy_echo", lambda payload: {}, financial=-1.0)
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_requirement("dummy_echo", needs=("pas_une_frontière",))
    assert acquisition.builders() == {}


# --- persistance, concurrence et reprise ----------------------------------------------------------------

def test_concurrent_planning_writes_a_single_annotation():
    facts = inventory(present={"search"}, executable={"search"})
    objective_id, task_id, stored = persisted([proposal()], facts)
    records, errors = [], []

    def plan_once():
        try:
            records.append(acquisition.plan_for(BUSINESS, objective_id, task_id, stored, inventory=facts))
        except Exception as exc:  # pragma: no cover - échec attendu nul
            errors.append(exc)

    threads = [threading.Thread(target=plan_once) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert len(records) == 6
    assert len({record["evidence_annotation_id"] for record in records}) == 1
    rows = journal.query("SELECT id FROM strategy_evidence WHERE source_type=? AND created_by=?",
                         (acquisition.SOURCE_TYPE, acquisition.ACTOR))
    assert len(rows) == 1
    assert len(acquisition.recorded(BUSINESS, objective_id)) == 1


def test_study_survives_a_restart_and_is_reread_on_resume():
    facts = inventory(present={"search"}, executable={"search"})
    objective_id, task_id, stored, record = planned([proposal()], facts)
    annotation_id = record["evidence_annotation_id"]
    reread = acquisition.latest(BUSINESS, stored["hypothesis_id"])
    assert reread["evidence_id"] == annotation_id
    assert reread["capability_gaps"] == ["phone_call"]
    assert reread["decisions"]["phone_call"] == "human_required"
    assert reread["hypothesis_status"] == "proposed"
    assert acquisition.latest_for_capability(BUSINESS, "phone_call", objective_id=objective_id)[
        "evidence_id"] == annotation_id
    assert acquisition.latest_for_capability(BUSINESS, "phone_call")["evidence_id"] == annotation_id
    assert acquisition.latest_for_capability(BUSINESS, "sms_send", objective_id=objective_id) is None
    context = acquisition.study_context(reread)
    assert context["acquisition"]["phone_call"]["décision"] == "human_required"
    assert context["aucune_exécution"] is True


def test_a_changed_study_adds_an_annotation_without_rewriting_the_previous_one():
    facts = inventory(present={"search"}, executable={"search"})
    objective_id, task_id, stored, first = planned([proposal()], facts)
    changed = acquisition.plan_for(BUSINESS, objective_id, task_id, stored,
                                   inventory=inventory(present={"search", "phone:call"},
                                                       executable={"search", "phone:call"}))
    assert changed["capability_gaps"] == []
    assert changed["evidence_annotation_id"] != first["evidence_annotation_id"]
    rows = journal.query("SELECT id FROM strategy_evidence WHERE source_type=? AND created_by=? ORDER BY id",
                         (acquisition.SOURCE_TYPE, acquisition.ACTOR))
    assert len(rows) == 2
    assert acquisition.latest(BUSINESS, stored["hypothesis_id"])["evidence_id"] == rows[-1]["id"]
    assert first["evidence_annotation_id"] == rows[0]["id"]


# --- intégration bornée au démarrage existant -----------------------------------------------------------

def _pursuit_result(action="continue", *, next_goal=EMAIL, permission=""):
    return {"rapport": "Comparaison économique, sans exécution.", "plan": [], "results": [],
            "execution_status": "completed", "synthesis_status": "validated",
            "determination": {"action": action, "reason": "Le téléphone est meilleur.",
                              "next_goal": next_goal, "permission": permission,
                              "strategies": [{"statement": PHONE, "economic_criteria": ["cash_received"],
                                              "economic_rank": 1, "required_capabilities": ["phone_call"],
                                              "expected_signal": "Paiement client après appel"},
                                             {"statement": EMAIL, "economic_criteria": ["growth"],
                                              "economic_rank": 2, "required_capabilities": ["search"]}]}}


def test_pursuit_reports_the_gap_without_executing_anything(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    goals = []

    def offline(goal, **kwargs):
        goals.append(goal)
        assert kwargs["allowed_tools"] == supervisor.PURSUIT_TOOLS
        return _pursuit_result()

    monkeypatch.setattr(runtime, "run_mission", offline)
    triggered = []
    monkeypatch.setattr(supervisor, "pursuit_strategy_effect",
                        lambda item: triggered.append(item) or {"action_id": 1})
    objective_id = supervisor.start_pursuit("Choisir le meilleur canal commercial")
    supervisor.run_pursuit(objective_id)
    work = supervisor.work_tasks(BUSINESS, objective_id)[0]
    output = work["output"]
    assert output["strategy_assessment"]["retained"]["statement"] == PHONE
    study = output["capability_acquisition"]
    assert study["capability_gaps"] == ["phone_call"]
    # Seule la stratégie retenue est étudiée : la capacité de la candidate n'est pas inventée.
    assert study["states"] == {"phone_call": "missing"}
    assert study["decisions"] == {"phone_call": "human_required"}
    assert study["frontiers"] == ["login", "payment_method"]
    assert study["executed"] is False
    assert "phone_call" in study["summary"] and "human_required" in study["summary"]
    assert triggered == []
    assert output["strategy_execution"]["triggered"] is False
    # Aucune demande humaine inventée pour une capacité absente, aucune action réelle.
    assert tasks.pending_human_requests(BUSINESS) == []
    assert not journal.query("SELECT id FROM channel_actions")
    assert not journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))
    assert work["status"] != "waiting_human"
    assert output["decision"] in ("continue", "pause")
    assert "limitent l'exécution, pas la réflexion stratégique" in goals[0]
    assert acquisition.CAPABILITY_ACQUISITION_CLAUSE not in goals[0]
    state = json.loads(goals[0].rsplit("\n", 1)[-1])
    assert state["écarts_de_capacités"] == []
    persisted_study = tasks.step_value(work["id"], "pursuit.capability_acquisition")
    assert persisted_study["capability_gaps"] == ["phone_call"]
    assert acquisition.recorded(BUSINESS, objective_id)[0]["decisions"]["phone_call"] == "human_required"


def test_pursuit_resume_reuses_the_persisted_study(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)
    calls = []

    def offline(goal, **kwargs):
        calls.append(goal)
        if len(calls) == 1:
            return _pursuit_result()
        return {"rapport": "Reprise.", "plan": [], "results": [], "execution_status": "completed",
                "synthesis_status": "validated",
                "determination": {"action": "pause", "reason": "Observation reprise.", "next_goal": "",
                                  "permission": ""}}

    real_queue = supervisor._queue_pursuit

    def crash_queue(*args, **kwargs):
        if kwargs.get("round_no", 1) > 1:
            raise RuntimeError("crash après l'étude d'acquisition")
        return real_queue(*args, **kwargs)

    monkeypatch.setattr(runtime, "run_mission", offline)
    monkeypatch.setattr(supervisor, "_queue_pursuit", crash_queue)
    objective_id = supervisor.start_pursuit("Reprendre l'étude des capacités")
    supervisor.run_pursuit(objective_id)
    failed = supervisor.work_tasks(BUSINESS, objective_id)[0]
    assert failed["status"] == "failed"
    study = tasks.step_value(failed["id"], "pursuit.capability_acquisition")
    hypothesis_id = study["hypothesis_id"]
    annotation_id = study["evidence_annotation_id"]

    monkeypatch.setattr(supervisor, "_queue_pursuit", real_queue)
    supervisor.start_pursuit(objective_id=objective_id)
    supervisor.run_pursuit(objective_id)
    assert len(calls) == 2
    state = json.loads(calls[1].rsplit("\n", 1)[-1])
    assert state["écarts_de_capacités"][0]["acquisition"]["phone_call"]["décision"] == "human_required"
    assert state["écarts_de_capacités"][0]["aucune_exécution"] is True
    rows = journal.query("SELECT id FROM strategy_evidence WHERE source_type=? AND created_by=?",
                         (acquisition.SOURCE_TYPE, acquisition.ACTOR))
    assert [row["id"] for row in rows] == [annotation_id]
    assert acquisition.latest(BUSINESS, hypothesis_id)["evidence_id"] == annotation_id
    assert not journal.query("SELECT id FROM channel_actions")


def test_pursuit_without_strategies_invents_no_gap(monkeypatch):
    monkeypatch.setattr(octopus, "enabled", lambda: True)

    def offline(goal, **kwargs):
        return {"rapport": "Observation publique.", "plan": [], "results": [],
                "execution_status": "completed", "synthesis_status": "validated",
                "determination": {"action": "pause", "reason": "Rien à comparer.", "next_goal": "",
                                  "permission": ""}}

    monkeypatch.setattr(runtime, "run_mission", offline)
    objective_id = supervisor.start_pursuit("Observer sans stratégie")
    supervisor.run_pursuit(objective_id)
    output = supervisor.work_tasks(BUSINESS, objective_id)[0]["output"]
    assert "capability_acquisition" not in output
    work = supervisor.work_tasks(BUSINESS, objective_id)[0]
    assert tasks.step_value(work["id"], "pursuit.capability_acquisition", None) is None
    assert not journal.query("SELECT id FROM strategy_evidence WHERE source_type=?",
                             (acquisition.SOURCE_TYPE,))


@pytest.mark.parametrize("stop", ["pause", "cancel"])
def test_stopped_pursuit_does_not_study_capabilities(monkeypatch, stop):
    monkeypatch.setattr(octopus, "enabled", lambda: True)

    def offline(*args, **kwargs):
        work = next(item for item in tasks.list_tasks(business=BUSINESS) if item["status"] == "running")
        if stop == "pause":
            strategy.transition("objective", work["input"]["objective_id"], BUSINESS, "paused", actor="human")
        return _pursuit_result()

    monkeypatch.setattr(runtime, "run_mission", offline)
    real_inventory = supervisor.pursuit_capability_inventory

    def inventory_before_study():
        # Annulation tardive : après la mission, avant l'étude des écarts de capacité.
        if stop == "cancel":
            work = next(item for item in tasks.list_tasks(business=BUSINESS) if item["status"] == "running")
            tasks.cancel(work["id"])
        return real_inventory()

    monkeypatch.setattr(supervisor, "pursuit_capability_inventory", inventory_before_study)
    objective_id = supervisor.start_pursuit("Arrêter pendant la mission")
    supervisor.run_pursuit(objective_id)
    work = supervisor.work_tasks(BUSINESS, objective_id)[0]
    assert "capability_acquisition" not in (work["output"] or {})
    assert tasks.step_value(work["id"], "pursuit.capability_acquisition", None) is None
    assert not journal.query("SELECT id FROM strategy_evidence WHERE source_type=?",
                             (acquisition.SOURCE_TYPE,))
    assert tasks.pending_human_requests(BUSINESS) == []
    assert not journal.query("SELECT id FROM channel_actions")
    if stop == "pause":
        assert work["output"]["strategy_assessment"]["status"] == "ignored"
    else:
        assert work["status"] == "cancelled"


# --- CLI bornée ---------------------------------------------------------------------------------------

def test_cli_reports_recalculated_states_and_refuses_an_undesided_acquisition(capsys):
    from octopus import __main__ as cli

    assert cli.cmd_capability(_args("state", capability=["phone_call", "search"])) == 0
    shown = capsys.readouterr().out
    assert "phone_call" in shown and "missing" in shown
    assert "search" in shown and "available" in shown
    assert "autorité : tool_registry" in shown

    assert cli.cmd_capability(_args("validate", capability="phone_call")) == 2
    assert json.loads(capsys.readouterr().out)["capable"] is False

    assert cli.cmd_capability(_args("gaps", business=BUSINESS, objective=1)) == 0
    assert "aucune étude" in capsys.readouterr().out

    assert cli.cmd_capability(_args("acquire", business=BUSINESS, capability="phone_call")) == 2
    assert "aucune étude persistée" in capsys.readouterr().out
    assert not journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))


def test_cli_acquires_only_an_already_decided_local_capability(capsys):
    from octopus import __main__ as cli

    calls, _ = local_double()
    facts = inventory(present=set(), executable=set())
    objective_id, task_id, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], facts)
    assert cli.cmd_capability(_args("acquire", business=BUSINESS, capability="dummy_echo",
                                    objective=objective_id, hypothesis=stored["hypothesis_id"])) == 0
    printed = json.loads(capsys.readouterr().out.split("\ntâche")[0])
    assert printed["status"] == "queued"
    assert calls["build"] == 0  # la CLI met en file ; seul le worker construit
    assert cli.cmd_capability(_args("gaps", business=BUSINESS, objective=objective_id)) == 0
    assert "dummy_echo" in capsys.readouterr().out
    assert run_task(printed["task_id"])["output"]["status"] == "acquired"
    assert cli.cmd_capability(_args("validate", capability="dummy_echo")) == 0
    assert json.loads(capsys.readouterr().out)["confirmed_by"] == ["executor_registered"]


# Targeted review of PR117: failures reproduced on c8ae5b0, offline only.
def review_plan(**builder_fields):
    calls, _ = local_double()
    if builder_fields:
        original = acquisition.builders()["dummy_echo"].build
        acquisition.register_builder("dummy_echo", original, **builder_fields)
    objective, task, stored, record = planned(
        [proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    return calls, objective, stored, record


def test_review_authorization_survives_temporary_outage():
    facts = separation.build_inventory(present_tools={"browse"}, allowed_execution={"browse"},
                                       temporarily_unavailable={"browse"})
    state = acquisition.capability_state("browser_interaction", facts)
    assert state["capable"] and state["authorized"] and not state["available"]


def test_review_absent_but_authorized_is_not_available():
    facts = acquisition.system_inventory(allowed_execution={"dummy_echo"})
    state = acquisition.capability_state("dummy_echo", facts)
    assert state["authorized"] and not state["capable"] and not state["available"]


@pytest.mark.parametrize("builder_financial,requirement_financial", [(None, 0.0), (0.0, None)])
def test_review_unknown_cost_cannot_be_erased_by_builder(builder_financial, requirement_financial):
    local_double()
    acquisition.register_builder("dummy_echo", acquisition.builders()["dummy_echo"].build,
                                 financial=builder_financial)
    acquisition.register_requirement("dummy_echo", financial=requirement_financial, cost_class="unknown",
                                     complexity=1, human_minutes=0.0, llm_usd=0.0)
    _, _, _, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    assert record["plans"][0]["decision"] == "defer"
    assert record["plans"][0]["options"][0]["cost"]["financial"] is None


def test_review_explicit_unknown_replaces_a_previous_cost():
    acquisition.register_requirement("phone_call", financial=None)
    assert acquisition.requirement("phone_call")["financial"] is None


def test_review_unpersisted_plan_cannot_enqueue():
    local_double()
    objective, task, stored = persisted([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    record = acquisition.plan_for(BUSINESS, objective, task, stored, inventory=inventory(), persist=False)
    assert acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")["status"] == "refused"
    assert not journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))


def test_review_scope_is_derived_from_persisted_study():
    calls, objective, stored, record = review_plan()
    strategy.transition("hypothesis", stored["hypothesis_id"], BUSINESS, "testing", actor="human")
    strategy.transition("hypothesis", stored["hypothesis_id"], BUSINESS, "invalidated", actor="human")
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    assert started["status"] in ("refused", "not_needed")
    assert calls["build"] == 0


@pytest.mark.parametrize("change", ["cost", "frontier", "unsafe"])
def test_review_worker_rechecks_current_builder_and_requirements(change):
    calls, objective, stored, record = review_plan()
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    original = acquisition.builders()["dummy_echo"].build
    if change == "cost":
        acquisition.register_builder("dummy_echo", original, financial=10.0)
    elif change == "unsafe":
        acquisition.register_builder("dummy_echo", original, local_safe=False)
    else:
        acquisition.register_requirement("dummy_echo", needs=("signature",), financial=0.0,
                                         complexity=1, human_minutes=0.0, cost_class="local")
    result = run_task(started["task_id"])
    assert calls["build"] == 0
    assert result["output"]["status"] != "acquired"
    assert not tasks.pending_human_requests(BUSINESS)


def test_review_unsafe_input_is_not_a_human_frontier():
    local_double()
    tid = tasks.enqueue(BUSINESS, acquisition.TASK_KIND,
                        {"capability": "dummy_echo", "decision": "acquire",
                         "option": {"local_safe": False, "executable_here": False}})
    result = run_task(tid)
    assert result["status"] != "waiting_human"
    assert not tasks.pending_human_requests(BUSINESS)


def test_review_contract_claim_requires_actual_source():
    acquisition.register_validator("dummy_echo", lambda _: {"ok": True, "source_ref": "invented://proof"})
    try:
        assert not acquisition.validate("dummy_echo")["capable"]
    finally:
        acquisition.unregister_builder("dummy_echo")


def test_review_validate_resolves_alias_and_never_grants_permission():
    actions.register_executor("dummy", "echo", lambda *_: {}, cost_class="local")
    assert acquisition.validate("dummy_echo", inventory=inventory(
        present={"dummy:echo"}, executable={"dummy:echo"}))["available"]
    assert not acquisition.validate("dummy_echo")["available"]


def test_review_probe_requires_a_successful_probe_not_persisted_state(monkeypatch):
    resources.declare("dummy_resource", "service", "Dummy", created_by="human",
                      capabilities=["dummy_echo"])
    resources.update("dummy_resource", actor="human", state="available", source_ref="old://state")
    assert not acquisition.validate("dummy_echo", probe=True)["capable"]


def test_review_builder_validator_is_used():
    calls, _ = local_double()
    build = acquisition.builders()["dummy_echo"].build
    checks = []
    acquisition.register_builder("dummy_echo", build,
                                 validate=lambda name: checks.append(name) or {"ok": False, "detail": "invalid"})
    objective, task, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    result = run_task(started["task_id"])
    assert checks and result["output"]["status"] == "failed"


@pytest.mark.parametrize("partial", [None, False, {"ok": False, "detail": "partial failure"}])
def test_review_ambiguous_builder_return_cannot_be_acquired(partial):
    calls, _ = local_double()
    original = acquisition.builders()["dummy_echo"].build
    acquisition.register_builder("dummy_echo", lambda payload: (original(payload), partial)[1])
    objective, task, stored, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    assert run_task(started["task_id"])["output"]["status"] == "failed"


def test_review_crash_before_build_memo_never_builds_twice(monkeypatch):
    calls, objective, stored, record = review_plan()
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    save = tasks.save_step
    crashed = []
    def crash(tid, key, value, **kwargs):
        if key == "capability.build" and not crashed:
            crashed.append(True)
            raise RuntimeError("crash after builder, before memo")
        return save(tid, key, value, **kwargs)
    monkeypatch.setattr(tasks, "save_step", crash)
    run_task(started["task_id"])
    actions._EXECUTORS.pop(("dummy", "echo"), None)
    ready(started["task_id"])
    run_task(started["task_id"])
    assert calls["build"] == 1


@pytest.mark.parametrize("boundary", ["decision_memo", "validation", "completion"])
def test_review_crash_does_not_duplicate_decision_or_validation(monkeypatch, boundary):
    calls, objective, stored, record = review_plan()
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    crashed = []
    if boundary == "decision_memo":
        original = tasks.save_step
        def crash(tid, key, value, **kwargs):
            if key == "capability.decision" and not crashed:
                crashed.append(True)
                raise RuntimeError("crash before memo")
            return original(tid, key, value, **kwargs)
        monkeypatch.setattr(tasks, "save_step", crash)
    else:
        original = acquisition._persist_validation if boundary == "validation" else tasks.complete
        def crash(*args, **kwargs):
            value = original(*args, **kwargs) if boundary == "validation" else None
            if not crashed:
                crashed.append(True)
                raise RuntimeError("crash after validation")
            return value if boundary == "validation" else original(*args, **kwargs)
        monkeypatch.setattr(acquisition if boundary == "validation" else tasks,
                            "_persist_validation" if boundary == "validation" else "complete", crash)
    run_task(started["task_id"])
    ready(started["task_id"])
    result = run_task(started["task_id"])
    assert result["output"]["status"] == "acquired"
    assert calls["build"] == 1
    assert len(strategy.list_items("decision", BUSINESS)) == 1
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_ref LIKE '%#validation#%'")) == 1


def test_review_validation_does_not_hide_the_study_or_acquisition_state():
    calls, objective, stored, record = review_plan()
    tid = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human",
                                         objective_id=objective, hypothesis_id=stored["hypothesis_id"])["task_id"]
    assert run_task(tid)["output"]["status"] == "acquired"
    assert acquisition.latest(BUSINESS, stored["hypothesis_id"])["decisions"] == {"dummy_echo": "acquire"}
    assert acquisition.snapshot(["dummy_echo"], business=BUSINESS)["capabilities"][0]["acquisition_state"] == "acquired"
    assert acquisition.recorded(BUSINESS, objective)[0]["decisions"] == {"dummy_echo": "acquire"}


def test_review_concurrent_scopes_share_one_acquisition():
    calls, objective, stored, record = review_plan()
    second_objective, _, second_stored, second_record = planned(
        [proposal("Un second objectif pour le même écho.", capabilities=("dummy_echo",))], inventory())
    results, errors = [], []
    def enqueue(study, oid, hid):
        try:
            results.append(acquisition.start_acquisition(BUSINESS, "dummy_echo", study, requested_by="human",
                                                         objective_id=oid, hypothesis_id=hid))
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=enqueue, args=args) for args in
               [(record, objective, stored["hypothesis_id"]),
                (second_record, second_objective, second_stored["hypothesis_id"])] * 3]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors
    assert len({item["task_id"] for item in results}) == 1


def test_review_computed_study_is_not_an_economic_proof():
    calls, objective, stored, record = review_plan()
    value = acquisition.strategy_value({**stored["retained"], "evidence_ids": [record["evidence_annotation_id"]]})
    assert value["proof_count"] == 0


def test_review_reusability_cannot_outrank_a_stronger_criterion():
    cash = acquisition.strategy_value({"economic_criteria": ["cash_received"], "economic_rank": 1})
    margin = acquisition.strategy_value({"economic_criteria": ["margin"], "economic_rank": 1}, reusability=12)
    assert cash["total"] > margin["total"]


def test_review_no_criterion_or_proof_is_not_positive_value():
    value = acquisition.strategy_value({"economic_rank": 1}, reusability=12)
    assert value["total"] == 0.0


def test_review_same_study_after_an_intervening_change_is_reused():
    local_double()
    objective, task, stored = persisted([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    first = acquisition.plan_for(BUSINESS, objective, task, stored, inventory=inventory())
    acquisition.plan_for(BUSINESS, objective, None, stored,
                         inventory=inventory(present={"dummy:echo"}, executable={"dummy:echo"}))
    third = acquisition.plan_for(BUSINESS, objective, None, stored, inventory=inventory())
    assert third["evidence_annotation_id"] == first["evidence_annotation_id"]
    assert acquisition.latest(BUSINESS, stored["hypothesis_id"])["evidence_id"] == first["evidence_annotation_id"]


@pytest.mark.parametrize("after_insert", [False, True])
def test_review_crash_at_enqueue_keeps_one_task(monkeypatch, after_insert):
    calls, objective, stored, record = review_plan()
    enqueue = tasks.enqueue
    crashed = []
    def interrupted(*args, **kwargs):
        if not crashed:
            crashed.append(True)
            if after_insert:
                enqueue(*args, **kwargs)
            raise RuntimeError("crash at task creation")
        return enqueue(*args, **kwargs)
    monkeypatch.setattr(tasks, "enqueue", interrupted)
    with pytest.raises(RuntimeError):
        acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    assert started["status"] == "queued"
    assert len(journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))) == 1
    assert run_task(started["task_id"])["output"]["status"] == "acquired"
    assert calls["build"] == 1


@pytest.mark.parametrize("boundary", ["before_intent", "after_intent", "after_validation", "before_decision", "after_event"])
def test_review_remaining_crash_boundaries(monkeypatch, boundary):
    calls, objective, stored, record = review_plan()
    tid = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")["task_id"]
    interrupted = []
    if boundary in ("before_intent", "after_intent"):
        original = tasks.save_step
        def crash(task_id, key, value, **kwargs):
            if key == "capability.build.started" and not interrupted:
                interrupted.append(True)
                if boundary == "after_intent": original(task_id, key, value, **kwargs)
                raise RuntimeError("crash at build intent")
            return original(task_id, key, value, **kwargs)
        monkeypatch.setattr(tasks, "save_step", crash)
    elif boundary == "after_validation":
        original = acquisition.validate
        def crash(*args, **kwargs):
            value = original(*args, **kwargs)
            if calls["build"] and not interrupted:
                interrupted.append(True)
                raise RuntimeError("crash after validation")
            return value
        monkeypatch.setattr(acquisition, "validate", crash)
    else:
        original = acquisition._record_decision if boundary == "before_decision" else tasks._emit
        def crash(*args, **kwargs):
            selected = boundary == "before_decision" or args[3] == "capability.acquisition"
            if selected and not interrupted:
                interrupted.append(True)
                if boundary == "after_event": original(*args, **kwargs)
                raise RuntimeError("crash at persistence")
            return original(*args, **kwargs)
        monkeypatch.setattr(acquisition if boundary == "before_decision" else tasks,
                            "_record_decision" if boundary == "before_decision" else "_emit", crash)
    run_task(tid)
    ready(tid)
    result = run_task(tid)
    if boundary == "after_intent":
        assert calls["build"] == 0 and result["output"]["status"] == "failed"
    else:
        assert calls["build"] == 1 and result["output"]["status"] == "acquired"
        assert len(strategy.list_items("decision", BUSINESS)) == 1
        assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_ref LIKE '%#validation#%'")) == 1
        assert len(journal.query("SELECT id FROM events WHERE type='capability.acquisition' AND task_id=?", (tid,))) == 1


@pytest.mark.parametrize("failure", ["timeout", "DNS", "invalid JSON", "403", "404", "broken endpoint", "crash", "validation"])
def test_review_builder_exception_is_not_retried_or_sent_to_a_human(failure):
    calls, objective, stored, record = review_plan()
    original = acquisition.builders()["dummy_echo"].build
    def broken(payload):
        original(payload)
        raise RuntimeError(failure)
    acquisition.register_builder("dummy_echo", broken)
    tid = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")["task_id"]
    run_task(tid)
    ready(tid)
    result = run_task(tid)
    assert calls["build"] == 1
    assert result["output"]["status"] != "acquired"
    assert not tasks.pending_human_requests(BUSINESS)


@pytest.mark.parametrize("need", sorted(acquisition.HUMAN_FRONTIERS))
def test_review_all_human_frontiers_refuse_local_construction(need):
    local_double()
    acquisition.register_requirement("dummy_echo", needs=[need], financial=0.0, complexity=1,
                                     human_minutes=0.0, cost_class="local")
    _, _, _, study = planned([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    assert study["decisions"]["dummy_echo"] == "human_required"
    assert acquisition.start_acquisition(BUSINESS, "dummy_echo", study, requested_by="human")["status"] == "refused"
    assert not tasks.pending_human_requests(BUSINESS)


@pytest.mark.parametrize("operation", ["state", "gaps", "validate"])
def test_review_cli_read_only_without_probe(monkeypatch, capsys, operation):
    from octopus import __main__ as cli
    _, objective, _, _ = review_plan()
    before = [dict(row) for row in journal.query("SELECT * FROM events")]
    def forbidden(*args, **kwargs):
        pytest.fail("read-only CLI attempted a probe, build, or acquisition")
    monkeypatch.setattr(resources, "check", forbidden)
    monkeypatch.setattr(acquisition, "start_acquisition", forbidden)
    args = _args(operation, business=BUSINESS, objective=objective,
                 capability=["dummy_echo"] if operation == "state" else "dummy_echo")
    cli.cmd_capability(args)
    capsys.readouterr()
    assert before == [dict(row) for row in journal.query("SELECT * FROM events")]


def test_review_process_workers_and_planners_share_one_builder(tmp_path):
    import os
    import subprocess
    import sys
    _, objective, stored, record = review_plan()
    study = tmp_path / "study.json"
    study.write_text(json.dumps(record), encoding="utf-8")
    builds = tmp_path / "builds.txt"
    program = '''
import json, sys
from pathlib import Path
from octopus import actions, capability_acquisition as acquisition, worker
def build(payload):
    with Path(sys.argv[2]).open("a") as out: out.write("build\\n")
    actions.register_executor("dummy", "echo", lambda *_: {}, cost_class="local")
    return {"detail": "process-local double"}
acquisition.register_builder("dummy_echo", build)
record = json.loads(Path(sys.argv[1]).read_text())
started = acquisition.start_acquisition("octopus", "dummy_echo", record, requested_by="human")
if started["task_id"]:
    worker.load_handlers(["octopus.builtin_handlers"])
    worker.run_one(task_id=started["task_id"], log=lambda *_: None)
'''
    processes = [subprocess.Popen([sys.executable, "-c", program, str(study), str(builds)],
                                  cwd=Path(__file__).resolve().parents[1], env=dict(os.environ),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    for process in processes:
        out, error = process.communicate(timeout=30)
        assert process.returncode == 0, out + error
    assert builds.read_text().splitlines() == ["build"]
    assert len(journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))) == 1
    assert len(strategy.list_items("decision", BUSINESS)) == 1
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_ref LIKE '%#validation#%'")) == 1
    # A process-local registration is not durable availability in another process.
    state = acquisition.snapshot(["dummy_echo"], business=BUSINESS)["capabilities"][0]
    assert state["state"] == "missing" and state["stale_acquisition"]


@pytest.mark.parametrize("llm_cost", [0.1, None])
def test_review_llm_financial_cost_is_never_implicitly_authorized(llm_cost):
    local_double()
    acquisition.register_requirement("dummy_echo", financial=0.0, llm_usd=llm_cost, complexity=1,
                                     human_minutes=0.0, cost_class="local")
    _, _, _, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    assert record["decisions"]["dummy_echo"] in ("defer", "human_required")
    assert acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")["status"] == "refused"


def test_review_local_safe_must_be_an_explicit_boolean():
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_builder("dummy_echo", lambda _: {}, local_safe="false")


@pytest.mark.parametrize("need", ["permission", "physical_hardware"])
def test_review_explicit_permission_and_physical_hardware_are_real_boundaries(need):
    acquisition.register_requirement("dummy_echo", needs=[need], financial=0.0, llm_usd=0.0,
                                     complexity=1, human_minutes=0.0, cost_class="local")
    local_double()
    _, _, _, record = planned([proposal(DUMMY, capabilities=("dummy_echo",))], inventory())
    assert record["decisions"]["dummy_echo"] == "human_required"
    assert acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")["status"] == "refused"


def test_review_permission_loss_does_not_make_a_valid_acquisition_stale():
    state = acquisition.capability_state("dummy_echo", inventory(
        present={"dummy:echo"}, human={"dummy:echo"}), acquisition={"state": "acquired"})
    assert state["capable"] and not state["authorized"] and not state["available"]
    assert not state["stale_acquisition"]


def test_review_cli_empty_allowed_does_not_restore_default_permissions(capsys):
    from octopus import __main__ as cli
    cli.cmd_capability(_args("state", capability=["search"], allowed="", json=True))
    state = json.loads(capsys.readouterr().out)["capabilities"][0]
    assert state["capable"] and not state["authorized"] and not state["available"]


def test_review_updating_cost_preserves_known_boundaries_and_risk():
    acquisition.register_requirement("phone_call", financial=None)
    needed = acquisition.requirement("phone_call")
    assert set(needed["needs"]) == {"login", "payment_method"}
    assert needed["risk"] == "high" and needed["cost_class"] == "paid"


def test_review_known_money_legal_boundary_cannot_be_erased_by_registration():
    with pytest.raises(acquisition.CapabilityAcquisitionError):
        acquisition.register_requirement("payment_receive", needs=(), financial=0.0, risk="low",
                                         complexity=1, human_minutes=0.0, cost_class="local")


def test_review_economic_validation_is_not_an_invalidation():
    calls, _, stored, record = review_plan()
    strategy.transition("hypothesis", stored["hypothesis_id"], BUSINESS, "testing", actor="human")
    strategy.transition("hypothesis", stored["hypothesis_id"], BUSINESS, "validated", actor="human")
    started = acquisition.start_acquisition(BUSINESS, "dummy_echo", record, requested_by="human")
    assert run_task(started["task_id"])["output"]["status"] == "acquired"
    assert calls["build"] == 1


def test_review_valid_contract_attests_an_actual_registered_source():
    actions.register_executor("dummy", "echo", lambda *_: {}, cost_class="local")
    acquisition.register_validator("dummy_echo", lambda _: {"ok": True, "source_ref": "actions.executor:dummy:echo"})
    try:
        checked = acquisition.validate("dummy_echo")
        assert checked["capable"] and "contract_test" in checked["confirmed_by"]
        assert not checked["available"]
    finally:
        acquisition.unregister_builder("dummy_echo")


def test_review_contract_can_attest_an_explicit_successful_probe(monkeypatch):
    from octopus import resource_probes
    resources.declare("dummy_resource", "service", "Dummy", created_by="human", capabilities=["dummy_echo"])
    probes = []
    monkeypatch.setattr(resource_probes, "run", lambda resource: probes.append(resource["key"]) or
                        resource_probes.ProbeResult(True, "local fixture", source_ref="local://fixture"))
    acquisition.register_validator("dummy_echo", lambda _: {"ok": True, "source_ref": "resource:dummy_resource:local://fixture"})
    try:
        assert not acquisition.validate("dummy_echo")["capable"]
        assert probes == []
        checked = acquisition.validate("dummy_echo", probe=True)
        assert checked["capable"] and not checked["available"]
        assert checked["confirmed_by"] == ["contract_test", "resource_probe"]
        assert probes == ["dummy_resource"]
    finally:
        acquisition.unregister_builder("dummy_echo")
