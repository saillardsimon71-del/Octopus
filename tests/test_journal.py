"""Journal des runs et effets sur le code metier (sondes 5 et 6 de l'audit)."""
from __future__ import annotations

import time
import hashlib
import sqlite3
import os
import subprocess
import sys

import pytest

from agents import agents as ag
from agents import config, db, deepseek, runtime
from octopus import journal


@pytest.mark.parametrize("version", [0, 8])
def test_migrations_converge_across_processes(version):
    path = journal.paths.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if version:
        with sqlite3.connect(path) as conn:
            for target, script in journal._MIGRATIONS:
                if target <= version:
                    conn.executescript(script)
            conn.execute(f"PRAGMA user_version={version}")
    command = [sys.executable, "-c", "from octopus import journal; "
               "c=journal.connect(); "
               "assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; "
               "assert c.execute('PRAGMA user_version').fetchone()[0]==journal.SCHEMA_VERSION; "
               "c.close()"]
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=os.environ.copy()) for _ in range(8)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    conn = journal.connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_calls)")}
        assert all(name in columns for name, _ in journal._SCHEMA_V9_COLUMNS)
    finally:
        conn.close()


def test_failed_migration_rolls_back_schema_and_version(monkeypatch):
    monkeypatch.setattr(journal, "_MIGRATIONS", ((1, "CREATE TABLE example(id INTEGER);\n"),
                                               (2, "INVALID SQL;\n")))
    with pytest.raises(sqlite3.OperationalError):
        journal.connect()
    with sqlite3.connect(journal.paths.journal_path()) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='example'").fetchone() is None


def test_journal_enables_foreign_keys_on_every_connection():
    conn = journal.connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("wal", [False, True])
def test_readonly_snapshot_includes_wal_without_touching_source(tmp_path, wal):
    source = tmp_path / "source.db"
    writer = sqlite3.connect(source)
    if wal:
        writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE facts(value TEXT)")
    writer.execute("INSERT INTO facts VALUES('observation conservée')")
    writer.commit()
    if not wal:
        writer.close()
    def hashes():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("source.db*")}
    before = hashes()
    view = journal.readonly_connection(source)
    try:
        assert view.execute("SELECT value FROM facts").fetchone()[0] == "observation conservée"
        with pytest.raises(sqlite3.OperationalError):
            view.execute("DELETE FROM facts")
    finally:
        view.close()
    assert hashes() == before
    writer.close()


def runs() -> list[dict]:
    return [dict(r) for r in journal.query("SELECT * FROM runs ORDER BY id")]


def test_nested_runs_share_root_and_close_with_status():
    with journal.run("podalux", "mission", label="m", budget_usd=1.0) as mission:
        with journal.run("podalux", "agent", budget_usd=0.2) as agent:
            assert agent.root_id == mission.id
            assert agent.budgets == ((mission.id, 1.0), (agent.id, 0.2))
        with pytest.raises(ValueError):
            with journal.run("podalux", "agent"):
                raise ValueError("boum")
    rows = runs()
    assert [(r["kind"], r["status"], r["parent_id"], r["root_id"]) for r in rows] == [
        ("mission", "done", None, 1), ("agent", "done", 1, 1), ("agent", "error", 1, 1)]
    assert rows[2]["error"] == "ValueError: boum"
    assert all(r["finished_at"] for r in rows)


def test_interrupted_run_is_marked():
    with pytest.raises(KeyboardInterrupt):
        with journal.run("podalux", "video_cycle"):
            raise KeyboardInterrupt
    assert runs()[0]["status"] == "interrupted"


def test_profile_is_inherited_by_child_runs():
    with journal.run("octopus", "bench", profile="bench"):
        with journal.run("octopus", "sub") as child:
            assert child.profile == "bench"


def test_with_run_labels_with_text_arguments():
    @journal.with_run("podalux", "agent", budget_usd=0.5)
    def work(role, goal, max_steps=3):
        return journal.current_run()

    ctx = work("SOUT", "veille impayes")
    row = runs()[0]
    assert row["label"] == "SOUT : veille impayes" and row["budget_usd"] == 0.5 and ctx.id == row["id"]
    assert journal.current_run() is None


def test_unknown_columns_are_rejected():
    with pytest.raises(ValueError, match="colonnes inconnues"):
        journal.record_llm_call({"ts": time.time(), "secret": "x"})


def test_spent_today_by_business():
    base = {"task": "t", "profile": "legacy", "model": "deepseek/flash", "provider": "deepseek",
            "cost_class": "paid", "status": "ok"}
    journal.record_llm_call({**base, "ts": time.time(), "business": "podalux", "cost_usd": 0.01})
    journal.record_llm_call({**base, "ts": time.time(), "business": "autre", "cost_usd": 0.02})
    journal.record_llm_call({**base, "ts": time.time() - 3 * 86400, "business": "podalux", "cost_usd": 5})
    assert journal.spent_today("podalux") == pytest.approx(0.01)
    assert journal.spent_today() == pytest.approx(0.03)


# --- code metier ---------------------------------------------------------------------------

GROWTH_OK = {"total_calcule": 30, "humanite": 4, "warm_pass": True}
MEDIA_OK = {"duration_s": 25.2, "resolution": "1080x1920", "lufs_integrated": -14.0, "freezes_gt1_2s": 0}


def test_ledger_uses_the_cycle_cost_not_the_lifetime_cost():
    db.log_cost("HIST", "ancien", config.MODEL_PRO, 0, 1_000_000)  # 2,19 $ historiques
    assert ag.LEDGER.run("o", MEDIA_OK, GROWTH_OK)["go"] is True  # sonde 5 : etait False
    with journal.run("podalux", "video_cycle", budget_usd=1.0):
        assert ag.LEDGER.run("o", MEDIA_OK, GROWTH_OK)["cost_usd"] == 0


def test_ledger_no_go_when_the_cycle_itself_overspends():
    base = {"task": "t", "profile": "legacy", "model": "deepseek/flash", "provider": "deepseek",
            "cost_class": "paid", "status": "ok"}
    with journal.run("podalux", "video_cycle", budget_usd=1.0) as ctx:
        journal.record_llm_call({**base, "ts": time.time(), "run_id": ctx.id, "cost_usd": 1.5})
        assert ag.LEDGER.run("o", MEDIA_OK, GROWTH_OK)["go"] is False


def test_agent_not_blocked_by_historical_spending(monkeypatch):
    db.log_cost("HIST", "ancien", config.MODEL_PRO, 0, 1_000_000)
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: {"final": "fini"})
    assert runtime.run_agent("SOUT", "veille")["final"] == "fini"  # sonde 6 : etait "(budget depasse)"
    assert runs()[0]["kind"] == "agent" and runs()[0]["status"] == "done"


def test_agent_stops_when_its_run_budget_is_spent(monkeypatch):
    base = {"task": "t", "profile": "legacy", "model": "deepseek/flash", "provider": "deepseek",
            "cost_class": "paid", "status": "ok"}
    calls = []

    def call_json(*a, **k):
        ctx = journal.current_run()
        journal.record_llm_call({**base, "ts": time.time(), "run_id": ctx.id, "cost_usd": 0.6})
        calls.append(1)
        return {"tool": "recall", "args": {"agent": "SOUT", "key": f"k{len(calls)}"}}

    monkeypatch.setattr(deepseek, "call_json", call_json)
    result = runtime.run_agent("SOUT", "veille", max_steps=5)
    assert result["final"] == "(budget dépassé)" and len(calls) == 2



def test_zero_budget_allows_free_agent_steps(monkeypatch):
    calls = []
    def call(*a, **k):
        calls.append(1)
        return {"final": "done"}
    monkeypatch.setattr(deepseek, "call_json", call)
    with journal.run("atelier", "task", budget_usd=0, profile="zero_cost") as ctx:
        result = runtime.run_agent("SOUT", "collect")
        assert not journal.budget_exhausted(ctx)
        assert journal.subtree_cost(ctx.id) == 0
    assert result["execution_status"] == "completed"
    assert len(calls) == 1
