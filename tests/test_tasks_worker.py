"""Etape 4 : file de taches persistee, worker, reprise humaine, planification."""
from __future__ import annotations

import threading
import time

import pytest

from agents import cycle, db, deepseek, task_handlers  # noqa: F401  (enregistre les handlers Podalux)
from octopus import builtin_handlers, journal, llm, tasks, worker  # noqa: F401
from octopus.pricing import Usage

QUIET = dict(log=lambda s: None)


@pytest.fixture
def handlers(monkeypatch):
    saved = dict(worker.HANDLERS)
    yield worker.HANDLERS
    worker.HANDLERS.clear()
    worker.HANDLERS.update(saved)


# --- file -----------------------------------------------------------------------------------

def test_claim_order_priority_and_delay():
    low = tasks.enqueue("b", "k", {"n": 1})
    high = tasks.enqueue("b", "k", {"n": 2}, priority=5)
    later = tasks.enqueue("b", "k", {"n": 3}, priority=9, delay_s=3600)
    assert tasks.claim("w1")["id"] == high
    assert tasks.claim("w1")["id"] == low
    assert tasks.claim("w1") is None and tasks.get(later)["status"] == "queued"


def test_only_one_worker_gets_a_task():
    tasks.enqueue("b", "k")
    results = []
    barrier = threading.Barrier(4)

    def grab(name):
        barrier.wait()
        results.append(tasks.claim(name))

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for r in results if r) == 1


def test_exclusive_resource():
    first = tasks.enqueue("b", "render", resource="cpu_heavy")
    tasks.enqueue("b", "render", resource="cpu_heavy")
    light = tasks.enqueue("b", "llm")
    assert tasks.claim("w1")["id"] == first
    assert tasks.claim("w2")["id"] == light  # la seconde tache lourde attend
    assert tasks.claim("w3") is None


def test_idempotency_key():
    a = tasks.enqueue("podalux", "publish", {"offer": "x"}, idempotency_key="publish:x:sha")
    b = tasks.enqueue("podalux", "publish", {"offer": "x"}, idempotency_key="publish:x:sha")
    assert a == b and len(tasks.list_tasks()) == 1


def test_expired_lease_is_retried_then_failed():
    task_id = tasks.enqueue("b", "k", max_attempts=2)
    tasks.claim("mort", lease_s=-1)
    assert tasks.reap()["requeued"] == [task_id]
    tasks.claim("mort-aussi", lease_s=-1)
    assert tasks.reap()["failed"] == [task_id]
    assert "bail expiré" in tasks.get(task_id)["error"]


def test_lost_lease_prevents_stale_completion():
    task_id = tasks.enqueue("b", "k", max_attempts=2)
    tasks.claim("lent", lease_s=-1)
    tasks.reap()
    tasks.claim("nouveau")
    with pytest.raises(tasks.LeaseLost):
        tasks.complete(task_id, "lent", {"trop": "tard"})
    tasks.complete(task_id, "nouveau", {"ok": True})
    assert tasks.get(task_id)["output"] == {"ok": True}


def test_cancel_queued_and_running():
    queued = tasks.enqueue("b", "k")
    running = tasks.enqueue("b", "k")
    assert tasks.cancel(queued) == "cancelled"
    claimed = tasks.claim("w")
    assert claimed["id"] == running
    assert tasks.cancel(running) == "cancel_requested"
    assert tasks.heartbeat(running, "w") is True
    assert tasks.fail(running, "w", "stop") == "failed"  # pas de nouvelle tentative apres annulation


# --- worker ---------------------------------------------------------------------------------

def test_worker_runs_handler_in_a_budgeted_run(handlers, transport):
    transport.reply('{"a": 1}', prompt_tokens=10, completion_tokens=100)

    @worker.handler("test.llm", budget_usd=0.5)
    def call_model(ctx):
        return {"text": llm.complete("podalux.write_job", [{"role": "user", "content": "x"}],
                                     pin_model="deepseek/flash").text}

    task_id = worker.enqueue("test", "test.llm")
    result = worker.run_one("w", **QUIET)
    assert result["status"] == "done" and result["output"] == {"text": '{"a": 1}'}
    run = journal.query("SELECT * FROM runs WHERE id=?", (result["run_id"],))[0]
    assert run["kind"] == "task:test.llm" and run["budget_usd"] == 0.5 and run["status"] == "done"
    assert journal.subtree_cost(run["id"]) > 0
    assert [e["type"] for e in tasks.events(task_id=task_id)] == ["task.queued", "task.started", "task.done"]


def test_worker_retries_with_delay(handlers):
    attempts = []

    @worker.handler("test.flaky", max_attempts=2, retry_delay_s=0)
    def flaky(ctx):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("Chatterbox indisponible")
        return "ok"

    worker.enqueue("test", "test.flaky")
    assert worker.run_one("w", **QUIET)["status"] == "queued"
    assert worker.run_one("w", **QUIET)["status"] == "done"


def test_unknown_kind_fails_cleanly(handlers):
    tasks.enqueue("test", "inconnu.kind")
    assert worker.run_one("w", kinds=["inconnu.kind"], **QUIET)["status"] == "failed"


def test_human_in_the_loop_resumes_after_answer(handlers):
    runs = []

    @worker.handler("test.publish")
    def publish(ctx):
        runs.append(1)
        answer = ctx.ask_human("confirm", "Publier la vidéo ?")
        return {"approved": answer == "oui"}

    task_id = worker.enqueue("podalux", "test.publish")
    first = worker.run_one("w", **QUIET)
    assert first["status"] == "waiting_human" and first["attempts"] == 0
    assert worker.run_one("w", **QUIET) is None  # rien a faire tant que l'humain n'a pas repondu
    (request,) = tasks.pending_human_requests()
    assert request["question"] == "Publier la vidéo ?"
    assert tasks.answer(request["id"], "oui") == task_id
    done = worker.run_one("w", **QUIET)
    assert done["status"] == "done" and done["output"] == {"approved": True} and len(runs) == 2
    statuses = [r["status"] for r in journal.query("SELECT status FROM runs WHERE kind='task:test.publish' ORDER BY id")]
    assert statuses == ["waiting_human", "done"]


def test_unanswered_request_expires(handlers):
    @worker.handler("test.wait")
    def wait(ctx):
        ctx.ask_human("k", "Question ?", expires_s=-1)

    task_id = worker.enqueue("b", "test.wait")
    worker.run_one("w", **QUIET)
    assert tasks.reap()["expired_requests"]
    assert tasks.get(task_id)["status"] == "failed"


def test_cooperative_cancellation(handlers):
    started = threading.Event()

    @worker.handler("test.long")
    def long_task(ctx):
        started.set()
        for _ in range(100):
            ctx.check_cancel()
            time.sleep(0.05)
        return "fini"

    task_id = worker.enqueue("b", "test.long")
    threading.Timer(0.2, lambda: tasks.cancel(task_id)).start()
    assert worker.run_one("w", **QUIET)["status"] == "cancelled"


def test_schedule_does_not_pile_up(handlers):
    tasks.schedule("octopus", "octopus.cost_report", 3600)
    first = tasks.materialize_due()
    assert len(first) == 1
    assert tasks.materialize_due(now=time.time() + 4000) == []  # l'occurrence precedente n'a pas tourne
    assert worker.run_one("w", **QUIET)["status"] == "done"
    assert len(tasks.materialize_due(now=time.time() + 8000)) == 1
    with pytest.raises(tasks.TaskError, match="60 s"):
        tasks.schedule("b", "k", 5)


# --- handlers Podalux -----------------------------------------------------------------------

def test_podalux_video_cycle_task(handlers, monkeypatch):
    monkeypatch.setattr(cycle, "run_cycle", lambda offer_id=None, max_iterations=3: {
        "offer_id": offer_id, "ledger": {"score": 27, "go": True, "blocking": []},
        "orbit": {"decision": "done"}, "iterations": [{}]})
    worker.enqueue("podalux", "podalux.video_cycle", {"offer_id": "cash_devis_cgv01"})
    result = worker.run_one("w", **QUIET)
    assert result["resource"] == "cpu_heavy" and result["max_attempts"] == 3
    assert result["output"] == {"offer_id": "cash_devis_cgv01", "score": 27, "go": True, "decision": "done",
                                "iterations": 1, "blocking": []}


def test_podalux_task_cancel_reaches_the_cycle(handlers, monkeypatch):
    seen = {}

    def fake_cycle(offer_id=None, max_iterations=3):
        for _ in range(60):
            if db.stop_requested():
                seen["stopped"] = True
                break
            time.sleep(0.1)
        return {"offer_id": offer_id, "ledger": {}, "orbit": {}, "iterations": []}

    monkeypatch.setattr(cycle, "run_cycle", fake_cycle)
    task_id = worker.enqueue("podalux", "podalux.video_cycle", {})
    threading.Timer(0.3, lambda: tasks.cancel(task_id)).start()
    assert worker.run_one("w", **QUIET)["status"] == "cancelled" and seen.get("stopped")


def test_podalux_agent_message_task(handlers, monkeypatch):
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: {"final": "Bonjour !"})
    worker.enqueue("podalux", "podalux.agent_message", {"role": "orbit", "text": "salut"})
    assert worker.run_one("w", **QUIET)["output"] == {"role": "ORBIT", "final": "Bonjour !", "steps": 0}


def test_cli_roundtrip(handlers, capsys):
    from octopus.__main__ import main
    assert main(["enqueue", "octopus", "octopus.cost_report"]) == 0
    assert main(["worker", "--once"]) == 0
    assert main(["tasks"]) == 0
    out = capsys.readouterr().out
    assert "tâche #1 en file" in out and "done" in out
    assert main(["enqueue", "octopus", "inconnu"]) == 2
