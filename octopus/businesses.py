"""Registre des activités : chaque dossier businesses/<id>/business.toml déclare une activité au moteur.

Champs lus : id, name, description, handlers (modules de tâches), budget_daily_usd (plafond LLM par
jour pour cette activité), kpis (indicateurs suivis). Les autres champs restent propres à l'activité.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import journal, paths

ENGINE_HANDLERS = ("octopus.builtin_handlers", "octopus.media.handlers", "octopus.dev_worker")
_cache: dict[str, tuple[tuple, dict]] = {}
_problems: dict[str, list[str]] = {}


@dataclass
class Business:
    id: str
    name: str
    description: str = ""
    handlers: list[str] = field(default_factory=list)
    budget_daily_usd: float | None = None
    kpis: list[str] = field(default_factory=list)
    path: Path | None = None
    raw: dict = field(default_factory=dict)


def root() -> Path:
    return paths.home() / "businesses"


def discover(base: Path | None = None) -> dict[str, Business]:
    base = base or root()
    files = sorted(base.glob("*/business.toml")) if base.exists() else []
    signature = tuple((str(f), f.stat().st_mtime) for f in files)
    cached = _cache.get(str(base))
    if cached and cached[0] == signature:
        return cached[1]
    found: dict[str, Business] = {}
    problems: list[str] = []
    for file in files:
        try:
            raw = tomllib.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            problems.append(f"{file} illisible : {exc}")  # une déclaration cassée n'arrête pas les autres
            continue
        business_id = raw.get("id") or file.parent.name
        if business_id in found:
            problems.append(f"activité {business_id!r} déclarée deux fois ({file} ignoré)")
            continue
        handlers = raw.get("handlers", [])
        found[business_id] = Business(
            id=business_id, name=raw.get("name", business_id), description=raw.get("description", ""),
            handlers=[handlers] if isinstance(handlers, str) else list(handlers),
            budget_daily_usd=raw.get("budget_daily_usd"), kpis=list(raw.get("kpis", [])), path=file, raw=raw)
    _problems[str(base)] = problems
    _cache[str(base)] = (signature, found)
    return found


def problems(base: Path | None = None) -> list[str]:
    """Déclarations ignorées lors de la dernière découverte (TOML invalide, identifiant en double)."""
    discover(base)
    return list(_problems.get(str(base or root()), []))


def get(business_id: str) -> Business | None:
    return discover().get(business_id)


def handler_modules() -> list[str]:
    modules = list(ENGINE_HANDLERS)
    for business in discover().values():
        for module in business.handlers:
            if module not in modules:
                modules.append(module)
    return modules


def overview(days: int = 7) -> list[dict]:
    """Tableau de bord par activité : tâches, coûts LLM, vidéos, demandes humaines, planifications."""
    since = time.time() - days * 86400
    day_start = time.time() - (time.time() % 86400)
    known = discover()
    ids = set(known)
    for row in journal.query("SELECT DISTINCT business FROM tasks WHERE created_at >= ? UNION "
                             "SELECT DISTINCT business FROM llm_calls WHERE ts >= ? AND business IS NOT NULL",
                             (since, since)):
        if row[0]:
            ids.add(row[0])
    out = []
    for business_id in sorted(ids):
        business = known.get(business_id)
        statuses = {r["status"]: r["n"] for r in journal.query(
            "SELECT status, COUNT(*) AS n FROM tasks WHERE business=? AND created_at >= ? GROUP BY status",
            (business_id, since))}
        cost = journal.query("SELECT COALESCE(SUM(cost_usd),0) AS c, COUNT(*) AS n FROM llm_calls "
                             "WHERE business=? AND ts >= ?", (business_id, since))[0]
        today = journal.query("SELECT COALESCE(SUM(cost_usd),0) AS c FROM llm_calls WHERE business=? AND ts >= ?",
                              (business_id, day_start))[0]["c"]
        media = {r["status"]: r["n"] for r in journal.query(
            "SELECT status, COUNT(*) AS n FROM media_generations WHERE business=? AND created_at >= ? GROUP BY status",
            (business_id, since))}
        pending = journal.query("SELECT COUNT(*) AS n FROM human_requests WHERE business=? AND status='pending'",
                                (business_id,))[0]["n"]
        schedules = journal.query("SELECT kind, interval_s, enabled FROM schedules WHERE business=?", (business_id,))
        out.append({
            "id": business_id, "name": business.name if business else business_id, "declared": business is not None,
            "tasks": statuses, "llm_calls": cost["n"], "llm_cost_usd": round(cost["c"], 6),
            "llm_cost_today_usd": round(today, 6),
            "budget_daily_usd": business.budget_daily_usd if business else None,
            "media": media, "pending_human": pending,
            "schedules": [dict(s) for s in schedules],
        })
    return out


def render(rows: list[dict], days: int) -> str:
    lines = [f"Activités ({days} derniers jours)"]
    lines += [f"  [!!] {p}" for p in problems()]
    for r in rows:
        tasks = ", ".join(f"{k} {v}" for k, v in sorted(r["tasks"].items())) or "aucune tâche"
        media = ", ".join(f"{k} {v}" for k, v in sorted(r["media"].items())) or "-"
        budget = "-" if r["budget_daily_usd"] is None else f"{r['llm_cost_today_usd']:.4f} / {r['budget_daily_usd']:.2f} $"
        lines.append(f"\n{r['name']} [{r['id']}]{'' if r['declared'] else ' (non déclarée)'}")
        lines.append(f"  tâches : {tasks}")
        lines.append(f"  LLM : {r['llm_calls']} appels, {r['llm_cost_usd']:.4f} $ ; aujourd'hui {budget}")
        lines.append(f"  vidéos : {media} ; demandes humaines en attente : {r['pending_human']}")
        if r["schedules"]:
            lines.append("  planifications : " + ", ".join(
                f"{s['kind']} /{s['interval_s']:g}s{'' if s['enabled'] else ' (off)'}" for s in r["schedules"]))
    return "\n".join(lines)
