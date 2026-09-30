"""Phase G — critère H : l'état du runtime est observable sans lire SQLite à la main.

Chaque fait exigé par le contrat doit être lisible dans `octopus.status` : objectif courant,
tâche/mission courante, run et sous-runs, dernière progression utile, raison d'attente humaine,
raison d'échec/timeout, travail suivant planifié, routage/repli LLM et coût cumulé.
"""
from __future__ import annotations

import json
import time

import pytest

from octopus import journal, status, strategy, supervisor, tasks, worker

BUSINESS = "octopus"


@pytest.fixture
def handlers():
    """Le registre des handlers est global : il est restauré après chaque test."""
    saved = dict(worker.HANDLERS)
    yield worker.HANDLERS
    worker.HANDLERS.clear()
    worker.HANDLERS.update(saved)


def _llm_call(**overrides) -> int:
    row = {"ts": time.time(), "run_id": None, "root_run_id": None, "business": BUSINESS, "agent": "ORBIT",
           "task": "agent.plan", "profile": "zero_cost", "model": "kilo/auto-free", "provider": "kilo",
           "cost_class": "free", "attempt": 1, "status": "ok", "error": None, "prompt_tokens": 10,
           "completion_tokens": 5, "cost_usd": 0.0, "peak": 0, "duration_ms": 100}
    row.update(overrides)
    return journal.record_llm_call(row)


@pytest.fixture
def populated():
    """Un runtime dans un état riche : travail en cours, attente humaine, échec, suite planifiée."""
    objective_id = strategy.create("objective", BUSINESS, "Premier client", created_by="human",
                                   statement="Trouver une demande ouverte",
                                   success_criteria="usable_browse_count>=2")
    strategy.transition("objective", objective_id, BUSINESS, "active", actor="human")

    running = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {"objective_id": objective_id})
    waiting = tasks.enqueue(BUSINESS, supervisor.TICK_KIND, {})
    failed = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {"objective_id": objective_id})
    queued = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {"objective_id": objective_id}, delay_s=900)
    now = time.time()
    conn = journal.connect()
    try:
        conn.execute("INSERT INTO runs (root_id, business, kind, label, status, started_at, pid, budget_usd) "
                     "VALUES (1, ?, 'task:supervisor.objective_work', 'mission', 'running', ?, 4242, 0.05)",
                     (BUSINESS, now - 12))
        conn.execute("UPDATE runs SET root_id=id WHERE id=1")
        conn.execute("INSERT INTO runs (parent_id, root_id, business, kind, label, status, started_at, pid) "
                     "VALUES (1, 1, ?, 'agent:react', 'sous-run', 'running', ?, 4242)", (BUSINESS, now - 6))
        conn.execute("UPDATE tasks SET status='running', lease_owner='w1', lease_until=?, run_id=1, attempts=1 "
                     "WHERE id=?", (now + 60, running))
        conn.execute("UPDATE tasks SET status='waiting_human' WHERE id=?", (waiting,))
        conn.execute("INSERT INTO human_requests (ts, business, task_id, key, question, expires_at) "
                     "VALUES (?, ?, ?, 'objective#1', 'Ressource LLM à fournir pour continuer', ?)",
                     (now, BUSINESS, waiting, now + 3600))
        conn.execute("UPDATE tasks SET status='failed', error='RuntimeError: route LLM indisponible\ntrace', "
                     "finished_at=? WHERE id=?", (now - 5, failed))
        conn.execute("INSERT INTO runs (root_id, business, kind, label, status, started_at, finished_at, error) "
                     "VALUES (3, ?, 'task:supervisor.objective_work', 'tâche', 'abandoned', ?, ?, "
                     "'exécution interrompue : bail expiré')", (BUSINESS, now - 40, now - 5))
        conn.commit()
    finally:
        conn.close()
    tasks.emit(BUSINESS, running, "supervisor.work.progress", {"acquired": 3})
    _llm_call(status="request_too_large", error="HTTP 413 Request too large", model="groq/openai/gpt-oss-120b",
              provider="omniroute", ts=now - 30, run_id=1, root_run_id=1)
    _llm_call(status="ok", model="kilo/auto-free", provider="kilo", ts=now - 20, run_id=2, root_run_id=1,
              cost_usd=0.0)
    return {"objective_id": objective_id, "running": running, "waiting": waiting, "failed": failed,
            "queued": queued}


def test_status_snapshot_exposes_every_required_fact(populated):
    state = status.snapshot(BUSINESS)

    assert [o["id"] for o in state["objectives"]] == [populated["objective_id"]]
    assert state["objectives"][0]["success_criteria"] == "usable_browse_count>=2"

    by_id = {row["id"]: row for row in state["running_tasks"]}
    task = by_id[populated["running"]]
    assert task["kind"] == supervisor.WORK_KIND and task["status"] == "running"
    assert task["lease_owner"] == "w1" and task["lease_remaining_s"] > 0
    assert by_id[populated["waiting"]]["status"] == "waiting_human"

    assert state["current_run_id"] == 1
    kinds = {run["id"]: run["kind"] for run in state["running_runs"]}
    assert kinds == {1: "task:supervisor.objective_work", 2: "agent:react"}
    assert state["running_runs"][0]["age_s"] > 0

    assert "supervisor.work.progress" in [row["type"] for row in state["last_progress"]]

    (request,) = state["waiting_human"]
    assert request["task_id"] == populated["waiting"] and "Ressource LLM" in request["question"]
    assert request["expires_in_s"] > 0

    failure = state["failures"][0]
    assert failure["source"] == "task" and failure["id"] == populated["failed"]
    assert failure["error"] == "RuntimeError: route LLM indisponible"
    assert any(row["source"] == "run" and row["status"] == "abandoned" for row in state["failures"])

    assert populated["queued"] in [row["id"] for row in state["next_work"] if row["source"] == "queue"]
    assert state["next_work"][0]["ready_in_s"] >= 0

    routing = state["llm_routing"]
    assert routing["by_status"] == {"ok": 1, "request_too_large": 1}
    assert routing["deterministic_refusals"] == 1
    assert routing["fallbacks_observed"] == 1
    assert routing["recent"][0]["model"] == "kilo/auto-free"

    assert state["cost"]["today_usd"] == 0.0
    assert state["cost"]["running_runs_usd"] == 0.0
    assert state["task_status_counts"] == {"queued": 1, "running": 1, "waiting_human": 1, "failed": 1}


def test_status_render_is_readable_without_sqlite(populated, capsys):
    from octopus.__main__ import main

    assert main(["status", "--business", BUSINESS]) == 0
    out = capsys.readouterr().out
    for expected in ("Objectifs :", "Trouver une demande ouverte", "usable_browse_count>=2", "Tâches en cours :", "supervisor.objective_work",
                     "Runs actifs :", "Dernière progression :", "Attente humaine :",
                     "Ressource LLM à fournir pour continuer", "Échecs / timeouts :",
                     "RuntimeError: route LLM indisponible", "Travail suivant :", "Routage LLM :",
                     "refus déterministes (413) : 1", "Coût cumulé :"):
        assert expected in out, expected


def test_status_cli_json_is_machine_readable(populated, capsys):
    from octopus.__main__ import main

    assert main(["status", "--json"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["business"] is None
    assert {"objectives", "running_tasks", "running_runs", "last_progress", "waiting_human", "failures",
            "next_work", "llm_routing", "cost", "task_status_counts"} <= set(state)


def test_status_reports_idle_runtime_without_error(capsys):
    from octopus.__main__ import main

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "aucun objectif actif ou suspendu" in out
    assert "aucune (worker au repos)" in out
    assert "rien de planifié" in out


def test_runtime_cli_bootstraps_supervisor_then_rejects_invalid_bounds(handlers, monkeypatch, capsys):
    from octopus.__main__ import main

    started = {}
    monkeypatch.setattr(worker, "load_handlers", lambda modules=None: worker.HANDLERS)
    for kind in (supervisor.TICK_KIND, supervisor.WORK_KIND):
        worker.HANDLERS[kind] = worker.Handler(kind, lambda ctx: None)
    monkeypatch.setattr(worker, "loop", lambda **kwargs: started.setdefault("loop", kwargs) or 0)
    monkeypatch.setattr(supervisor, "bootstrap",
                        lambda business, **kwargs: started.setdefault("bootstrap", (business, kwargs)) and 7)

    assert main(["runtime", "--business", "atelier", "--tick-every", "60"]) == 0
    assert started["bootstrap"] == ("atelier", {"tick_every_s": 60.0})
    assert started["loop"]["poll_s"] == 2.0
    assert "tâche #7" in capsys.readouterr().out

    for options in (["--tick-every", "0"], ["--poll", "-1"], ["--max-tasks", "0"]):
        assert main(["runtime", *options]) == 2
    assert "strictement positif" in capsys.readouterr().out


def test_runtime_cli_fails_closed_without_supervisor_handlers(handlers, monkeypatch, capsys):
    from octopus.__main__ import main

    monkeypatch.setattr(worker, "load_handlers", lambda modules=None: worker.HANDLERS)
    monkeypatch.setattr(worker, "loop", lambda **kwargs: pytest.fail("le runtime ne doit pas démarrer"))
    handlers.clear()
    assert main(["runtime"]) == 2
    assert "handlers manquants" in capsys.readouterr().out
