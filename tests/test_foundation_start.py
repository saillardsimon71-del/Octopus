from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agents import agent_browser, deepseek, runtime
from agents.gui.workbench_v2_data import mission_state, read_snapshot
from octopus import browser_workspace, journal, llm, strategy, supervisor, tasks, worker


def result(action="pause", *, reason="Aucune expérience actuellement justifiée", next_goal="", permission=""):
    return {"rapport": "Hypothèse à vérifier, aucun revenu observé.", "plan": [], "results": [],
            "execution_status": "completed", "synthesis_status": "validated",
            "determination": {"action": action, "reason": reason, "next_goal": next_goal, "permission": permission}}


def test_empty_start_persists_reason_and_never_creates_business_or_video(monkeypatch):
    calls = []
    def run(goal, **kwargs):
        calls.append((goal, kwargs))
        return result()
    monkeypatch.setattr(runtime, "run_mission", run)
    oid = supervisor.start_pursuit()
    assert supervisor.start_pursuit() == oid
    supervisor.run_pursuit(oid)
    assert len(calls) == 1
    assert "# OCTOPUS" in calls[0][0] and "0 EUR" in calls[0][0]
    assert calls[0][1]["profile"] == "economical"
    assert calls[0][1]["allowed_tools"] == supervisor.PURSUIT_TOOLS
    state = read_snapshot()
    assert all(t["kind"] == supervisor.WORK_KIND and t["budget_usd"] == 0.20 for t in state["tasks"])
    assert state["allowances"] == [] and state["ledger"] == []
    assert state["generations"] == []
    assert state["objectives"][0]["created_by"] == "octopus"
    assert state["objectives"][0]["status"] == "paused"
    assert state["decisions"][0]["rationale"] == "Aucune expérience actuellement justifiée"
    assert state["tasks"][0]["result"]["economic_result"] is None


def test_first_start_llm_cap_is_configurable_without_economic_allowance(monkeypatch):
    monkeypatch.setenv("OCTOPUS_PURSUIT_LLM_BUDGET_USD", "0.05")
    objective_id = supervisor.start_pursuit()
    task = supervisor.work_tasks("octopus", objective_id)[0]
    assert task["budget_usd"] == 0.05
    assert task["input"]["profile"] == "economical"
    assert not journal.query("SELECT id FROM spend_allowances")
    assert not journal.query("SELECT id FROM spend_requests")


def test_first_start_rejects_invalid_llm_cap(monkeypatch):
    monkeypatch.setenv("OCTOPUS_PURSUIT_LLM_BUDGET_USD", "nan")
    with pytest.raises(supervisor.SupervisorError, match="positif et fini"):
        supervisor.start_pursuit()


def test_pursuit_cycles_share_one_llm_cap():
    oid = supervisor.start_pursuit()
    first = supervisor.work_tasks("octopus", oid)[0]
    with journal.run("octopus", "task:supervisor.objective_work", budget_usd=0.20) as run:
        journal.record_llm_call({"ts": 1, "run_id": run.id, "root_run_id": run.root_id,
                                 "business": "octopus", "task": "agent.plan", "profile": "economical",
                                 "model": "deepseek/v4-pro", "provider": "deepseek", "cost_class": "paid",
                                 "status": "ok", "cost_usd": 0.07})
    with journal.connect() as connection:
        connection.execute("UPDATE tasks SET run_id=? WHERE id=?", (run.id, first["id"]))
    second_id = supervisor._queue_pursuit(oid, round_no=2, previous_id=first["id"], next_goal="Suite")
    second = tasks.get(second_id)
    assert second["budget_usd"] == pytest.approx(0.13)
    with journal.run("octopus", "task:supervisor.objective_work", budget_usd=second["budget_usd"]) as run2:
        journal.record_llm_call({"ts": 2, "run_id": run2.id, "root_run_id": run2.root_id,
                                 "business": "octopus", "task": "agent.react_step", "profile": "economical",
                                 "model": "deepseek/flash", "provider": "deepseek", "cost_class": "paid",
                                 "status": "ok", "cost_usd": 0.05})
    with journal.connect() as connection:
        connection.execute("UPDATE tasks SET run_id=? WHERE id=?", (run2.id, second_id))
    third_id = supervisor._queue_pursuit(oid, round_no=3, previous_id=second_id, next_goal="Suite")
    assert tasks.get(third_id)["budget_usd"] == pytest.approx(0.08)
    snapshot = read_snapshot("octopus")
    assert snapshot["pursuit_llm"] == {"spent_usd": 0.12, "budget_usd": 0.20,
                                       "remaining_usd": 0.08}
    assert snapshot["allowances"] == []


def test_free_mission_and_bounded_reevaluation_keep_previous_observations(monkeypatch):
    goals = []
    def run(goal, **kwargs):
        goals.append(goal)
        return result("continue", reason="Comparer les observations", next_goal="Vérifier une autre source")
    monkeypatch.setattr(runtime, "run_mission", run)
    oid = supervisor.start_pursuit("Étudie une possibilité économique sans utiliser de génération vidéo.")
    supervisor.run_pursuit(oid)
    assert len(goals) == 3
    assert "Hypothèse à vérifier" in goals[1]
    assert "Vérifier une autre source" in goals[1]
    assert strategy.get("objective", oid, "octopus")["created_by"] == "human"
    assert strategy.get("objective", oid, "octopus")["status"] == "paused"
    assert len(strategy.list_items("decision", "octopus")) == 3
    assert not supervisor.active_objectives("octopus")  # legacy tick cannot restart this bounded work
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert len(goals) == 6


def test_missing_llm_model_is_an_explicit_obstacle(monkeypatch):
    def unavailable(*args, **kwargs):
        raise llm.GatewayError("Routes gratuites et DeepSeek indisponibles")
    monkeypatch.setattr(deepseek, "call_json", unavailable)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    work = supervisor.work_tasks("octopus", oid)[0]
    assert work["status"] == "done_degraded"
    assert work["output"]["execution_status"] == "llm_unavailable"
    assert "DeepSeek" in work["output"]["reason"]
    assert work["output"]["economic_result"] is None


def test_permission_wait_does_not_replay_mission_or_grant_rights(monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return result("request_permission", permission="Envoyer une proposition commerciale nécessite une autorisation.")
    monkeypatch.setattr(runtime, "run_mission", run)
    oid = supervisor.start_pursuit("Prépare une proposition")
    supervisor.run_pursuit(oid)
    request = tasks.pending_human_requests("octopus")[0]
    assert "proposition" in request["question"]
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert len(calls) == 1
    tasks.answer(request["id"], "Revoir la stratégie sans envoi")
    supervisor.run_pursuit(oid)
    assert len(calls) == 1
    assert strategy.get("objective", oid, "octopus")["status"] == "paused"
    assert not journal.query("SELECT id FROM channel_actions")


def test_pause_during_work_then_resume_cannot_execute_external_effect(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def run(*args, **kwargs):
        assert "agnes_generate_video" not in kwargs["allowed_tools"]
        assert "browser_click" not in kwargs["allowed_tools"]
        entered.set()
        assert release.wait(8)
        return result("continue", next_goal="Autre observation")
    monkeypatch.setattr(runtime, "run_mission", run)
    oid = supervisor.start_pursuit()
    thread = threading.Thread(target=supervisor.run_pursuit, args=(oid,))
    thread.start()
    try:
        assert entered.wait(8)
        supervisor.pause_pursuit(oid)
    finally:
        release.set()
        thread.join(8)
    assert not thread.is_alive()
    assert supervisor.work_tasks("octopus", oid)[0]["status"] == "cancelled"
    assert not journal.query("SELECT id FROM channel_actions")
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: result())
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert len(supervisor.work_tasks("octopus", oid)) == 2
    assert not journal.query("SELECT id FROM channel_actions")


def test_generic_done_is_not_mp4_or_economic_success():
    objective = {"status": "active", "success_criteria": "bounded_determination", "generations": [],
                 "work_tasks": [{"status": "done"}]}
    label, detail = mission_state(objective)
    assert label == "Travail terminé"
    assert "MP4" not in detail and "Aucun résultat économique" in detail


def test_runtime_refuses_effect_even_when_model_asks_and_exposes_request(monkeypatch):
    selections = []
    def model(agent, stage, model, messages, **kwargs):
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": "Examiner la possibilité"}]}
        if stage == "action":
            if not selections:
                selections.append("click")
                return {"tool": "browser_click", "args": {"ref": "@e1"}}
            return {"final": "Action refusée"}
        return result()
    monkeypatch.setattr(deepseek, "call_json", model)
    monkeypatch.setattr(browser_workspace, "call", lambda *a, **k: pytest.fail("Effet interdit exécuté"))
    oid = supervisor.start_pursuit("Préparer une proposition")
    supervisor.run_pursuit(oid)
    assert "browser_click" in tasks.pending_human_requests("octopus")[0]["question"]
    assert not journal.query("SELECT id FROM channel_actions")


def test_legacy_bypass_cannot_spend_in_first_start(monkeypatch):
    monkeypatch.setenv("OCTOPUS", "off")
    monkeypatch.setenv("OCTOPUS_ALLOW_LEGACY_DIRECT", "1")
    monkeypatch.setattr(deepseek, "_client", lambda: pytest.fail("Client payant interdit"))
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert supervisor.work_tasks("octopus", oid)[0]["output"]["execution_status"] == "gateway_disabled"


def test_first_start_never_opens_an_existing_account_profile(monkeypatch):
    from agents import browser, web_guard
    oid = supervisor.start_pursuit()
    task = supervisor.work_tasks("octopus", oid)[0]
    space = browser_workspace.Workspace(
        browser_workspace.Scope(f"t{task['id']}", "octopus", task["id"]), web_guard.BrowseState())
    monkeypatch.setattr(space, "_start", lambda: None)
    monkeypatch.setattr(space, "_open", lambda url: None)
    monkeypatch.setattr(space, "_observe", lambda: {"ok": True})
    monkeypatch.setattr(web_guard, "check", lambda url, state: web_guard.ACCOUNT)
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url: pytest.fail("Profil connecté consulté"))
    assert space.navigate("https://public.example/page")["ok"]
    assert not space.account_mode
    assert space.anonymous_domains == {"public.example"}


def test_read_only_start_pause_and_execution_leave_journal_unchanged(monkeypatch):
    oid = supervisor.start_pursuit()
    before = [dict(r) for r in journal.query("SELECT * FROM events")]
    monkeypatch.setenv("OCTOPUS_WORKBENCH_READONLY", "1")
    for operation in (lambda: supervisor.start_pursuit(), lambda: supervisor.pause_pursuit(oid),
                      lambda: supervisor.run_pursuit(oid)):
        with pytest.raises(PermissionError):
            operation()
    read_snapshot()
    assert [dict(r) for r in journal.query("SELECT * FROM events")] == before


def test_real_hermes_observation_is_visible_in_workbench(monkeypatch):
    if not agent_browser.availability()["ready"]:
        pytest.skip("Backend Hermes/Chromium absent")
    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><title>Laboratoire OCTOPUS</title><h1>Demande locale de traduction</h1></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv(browser_workspace.LAB_ORIGINS_ENV, origin)
    monkeypatch.setenv("OCTOPUS_BROWSER_HEADLESS", "1")
    observed = []
    def model(agent, stage, model, messages, **kwargs):
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": "Observer la demande sur " + origin}]}
        if stage == "action":
            if not observed:
                observed.append("navigate")
                return {"tool": "browser_navigate", "args": {"url": origin}}
            assert "Demande locale de traduction" in json.dumps(messages, ensure_ascii=False)
            return {"final": "Demande locale observée dans le laboratoire, pas un revenu."}
        if stage == "synthese":
            assert "Demande locale de traduction" in json.dumps(messages, ensure_ascii=False)
            return result()
        raise AssertionError(stage)
    monkeypatch.setattr(deepseek, "call_json", model)
    try:
        oid = supervisor.start_pursuit("Observer une possibilité sur " + origin)
        supervisor.run_pursuit(oid)
        work = supervisor.work_tasks("octopus", oid)[0]
        assert work["status"] == "done", work
        snapshot = read_snapshot()
        observation = snapshot["browser"][0]
        assert observation["url"].startswith(origin)
        assert "Demande locale de traduction" in observation["snapshot"]
        assert observation["session"] == "closed" and observation["action"] == "navigate"
        assert snapshot["tasks"][0]["result"]["economic_result"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
