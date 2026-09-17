"""Journal d'execution OCTOPUS (SQLite) : runs, appels LLM, resultats d'evaluation.

Le journal est la source de verite de ce qui a ete fait et de ce que cela a coute.
Tout appel LLM y est ecrit (reussi, invalide, en erreur ou bloque par un budget).
"""
from __future__ import annotations

import contextvars
import functools
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass

from . import enabled, paths

SCHEMA_VERSION = 2

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES runs(id),
    root_id INTEGER,
    business TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT,
    profile TEXT,
    budget_usd REAL,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    pid INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_id);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id INTEGER REFERENCES runs(id),
    root_run_id INTEGER,
    business TEXT,
    agent TEXT,
    task TEXT NOT NULL,
    profile TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    cost_class TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    error TEXT,
    prompt_tokens INTEGER,
    cache_hit_tokens INTEGER,
    cache_miss_tokens INTEGER,
    completion_tokens INTEGER,
    reasoning_tokens INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    peak INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    prompt_sha256 TEXT,
    prompt_chars INTEGER,
    output_preview TEXT,
    justification TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_calls_task_model ON llm_calls(task, model);
CREATE TABLE IF NOT EXISTS bench_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    bench_run_id INTEGER REFERENCES runs(id),
    suite TEXT NOT NULL,
    task TEXT NOT NULL,
    item TEXT NOT NULL,
    model TEXT NOT NULL,
    repeat INTEGER NOT NULL DEFAULT 0,
    prompt_version TEXT,
    passed INTEGER NOT NULL,
    score REAL NOT NULL,
    value REAL,
    checks TEXT,
    latency_ms INTEGER,
    cost_usd REAL,
    llm_call_id INTEGER,
    error TEXT,
    output_preview TEXT
);
CREATE INDEX IF NOT EXISTS idx_bench_task_model ON bench_results(task, model);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    input TEXT NOT NULL DEFAULT '{}',
    output TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    not_before REAL NOT NULL DEFAULT 0,
    resource TEXT,
    lease_owner TEXT,
    lease_until REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    parent_id INTEGER REFERENCES tasks(id),
    run_id INTEGER,
    budget_usd REAL,
    idempotency_key TEXT UNIQUE,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_ready ON tasks(status, not_before, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_business ON tasks(business, status);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    business TEXT,
    task_id INTEGER,
    type TEXT NOT NULL,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE TABLE IF NOT EXISTS human_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    business TEXT NOT NULL,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    key TEXT NOT NULL,
    question TEXT NOT NULL,
    context TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    answer TEXT,
    answered_at REAL,
    expires_at REAL,
    UNIQUE(task_id, key)
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business TEXT NOT NULL,
    kind TEXT NOT NULL,
    input TEXT NOT NULL DEFAULT '{}',
    interval_s REAL NOT NULL,
    next_run REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    budget_usd REAL,
    last_task_id INTEGER,
    UNIQUE(business, kind)
);
"""
_MIGRATIONS = ((1, _SCHEMA_V1), (2, _SCHEMA_V2))

_LLM_COLUMNS = (
    "ts", "run_id", "root_run_id", "business", "agent", "task", "profile", "model", "provider",
    "cost_class", "attempt", "status", "error", "prompt_tokens", "cache_hit_tokens",
    "cache_miss_tokens", "completion_tokens", "reasoning_tokens", "cost_usd", "peak",
    "duration_ms", "prompt_sha256", "prompt_chars", "output_preview", "justification",
)
_BENCH_COLUMNS = (
    "ts", "bench_run_id", "suite", "task", "item", "model", "repeat", "prompt_version", "passed",
    "score", "value", "checks", "latency_ms", "cost_usd", "llm_call_id", "error", "output_preview",
)


def connect() -> sqlite3.Connection:
    path = paths.journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.execute("PRAGMA journal_mode=WAL")
        for target, script in _MIGRATIONS:
            if version < target:
                conn.executescript(script)  # IF NOT EXISTS : rejouable si deux processus migrent ensemble
                conn.execute(f"PRAGMA user_version={target}")
                conn.commit()
    return conn


def _insert(table: str, columns: tuple[str, ...], row: dict) -> int:
    unknown = set(row) - set(columns)
    if unknown:
        raise ValueError(f"colonnes inconnues pour {table} : {sorted(unknown)}")
    cols = [c for c in columns if c in row]
    conn = connect()
    try:
        cur = conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def record_llm_call(row: dict) -> int:
    return _insert("llm_calls", _LLM_COLUMNS, row)


def record_bench_result(row: dict) -> int:
    return _insert("bench_results", _BENCH_COLUMNS, row)


# --- runs --------------------------------------------------------------------

@dataclass(frozen=True)
class RunContext:
    id: int
    root_id: int
    business: str
    kind: str
    profile: str | None
    budget_usd: float | None
    budgets: tuple[tuple[int, float], ...]  # (run_id, budget) du run et de ses parents budgetes


_current: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar("octopus_run", default=None)


def current_run() -> RunContext | None:
    return _current.get() if enabled() else None


@contextmanager
def run(business: str, kind: str, *, label: str | None = None, budget_usd: float | None = None,
        profile: str | None = None):
    """Ouvre un run (imbrique dans le run courant s'il existe). Cede None si OCTOPUS=off."""
    if not enabled():
        yield None
        return
    parent = _current.get()
    effective_profile = profile or (parent.profile if parent else None)
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO runs (parent_id, root_id, business, kind, label, profile, budget_usd, status, "
            "started_at, pid) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (parent.id if parent else None, parent.root_id if parent else None, business, kind,
             (label or "")[:200], effective_profile, budget_usd, time.time(), os.getpid()),
        )
        run_id = int(cur.lastrowid)
        if parent is None:
            conn.execute("UPDATE runs SET root_id=? WHERE id=?", (run_id, run_id))
        conn.commit()
    finally:
        conn.close()
    budgets = (parent.budgets if parent else ()) + (((run_id, budget_usd),) if budget_usd is not None else ())
    ctx = RunContext(run_id, parent.root_id if parent else run_id, business, kind, effective_profile,
                     budget_usd, budgets)
    token = _current.set(ctx)
    status, error = "done", None
    try:
        yield ctx
    except KeyboardInterrupt:
        status, error = "interrupted", "KeyboardInterrupt"
        raise
    except BaseException as exc:
        # Une exception peut porter son propre statut (attente humaine, annulation) : ce n'est pas une erreur.
        status, error = getattr(exc, "run_status", "error"), f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        _current.reset(token)
        _finish(run_id, status, error)


def _finish(run_id: int, status: str, error: str | None) -> None:
    try:
        conn = connect()
        try:
            conn.execute("UPDATE runs SET status=?, error=?, finished_at=? WHERE id=?",
                         (status, error, time.time(), run_id))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:  # ne jamais masquer l'exception metier d'origine
        print(f"[octopus] impossible de clore le run #{run_id} : {exc}", file=sys.stderr)


def with_run(business: str, kind: str, *, budget_usd: float | None = None, profile: str | None = None):
    """Decorateur : execute la fonction dans un run (libelle = premier argument texte)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            texts = [v for v in list(args) + list(kwargs.values()) if isinstance(v, str)]
            label = " : ".join(texts[:2]) or fn.__name__
            with run(business, kind, label=label, budget_usd=budget_usd, profile=profile):
                return fn(*args, **kwargs)
        return wrapper
    return decorator


def subtree_cost(run_id: int) -> float:
    row = query(
        "WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT r.id FROM runs r JOIN sub ON r.parent_id = sub.id) "
        "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE run_id IN (SELECT id FROM sub)",
        (run_id,),
    )
    return float(row[0]["c"])


def budget_exhausted(ctx: RunContext) -> bool:
    return any(subtree_cost(run_id) >= budget for run_id, budget in ctx.budgets)


def spent_today(business: str | None = None) -> float:
    start = time.time() - (time.time() % 86400)  # minuit UTC
    if business:
        row = query("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE ts >= ? AND business = ?",
                    (start, business))
    else:
        row = query("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE ts >= ?", (start,))
    return float(row[0]["c"])


def evidence(task: str, model: str, rules: dict) -> dict:
    """Preuve d'aptitude d'un modele pour une tache : dernier banc recent et suffisant."""
    since = time.time() - rules["max_age_days"] * 86400
    latest = query(
        "SELECT bench_run_id FROM bench_results WHERE task=? AND model=? AND ts>=? ORDER BY ts DESC LIMIT 1",
        (task, model, since),
    )
    if not latest:
        return {"eligible": False, "reason": "preuve insuffisante : aucun banc recent pour cette tache"}
    rows = query("SELECT passed FROM bench_results WHERE task=? AND model=? AND bench_run_id IS ?",
                 (task, model, latest[0]["bench_run_id"]))
    n = len(rows)
    rate = sum(r["passed"] for r in rows) / n if n else 0.0
    if n < rules["min_samples"]:
        return {"eligible": False, "n": n, "pass_rate": rate,
                "reason": f"preuve insuffisante : {n} essais < {rules['min_samples']}"}
    if rate < rules["min_pass_rate"]:
        return {"eligible": False, "n": n, "pass_rate": rate,
                "reason": f"qualite insuffisante au banc : {rate:.0%} < {rules['min_pass_rate']:.0%}"}
    return {"eligible": True, "n": n, "pass_rate": rate, "reason": f"valide au banc : {rate:.0%} sur {n} essais"}
