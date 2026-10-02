"""Pile #114–#117 : journal temporaire, détermination factice, aucun effet externe."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

import octopus
from agents import deepseek, runtime
from octopus import capability_acquisition as acquisition
from octopus import browser_workspace, economy, journal, llm, strategy, supervisor, tasks, worker
from octopus.pricing import Usage
from octopus import strategy_separation as separation
from test_pursuit_learning import _experience

BUSINESS = "octopus"
A = "Vendre une offre récurrente liée aux paiements clients par téléphone"
B = "Étudier la croissance via une recherche publique"


class ProcessStop(BaseException):
    """Interruption non absorbée par le handler du worker."""


def _offline_cycle(monkeypatch, history, *, native_runtime=False, transport=None,
                   technical_failure=None, permission="", capability="phone_call"):
    states = []
    original_mission = runtime.run_mission
    replies = []

    def mission(goal, **kwargs):
        state = json.loads(goal.rsplit("\n", 1)[-1])
        states.append(state)
        lesson = state["expériences_antérieures"][0]
        assert lesson["review_id"] == history["result"]["review_id"]
        assert lesson["result"]["cash_by_currency"]["EUR"]["net_observed"] == 35
        assert lesson["costs"]["llm_usd_at_evaluation"] == pytest.approx(0.031)
        assert lesson["costs"]["sunk_costs_are_not_a_decision_input"] is True
        assert kwargs["allowed_tools"] == supervisor.PURSUIT_TOOLS
        assert "phone_call" not in kwargs["allowed_tools"]
        # La fake utilise la leçon effectivement rechargée; sans elle, pas de proposition A.
        assert lesson["result"]["verdict"] == "supports"
        journal.record_llm_call({"ts": time.time(), "run_id": journal.current_run().id,
                                 "root_run_id": journal.current_run().root_id, "business": BUSINESS,
                                 "task": "fixture.analysis", "profile": "economical", "model": "fixture-model",
                                 "provider": "fixture", "cost_class": "paid", "status": "ok", "cost_usd": 0.01})
        proof = lesson["evaluation_evidence_id"]
        assert history["unverified_id"] not in state["identifiants_de_preuves_persistées_disponibles"]
        reply = {"rapport": "Comparaison déterministe à partir de la leçon persistée.",
                "plan": [], "results": [], "execution_status": "completed",
                "synthesis_status": "validated",
                "determination": {"action": "request_permission" if permission else "continue",
                                  "reason": "La leçon soutient une marge mesurée.",
                                  "next_goal": B, "permission": permission,
                                  "strategies": [
                                      {"statement": A, "economic_criteria": ["cash_received", "margin"],
                                       "economic_rank": 1, "required_capabilities": [capability],
                                       "evidence_ids": [proof], "expected_signal": "Paiements clients répétés"},
                                      {"statement": B, "economic_criteria": ["growth"], "economic_rank": 2,
                                       "required_capabilities": ["search"]}]}}
        replies.append(reply)
        return original_mission(goal, **kwargs) if native_runtime else reply

    actions = []

    def model(agent, stage, model, messages, **kwargs):
        if stage == "planification":
            value = {"tasks": [{"role": "SOUT", "task": "Analyser la leçon déjà persistée"}]}
        elif stage == "action":
            if technical_failure is not None and not actions:
                actions.append("browser_navigate")
                value = {"tool": "browser_navigate", "args": {"url": "https://public.example"}}
            else:
                value = {"final": "La leçon soutient la marge configurée; aucune observation nouvelle."}
        else:
            value = replies[-1]
        validator = kwargs.get("validate")
        return validator(value) if validator else value

    def navigate(*_a, **_k):
        if isinstance(technical_failure, Exception):
            raise technical_failure
        return {"ok": False, "refused": True, "reason": technical_failure}

    monkeypatch.setattr(octopus, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "run_mission", mission)
    if technical_failure is not None:
        monkeypatch.setattr(browser_workspace, "call", navigate)
    if transport is not None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        def respond(provider, request):
            stage = {700: "planification", 500: "action"}.get(request["max_tokens"], "determination")
            value = model("SOUT", stage, None, [])
            return llm.TransportResult(text=json.dumps(value), usage=Usage(prompt_tokens=10, completion_tokens=5),
                                       requested_model=request["model"], resolved_model=request["model"],
                                       resolved_provider="OfflineFake", provider_cost_usd=0.0)

        transport.handler = respond
    elif native_runtime:
        monkeypatch.setattr(deepseek, "call_json", model)
    monkeypatch.setattr(supervisor, "pursuit_strategy_effect",
                        lambda *_: pytest.fail("Substitution/exécution de stratégie"))
    monkeypatch.setattr(acquisition, "start_acquisition",
                        lambda *_a, **_k: pytest.fail("Acquisition automatique"))
    monkeypatch.setattr(acquisition, "execute_acquisition",
                        lambda *_: pytest.fail("Builder automatiquement lancé"))
    return states


def _assert_final(objective_id, history, before):
    work = supervisor.work_tasks(BUSINESS, objective_id)
    assert not any(item["status"] in tasks.ACTIVE for item in work)
    assert strategy.get("objective", objective_id, BUSINESS)["status"] == "paused"
    assert len({item["idempotency_key"] for item in work}) == len(work)
    for item in work:
        assert item["input"]["allowed_tools"] == sorted(supervisor.PURSUIT_TOOLS)
        if item["status"] == "done":
            output = item["output"]
            retained = output["strategy_assessment"]["retained"]
            assert retained["statement"] == A and retained["economic_rank"] == 1
            assert retained["executability"] == "missing_capability"
            assert retained["evidence_ids"] == [history["result"]["evidence_id"]]
            assert output["strategy_execution"]["triggered"] is False
            assert output["strategy_execution"]["substituted"] is False
            assert output["capability_acquisition"]["capability_gaps"] == ["phone_call"]
            assert output["capability_acquisition"]["executed"] is False
    for kind, item_id, row in before:
        assert strategy.get(kind, item_id, BUSINESS) == row
    assert len(strategy.list_items("review", BUSINESS)) == 1
    assert economy.llm_cost_usd(BUSINESS, experiment_id=history["experiment_id"]) == pytest.approx(0.031)
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_type=?", (acquisition.SOURCE_TYPE,))) == 1
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_type=?", (separation.SOURCE_TYPE,))) == 2
    assert not journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))
    assert not journal.query("SELECT id FROM channel_actions")
    assert not tasks.pending_human_requests(BUSINESS)
    for item in work:
        decisions = journal.query("SELECT id FROM strategy_decisions WHERE origin_task_id=?", (item["id"],))
        assert len(decisions) <= 1
    context = strategy.learning_context(BUSINESS, topic=A)
    studies = {row["id"] for row in journal.query("SELECT id FROM strategy_evidence WHERE source_type IN (?,?)",
                                                (acquisition.SOURCE_TYPE, separation.SOURCE_TYPE))}
    assert not studies.intersection(context["available_evidence_ids"])
    assert len(context["lessons"]) == 1


@pytest.mark.parametrize("boundary", [
    "none", "annotation.before", "annotation.after", "study.before", "study.after",
    "decision.before", "decision.after", "queue.before", "queue.after", "complete.before",
    "pause", "cancel", "native_runtime", "explicit_restart",
])
def test_history_strategy_gap_and_worker_resume_converge(monkeypatch, boundary):
    history = _experience(unverified_claim=True)
    before = [(kind, item_id, strategy.get(kind, item_id, BUSINESS)) for kind, item_id in
              [("evidence", history["result"]["evidence_id"]),
               ("decision", history["result"]["decision_id"]),
               ("review", history["result"]["review_id"])]]
    states = _offline_cycle(monkeypatch, history, native_runtime=boundary == "native_runtime")
    oid = supervisor.start_pursuit("Mesurer les paiements clients récurrents et la marge")
    first = supervisor.work_tasks(BUSINESS, oid)[0]
    worker.load_handlers(["octopus.builtin_handlers"])
    # Tester une reprise du même task par le worker, sous une politique de fixture à 2 essais.
    # Le cas pause/cancel conserve la vraie politique pursuit (1 essai) et reprend explicitement.
    if boundary not in {"none", "native_runtime", "explicit_restart", "pause", "cancel"}:
        with tasks._tx() as conn:
            conn.execute("UPDATE tasks SET max_attempts=2 WHERE id=?", (first["id"],))
    module, name = {
        "annotation": (separation, "persist"), "study": (acquisition, "plan_for"),
        "decision": (strategy, "create"), "queue": (supervisor, "_queue_pursuit"),
        "complete": (tasks, "complete"), "pause": (acquisition, "plan_for"),
        "cancel": (acquisition, "plan_for"), "none": (acquisition, "plan_for"),
        "native_runtime": (acquisition, "plan_for"), "explicit_restart": (acquisition, "plan_for"),
    }[boundary.split(".")[0]]
    original = getattr(module, name)
    interrupted = []

    def intercept(*args, **kwargs):
        relevant = boundary not in {"none", "native_runtime"} and not interrupted
        if name == "create":
            relevant = relevant and args[0] == "decision" and kwargs.get("created_by") == "octopus"
        if relevant:
            interrupted.append(boundary)
            if boundary == "pause":
                supervisor.pause_pursuit(oid)
                raise ProcessStop(boundary)
            if boundary == "cancel":
                tasks.cancel(first["id"])
                raise ProcessStop(boundary)
            if boundary.endswith("before"):
                raise ProcessStop(boundary)
        result = original(*args, **kwargs)
        if relevant:
            raise ProcessStop(boundary)
        return result

    monkeypatch.setattr(module, name, intercept)
    if boundary in {"none", "native_runtime"}:
        supervisor.run_pursuit(oid)
    else:
        with pytest.raises(ProcessStop):
            supervisor.run_pursuit(oid)
        monkeypatch.setattr(module, name, original)
        tasks.reap(now=time.time() + 120)
        if boundary in {"pause", "cancel", "explicit_restart"}:
            assert tasks.get(first["id"])["status"] == ("failed" if boundary == "explicit_restart" else "cancelled")
            supervisor.start_pursuit(objective_id=oid)
        else:
            assert tasks.get(first["id"])["status"] == "queued"
            with tasks._tx() as conn:
                conn.execute("UPDATE tasks SET not_before=0 WHERE id=?", (first["id"],))
        supervisor.run_pursuit(oid)
    # Un appel supplémentaire ne fait rien : aucun nouveau travail autonome après la borne.
    count = len(states)
    supervisor.run_pursuit(oid)
    assert len(states) == count <= supervisor.PURSUIT_ROUNDS + (boundary in {"pause", "cancel", "explicit_restart"})
    _assert_final(oid, history, before)
    assert sum(row["cost_usd"] for row in journal.query(
        "SELECT cost_usd FROM llm_calls WHERE provider='fixture' AND task='fixture.analysis' AND profile='economical'"
    )) == pytest.approx(0.01 * count)
    for task in supervisor.work_tasks(BUSINESS, oid):
        assert task["budget_usd"] is None
    if len(states) > 1:
        assert states[1]["expériences_antérieures"][0]["review_id"] == history["result"]["review_id"]
    assert len(json.dumps(states[-1], ensure_ascii=False)) <= 64000


def test_full_stack_uses_real_routing_and_contracts_with_fake_transport(monkeypatch, transport, providers_up):
    history = _experience(unverified_claim=True)
    states = _offline_cycle(monkeypatch, history, native_runtime=True, transport=transport)
    oid = supervisor.start_pursuit("Mesurer les paiements clients récurrents")
    supervisor.run_pursuit(oid)
    _assert_final(oid, history, [])
    assert len(states) == supervisor.PURSUIT_ROUNDS
    assert 0 < len(transport.calls) <= 15
    assert all(row["cost_usd"] == 0 for row in journal.query("SELECT cost_usd FROM llm_calls WHERE provider!='fixture'"))


def test_pursuit_keeps_caller_authorization_when_a_tool_is_absent(monkeypatch):
    monkeypatch.delitem(runtime.TOOLS, "search")
    inventory = supervisor.pursuit_capability_inventory()
    state = acquisition.capability_state("search", inventory)
    assert state["state"] == "missing"
    assert state["capable"] is False and state["available"] is False
    assert state["authorized"] is True
    assert supervisor.PURSUIT_TOOLS == frozenset({"search", "browse", "resources_status", "economy_status",
                                                "browser_navigate", "browser_snapshot", "browser_scroll", "browser_back",
        "account_task", "request_account", "create_artifact", "register_channel", "act_on_channel",
        "browser_click", "browser_type", "browser_select", "browser_check", "browser_press", "browser_verify", "browser_upload"})


@pytest.mark.parametrize("capability,initial,final", [
    ("phone_call", "missing", "permission_denied"),
    ("phone_call", "permission_denied", "missing"),
    ("search", "available", "missing"),
    ("search", "missing", "available"),
    ("search", "available", "temporarily_unavailable"),
    ("search", "temporarily_unavailable", "available"),
])
def test_same_worker_resume_refreshes_study_when_the_real_registry_changes(monkeypatch, capability, initial, final):
    history = _experience(unverified_claim=True)
    states = _offline_cycle(monkeypatch, history, capability=capability)
    # Le hook de production est sans effet, même lorsque A devient exécutable.
    monkeypatch.setattr(supervisor, "pursuit_strategy_effect", lambda item: {"dispatched": False})
    tool = dict(runtime.TOOLS["search"]) if capability == "search" else {
        "desc": "fixture", "params": {}, "fn": lambda *_: pytest.fail("Appel réel")}

    def registry_state(state):
        if state == "missing":
            monkeypatch.delitem(runtime.TOOLS, capability, raising=False)
        else:
            monkeypatch.setitem(runtime.TOOLS, capability, tool)
        monkeypatch.setattr(separation, "browser_unavailable_tools",
                            lambda _: {capability} if state == "temporarily_unavailable" else set())

    registry_state(initial)
    oid = supervisor.start_pursuit("Mesurer les paiements clients récurrents")
    tid = supervisor.work_tasks(BUSINESS, oid)[0]["id"]
    with tasks._tx() as conn:
        conn.execute("UPDATE tasks SET max_attempts=2 WHERE id=?", (tid,))
    original = strategy.create

    def crash(kind, *args, **kwargs):
        if kind == "decision" and kwargs.get("created_by") == "octopus":
            raise ProcessStop("après étude, avant décision")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(strategy, "create", crash)
    with pytest.raises(ProcessStop):
        supervisor.run_pursuit(oid)
    old = tasks.step_value(tid, "pursuit.capability_acquisition")
    assert old["items"][0]["state"] == initial
    old_row = dict(journal.query("SELECT * FROM strategy_evidence WHERE id=?", (old["evidence_annotation_id"],))[0])
    # Le vrai registre ou son état technique change, sans extension de PURSUIT_TOOLS.
    registry_state(final)
    monkeypatch.setattr(strategy, "create", original)
    tasks.reap(now=time.time() + 120)
    with tasks._tx() as conn:
        conn.execute("UPDATE tasks SET not_before=0 WHERE id=?", (tid,))
    worker.run_one(task_id=tid)
    output = tasks.get(tid)["output"]
    expected = {"missing": "missing_capability", "available": "executable"}.get(final, final)
    assert output["strategy_assessment"]["retained"]["executability"] == expected
    assert output["capability_acquisition"]["states"] == {capability: final}
    current = tasks.step_value(tid, "pursuit.capability_acquisition")
    assert current["items"][0]["capable"] is (final != "missing")
    assert current["items"][0]["authorized"] is (capability == "search")
    assert current["evidence_annotation_id"] != old["evidence_annotation_id"]
    assert dict(journal.query("SELECT * FROM strategy_evidence WHERE id=?", (old["evidence_annotation_id"],))[0]) == old_row
    assert len(states) == 1  # La détermination LLM est conservée; seul l'état opérationnel change.
    assert not tasks.pending_human_requests(BUSINESS)
    assert not journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))


@pytest.mark.parametrize("failure", [
    "timeout réseau : 30s", "hôte non résolvable : public.example", "HTTP 403 : interdit",
    "HTTP 404 : absent", "JSON invalide : tronqué", "source Web inaccessible : endpoint cassé",
    "navigateur indisponible : session fermée", RuntimeError("crash navigateur factice"),
    ValueError("validation échouée"),
])
def test_technical_failure_with_learning_strategy_and_gap_never_requests_human(monkeypatch, failure):
    history = _experience(unverified_claim=True)
    _offline_cycle(monkeypatch, history, native_runtime=True, technical_failure=failure)
    oid = supervisor.start_pursuit("Mesurer les paiements clients récurrents")
    supervisor.run_pursuit(oid)
    _assert_final(oid, history, [])


@pytest.mark.parametrize("frontier", [
    "login", "oauth", "2FA", "captcha", "KYC", "signature", "validation bancaire", "légal",
    "moyen de paiement", "permission explicite", "matériel physique",
])
def test_true_human_frontier_stays_blocking_with_learning_and_gap(monkeypatch, frontier):
    history = _experience(unverified_claim=True)
    _offline_cycle(monkeypatch, history, native_runtime=True, permission=f"Intervention nécessaire : {frontier}")
    oid = supervisor.start_pursuit("Mesurer les paiements clients récurrents")
    supervisor.run_pursuit(oid)
    work = supervisor.work_tasks(BUSINESS, oid)
    assert len(work) == 1 and work[0]["status"] == "waiting_human"
    requests = tasks.pending_human_requests(BUSINESS)
    assert len(requests) == 1 and frontier in requests[0]["question"]
    before = [dict(row) for row in journal.query("SELECT * FROM human_requests")]
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert [dict(row) for row in journal.query("SELECT * FROM human_requests")] == before
    assert len(strategy.list_items("review", BUSINESS)) == 1
    assert separation.recorded_strategies(BUSINESS, oid)[0]["statement"] == A
    assert work[0]["input"]["allowed_tools"] == sorted(supervisor.PURSUIT_TOOLS)
    assert not journal.query("SELECT id FROM channel_actions")
    assert not journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))


def test_processes_share_one_review_annotation_study_and_task(monkeypatch, tmp_path):
    history = _experience()
    hypothesis = strategy.create("hypothesis", BUSINESS, "Nouvelle mesure", created_by="human",
                                 parent_id=history["objective_id"], statement="La livraison est attestable")
    experiment = strategy.create("experiment", BUSINESS, "Concurrence intégrée", created_by="human",
                                 parent_id=hypothesis, action="Attester une livraison factice", metric="delivery",
                                 target_value=1)
    strategy.transition("experiment", experiment, BUSINESS, "running", actor="human")
    strategy.create("evidence", BUSINESS, "Livraison fixture", created_by="human", nature="observed",
                    source_type="fixture", source_ref="fixture://delivery", captured_at=time.time(),
                    experiment_id=experiment, metric="delivery", value=1, observation="Livraison factice attestée")
    oid = supervisor.start_pursuit("Paiements clients récurrents")
    tid = supervisor.work_tasks(BUSINESS, oid)[0]["id"]
    assessment = separation.assess(separation.normalize_proposals([
        {"statement": A, "economic_criteria": ["cash_received"], "economic_rank": 1,
         "required_capabilities": ["phone_call"]},
        {"statement": B, "economic_criteria": ["growth"], "economic_rank": 2,
         "required_capabilities": ["search"]}]), supervisor.pursuit_capability_inventory())
    payload = tmp_path / "assessment.json"
    payload.write_text(json.dumps(assessment), encoding="utf-8")
    script = """
import json, sys
from octopus import economy, strategy, supervisor, tasks, worker
from octopus import capability_acquisition as acquisition, strategy_separation as separation
exp, oid, tid = map(int, sys.argv[1:4])
assessment = json.load(open(sys.argv[4]))
result = economy.evaluate_experiment('octopus', exp)
record = separation.persist('octopus', oid, tid, assessment, allowed_evidence_ids=[])
study = acquisition.plan_for('octopus', oid, tid, record, inventory=supervisor.pursuit_capability_inventory())
task = tasks.enqueue('octopus', 'fixture.integrated', {}, resource='fixture', idempotency_key='fixture:same')
@worker.handler('fixture.integrated')
def offline(ctx):
    ctx.emit('fixture.integrated.executed', {})
    return {'fixture': True}
worker.run_one(task_id=task, log=lambda *_: None)
print(json.dumps([result['review_id'], result['decision_id'], result['evidence_id'],
                  record['hypothesis_id'], study['evidence_annotation_id'], task]))
"""
    env = dict(os.environ)
    for key in tuple(env):
        if key.endswith("API_KEY"):
            env.pop(key)
    commands = [sys.executable, "-c", script, str(experiment), str(oid), str(tid), str(payload)]
    processes = [subprocess.Popen(commands, env=env, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for _ in range(4)]
    outcomes = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            outcomes.append(json.loads(stdout.strip().splitlines()[-1]))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert all(item == outcomes[0] for item in outcomes)
    assert len(journal.query("SELECT id FROM strategy_reviews")) == 2
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_type=?", (separation.SOURCE_TYPE,))) == 2
    assert len(journal.query("SELECT id FROM strategy_evidence WHERE source_type=?", (acquisition.SOURCE_TYPE,))) == 1
    assert len(journal.query("SELECT id FROM events WHERE type='fixture.integrated.executed'")) == 1
    assert tasks.get(outcomes[0][-1])["status"] == "done"
    assert not tasks.pending_human_requests(BUSINESS)
    assert not journal.query("SELECT id FROM channel_actions")
    assert not journal.query("SELECT id FROM tasks WHERE kind=?", (acquisition.TASK_KIND,))
