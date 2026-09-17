"""Rapport de couts : ce qui a ete appele, par qui, avec quel modele, pour combien et pourquoi."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from . import catalog, journal, pricing


def _money(value: float) -> str:
    return f"{value:.4f} $"


def build(days: int = 7) -> dict:
    since = time.time() - days * 86400
    calls = [dict(r) for r in journal.query(
        "SELECT c.*, r.kind AS run_kind FROM llm_calls c LEFT JOIN runs r ON r.id = c.root_run_id WHERE c.ts >= ?",
        (since,))]
    cat = catalog.load()
    out: dict = {"days": days, "calls": len(calls), "cost": sum(c["cost_usd"] for c in calls)}

    def group(key: Callable[[dict], str]) -> list[tuple[str, int, float]]:
        acc: dict[str, list] = defaultdict(lambda: [0, 0.0])
        for c in calls:
            acc[key(c)][0] += 1
            acc[key(c)][1] += c["cost_usd"]
        return sorted(((k, v[0], v[1]) for k, v in acc.items()), key=lambda x: -x[2])

    out["by_status"] = group(lambda c: c["status"])
    out["by_cost_class"] = group(lambda c: c["cost_class"])
    out["by_task"] = group(lambda c: c["task"])
    out["by_model"] = group(lambda c: c["model"])
    out["by_agent"] = group(lambda c: f"{c['business'] or '-'}/{c['agent'] or '-'}")
    out["by_run_kind"] = group(lambda c: c["run_kind"] or "hors run")
    paid_reasons: dict[str, list] = defaultdict(lambda: [0, 0.0, ""])
    sensitive_cloud = 0
    for c in calls:
        just = json.loads(c["justification"]) if c["justification"] else {}
        if c["cost_class"] == "paid" and c["status"] != "blocked":
            reason = just.get("paid_reason", "inconnu")
            paid_reasons[reason][0] += 1
            paid_reasons[reason][1] += c["cost_usd"]
            paid_reasons[reason][2] = just.get("explanation", "")
        if cat.task(c["task"]).get("privacy") == "sensitive" and c["cost_class"] != "local":
            sensitive_cloud += 1
    out["paid_reasons"] = sorted(((k, v[0], v[1], v[2]) for k, v in paid_reasons.items()), key=lambda x: -x[2])
    out["sensitive_to_cloud"] = sensitive_cloud
    out["top_runs"] = [dict(r) for r in journal.query(
        "SELECT r.id, r.business, r.kind, r.label, r.status, COALESCE(SUM(c.cost_usd), 0) AS cost, COUNT(c.id) AS n "
        "FROM runs r JOIN llm_calls c ON c.root_run_id = r.id WHERE r.parent_id IS NULL AND r.started_at >= ? "
        "GROUP BY r.id ORDER BY cost DESC LIMIT 5", (since,))]
    return out


def reprice_legacy(db_path: Path) -> dict:
    """Relit la table `costs` historique de Podalux (lecture seule) avec la grille du catalogue."""
    cat = catalog.load()
    by_api = {m["api_model"]: (mid, m) for mid, m in cat.raw["models"].items()}
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT ts, agent, task, model, prompt_tokens, completion_tokens, cost_usd FROM costs").fetchall()
    finally:
        conn.close()
    recorded = official = 0.0
    unknown = 0
    for r in rows:
        recorded += r["cost_usd"]
        entry = by_api.get(r["model"])
        if not entry:
            unknown += 1
            continue
        model = entry[1]
        peak = pricing.is_peak(r["ts"], cat.provider(model["provider"]).get("peak_utc_weekdays"))
        usage = pricing.Usage(prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"])
        official += pricing.call_cost(model.get("price"), usage, peak)
    return {"rows": len(rows), "recorded": recorded, "official_no_cache": official, "unknown_models": unknown}


def render(data: dict, legacy: dict | None = None) -> str:
    lines = [f"OCTOPUS - couts LLM sur {data['days']} jours : {data['calls']} appels, {_money(data['cost'])}"]

    def section(title: str, rows: list[tuple[str, int, float]]) -> None:
        lines.append("")
        lines.append(title)
        for key, n, cost in rows:
            lines.append(f"  {key:40} {n:6} appels  {_money(cost)}")

    section("Par statut", data["by_status"])
    section("Par classe de cout", data["by_cost_class"])
    section("Par tache", data["by_task"])
    section("Par modele", data["by_model"])
    section("Par activite/agent", data["by_agent"])
    section("Par type de run", data["by_run_kind"])
    lines.append("")
    lines.append("Pourquoi des appels payants ?")
    if not data["paid_reasons"]:
        lines.append("  aucun appel payant")
    for reason, n, cost, explanation in data["paid_reasons"]:
        lines.append(f"  {reason:28} {n:6} appels  {_money(cost)}  {explanation[:90]}")
    if any(r[0] == "legacy_pin" for r in data["paid_reasons"]):
        lines.append("  -> legacy_pin : modele impose par le code, jamais compare. Lancer le banc pour ces taches.")
    lines.append("")
    lines.append(f"Appels de taches sensibles envoyes hors de la machine : {data['sensitive_to_cloud']}")
    lines.append("")
    lines.append("Runs les plus couteux")
    for r in data["top_runs"]:
        lines.append(f"  #{r['id']:<5} {r['business']}/{r['kind']:12} {r['status']:8} {r['n']:4} appels  "
                     f"{_money(r['cost'])}  {(r['label'] or '')[:50]}")
    if legacy:
        lines.append("")
        lines.append(f"Table historique Podalux : {legacy['rows']} appels, enregistre {_money(legacy['recorded'])}, "
                     f"grille officielle sans cache {_money(legacy['official_no_cache'])}"
                     + (f", {legacy['unknown_models']} modeles inconnus" if legacy["unknown_models"] else ""))
    return "\n".join(lines)
