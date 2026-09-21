"""Journal des runs et effets sur le code metier (sondes 5 et 6 de l'audit)."""
from __future__ import annotations

import time

import pytest

from agents import agents as ag
from agents import config, db, deepseek, runtime
from octopus import journal


def test_journal_enables_foreign_keys_on_every_connection():
    conn = journal.connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


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
