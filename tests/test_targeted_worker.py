"""Tests de regression pour le worker cible."""
from __future__ import annotations

import threading
import time

import pytest

from agents import db, deepseek, task_handlers  # noqa: F401
from octopus import builtin_handlers, journal, tasks, worker  # noqa: F401

QUIET = dict(log=lambda s: None)


@pytest.fixture
def handlers(monkeypatch):
    saved = dict(worker.HANDLERS)
    yield worker.HANDLERS
    worker.HANDLERS.clear()
    worker.HANDLERS.update(saved)


# --- parametrisation cible ------------------------------------------------------------------


class TestTargetedClaim:
    def test_selected_low_priority_task(self):
        high1 = tasks.enqueue("b", "k", priority=10)
        high2 = tasks.enqueue("b", "k", priority=5)
        low = tasks.enqueue("b", "k", priority=1)
        result = worker.run_one("w", task_id=low, **QUIET)
        assert result is not None and result["id"] == low
        assert tasks.get(high1)["status"] == "queued"
        assert tasks.get(high2)["status"] == "queued"

    def test_absent_task_returns_none(self):
        result = worker.run_one("w", task_id=999999, **QUIET)
        assert result is None

    def test_wrong_kind_refusal(self, handlers):
        @worker.handler("test.ok")
        def ok(ctx):
            return "done"

        @worker.handler("test.other")
        def other(ctx):
            return "done"

        task_id = tasks.enqueue("b", "test.other")
        result = worker.run_one("w", task_id=task_id, kinds=["test.ok"], **QUIET)
        assert result is None
        assert tasks.get(task_id)["status"] == "queued"

    def test_terminal_cancelled_refusal(self):
        task_id = tasks.enqueue("b", "k")
        assert tasks.cancel(task_id) == "cancelled"
        assert tasks.get(task_id)["status"] == "cancelled"
        assert worker.run_one("w", task_id=task_id, **QUIET) is None
        assert tasks.get(task_id)["status"] == "cancelled"

    def test_terminal_failed_refusal(self):
        task_id = tasks.enqueue("b", "k")
        claimed = tasks.claim("w")
        assert claimed["id"] == task_id
        assert tasks.fail(task_id, "w", "boom") == "failed"
        assert tasks.get(task_id)["status"] == "failed"
        assert worker.run_one("w", task_id=task_id, **QUIET) is None
        assert tasks.get(task_id)["status"] == "failed"

    def test_resource_blocked_refusal(self, handlers):
        @worker.handler("test.blocked", resource="cpu_heavy")
        def blocked(ctx):
            return "done"

        blocked_id = tasks.enqueue("b", "test.blocked", resource="cpu_heavy")
        task_id = tasks.enqueue("b", "test.blocked", resource="cpu_heavy")
        tasks.claim("blocker", lease_s=60, task_id=blocked_id)
        result = worker.run_one("w", task_id=task_id, **QUIET)
        assert result is None
        assert tasks.get(task_id)["status"] == "queued"

    def test_atomic_concurrent_claim(self):
        task_id = tasks.enqueue("b", "k")
        results = []
        barrier = threading.Barrier(4)

        def grab(name):
            barrier.wait()
            results.append(worker.run_one(name, task_id=task_id, **QUIET))

        threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sum(1 for r in results if r) == 1
        assert sum(1 for r in results if r is None) == 3

    def test_human_answer_resumption(self, handlers):
        runs = []

        @worker.handler("test.publish")
        def publish(ctx):
            runs.append(1)
            answer = ctx.ask_human("confirm", "Publier la vidéo ?")
            return {"approved": answer == "oui"}

        task_id = worker.enqueue("podalux", "test.publish")
        first = worker.run_one("w", task_id=task_id, **QUIET)
        assert first["status"] == "waiting_human" and first["attempts"] == 0
        (request,) = tasks.pending_human_requests()
        assert tasks.answer(request["id"], "oui") == task_id
        done = worker.run_one("w", task_id=task_id, **QUIET)
        assert done["status"] == "done" and done["output"] == {"approved": True} and len(runs) == 2


# --- validation et maintenance -------------------------------------------------------------


class TestTargetedValidation:
    def test_invalid_task_ids_reject_before_maintenance(self, monkeypatch):
        def bad_reap(*args, **kwargs):
            raise AssertionError("reap must not be called")

        def bad_materialize(*args, **kwargs):
            raise AssertionError("materialize_due must not be called")

        monkeypatch.setattr(tasks, "reap", bad_reap)
        monkeypatch.setattr(tasks, "materialize_due", bad_materialize)
        for bad in [True, False, 0, -1, 1.5, "abc"]:
            with pytest.raises(ValueError, match="task_id"):
                worker.run_one(task_id=bad, **QUIET)

    def test_queued_not_ready_does_not_run(self):
        task_id = tasks.enqueue("b", "k", delay_s=3600)
        assert tasks.get(task_id)["not_before"] > time.time()
        assert worker.run_one("w", task_id=task_id, **QUIET) is None
        assert tasks.get(task_id)["status"] == "queued"

    def test_queued_not_ready_does_not_fallback_to_ready_task(self):
        delayed = tasks.enqueue("b", "k", delay_s=3600)
        ready = tasks.enqueue("b", "k")
        assert worker.run_one("w", task_id=delayed, **QUIET) is None
        assert tasks.get(delayed)["status"] == "queued"
        assert tasks.get(ready)["status"] == "queued"


# --- CLI -----------------------------------------------------------------------------------


class TestTargetedCLI:
    def test_cli_done(self, handlers, capsys):
        from octopus.__main__ import main

        @worker.handler("test.done")
        def done(ctx):
            return "ok"

        task_id = worker.enqueue("b", "test.done")
        assert main(["worker", "--task", str(task_id)]) == 0
        out = capsys.readouterr().out
        assert '"status": "done"' in out

    def test_cli_failure(self, handlers, capsys):
        from octopus.__main__ import main

        @worker.handler("test.fail")
        def fail(ctx):
            raise RuntimeError("boom")

        task_id = worker.enqueue("b", "test.fail")
        assert main(["worker", "--task", str(task_id)]) == 0
        out = capsys.readouterr().out
        assert '"status": "failed"' in out

    def test_cli_waiting(self, handlers, capsys):
        from octopus.__main__ import main

        @worker.handler("test.wait")
        def wait(ctx):
            ctx.ask_human("confirm", "Continue?")

        task_id = worker.enqueue("b", "test.wait")
        assert main(["worker", "--task", str(task_id)]) == 0
        out = capsys.readouterr().out
        assert '"status": "waiting_human"' in out

    def test_cli_no_claim(self, handlers, capsys):
        from octopus.__main__ import main

        assert main(["worker", "--task", "999999"]) == 0
        out = capsys.readouterr().out
        assert "aucune tâche prête" in out

    def test_cli_invalid(self, capsys):
        from octopus.__main__ import main

        with pytest.raises(SystemExit) as exc:
            main(["worker", "--task", "0"])
        assert exc.value.code == 2

    def test_cli_conflicting_flags(self, capsys):
        from octopus.__main__ import main

        with pytest.raises(SystemExit) as exc:
            main(["worker", "--task", "1", "--max-tasks", "2"])
        assert exc.value.code == 2
