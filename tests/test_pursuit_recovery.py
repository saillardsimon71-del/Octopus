"""Offline first-start failures, durable recovery and unchanged authority."""
from __future__ import annotations

import json
import time
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agents import browser, deepseek, runtime, web_guard
from agents.gui.workbench_v2_data import mission_state, read_snapshot
from octopus import browser_workspace, journal, llm, strategy, supervisor, tasks, worker
from octopus.pricing import Usage


BUSINESS = "octopus"
SUFFIX = " Une réponse seule n'accorde aucun droit. Adaptez l'objectif ou configurez une autorisation explicite."


def determination(action="pause", permission=""):
    return {"rapport": "Observation publique, revenu inconnu.", "plan": [], "results": [],
            "execution_status": "completed", "synthesis_status": "validated",
            "determination": {"action": action, "reason": "Observation conservée.",
                              "permission": permission, "next_goal": "Comparer une autre source publique"}}


TECHNICAL_REFUSALS = [
    "hôte local refusé : api.octopus.local",
    "adresse locale ou privée refusée : 127.0.0.1",
    "adresse privée refusée : 10.0.0.1",
    "hôte non résolvable : unavailable.example",
    "URL sans hôte", "schéma refusé : file",
    "argument obligatoire manquant : url",
    "args doit être un objet, reçu list",
    "argument url : type attendu str, reçu int",
    "outil inconnu : unknown_tool",
    "HTTP 404 : source inexistante", "HTTP 403 : source inaccessible",
    "timeout réseau : 30s", "source Web inaccessible : public.example",
    "JSON invalide : sortie tronquée", "InvalidOutput: JSONDecodeError",
    "provider temporairement indisponible : 503",
    "navigateur indisponible : session fermée",
    "aucune page ouverte : commence par browser_navigate",
    "ref @e12 absente du dernier snapshot : la page a changé, observe-la à nouveau",
]


@pytest.mark.parametrize("refusal", TECHNICAL_REFUSALS)
def test_invalid_source_changes_to_public_alternative_without_human(monkeypatch, refusal):
    actions = iter([
        {"tool": "browser_navigate", "args": {"url": "https://api.octopus.local"}},
        {"tool": "browser_navigate", "args": {"url": "https://public.example"}},
        {"final": "Observation publique conservée"},
    ])
    visited = []

    def model(agent, stage, model, messages, **kwargs):
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": "Observer une source publique"}]}
        if stage == "action":
            return next(actions)
        return determination()

    def navigate(method, **kwargs):
        visited.append(kwargs["url"])
        if len(visited) == 1:
            return {"ok": False, "refused": True, "reason": refusal}
        return {"ok": True, "url": kwargs["url"], "snapshot": "Observation publique datée"}

    monkeypatch.setattr(deepseek, "call_json", model)
    monkeypatch.setattr(browser_workspace, "call", navigate)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    work = supervisor.work_tasks(BUSINESS, oid)[0]
    assert work["status"] == "done", work
    assert len(visited) == 2
    assert work["output"]["results"][0]["steps"][0]["failure_class"] == "technical"
    assert work["output"]["results"][0]["steps"][1]["result_data"]["ok"] is True
    assert tasks.pending_human_requests(BUSINESS) == []
    assert not journal.query("SELECT id FROM channel_actions")


@pytest.mark.parametrize("url", ["https://api.octopus.local", "https://localhost", "http://127.0.0.1",
                               "http://10.0.0.1", "file:///tmp/secret", "https:///missing"])
def test_real_web_guard_still_refuses_local_private_and_invalid_urls(url):
    with pytest.raises(web_guard.BrowseRefused):
        web_guard.classify(url)


def old_waiting_request(*, reason=TECHNICAL_REFUSALS[0], permission="", forbidden_tool=None, extra_request=False):
    oid = supervisor.start_pursuit()
    work = supervisor.work_tasks(BUSINESS, oid)[0]
    output = determination("request_permission" if permission else "pause", permission)
    output["results"] = [{"steps": [{"tool": forbidden_tool or "browser_navigate",
                                    "result_data": {"ok": False, "refused": True, "reason": reason}}]}]
    tasks.save_step(work["id"], "determination", output)
    claimed = tasks.claim("old-worker", task_id=work["id"])
    request_id = tasks.request_human(claimed["id"], "old-worker", "pursuit.permission", reason + SUFFIX,
                                     context={"objective_id": oid})
    if extra_request:
        with tasks._tx() as conn:
            conn.execute("INSERT INTO human_requests (ts, business, task_id, key, question) VALUES (?, ?, ?, ?, ?)",
                         (time.time(), BUSINESS, work["id"], "real.permission", "Autoriser un paiement ?"))
    return oid, work["id"], request_id, output


def test_restart_reconciles_only_proven_old_technical_request_without_recollecting(monkeypatch):
    oid, tid, rid, raw = old_waiting_request()
    before_cost = read_snapshot()["token_cost_usd"]
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: pytest.fail("Ancienne collecte rejouée"))
    assert supervisor.start_pursuit(objective_id=oid) == oid
    assert tasks.get(tid)["status"] == "queued"
    assert tasks.step_value(tid, "determination") == raw
    assert tasks.pending_human_requests(BUSINESS) == []
    assert supervisor.reconcile_pursuit_requests(oid) == []
    supervisor.run_pursuit(oid)
    assert tasks.get(tid)["status"] == "done"
    assert tasks.get(tid)["output"]["decision"] == "pause"
    request = journal.query("SELECT * FROM human_requests WHERE id=?", (rid,))[0]
    assert request["status"] == "cancelled" and request["answer"] is None
    assert len(journal.query("SELECT id FROM events WHERE type='human.technical_reconciled'")) == 1
    assert read_snapshot()["token_cost_usd"] == before_cost
    assert not journal.query("SELECT id FROM channel_actions")


@pytest.mark.parametrize("options", [
    {"reason": "modifier cette page exige un canal actif avec accès 'act' accordé par l'humain"},
    {"permission": "Envoyer une proposition commerciale exige une autorisation."},
    {"forbidden_tool": "browser_click"},
    {"reason": "action #1 au résultat inconnu (interruption) : ne pas la répéter."},
    {"reason": "Refus inconnu, à examiner"},
])
def test_restart_preserves_real_mixed_unknown_and_ambiguous_boundaries(options):
    oid, tid, rid, _ = old_waiting_request(**options)
    supervisor.start_pursuit(objective_id=oid)
    assert tasks.get(tid)["status"] == "waiting_human"
    assert [r["id"] for r in tasks.pending_human_requests(BUSINESS)] == [rid]
    assert not journal.query("SELECT id FROM events WHERE type='human.technical_reconciled'")


def test_reconciliation_does_not_requeue_task_with_another_real_request():
    oid, tid, rid, _ = old_waiting_request(extra_request=True)
    assert supervisor.reconcile_pursuit_requests(oid) == [rid]
    assert tasks.get(tid)["status"] == "waiting_human"
    assert tasks.pending_human_requests(BUSINESS)[0]["key"] == "real.permission"


def test_read_only_workbench_distinguishes_obsolete_request_and_preserves_database(monkeypatch):
    oid, tid, _, _ = old_waiting_request()
    before = [dict(row) for row in journal.query("SELECT * FROM events")]
    monkeypatch.setenv("OCTOPUS_WORKBENCH_READONLY", "1")
    snapshot = read_snapshot()
    assert snapshot["requests"][0]["technical_obsolete"] is True
    assert mission_state(snapshot["objectives"][0])[0] == "Reprise technique disponible"
    assert tasks.get(tid)["status"] == "waiting_human"
    assert [dict(row) for row in journal.query("SELECT * FROM events")] == before
    with pytest.raises(PermissionError):
        supervisor.start_pursuit(objective_id=oid)


def test_workbench_preserves_human_label_for_mixed_request():
    old_waiting_request(extra_request=True)
    snapshot = read_snapshot()
    assert sum(r["technical_obsolete"] for r in snapshot["requests"]) == 1
    assert mission_state(snapshot["objectives"][0])[0] == "Votre réponse attendue"


@pytest.mark.parametrize("real_permission", [False, True])
def test_windows_smoke_script_simulates_resume_on_copy_and_preserves_real_source(monkeypatch, isolated, real_permission):
    from scripts.check_pursuit_resume import check_resume, fingerprints
    monkeypatch.setenv("OCTOPUS_DB", str(isolated / "data" / "octopus.db"))
    options = {"permission": "Publier une offre exige une autorisation."} if real_permission else {}
    oid, tid, rid, _ = old_waiting_request(**options)
    strategy.create("evidence", BUSINESS, "Preuve antérieure", created_by="human", nature="observed",
                    source_type="url", source_ref="https://public.example", captured_at=time.time(),
                    observation="Observation antérieure")
    before = fingerprints(isolated / "data" / "octopus.db")
    report = check_resume(isolated)
    assert report["source_read_only"] and report["source_hashes_unchanged"]
    assert report["evidence_preserved"] and report["llm_cost_preserved"]
    assert report["existing_objective_reused"] and report["objective_id"] == oid
    assert report["real_work_executed"] is False
    assert report["cancelled_technical_request_ids"] == ([] if real_permission else [rid])
    assert fingerprints(isolated / "data" / "octopus.db") == before
    assert tasks.get(tid)["status"] == "waiting_human"
    assert [r["id"] for r in tasks.pending_human_requests(BUSINESS)] == [rid]
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_pursuit_resume.py"
    result = subprocess.run([sys.executable, str(script), "--data-root", str(isolated)],
                            env=dict(os.environ), text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["source_hashes_unchanged"] is True
    assert "test-key" not in result.stdout


def test_technical_model_permission_retries_bounded_but_real_permission_survives(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return determination("request_permission", TECHNICAL_REFUSALS[0])

    monkeypatch.setattr(runtime, "run_mission", run)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert len(calls) == supervisor.PURSUIT_ROUNDS
    assert tasks.pending_human_requests(BUSINESS) == []
    assert strategy.get("objective", oid, BUSINESS)["status"] == "paused"
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: determination(
        "request_permission", "Publier une offre exige une autorisation."))
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert "Publier" in tasks.pending_human_requests(BUSINESS)[0]["question"]


def test_explicit_llm_budget_exhaustion_remains_human_boundary(monkeypatch):
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: {
        "execution_status": "llm_unavailable", "synthesis_status": "degraded",
        "synthesis_error": "BudgetExceeded: plafond du run atteint", "results": []})
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert "Plafond LLM" in tasks.pending_human_requests(BUSINESS)[0]["question"]
    supervisor.start_pursuit(objective_id=oid)
    assert supervisor.work_tasks(BUSINESS, oid)[0]["status"] == "waiting_human"


@pytest.mark.parametrize("old_metadata", [False, True])
def test_resumed_task_keeps_root_and_costs_for_next_cycle(old_metadata):
    oid = supervisor.start_pursuit()
    work = supervisor.work_tasks(BUSINESS, oid)[0]
    with journal.run(BUSINESS, "task:supervisor.objective_work", budget_usd=0.20) as first:
        journal.record_llm_call({"ts": time.time(), "run_id": first.id, "root_run_id": first.root_id,
                                 "business": BUSINESS, "task": "agent.plan", "profile": "economical",
                                 "model": "deepseek/flash", "provider": "deepseek", "cost_class": "paid",
                                 "status": "ok", "cost_usd": 0.07})
    with journal.run(BUSINESS, "task:supervisor.objective_work", budget_usd=0.20, resume_run_id=first.id) as resumed:
        journal.record_llm_call({"ts": time.time(), "run_id": resumed.id, "root_run_id": resumed.root_id,
                                 "business": BUSINESS, "task": "agent.react_step", "profile": "economical",
                                 "model": "deepseek/flash", "provider": "deepseek", "cost_class": "paid",
                                 "status": "ok", "cost_usd": 0.02})
        assert resumed.root_id == first.root_id
        assert journal.query("SELECT root_id FROM runs WHERE id=?", (resumed.id,))[0]["root_id"] == first.root_id
    with tasks._tx() as conn:
        if old_metadata:
            conn.execute("UPDATE runs SET root_id=id WHERE id=?", (resumed.id,))
            conn.execute("UPDATE llm_calls SET root_run_id=? WHERE run_id=?", (resumed.id, resumed.id))
        conn.execute("UPDATE tasks SET run_id=? WHERE id=?", (resumed.id, work["id"]))
    with journal.run(BUSINESS, "task:supervisor.objective_work", resume_run_id=resumed.id) as third:
        assert third.root_id == first.id
    next_id = supervisor._queue_pursuit(oid, round_no=2, previous_id=work["id"], next_goal="Suite")
    assert tasks.get(next_id)["budget_usd"] == pytest.approx(0.11)
    assert read_snapshot()["pursuit_llm"]["spent_usd"] == pytest.approx(0.09)


def test_synthesis_retry_reuses_raw_collection_and_only_repeats_synthesis(monkeypatch):
    calls, sources = [], []

    def model(agent, stage, model, messages, **kwargs):
        calls.append(stage)
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": "Observer le Web public"}]}
        if stage == "action":
            return {"tool": "search", "args": {"query": "source publique"}} if not sources else {"final": "Collecte terminée"}
        if calls.count("determination") == 1:
            raise llm.InvalidOutput("JSONDecodeError: sortie tronquée")
        assert "Preuve brute conservée" in json.dumps(messages, ensure_ascii=False)
        return determination()

    def source(args):
        sources.append(args)
        return {"ok": True, "text": "Preuve brute conservée", "url": "https://public.example"}

    monkeypatch.setattr(deepseek, "call_json", model)
    monkeypatch.setitem(runtime.TOOLS["search"], "fn", source)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    works = supervisor.work_tasks(BUSINESS, oid)
    assert [w["status"] for w in works] == ["done_degraded", "done"]
    assert calls.count("planification") == 1 and len(sources) == 1
    assert calls.count("determination") == 2
    assert works[0]["output"]["results"] == works[1]["output"]["results"]
    assert tasks.pending_human_requests(BUSINESS) == []


def test_generic_supervisor_synthesis_retry_also_reuses_collection(monkeypatch):
    calls, sources = [], []

    def model(agent, stage, model, messages, **kwargs):
        calls.append(stage)
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": "Observer une source publique"}]}
        if stage == "action":
            return {"tool": "search", "args": {"query": "source publique"}} if not sources else {"final": "Collecte terminée"}
        if calls.count("synthese") == 1:
            raise llm.InvalidOutput("JSON invalide")
        return {"rapport": "Observation publique conservée, résultat économique inconnu"}

    def search(args):
        sources.append(args)
        return {"ok": True, "text": "Observation publique conservée"}

    monkeypatch.setattr(deepseek, "call_json", model)
    monkeypatch.setitem(runtime.TOOLS["search"], "fn", search)
    worker.load_handlers(["octopus.builtin_handlers"])
    oid = strategy.create("objective", BUSINESS, "Étude publique", created_by="human", statement="Étudier une piste")
    strategy.transition("objective", oid, BUSINESS, "active", actor="human")
    first_id = supervisor.plan_work(BUSINESS, strategy.get("objective", oid, BUSINESS), profile="economical")
    assert worker.run_one(task_id=first_id, log=lambda _: None)["status"] == "done_degraded"
    decision = supervisor.decide(BUSINESS, strategy.get("objective", oid, BUSINESS), tasks.get(first_id),
                                 retry_delay_s=0, max_attempts=2)
    second = worker.run_one(task_id=decision["next_work_task_id"], log=lambda _: None)
    assert second["status"] == "done"
    assert calls.count("planification") == 1 and calls.count("synthese") == 2
    assert len(sources) == 1
    assert second["output"]["results"] == tasks.get(first_id)["output"]["results"]
    assert tasks.pending_human_requests(BUSINESS) == []


def test_crash_after_observation_resumes_partial_step_without_duplicate_collection(monkeypatch):
    calls, sources = [], []

    class Crash(BaseException):
        pass

    def model(agent, stage, model, messages, **kwargs):
        calls.append(stage)
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": "Observer une source publique"}]}
        if stage == "action":
            if not sources:
                return {"tool": "search", "args": {"query": "source publique"}}
            if calls.count("action") == 2:
                raise Crash()
            assert "Observation avant crash" in json.dumps(messages, ensure_ascii=False)
            return {"final": "Observation réutilisée"}
        return determination()

    def source(args):
        sources.append(args)
        return {"ok": True, "text": "Observation avant crash"}

    monkeypatch.setattr(deepseek, "call_json", model)
    monkeypatch.setitem(runtime.TOOLS["search"], "fn", source)
    oid = supervisor.start_pursuit()
    first = supervisor.work_tasks(BUSINESS, oid)[0]
    with pytest.raises(Crash):
        supervisor.run_pursuit(oid)
    assert tasks.step_value(first["id"], "pursuit.progress")["agent_steps"][0]["result_data"]["ok"]
    tasks.reap(now=time.time() + 120)
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert len(sources) == 1 and calls.count("planification") == 1
    assert supervisor.work_tasks(BUSINESS, oid)[-1]["status"] == "done"
    assert tasks.pending_human_requests(BUSINESS) == []


@pytest.mark.parametrize("error", ["429 cooldown", "provider indisponible", "timeout réseau"])
def test_persistent_technical_llm_failures_pause_after_three_cycles_without_human(monkeypatch, error):
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: (_ for _ in ()).throw(llm.GatewayError(error)))
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert len(supervisor.work_tasks(BUSINESS, oid)) == supervisor.PURSUIT_ROUNDS
    assert strategy.get("objective", oid, BUSINESS)["status"] == "paused"
    assert tasks.pending_human_requests(BUSINESS) == []
    assert all(w["status"] == "done_degraded" for w in supervisor.work_tasks(BUSINESS, oid))
    assert read_snapshot()["pursuit_llm"]["spent_usd"] == 0


@pytest.mark.parametrize("failure", ["structured_400", "invalid_json", "429", "provider_down"])
def test_real_gateway_supervisor_path_recovers_without_live_provider(monkeypatch, transport, providers_up, failure):
    """Real routing/contracts, fake HTTP transport, real durable worker and supervisor."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    failed = False
    searches = []

    class UnsupportedFormat(Exception):
        status_code = 400
        body = {"error": {"message": "response_format json_object is not supported"}}

    class RateLimit(Exception):
        status_code = 429
        response = type("Response", (), {"status_code": 429, "headers": {"retry-after": "60"}})()

    def respond(provider, request):
        nonlocal failed
        if not failed:
            failed = True
            if failure == "structured_400":
                return UnsupportedFormat("400")
            if failure == "429":
                return RateLimit("429")
            if failure == "provider_down":
                return ConnectionError("provider indisponible")
            text = '{"tasks":'
        elif request["max_tokens"] == 700:
            text = json.dumps({"tasks": [{"role": "SOUT", "task": "Observer une source publique"}]})
        elif request["max_tokens"] == 500:
            text = json.dumps({"final": "Observation publique acquise"} if searches else {
                "tool": "search", "args": {"query": "demande publique"}})
        else:
            text = json.dumps(determination())
        return llm.TransportResult(text=text, usage=Usage(prompt_tokens=10, completion_tokens=5),
                                   requested_model=request["model"], resolved_model=request["model"],
                                   resolved_provider="OfflineFake", provider_cost_usd=0.0)

    def search(args):
        searches.append(args)
        return {"ok": True, "text": "Une demande publique observée", "url": "https://public.example"}

    transport.handler = respond
    monkeypatch.setitem(runtime.TOOLS["search"], "fn", search)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert supervisor.work_tasks(BUSINESS, oid)[-1]["status"] == "done"
    assert len(searches) == 1
    assert tasks.pending_human_requests(BUSINESS) == []
    assert len(transport.calls) < 15
    assert read_snapshot()["token_cost_usd"] == 0
    assert all(request["model"].endswith(":free") or provider["base_url"].startswith("https://api.deepseek")
               for provider, request in transport.calls)
