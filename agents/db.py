"""Couche SQLite du groupe : salon + coûts + métriques + décisions.

Une seule base `podalux.db` (l'équivalent des `store.db` par agent de l'archive,
namespace par agent via la table `messages`).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from . import config


def _conn() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # GUI, cycle et agents écrivent depuis des processus distincts : attendre plutôt qu'échouer (audit M6)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # lectures de la GUI sans bloquer les écritures ; persistant
    except sqlite3.OperationalError:
        pass  # base occupée : on garde le mode courant, nouvel essai au prochain démarrage
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT,
            mention TEXT,
            kind TEXT NOT NULL DEFAULT 'message',
            content TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            agent TEXT NOT NULL,
            task TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            offer_id TEXT NOT NULL,
            score INTEGER,
            humanite INTEGER,
            verdict TEXT,
            payload TEXT
        );
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            agent TEXT NOT NULL,
            decision TEXT NOT NULL,
            payload TEXT
        );
        CREATE TABLE IF NOT EXISTS metrics_j1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            offer_id TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            conversions INTEGER NOT NULL DEFAULT 0,
            revenue_usd REAL NOT NULL DEFAULT 0,
            payload TEXT
        );
        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            agent TEXT NOT NULL,
            kind TEXT NOT NULL,
            question TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            answer TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            offer_id TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            step TEXT,
            result TEXT
        );
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            agent TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def post(agent: str, content: str, to: str | None = None,
         mention: str | None = None, kind: str = "message") -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO messages (ts, from_agent, to_agent, mention, kind, content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (time.time(), agent, to, mention, kind, content),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def log_cost(agent: str, task: str, model: str,
             prompt_tokens: int, completion_tokens: int, cost_usd: float | None = None) -> float:
    """Table historique (affichee par la GUI). `cost_usd` : cout officiel calcule par OCTOPUS."""
    if cost_usd is None:
        prices = config.PRICES.get(model, {"in": 0.0, "out": 0.0})
        cost = (prompt_tokens * prices["in"] + completion_tokens * prices["out"]) / 1_000_000
    else:
        cost = cost_usd
    conn = _conn()
    conn.execute(
        "INSERT INTO costs (ts, agent, task, model, prompt_tokens, completion_tokens, cost_usd) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (time.time(), agent, task, model, prompt_tokens, completion_tokens, round(cost, 6)),
    )
    conn.commit()
    conn.close()
    return cost


def total_cost() -> float:
    conn = _conn()
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM costs").fetchone()
    conn.close()
    return float(row["c"])


def record_metric(offer_id: str, score: int | None, humanite: int | None,
                  verdict: str | None, payload: dict) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO metrics (ts, offer_id, score, humanite, verdict, payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (time.time(), offer_id, score, humanite, verdict, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def decide(agent: str, decision: str, payload: dict | None = None) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO decisions (ts, agent, decision, payload) VALUES (?, ?, ?, ?)",
        (time.time(), agent, decision, json.dumps(payload, ensure_ascii=False) if payload else None),
    )
    conn.commit()
    conn.close()


def record_j1(offer_id: str, views: int = 0, likes: int = 0,
              conversions: int = 0, revenue_usd: float = 0.0,
              payload: dict | None = None) -> None:
    """Métriques J+1 (post-publication) : vues, likes, conversions, revenu."""
    conn = _conn()
    conn.execute(
        "INSERT INTO metrics_j1 (ts, offer_id, views, likes, conversions, revenue_usd, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (time.time(), offer_id, views, likes, conversions, revenue_usd,
         json.dumps(payload, ensure_ascii=False) if payload else None),
    )
    conn.commit()
    conn.close()


def j1_summary() -> list[dict]:
    """Agrégats J+1 par offre (vues, conversions, revenu)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT offer_id, SUM(views) AS v, SUM(likes) AS l, "
        "SUM(conversions) AS c, SUM(revenue_usd) AS r "
        "FROM metrics_j1 GROUP BY offer_id ORDER BY offer_id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Demandes humaines (handoffs) ---
def ask(agent: str, kind: str, question: str) -> int:
    """Crée une demande de confirmation/information à l'humain. Retourne l'id."""
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO handoffs (ts, agent, kind, question, status) VALUES (?, ?, ?, ?, 'pending')",
        (time.time(), agent, kind, question),
    )
    conn.commit()
    hid = cur.lastrowid
    conn.close()
    return hid


def get_handoff(hid: int) -> dict:
    conn = _conn()
    row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def answer(hid: int, answer: str) -> None:
    conn = _conn()
    conn.execute("UPDATE handoffs SET status='answered', answer=? WHERE id=?", (answer, hid))
    conn.commit()
    conn.close()


def pending_handoffs() -> list[dict]:
    conn = _conn()
    rows = conn.execute("SELECT * FROM handoffs WHERE status='pending' ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ask_human(agent: str, kind: str, question: str, timeout_s: int = 300,
              cancel=None) -> str | None:
    """Demande à l'humain puis poll la réponse (bloquant pour l'agent). `cancel()` vrai : abandon."""
    hid = ask(agent, kind, question)
    deadline = time.time() + timeout_s
    status = "timeout"
    while time.time() < deadline:
        h = get_handoff(hid)
        if h.get("status") == "answered":
            return h.get("answer")
        if cancel is not None and cancel():
            status = "cancelled"
            break
        time.sleep(1.0)
    conn = _conn()
    conn.execute("UPDATE handoffs SET status=? WHERE id=? AND status='pending'", (status, hid))
    conn.commit()
    conn.close()
    return None


# --- Statut de run ---
def start_run(offer_id: str | None) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO runs (ts, offer_id, status, step) VALUES (?, ?, 'running', 'démarrage')",
        (time.time(), offer_id),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def update_run(rid: int, status: str | None = None, step: str | None = None,
               result: str | None = None, offer_id: str | None = None) -> None:
    conn = _conn()
    sets, vals = [], []
    if status is not None:
        sets.append("status=?"); vals.append(status)
    if step is not None:
        sets.append("step=?"); vals.append(step)
    if result is not None:
        sets.append("result=?"); vals.append(result)
    if offer_id is not None:
        sets.append("offer_id=?"); vals.append(offer_id)
    if sets:
        vals.append(rid)
        conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
    conn.close()


def current_run() -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


# --- État global (flag stop, etc.) ---
def set_state(key: str, value: str) -> None:
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_state(key: str) -> str | None:
    conn = _conn()
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def request_stop() -> None:
    set_state("stop", repr(time.time()))


def clear_stop() -> None:
    set_state("stop", "0")


# --- Verrou de production (un seul cycle à la fois : fichiers partagés, TTS non concurrent) ---
RUN_LOCK_TTL_S = 1800  # bail renouvelé à chaque itération ; un processus tué libère le verrou en 30 min


def acquire_run_lock(owner: str, ttl_s: float = RUN_LOCK_TTL_S) -> bool:
    """Prend ou renouvelle le verrou de production. False si un autre propriétaire le détient."""
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM state WHERE key='run_lock'").fetchone()
        now = time.time()
        if row and row["value"]:
            holder, _, until = row["value"].rpartition("|")
            try:
                expired = float(until) <= now
            except ValueError:
                expired = True
            if holder != owner and not expired:
                conn.rollback()
                return False
        conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('run_lock', ?)",
                     (f"{owner}|{now + ttl_s}",))
        conn.commit()
        return True
    finally:
        conn.close()


def run_lock_holder() -> str | None:
    """Propriétaire du verrou s'il est encore valide."""
    value = get_state("run_lock")
    if not value:
        return None
    holder, _, until = value.rpartition("|")
    try:
        return holder if float(until) > time.time() else None
    except ValueError:
        return None


def release_run_lock(owner: str) -> None:
    conn = _conn()
    conn.execute("DELETE FROM state WHERE key='run_lock' AND value LIKE ?", (f"{owner}|%",))
    conn.commit()
    conn.close()


def stop_requested(since: float | None = None) -> bool:
    """Arrêt demandé. Avec `since` : seulement s'il a été demandé après ce moment."""
    value = get_state("stop")
    if value == "1":
        # Ancienne GUI (Podalux.exe non reconstruit) : "1" sans date. On honore l'arrêt une fois,
        # daté de maintenant ; les exécutions lancées ensuite ne sont pas concernées.
        requested_at = time.time()
        set_state("stop", repr(requested_at))
        return True
    try:
        requested_at = float(value) if value else 0.0
    except ValueError:
        requested_at = 0.0
    if since is None:
        return requested_at > 0
    return requested_at >= since


def recent_messages(limit: int = 100) -> list[dict]:
    """Derniers messages du salon (pour l'affichage)."""
    conn = _conn()
    rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def human_messages(limit: int = 10) -> list[dict]:
    """Derniers messages de l'humain (directives au groupe)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE from_agent='HUMAN' ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def metrics_list() -> list[dict]:
    """Toutes les métriques rubric (dernière par offre)."""
    conn = _conn()
    rows = conn.execute("SELECT offer_id, score, humanite, verdict FROM metrics ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Mémoire (apprentissage) ---
def norm_key(key: str) -> str:
    """Clé de mémoire normalisée : minuscules, sans accents, mots séparés par « _ » (audit M3)."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(key)).encode("ascii", "ignore").decode().lower()
    return "_".join(re.findall(r"[a-z0-9]+", text))


def remember(agent: str, key: str, value: str) -> None:
    conn = _conn()
    conn.execute("INSERT INTO memory (ts, agent, key, value) VALUES (?, ?, ?, ?)",
                 (time.time(), str(agent).upper(), norm_key(key), value))
    conn.commit()
    conn.close()


def recall(agent: str, key: str) -> str | None:
    """Dernière valeur pour la clé, casse, accents et séparateurs ignorés. None si inconnue."""
    wanted = norm_key(key)
    exact = [r for r in memory_keys(agent, limit=1000) if norm_key(r["key"]) == wanted]
    return exact[0]["value"] if exact else None


def memory_keys(agent: str, limit: int = 200) -> list[dict]:
    """Clés connues d'un agent, plus récentes d'abord (une ligne par clé)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT key, value, MAX(id) AS id FROM memory WHERE UPPER(agent)=? GROUP BY key ORDER BY id DESC LIMIT ?",
        (str(agent).upper(), limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recall_all(agent: str, limit: int = 50) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT key, value FROM memory WHERE agent=? ORDER BY id DESC LIMIT ?",
        (agent, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recent(agent: str, limit: int = 20) -> list[dict]:
    """Derniers messages adressés à un agent (fil + salon)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE to_agent = ? OR to_agent IS NULL OR mention = ? "
        "ORDER BY id DESC LIMIT ?",
        (agent, agent, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
