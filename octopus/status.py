"""État observable du runtime : ce qui tourne, ce qui attend, ce qui a échoué, ce qui vient.

Répond au critère d'observabilité de la phase G sans exiger de lire SQLite à la main :
une seule fonction (`snapshot`) assemble l'état canonique, `render` l'affiche, `python -m octopus
status` l'expose. Lecture seule : ce module n'écrit jamais dans le journal.

Sections : objectif courant, tâche/mission courante, run et sous-runs, dernière progression utile,
attente humaine (raison), échec/timeout (raison), travail suivant planifié, routage LLM et coût
cumulé.
"""
from __future__ import annotations

import json
import time

from . import journal


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in journal.query(sql, params)]


def _first_line(value, limit: int = 200) -> str:
    """Première ligne d'un message d'erreur, sans échouer sur un message vide."""
    lines = str(value or "").splitlines()
    return lines[0][:limit] if lines else ""


def objectives(business: str | None = None, limit: int = 10) -> list[dict]:
    sql = ("SELECT id, business, status, summary, statement, success_criteria, updated_at "
           "FROM strategy_objectives WHERE status IN ('active', 'paused')")
    params: list = []
    if business:
        sql += " AND business=?"
        params.append(business)
    return _rows(sql + " ORDER BY status, business, id LIMIT ?", tuple(params + [limit]))


def running_tasks(limit: int = 10) -> list[dict]:
    rows = _rows("SELECT id, business, kind, status, attempts, max_attempts, lease_owner, lease_until, run_id, "
                 "updated_at FROM tasks WHERE status IN ('running', 'waiting_human') ORDER BY updated_at DESC "
                 "LIMIT ?", (limit,))
    now = time.time()
    for row in rows:
        row["lease_remaining_s"] = None if row["lease_until"] is None else round(row["lease_until"] - now, 1)
    return rows


def running_runs(limit: int = 10) -> list[dict]:
    rows = _rows("SELECT id, parent_id, root_id, business, kind, label, status, started_at, budget_usd, pid "
                 "FROM runs WHERE status='running' ORDER BY started_at DESC LIMIT ?", (limit,))
    now = time.time()
    for row in rows:
        row["age_s"] = round(now - row["started_at"], 1)
        row["cost_usd"] = round(journal.subtree_cost(row["id"]), 6)
        budgets = journal.query(
            "WITH RECURSIVE a(id, parent_id, budget_usd) AS ("
            "SELECT id, parent_id, budget_usd FROM runs WHERE id=? UNION ALL "
            "SELECT r.id, r.parent_id, r.budget_usd FROM runs r JOIN a ON a.parent_id=r.id) "
            "SELECT id, budget_usd FROM a WHERE budget_usd IS NOT NULL", (row["id"],))
        row["remaining_usd"] = (round(max(0.0, min(item["budget_usd"] - journal.subtree_cost(item["id"])
                                                 for item in budgets)), 6) if budgets else None)
    return rows


def last_progress(limit: int = 8) -> list[dict]:
    """Derniers événements porteurs de sens (pas le bruit interne)."""
    rows = _rows("SELECT id, ts, business, task_id, type, data FROM events "
                 "WHERE type NOT IN ('task.queued') ORDER BY id DESC LIMIT ?", (limit,))
    for row in rows:
        data = json.loads(row["data"] or "{}")
        row["data"] = data or None
    return rows


def waiting_human(limit: int = 10) -> list[dict]:
    rows = _rows("SELECT h.id, h.business, h.task_id, h.key, h.question, h.expires_at, t.kind, t.status "
                 "FROM human_requests h JOIN tasks t ON t.id=h.task_id WHERE h.status='pending' "
                 "ORDER BY h.id LIMIT ?", (limit,))
    now = time.time()
    for row in rows:
        row["expires_in_s"] = None if row["expires_at"] is None else round(row["expires_at"] - now, 1)
    return rows


def failures(limit: int = 8) -> list[dict]:
    """Tâches en échec et runs anormaux : la raison, pas seulement le fait."""
    failed = _rows("SELECT id, business, kind, status, attempts, error, finished_at FROM tasks "
                   "WHERE status='failed' ORDER BY COALESCE(finished_at, updated_at) DESC LIMIT ?", (limit,))
    for row in failed:
        row["error"] = _first_line(row["error"])
        row["source"] = "task"
    runs = _rows("SELECT id, business, kind, label, status, error, finished_at FROM runs "
                 "WHERE status IN ('error', 'interrupted', 'abandoned') ORDER BY finished_at DESC LIMIT ?", (limit,))
    for row in runs:
        row["error"] = _first_line(row["error"])
        row["source"] = "run"
        row["attempts"] = None
    merged = ([{**row, "when": row.pop("finished_at")} for row in failed]
              + [{**row, "when": row.pop("finished_at")} for row in runs])
    merged.sort(key=lambda row: row["when"] or 0, reverse=True)
    return merged[:limit]


def next_work(limit: int = 10) -> list[dict]:
    """Travail déjà décidé : file d'attente (y compris différée) et planifications."""
    now = time.time()
    queued = _rows("SELECT id, business, kind, not_before, priority, parent_id FROM tasks WHERE status='queued' "
                   "ORDER BY not_before, priority DESC, id LIMIT ?", (limit,))
    for row in queued:
        row["ready_in_s"] = round(max(0.0, row["not_before"] - now), 1)
        row["source"] = "queue"
    planned = _rows("SELECT id, business, kind, interval_s, next_run, enabled FROM schedules WHERE enabled=1 "
                    "ORDER BY next_run LIMIT ?", (limit,))
    for row in planned:
        row["ready_in_s"] = round(max(0.0, row["next_run"] - now), 1)
        row["source"] = "schedule"
    merged = queued + [{**row, "priority": None, "parent_id": None} for row in planned]
    merged.sort(key=lambda row: row["ready_in_s"])
    return merged[:limit]


def llm_routing(limit: int = 10, window_s: float = 86400.0) -> dict:
    """Routage LLM : dernières routes, refus déterministes et replis, sur la fenêtre récente."""
    since = time.time() - window_s
    by_status = {row["status"]: row["n"] for row in _rows(
        "SELECT status, COUNT(*) AS n FROM llm_calls WHERE ts >= ? GROUP BY status", (since,))}
    recent = _rows("SELECT ts, task, provider, model, resolved_provider, resolved_model, cost_class, "
                   "attempt, status, error, cost_usd, provider_cost_usd, justification FROM llm_calls WHERE ts >= ? "
                   "ORDER BY id DESC LIMIT ?", (since, limit))
    for row in recent:
        row["error"] = _first_line(row["error"], 160)
        reason = json.loads(row.pop("justification") or "{}")
        failures = [item["reason"] for item in reason.get("considered", [])
                    if item.get("reason", "").startswith(("echec", "sortie invalide"))]
        row["fallback"] = row["status"] == "ok" and bool(failures)
        row["route_reason"] = reason.get("explanation") or "; ".join(failures) or row["error"]
    fallbacks = sum(row["fallback"] for row in recent)
    return {"window_s": window_s, "calls": sum(by_status.values()), "by_status": by_status,
            "deterministic_refusals": by_status.get("request_too_large", 0),
            "fallbacks_observed": fallbacks, "recent": recent}


def cost() -> dict:
    running = _rows("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE run_id IN "
                    "(SELECT id FROM runs WHERE status='running')")
    return {"today_usd": round(journal.spent_today(), 6),
            "running_runs_usd": round(float(running[0]["c"]) if running else 0.0, 6),
            "total_usd": round(float(_rows("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls")[0]["c"]), 6)}


def snapshot(business: str | None = None, *, limit: int = 8, now: float | None = None) -> dict:
    """État complet du runtime, prêt à être affiché ou sérialisé."""
    now = now or time.time()
    runs = running_runs(limit)
    return {
        "generated_at": now,
        "business": business,
        "objectives": objectives(business, limit=limit),
        "running_tasks": running_tasks(limit),
        "running_runs": runs,
        # Le run courant est la racine de l'exécution la plus récente ; ses sous-runs restent listés.
        "current_run_id": (runs[0]["root_id"] or runs[0]["id"]) if runs else None,
        "last_progress": last_progress(limit),
        "waiting_human": waiting_human(limit),
        "failures": failures(limit),
        "next_work": next_work(limit),
        "llm_routing": llm_routing(limit),
        "cost": cost(),
        "task_status_counts": {row["status"]: row["n"] for row in _rows(
            "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")},
    }


def _line(prefix: str, text: str) -> str:
    return f"{prefix:<22} {text}"


def render(state: dict) -> str:
    lines = [f"OCTOPUS — état runtime {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state['generated_at']))}"]
    scope = state["business"] or "tous business"
    lines.append(_line("périmètre", scope))

    lines.append("Objectifs :")
    if not state["objectives"]:
        lines.append(_line("", "aucun objectif actif ou suspendu"))
    for item in state["objectives"]:
        criteria = f" | critères : {item['success_criteria']}" if item["success_criteria"] else ""
        lines.append(_line("", f"#{item['id']} [{item['status']}] {item['business']} — {item['statement'][:90]}{criteria}"))

    lines.append("Tâches en cours :")
    if not state["running_tasks"]:
        lines.append(_line("", "aucune (worker au repos)"))
    for item in state["running_tasks"]:
        lease = "" if item["lease_remaining_s"] is None else f" | bail {item['lease_remaining_s']} s"
        lines.append(_line("", f"#{item['id']} [{item['status']}] {item['business']}/{item['kind']} "
                               f"tentative {item['attempts']}/{item['max_attempts']} | run #{item['run_id']}{lease}"))

    lines.append("Runs actifs :")
    if not state["running_runs"]:
        lines.append(_line("", "aucun"))
    for item in state["running_runs"]:
        parent = f" <- run #{item['parent_id']}" if item["parent_id"] else " (racine)"
        remaining = (f" | {item['remaining_usd']} USD restants" if item["remaining_usd"] is not None else "")
        lines.append(_line("", f"#{item['id']} {item['business']}/{item['kind']} {item['label'] or ''} "
                               f"{item['age_s']} s | {item['cost_usd']} USD{remaining}{parent}"))

    lines.append("Dernière progression :")
    if not state["last_progress"]:
        lines.append(_line("", "aucun événement"))
    for item in state["last_progress"]:
        stamp = time.strftime("%H:%M:%S", time.localtime(item["ts"]))
        extra = f" tâche #{item['task_id']}" if item["task_id"] else ""
        lines.append(_line("", f"{stamp} {item['type']}{extra} ({item['business']})"))

    lines.append("Attente humaine :")
    if not state["waiting_human"]:
        lines.append(_line("", "aucune"))
    for item in state["waiting_human"]:
        expiry = "" if item["expires_in_s"] is None else f" | expire dans {item['expires_in_s']} s"
        lines.append(_line("", f"demande #{item['id']} (tâche #{item['task_id']} {item['kind']}) "
                               f"{item['question'][:120]}{expiry}"))

    lines.append("Échecs / timeouts :")
    if not state["failures"]:
        lines.append(_line("", "aucun"))
    for item in state["failures"]:
        stamp = time.strftime("%H:%M:%S", time.localtime(item["when"])) if item["when"] else "?"
        lines.append(_line("", f"{stamp} {item['source']} #{item['id']} [{item['status']}] "
                               f"{item['business']}/{item['kind']} : {item['error'] or '(sans détail)'}"))

    lines.append("Travail suivant :")
    if not state["next_work"]:
        lines.append(_line("", "rien de planifié"))
    for item in state["next_work"]:
        lines.append(_line("", f"[{item['source']}] {item['business']}/{item['kind']} prêt dans {item['ready_in_s']} s"))

    routing = state["llm_routing"]
    lines.append("Routage LLM :")
    lines.append(_line("", f"{routing['calls']} appels sur {int(routing['window_s'] / 3600)} h | "
                           f"{', '.join(f'{k}={v}' for k, v in sorted(routing['by_status'].items())) or 'aucun'}"))
    lines.append(_line("", f"refus déterministes (413) : {routing['deterministic_refusals']} | "
                           f"replis observés : {routing['fallbacks_observed']}"))
    for item in routing["recent"][:5]:
        lines.append(_line("", f"{time.strftime('%H:%M:%S', time.localtime(item['ts']))} {item['task']} "
                               f"{item['resolved_provider'] or item['provider']}/"
                               f"{item['resolved_model'] or item['model']} {item['cost_class']} "
                               f"{item['cost_usd']} USD -> {item['status']}"
                               + (f" : {item['route_reason']}" if item['route_reason'] else "")))

    lines.append("Coût cumulé :")
    lines.append(_line("", f"aujourd'hui {state['cost']['today_usd']} USD | runs actifs "
                           f"{state['cost']['running_runs_usd']} USD | total {state['cost']['total_usd']} USD"))
    counts = ", ".join(f"{k} {v}" for k, v in sorted(state["task_status_counts"].items())) or "aucune tâche"
    lines.append(_line("file de tâches", counts))
    return "\n".join(lines)


def text(business: str | None = None, *, limit: int = 8) -> str:
    return render(snapshot(business, limit=limit))
