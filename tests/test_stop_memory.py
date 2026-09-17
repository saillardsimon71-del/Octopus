"""Audit C7 (arret reel), M3 (memoire retrouvable), M6 (SQLite multi-processus)."""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from agents import agents as ag
from agents import cancel, config, cycle, db, deepseek, runtime, tools

PY = sys.executable


# --- arret horodate ------------------------------------------------------------------------

def test_old_stop_does_not_cancel_a_new_run():
    db.request_stop()
    time.sleep(0.01)
    with cancel.scope():
        assert cancel.requested() is False
        db.request_stop()
        assert cancel.requested() is True
    assert cancel.requested() is False  # hors execution arretable


def test_legacy_stop_value_from_old_gui_is_honored_once():
    with cancel.scope():
        db.set_state("stop", "1")  # Podalux.exe non reconstruit
        assert cancel.requested() is True
        assert cancel.requested() is True  # converti en horodatage
    time.sleep(0.01)
    with cancel.scope():
        assert cancel.requested() is False


def test_nested_scope_keeps_outer_start():
    with cancel.scope() as outer:
        time.sleep(0.01)
        with cancel.scope() as inner:
            assert inner == outer


def test_agent_stops_between_steps(monkeypatch):
    calls = []

    def call_json(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            db.request_stop()  # l'humain clique pendant que l'agent travaille
        return {"tool": "recall", "args": {"key": f"k{len(calls)}"}}

    monkeypatch.setattr(deepseek, "call_json", call_json)
    result = runtime.run_agent("SOUT", "veille", max_steps=8)
    assert result["final"] == "(arrêt demandé)" and len(calls) == 2  # sonde 10 : 4 appels apres l'arret


def test_mission_stops_before_delegating(monkeypatch):
    calls = []

    def call_json(agent, task, model, messages, **k):
        calls.append(task)
        db.request_stop()
        return {"tasks": [{"role": "SOUT", "task": "veille"}, {"role": "CONVERT", "task": "offre"}]}

    monkeypatch.setattr(deepseek, "call_json", call_json)
    result = runtime.run_mission("objectif")
    assert calls == ["planification"] and result["rapport"] == "(arrêt demandé)"


def test_subprocess_tree_is_killed_on_stop(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    threading.Timer(1.5, db.request_stop).start()
    started = time.time()
    with cancel.scope():
        with pytest.raises(cancel.Cancelled, match="pendant"):
            tools._run_checked([PY, "-c", script], str(tmp_path), timeout=120)
    assert time.time() - started < 15
    if os.name != "nt":
        grandchild = int(pid_file.read_text())
        time.sleep(0.5)
        with pytest.raises(ProcessLookupError):
            os.kill(grandchild, 0)


def test_ask_human_can_be_cancelled():
    threading.Timer(0.3, db.request_stop).start()
    started = time.time()
    with cancel.scope():
        assert db.ask_human("GROWTH", "publish_confirmation", "Publier ?", timeout_s=30, cancel=cancel.requested) is None
    assert time.time() - started < 5
    assert db._conn().execute("SELECT status FROM handoffs").fetchone()["status"] == "cancelled"


def test_cycle_stop_during_forge(monkeypatch):
    growth_calls = []
    monkeypatch.setattr(ag.CONVERT, "run", staticmethod(lambda offer_id, angle, fixes=None: {"narration": []}))

    def forge(offer_id, job):
        db.request_stop()
        cancel.checkpoint("avant le rendu")

    monkeypatch.setattr(ag.FORGE, "run", staticmethod(forge))
    monkeypatch.setattr(ag.GROWTH, "run", staticmethod(lambda *a: growth_calls.append(1)))
    result = cycle.run_cycle(offer_id="cash_devis_cgv01")
    run = db.current_run()
    assert run["status"] == "stopped" and "avant le rendu" in run["step"]
    assert growth_calls == [] and result["iterations"] == [] and db.run_lock_holder() is None


def test_stop_before_cycle_start_is_not_applied(monkeypatch):
    db.request_stop()
    time.sleep(0.01)
    seen = []
    monkeypatch.setattr(ag.CONVERT, "run", staticmethod(lambda *a, **k: seen.append("convert") or {"narration": []}))
    monkeypatch.setattr(ag.FORGE, "run", staticmethod(lambda *a: (_ for _ in ()).throw(RuntimeError("fin du test"))))
    with pytest.raises(RuntimeError, match="fin du test"):
        cycle.run_cycle(offer_id="cash_devis_cgv01", max_iterations=1)
    assert seen == ["convert"]


# --- memoire -------------------------------------------------------------------------------

def test_recall_ignores_case_accents_and_separators():
    db.remember("sout", "Recouvrement Base", "info")  # sonde 11 : rappel impossible
    assert db.recall("SOUT", "recouvrement_base") == "info"
    assert db.recall("Sout", "recouvrement-base") == "info"
    assert db.recall("SOUT", "recouvrement") is None


def test_legacy_rows_are_found():
    conn = db._conn()
    conn.execute("INSERT INTO memory (ts, agent, key, value) VALUES (?, 'ORBIT', 'objectif_veille', 'ancien')", (time.time(),))
    conn.commit()
    conn.close()
    assert db.recall("orbit", "Objectif veille") == "ancien"


def test_runtime_memory_tools_use_the_running_role(monkeypatch):
    actions = iter([
        {"tool": "remember", "args": {"agent": "ORBIT", "key": "Prix cible", "value": "9 €"}},
        {"tool": "recall", "args": {"key": "cle inventee"}},
        {"final": "ok"},
    ])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))
    result = runtime.run_agent("LEDGER", "note le prix", max_steps=5)
    assert db.recall("LEDGER", "prix_cible") == "9 €" and db.recall("ORBIT", "prix_cible") is None
    assert "cles_connues" in result["steps"][1]["result"] and "prix_cible" in result["steps"][1]["result"]


# --- SQLite --------------------------------------------------------------------------------

def test_podalux_db_uses_wal_and_busy_timeout():
    conn = db._conn()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    conn.close()
