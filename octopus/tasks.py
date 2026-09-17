"""File de tâches persistée d'OCTOPUS (SQLite) : tâches, événements, demandes humaines, planifications.

Plusieurs processus (worker, GUI, CLI) partagent la même base. Toute transition d'état passe par une
transaction BEGIN IMMEDIATE : une tâche ne peut être prise que par un seul worker.

Statuts : queued -> running -> done | failed | cancelled, et running -> waiting_human -> queued
(reprise après réponse). Un bail (lease) expiré signale un worker mort : la tâche repart en file
si des tentatives restent, sinon elle échoue.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import journal

ACTIVE = ("queued", "running", "waiting_human")
FINAL = ("done", "failed", "cancelled")


class TaskError(RuntimeError):
    pass


class LeaseLost(TaskError):
    """Le worker n'est plus propriétaire de la tâche (bail expiré et repris, ou annulation)."""


@contextmanager
def _tx():
    conn = journal.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    task = dict(row)
    for key in ("input", "output"):
        if task.get(key):
            task[key] = json.loads(task[key])
    return task


def _emit(conn, business: str | None, task_id: int | None, type_: str, data: dict | None = None) -> None:
    conn.execute("INSERT INTO events (ts, business, task_id, type, data) VALUES (?, ?, ?, ?, ?)",
                 (time.time(), business, task_id, type_, json.dumps(data or {}, ensure_ascii=False)))


def emit(business: str | None, task_id: int | None, type_: str, data: dict | None = None) -> None:
    with _tx() as conn:
        _emit(conn, business, task_id, type_, data)


# --- création et lecture --------------------------------------------------------------------

def enqueue(business: str, kind: str, input: dict | None = None, *, priority: int = 0, delay_s: float = 0,
            max_attempts: int = 1, resource: str | None = None, parent_id: int | None = None,
            idempotency_key: str | None = None, budget_usd: float | None = None) -> int:
    """Ajoute une tâche. Avec `idempotency_key`, une tâche déjà créée pour cette clé est renvoyée telle quelle."""
    now = time.time()
    with _tx() as conn:
        if idempotency_key:
            existing = conn.execute("SELECT id FROM tasks WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                return int(existing["id"])
        cur = conn.execute(
            "INSERT INTO tasks (business, kind, input, priority, not_before, max_attempts, resource, parent_id, "
            "idempotency_key, budget_usd, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (business, kind, json.dumps(input or {}, ensure_ascii=False), priority, now + delay_s,
             max(1, max_attempts), resource, parent_id, idempotency_key, budget_usd, now, now))
        task_id = int(cur.lastrowid)
        _emit(conn, business, task_id, "task.queued", {"kind": kind})
        return task_id


def get(task_id: int) -> dict | None:
    rows = journal.query("SELECT * FROM tasks WHERE id=?", (task_id,))
    return _row(rows[0]) if rows else None


def list_tasks(status: str | None = None, business: str | None = None, limit: int = 50) -> list[dict]:
    sql, params = "SELECT * FROM tasks WHERE 1=1", []
    if status:
        sql += " AND status=?"
        params.append(status)
    if business:
        sql += " AND business=?"
        params.append(business)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in journal.query(sql, tuple(params))]


def events(since_id: int = 0, task_id: int | None = None, limit: int = 200) -> list[dict]:
    sql, params = "SELECT * FROM events WHERE id > ?", [since_id]
    if task_id is not None:
        sql += " AND task_id=?"
        params.append(task_id)
    rows = journal.query(sql + " ORDER BY id LIMIT ?", tuple(params + [limit]))
    return [{**dict(r), "data": json.loads(r["data"] or "{}")} for r in rows]


# --- exécution ------------------------------------------------------------------------------

def claim(owner: str, *, lease_s: float = 60, kinds: list[str] | None = None) -> dict | None:
    """Prend la tâche prête la plus prioritaire dont la ressource est libre. None si rien à faire."""
    now = time.time()
    with _tx() as conn:
        busy = {r["resource"] for r in conn.execute(
            "SELECT resource FROM tasks WHERE status='running' AND lease_until > ? AND resource IS NOT NULL", (now,))}
        sql = "SELECT * FROM tasks WHERE status='queued' AND not_before <= ?"
        params: list = [now]
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params += list(kinds)
        for row in conn.execute(sql + " ORDER BY priority DESC, id", params):
            if row["resource"] and row["resource"] in busy:
                continue
            conn.execute("UPDATE tasks SET status='running', lease_owner=?, lease_until=?, attempts=attempts+1, "
                         "updated_at=? WHERE id=?", (owner, now + lease_s, now, row["id"]))
            _emit(conn, row["business"], row["id"], "task.started", {"owner": owner, "attempt": row["attempts"] + 1})
            return get_in(conn, row["id"])
    return None


def get_in(conn, task_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def _owned(conn, task_id: int, owner: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or row["status"] != "running" or row["lease_owner"] != owner:
        raise LeaseLost(f"tâche #{task_id} : plus détenue par {owner}")
    return row


def heartbeat(task_id: int, owner: str, lease_s: float = 60) -> bool:
    """Renouvelle le bail. Renvoie True si une annulation a été demandée."""
    with _tx() as conn:
        row = _owned(conn, task_id, owner)
        conn.execute("UPDATE tasks SET lease_until=?, updated_at=? WHERE id=?",
                     (time.time() + lease_s, time.time(), task_id))
        return bool(row["cancel_requested"])


def set_run(task_id: int, run_id: int | None) -> None:
    with _tx() as conn:
        conn.execute("UPDATE tasks SET run_id=? WHERE id=?", (run_id, task_id))


def complete(task_id: int, owner: str, output=None) -> None:
    now = time.time()
    with _tx() as conn:
        row = _owned(conn, task_id, owner)
        conn.execute("UPDATE tasks SET status='done', output=?, error=NULL, lease_owner=NULL, lease_until=NULL, "
                     "updated_at=?, finished_at=? WHERE id=?",
                     (json.dumps(output, ensure_ascii=False, default=str), now, now, task_id))
        _emit(conn, row["business"], task_id, "task.done", {"kind": row["kind"]})


def fail(task_id: int, owner: str, error: str, *, retry_delay_s: float = 30) -> str:
    """Échec d'une tentative : nouvelle tentative différée s'il en reste, sinon échec définitif."""
    now = time.time()
    with _tx() as conn:
        row = _owned(conn, task_id, owner)
        status = "queued" if row["attempts"] < row["max_attempts"] and not row["cancel_requested"] else "failed"
        conn.execute("UPDATE tasks SET status=?, error=?, lease_owner=NULL, lease_until=NULL, not_before=?, "
                     "updated_at=?, finished_at=? WHERE id=?",
                     (status, error[:2000], now + retry_delay_s if status == "queued" else row["not_before"], now,
                      None if status == "queued" else now, task_id))
        _emit(conn, row["business"], task_id, "task.retry" if status == "queued" else "task.failed",
              {"kind": row["kind"], "error": error[:300], "attempt": row["attempts"]})
        return status


def mark_cancelled(task_id: int, owner: str, reason: str = "annulée") -> None:
    now = time.time()
    with _tx() as conn:
        row = _owned(conn, task_id, owner)
        conn.execute("UPDATE tasks SET status='cancelled', error=?, lease_owner=NULL, lease_until=NULL, "
                     "updated_at=?, finished_at=? WHERE id=?", (reason, now, now, task_id))
        _emit(conn, row["business"], task_id, "task.cancelled", {"reason": reason})


def cancel(task_id: int, reason: str = "annulée par l'humain") -> str:
    """Annule une tâche en attente tout de suite ; une tâche en cours est prévenue (annulation coopérative)."""
    now = time.time()
    with _tx() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(f"tâche #{task_id} inconnue")
        if row["status"] in FINAL:
            return row["status"]
        if row["status"] == "running":
            conn.execute("UPDATE tasks SET cancel_requested=1, updated_at=? WHERE id=?", (now, task_id))
            _emit(conn, row["business"], task_id, "task.cancel_requested", {"reason": reason})
            return "cancel_requested"
        conn.execute("UPDATE tasks SET status='cancelled', cancel_requested=1, error=?, updated_at=?, finished_at=? "
                     "WHERE id=?", (reason, now, now, task_id))
        conn.execute("UPDATE human_requests SET status='cancelled' WHERE task_id=? AND status='pending'", (task_id,))
        _emit(conn, row["business"], task_id, "task.cancelled", {"reason": reason})
        return "cancelled"


def cancel_requested(task_id: int) -> bool:
    rows = journal.query("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,))
    return bool(rows and rows[0]["cancel_requested"])


# --- étapes mémorisées (tâches rejouables) --------------------------------------------------

_MISSING = object()


def step_value(task_id: int, key: str, default=_MISSING):
    rows = journal.query("SELECT value FROM task_steps WHERE task_id=? AND key=?", (task_id, key))
    if rows:
        return json.loads(rows[0]["value"])
    if default is _MISSING:
        raise KeyError(key)
    return default


def save_step(task_id: int, key: str, value) -> None:
    with _tx() as conn:
        conn.execute("INSERT OR REPLACE INTO task_steps (task_id, key, value, ts) VALUES (?, ?, ?, ?)",
                     (task_id, key, json.dumps(value, ensure_ascii=False, default=str), time.time()))


# --- humain dans la boucle ------------------------------------------------------------------

def answer_for(task_id: int, key: str) -> str | None:
    rows = journal.query("SELECT answer FROM human_requests WHERE task_id=? AND key=? AND status='answered'",
                         (task_id, key))
    return rows[0]["answer"] if rows else None


def request_human(task_id: int, owner: str, key: str, question: str, *, context: dict | None = None,
                  expires_s: float | None = None) -> int:
    """La tâche passe en attente d'une réponse ; elle sera reprise (depuis le début) après la réponse."""
    now = time.time()
    with _tx() as conn:
        row = _owned(conn, task_id, owner)
        existing = conn.execute("SELECT id FROM human_requests WHERE task_id=? AND key=?", (task_id, key)).fetchone()
        if existing:
            request_id = int(existing["id"])
            conn.execute("UPDATE human_requests SET status='pending', question=?, answer=NULL, expires_at=? "
                         "WHERE id=?", (question, now + expires_s if expires_s else None, request_id))
        else:
            request_id = int(conn.execute(
                "INSERT INTO human_requests (ts, business, task_id, key, question, context, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now, row["business"], task_id, key, question, json.dumps(context or {}, ensure_ascii=False),
                 now + expires_s if expires_s else None)).lastrowid)
        # La tentative ne compte pas : attendre l'humain n'est pas un échec.
        conn.execute("UPDATE tasks SET status='waiting_human', attempts=attempts-1, lease_owner=NULL, "
                     "lease_until=NULL, updated_at=? WHERE id=?", (now, task_id))
        _emit(conn, row["business"], task_id, "human.requested", {"request_id": request_id, "question": question})
        return request_id


def pending_human_requests(business: str | None = None) -> list[dict]:
    sql = "SELECT * FROM human_requests WHERE status='pending'"
    params: tuple = ()
    if business:
        sql += " AND business=?"
        params = (business,)
    return [dict(r) for r in journal.query(sql + " ORDER BY id", params)]


def answer(request_id: int, text: str) -> int:
    """Enregistre la réponse et remet la tâche en file. Renvoie l'id de la tâche."""
    now = time.time()
    with _tx() as conn:
        req = conn.execute("SELECT * FROM human_requests WHERE id=?", (request_id,)).fetchone()
        if req is None or req["status"] != "pending":
            raise TaskError(f"demande #{request_id} inconnue ou déjà close")
        conn.execute("UPDATE human_requests SET status='answered', answer=?, answered_at=? WHERE id=?",
                     (text, now, request_id))
        remaining = conn.execute("SELECT COUNT(*) FROM human_requests WHERE task_id=? AND status='pending'",
                                 (req["task_id"],)).fetchone()[0]
        if remaining == 0:
            conn.execute("UPDATE tasks SET status='queued', not_before=?, updated_at=? "
                         "WHERE id=? AND status='waiting_human'", (now, now, req["task_id"]))
        _emit(conn, req["business"], req["task_id"], "human.answered", {"request_id": request_id})
        return int(req["task_id"])


# --- maintenance et planification -----------------------------------------------------------

def reap(now: float | None = None) -> dict:
    """Baux expirés (worker mort) et demandes humaines expirées."""
    now = now or time.time()
    out = {"requeued": [], "failed": [], "expired_requests": []}
    with _tx() as conn:
        for row in conn.execute("SELECT * FROM tasks WHERE status='running' AND lease_until < ?", (now,)).fetchall():
            retry = row["attempts"] < row["max_attempts"] and not row["cancel_requested"]
            status = "queued" if retry else ("cancelled" if row["cancel_requested"] else "failed")
            conn.execute("UPDATE tasks SET status=?, error=?, lease_owner=NULL, lease_until=NULL, updated_at=?, "
                         "finished_at=? WHERE id=?",
                         (status, f"bail expiré (worker {row['lease_owner']} arrêté ?)", now,
                          None if retry else now, row["id"]))
            _emit(conn, row["business"], row["id"], "task.lease_expired", {"owner": row["lease_owner"], "status": status})
            out["requeued" if retry else "failed"].append(row["id"])
        for req in conn.execute("SELECT * FROM human_requests WHERE status='pending' AND expires_at IS NOT NULL "
                                "AND expires_at < ?", (now,)).fetchall():
            conn.execute("UPDATE human_requests SET status='expired' WHERE id=?", (req["id"],))
            conn.execute("UPDATE tasks SET status='failed', error=?, updated_at=?, finished_at=? "
                         "WHERE id=? AND status='waiting_human'",
                         (f"sans réponse humaine : {req['question'][:120]}", now, now, req["task_id"]))
            _emit(conn, req["business"], req["task_id"], "human.expired", {"request_id": req["id"]})
            out["expired_requests"].append(req["id"])
    return out


def schedule(business: str, kind: str, interval_s: float, input: dict | None = None, *,
             start_in_s: float = 0, enabled: bool = True, budget_usd: float | None = None) -> int:
    if interval_s < 60:
        raise TaskError("intervalle minimal : 60 s")
    now = time.time()
    with _tx() as conn:
        conn.execute(
            "INSERT INTO schedules (business, kind, input, interval_s, next_run, enabled, budget_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(business, kind) DO UPDATE SET input=excluded.input, "
            "interval_s=excluded.interval_s, next_run=excluded.next_run, enabled=excluded.enabled, "
            "budget_usd=excluded.budget_usd",
            (business, kind, json.dumps(input or {}, ensure_ascii=False), interval_s, now + start_in_s,
             int(enabled), budget_usd))
        return int(conn.execute("SELECT id FROM schedules WHERE business=? AND kind=?", (business, kind)).fetchone()[0])


def schedules() -> list[dict]:
    return [dict(r) for r in journal.query("SELECT * FROM schedules ORDER BY business, kind")]


def materialize_due(now: float | None = None, resources: dict[str, str] | None = None) -> list[int]:
    """Crée les tâches des planifications échues. Pas d'empilement : une occurrence encore active est sautée."""
    now = now or time.time()
    created = []
    resources = resources or {}
    due = journal.query("SELECT * FROM schedules WHERE enabled=1 AND next_run <= ?", (now,))
    for sched in due:
        with _tx() as conn:
            current = conn.execute("SELECT next_run, last_task_id FROM schedules WHERE id=?", (sched["id"],)).fetchone()
            if current["next_run"] > now:
                continue  # un autre worker l'a déjà traitée
            active = current["last_task_id"] and conn.execute(
                f"SELECT 1 FROM tasks WHERE id=? AND status IN ({','.join('?' for _ in ACTIVE)})",
                (current["last_task_id"], *ACTIVE)).fetchone()
            task_id = current["last_task_id"]
            if active:
                _emit(conn, sched["business"], task_id, "schedule.skipped", {"kind": sched["kind"]})
            else:
                cur = conn.execute(
                    "INSERT INTO tasks (business, kind, input, resource, budget_usd, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sched["business"], sched["kind"], sched["input"], resources.get(sched["kind"]),
                     sched["budget_usd"], now, now))
                task_id = int(cur.lastrowid)
                _emit(conn, sched["business"], task_id, "task.queued", {"kind": sched["kind"], "schedule": sched["id"]})
                created.append(task_id)
            conn.execute("UPDATE schedules SET next_run=?, last_task_id=? WHERE id=?",
                         (now + sched["interval_s"], task_id, sched["id"]))
    return created
