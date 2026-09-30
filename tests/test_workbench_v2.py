from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from agents.gui import agnes_missions
from agents import db
from agents.gui.workbench_v2_data import mission_state, read_snapshot
from octopus import agnes, agnes_production, economy, journal, strategy, supervisor


def test_snapshot_is_read_only_and_task_done_does_not_close_objective(monkeypatch):
    channel = economy.add_channel("octopus", "agnes_video", "Agnes local", created_by="human",
                                  locator="http://127.0.0.1:8765", capabilities=["agnes_submit"])
    economy.update_channel("octopus", channel, actor="human", status="active", access="act")
    objective_id = agnes_missions.create("octopus", "Une mer calme", authorized=True)
    work = supervisor.work_tasks("octopus", objective_id)
    assert len(work) == 1
    with journal.connect() as connection:
        connection.execute("UPDATE tasks SET status='done' WHERE id=?", (work[0]["id"],))

    monkeypatch.setattr(agnes_missions, "create", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("La lecture ne crée aucune mission")))
    monkeypatch.setattr(agnes_production, "request_generation", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("La lecture ne soumet aucune génération")))
    monkeypatch.setattr(agnes, "_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("La lecture ne contacte pas l'API Agnes")))
    before = journal.query("SELECT COUNT(*) AS n FROM events")[0]["n"]
    state = read_snapshot("octopus")
    objective = state["objectives"][0]
    assert objective["id"] == objective_id
    assert objective["authorized"] is True
    assert mission_state(objective)[0] == "Vérification"
    assert objective["status"] == "active"
    assert journal.query("SELECT COUNT(*) AS n FROM events")[0]["n"] == before
    assert state["token_cost_usd"] == 0
    assert state["agnes_api_cost"] is None
    assert read_snapshot("autre")["objectives"] == []


def test_smoke_video_without_objective_requires_file_decode_sha_and_active_evidence(tmp_path):
    agnes_production.ensure_schema()
    target = tmp_path / "livrable.mp4"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "tiny.mp4", target)
    check = agnes.verify_mp4(target)
    assert check["verified"]
    evidence_id = strategy.create("evidence", "octopus", "MP4 observé", created_by="worker",
                                  nature="observed", source_type="file", source_ref=str(target),
                                  captured_at=time.time(), observation="Fichier local décodable")
    generation_id = agnes_production.create_generation(
        business="octopus", prompt="test", idempotency_key="test-smoke", status="done")
    agnes_production.update_generation(generation_id, output_path=str(target), sha256=check["sha256"],
                                       file_size=check["bytes"], evidence_id=evidence_id)

    state = read_snapshot("octopus")
    generation = state["generations"][0]
    assert generation["verified"] is True
    assert generation["objective_id"] is None

    target.write_bytes(b"corrupt")
    assert read_snapshot("octopus")["generations"][0]["verified"] is False


def test_missing_agnes_table_has_honest_empty_state():
    state = read_snapshot("all")
    assert state["generations"] == []
    assert state["objectives"] == []


def test_historic_achievement_with_missing_video_needs_attention():
    objective = {"status": "achieved", "success_criteria": "kept_video_files>=1", "generations": []}
    assert mission_state(objective)[0] == "À examiner"


def test_consultation_mode_rejects_database_writes(monkeypatch):
    journal.connect().close()
    monkeypatch.setenv("OCTOPUS_WORKBENCH_READONLY", "1")
    with journal.connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        try:
            connection.execute("INSERT INTO events(ts, type) VALUES(1, 'unexpected')")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("le journal a accepté une écriture en consultation")
    try:
        db.set_state("active_business", "octopus")
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("la base GUI a accepté une écriture en consultation")
