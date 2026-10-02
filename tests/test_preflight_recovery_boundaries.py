"""Regression only: browser-start failure, post-decision crash and waiting UI."""
import copy
import queue
import time
from types import SimpleNamespace

import pytest
from agents import browser, runtime
from octopus import businesses, journal, strategy, supervisor, tasks, worker
from agents.gui.workbench_v2_data import read_snapshot
from test_pursuit_real_discovery import Script, no_effects


@pytest.mark.parametrize("guard_refused", [False, True])
def test_browser_initialization_failure_preserves_acquisition_and_guard_origin(monkeypatch, guard_refused):
    url = "https://public.example/"
    http = browser._page_record(
        requested_url=url, final_url=url, status=None if guard_refused else 403,
        content_type="text/html", title="", method="http:html_body", rendered=False,
        blocked=True, text="", raw_chars=0,
        error="navigation refusée par le garde-fou" if guard_refused else "remote anti-bot",
    )
    monkeypatch.setattr(browser, "fetch_public_http", lambda *a, **k: http)
    def closed(**kwargs):
        raise RuntimeError("TargetClosedError: browser was closed before creation")
    monkeypatch.setattr(browser, "new_browser", closed)
    page = browser.acquire_public_page(url, guard=lambda _: not guard_refused).as_dict()
    assert page["requested_url"] == url and page["final_url"] == url
    assert page["rendered"] is False and not browser.is_public_text_acquisition(page)
    failure = runtime._public_source_failure({"page": page})
    if guard_refused:
        assert page["error"] == "navigation refusée par le garde-fou" and failure is None
    else:
        assert "TargetClosedError" in page["error"] and failure.startswith("source Web inaccessible :")


class InterruptedAfterDecision(BaseException):
    pass


@pytest.mark.parametrize("continue_after", [False, True])
def test_explicit_resume_after_decision_reuses_same_task_memo_cost_and_decision(
        monkeypatch, transport, providers_up, continue_after):
    activity = businesses.create_activity("Produits digitaux & automatisation IA B2B",
                                         "Produits numériques et automatisations IA B2B ; moyens et stratégie libres.")
    script = Script(monkeypatch, transport, cycle=continue_after)
    emit = worker.TaskContext.emit
    interrupted = False
    def crash(ctx, name, payload):
        nonlocal interrupted
        emit(ctx, name, payload)
        if name == "pursuit.decision" and not interrupted:
            interrupted = True
            raise InterruptedAfterDecision()
    monkeypatch.setattr(worker.TaskContext, "emit", crash)
    oid = supervisor.start_pursuit(business=activity.id)
    first = supervisor.work_tasks(activity.id, oid)[0]
    with pytest.raises(InterruptedAfterDecision):
        supervisor.run_pursuit(oid, business=activity.id)
    tid = first["id"]
    memo = copy.deepcopy(tasks.step_value(tid, "determination"))
    decision_id = tasks.step_value(tid, "pursuit.decision")
    calls, sources = len(script.calls), list(script.acquisitions)
    original_root = journal.root_run_id(tasks.get(tid)["run_id"])
    cost = read_snapshot(activity.id)["token_cost_usd"]
    tasks.reap(now=first["created_at"] + 100000)
    assert tasks.get(tid)["status"] == "failed"
    businesses._cache.clear()
    assert supervisor.start_pursuit(objective_id=oid, business=activity.id) == oid
    assert tasks.get(tid)["status"] == "queued"
    worker.run_one(task_id=tid, log=lambda _: None)
    final = tasks.get(tid)
    assert final["status"] == "done" and final["max_attempts"] == 1
    assert final["output"]["decision_id"] == decision_id
    assert tasks.step_value(tid, "determination") == memo
    assert journal.root_run_id(final["run_id"]) == original_root
    assert len(script.calls) == calls and script.acquisitions == sources
    assert read_snapshot(activity.id)["token_cost_usd"] == cost
    assert len(journal.query("SELECT id FROM strategy_decisions WHERE business=?", (activity.id,))) == 1
    if continue_after:
        following = supervisor.work_tasks(activity.id, oid)[-1]
        assert following["id"] != tid and following["status"] == "queued"
        assert following["input"]["round"] == 2
        assert following["budget_usd"] == pytest.approx(.20 - cost)
    else:
        assert strategy.get("objective", oid, activity.id)["status"] == "paused"
        assert len(supervisor.work_tasks(activity.id, oid)) == 1
    no_effects()
    assert not tasks.pending_human_requests(activity.id)


def test_start_ui_explains_real_waiting_without_spawning_or_granting(monkeypatch):
    from agents import procs
    from agents.gui import workbench_v2 as ui
    activity = businesses.create_activity("Produits digitaux & automatisation IA B2B", "Terrain libre B2B")
    oid = supervisor.start_pursuit(business=activity.id)
    work = supervisor.work_tasks(activity.id, oid)[0]
    tasks.claim("offline", task_id=work["id"])
    rid = tasks.request_human(work["id"], "offline", "pursuit.permission", "Autorisation réelle manquante")
    deferred, messages = [], []
    class Thread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): deferred.append(self.target)
    monkeypatch.setattr(ui.threading, "Thread", Thread)
    monkeypatch.setattr(procs, "spawn", lambda *a, **k: pytest.fail("no queued work: process must not spawn"))
    app = SimpleNamespace(_readonly=False, _creating=False, selected_business_id=activity.id,
                          _pursuit_processes=[], _background_results=queue.SimpleQueue(),
                          _snapshot_busy=False, _snapshot_at=time.monotonic(), current_page="Missions",
                          _worker_label=lambda: "idle", side_worker=SimpleNamespace(configure=lambda **_: None),
                          _set_status=lambda message, *_: messages.append(message), after=lambda *_: None)
    app._refresh = lambda: ui.WorkbenchV2._refresh(app)
    ui.WorkbenchV2._start_pursuit(app)
    deferred[0]()
    ui.WorkbenchV2._refresh(app)
    assert not app._creating and not app._pursuit_processes
    assert messages and "réponse" in messages[0] and "Humain" in messages[0]
    assert tasks.pending_human_requests(activity.id)[0]["id"] == rid
    assert tasks.get(work["id"])["status"] == "waiting_human"
    assert not journal.query("SELECT id FROM channel_actions")
    assert not journal.query("SELECT id FROM spend_requests")
