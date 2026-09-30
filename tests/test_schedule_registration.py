"""Tests for tasks.schedule if_absent runtime registration."""
from __future__ import annotations

import threading
import time

from octopus import journal, tasks


def test_schedule_if_absent_first_insert():
    sid = tasks.schedule("biz", "kind", 3600, if_absent=True)
    assert sid > 0
    row = journal.query("SELECT * FROM schedules WHERE business=? AND kind=?", ("biz", "kind"))
    assert len(row) == 1
    assert row[0]["id"] == sid


def test_schedule_if_absent_preserves_existing_fields():
    sid = tasks.schedule("biz", "kind", 3600, if_absent=True)

    conn = journal.connect()
    try:
        conn.execute(
            "UPDATE schedules SET enabled=0, next_run=?, last_task_id=? WHERE id=?",
            (time.time() + 86400, 42, sid),
        )
        conn.commit()
    finally:
        conn.close()

    sid2 = tasks.schedule("biz", "kind", 3600, input={"new": "input"}, if_absent=True)

    assert sid2 == sid
    row = journal.query("SELECT * FROM schedules WHERE id=?", (sid,))
    assert row[0]["enabled"] == 0
    assert row[0]["last_task_id"] == 42
    assert row[0]["next_run"] > time.time()
    assert row[0]["input"] == "{}"


def test_schedule_default_upsert_still_updates():
    sid = tasks.schedule("biz", "kind", 3600, if_absent=False)
    sid2 = tasks.schedule("biz", "kind", 7200, input={"v": 2}, if_absent=False)
    assert sid2 == sid
    row = journal.query("SELECT * FROM schedules WHERE id=?", (sid,))
    assert row[0]["interval_s"] == 7200
    assert row[0]["input"] == '{"v": 2}'


def test_schedule_if_absent_concurrent_inserts_converge():
    results = []

    def register():
        sid = tasks.schedule("biz2", "kind2", 3600, if_absent=True)
        results.append(sid)

    t1 = threading.Thread(target=register)
    t2 = threading.Thread(target=register)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results[0] == results[1]
    rows = journal.query("SELECT * FROM schedules WHERE business=? AND kind=?", ("biz2", "kind2"))
    assert len(rows) == 1
