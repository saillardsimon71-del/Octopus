"""Read-only view of the OCTOPUS journal for the Workbench."""
from __future__ import annotations

import json
import hashlib
import http.client
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from octopus import agnes, businesses, journal, paths


def _activity_rows(state: dict, declared: dict, business: str, costs: dict) -> list[dict]:
    """Projection du registre et du journal, sans deuxième ledger ni écriture."""
    out = []
    for identifier in state["businesses"]:
        if business != "all" and identifier != business:
            continue
        definition = declared.get(identifier)
        objectives = [row for row in state["objectives"] if row["business"] == identifier]
        work = [row for row in state["tasks"] if row["business"] == identifier]
        pending = sum(row["business"] == identifier and row["status"] == "pending"
                      and not row.get("technical_obsolete") for row in state["requests"])
        objective = next((row for row in objectives if row["status"] == "active"),
                         objectives[0] if objectives else None)
        status = ("Intervention attendue" if pending else "En cours" if any(
            row["status"] == "running" and (row.get("lease_until") or 0) > time.time() for row in work)
            else "En attente" if any(row["status"] == "queued" for row in work)
            else "En pause" if objective and objective["status"] == "paused"
            else "À reprendre" if objective and objective["status"] == "active"
            else "Historique disponible" if work or objectives else "Prête / non démarrée")
        cash = {}
        for row in state["ledger"]:
            if row["business"] != identifier:
                continue
            cur = cash.setdefault(row["currency"], {"in_observed": 0., "out_observed": 0.,
                                                    "in_unverified": 0., "out_unverified": 0.,
                                                    "customer_receipts": 0.})
            cur[f"{row['direction']}_{row['nature']}"] += row["amount"]
            if row["direction"] == "in" and row["nature"] == "observed" and row["category"] == "customer_receipt":
                cur["customer_receipts"] += row["amount"]
        for cur in cash.values():
            cur["net_observed"] = round(cur["in_observed"] - cur["out_observed"], 6)
        dates = [row["updated_at"] for row in work] + [row["ts"] for row in state["events"] if row["business"] == identifier]
        out.append({"id": identifier, "name": definition.name if definition else
                    "Discovery autonome (historique)" if identifier == "octopus" else identifier,
                    "description": definition.description if definition else "",
                    "declared": definition is not None, "state": status,
                    "last_activity": max(dates) if dates else None, "objective": objective,
                    "pending_human": pending, "llm_cost_usd": round(costs.get(identifier, 0.), 6),
                    "cash": cash, "budget_daily_usd": definition.budget_daily_usd if definition else None})
    return out


def _agnes_health(channels: list[dict]) -> str:
    active = next((channel for channel in channels if channel["kind"] == "agnes_video" and
                   channel["status"] == "active" and channel["access"] == "act" and channel["locator"]), None)
    if not active:
        return "Aucun canal autorisé"
    try:
        origin = urlsplit(agnes._base_url(active["locator"]))
        connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=2)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            if response.status != 200:
                return "Indisponible"
            payload = json.loads(response.read(4097))
            return "Disponible" if payload.get("ok") is True and payload.get("service") == "agnes-video-generator" else "Indisponible"
        finally:
            connection.close()
    except Exception:
        return "Indisponible"


def read_snapshot(business: str = "all", *, check_health: bool = False) -> dict:
    declared = businesses.discover()
    path = paths.journal_path()
    if not path.is_file():
        state = {"objectives": [], "tasks": [], "generations": [], "events": [],
                "requests": [], "channels": [], "allowances": [], "ledger": [],
                "businesses": sorted(set(declared) | {"octopus"}), "token_cost_usd": 0.0, "agnes_api_cost": None,
                "agnes_health": "Non sondé", "decisions": [], "evidence": [], "browser": [],
                "llm_calls": [], "pursuit_llm": None, "accounts": [], "mandates": []}
        state["activities"] = _activity_rows(state, declared, business, {})
        return state
    connection = (journal.readonly_connection(path) if os.environ.get("OCTOPUS_WORKBENCH_READONLY") == "1"
                  else sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    clause = "" if business == "all" else " WHERE business=?"
    args = () if business == "all" else (business,)

    present = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def rows(table: str, columns: str = "*", order: str = "id DESC", limit: int | None = None) -> list[dict]:
        if table not in present:
            return []
        sql = f"SELECT {columns} FROM {table}{clause} ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ?"
        return [dict(row) for row in connection.execute(sql, (*args, limit) if limit is not None else args)]

    try:
        known = set(declared) | {"octopus"}
        for table in ("tasks", "llm_calls", "strategy_objectives", "strategy_evidence", "economic_channels", "ledger_entries"):
            if table in present:
                known.update(row[0] for row in connection.execute(f"SELECT DISTINCT business FROM {table}") if row[0])
        mandate_rows = rows('operational_mandates')
        account_rows = []
        if 'resources' in present:
            for raw in connection.execute('SELECT * FROM resources ORDER BY label'):
                item = dict(raw)
                item['web_account'] = json.loads(item.get('web_account') or '{}')
                item['capabilities'] = json.loads(item.get('capabilities') or '[]')
                if item['kind'] == 'web_account' or item['web_account']:
                    account_rows.append(item)
        objectives = rows("strategy_objectives")
        tasks = rows("tasks")
        generations = rows("agnes_video_generations")
        events = rows("events", limit=100)
        requests = rows("human_requests")
        from octopus.supervisor import technical_pursuit_request
        by_task = {task["id"]: task for task in tasks}
        for request in requests:
            work = by_task.get(request["task_id"])
            request["technical_obsolete"] = False
            if not work or "task_steps" not in present or request["status"] != "pending":
                continue
            memo = connection.execute("SELECT value FROM task_steps WHERE task_id=? AND key='determination'",
                                      (work["id"],)).fetchone()
            request["technical_obsolete"] = technical_pursuit_request(
                {**work, "input": json.loads(work["input"] or "{}")}, request,
                json.loads(memo["value"]) if memo else {})
        channels = rows("economic_channels")
        allowances = rows("spend_allowances")
        ledger = rows("ledger_entries")
        evidence = {row["id"]: row for row in rows("strategy_evidence")}
        links = rows("strategy_links", "business, from_type, from_id, to_type, to_id, relation")
        decisions = {row["id"]: row for row in rows("strategy_decisions")}
        browser = []
        if "task_steps" in present:
            browser = [dict(row) for row in connection.execute(
                "SELECT s.task_id, s.value, t.status, t.lease_until, t.business, t.input FROM task_steps s "
                "JOIN tasks t ON t.id=s.task_id WHERE s.key='browser.observation'" +
                ("" if business == "all" else " AND t.business=?") + " ORDER BY s.ts DESC LIMIT 30", args)]
            for observation in browser:
                observation.update(json.loads(observation.pop("value")))
                work_input = json.loads(observation.pop("input") or "{}")
                objective = next((o for o in objectives if o["id"] == work_input.get("objective_id")), None)
                observation["goal"] = objective["statement"] if objective else work_input.get("goal", "")
                if observation["session"] == "open" and (observation["status"] != "running" or
                        (observation["lease_until"] or 0) < time.time()):
                    observation["session"] = "unknown"
        if "llm_calls" in present:
            costs = {row["business"]: float(row["cost"]) for row in connection.execute(
                "SELECT business, COALESCE(SUM(cost_usd),0) AS cost FROM llm_calls" + clause + " GROUP BY business", args)}
            token_sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls" + clause
            token_cost = round(float(connection.execute(token_sql, args).fetchone()[0]), 6)
            llm_calls = [dict(row) for row in connection.execute(
                "SELECT ts, task, provider, model, resolved_provider, resolved_model, cost_class, "
                "attempt, status, error, cost_usd, provider_cost_usd, justification FROM llm_calls" + clause +
                " ORDER BY id DESC LIMIT 20", args)]
            for call in llm_calls:
                reason = json.loads(call.pop("justification") or "{}")
                failures = [item["reason"] for item in reason.get("considered", [])
                            if item.get("reason", "").startswith(("echec", "sortie invalide"))]
                call["fallback"] = call["status"] == "ok" and bool(failures)
                call["route_reason"] = reason.get("explanation") or "; ".join(failures) or call["error"]
        else:
            costs = {}
            token_cost = 0.0
            llm_calls = []
        pursuit = next((task for task in tasks if task["kind"] == "supervisor.objective_work"
                        and json.loads(task["input"] or "{}").get("pursuit")), None)
        pursuit_llm = None
        if pursuit:
            spent = 0.0
            cost_roots = set()
            cursor = pursuit
            by_task_id = {task["id"]: task for task in tasks}
            while cursor:
                if cursor["run_id"] and "runs" in present and "llm_calls" in present:
                    root = connection.execute(
                        "WITH RECURSIVE a(id, parent_id) AS (SELECT id, parent_id FROM runs WHERE id=? "
                        "UNION ALL SELECT r.id, r.parent_id FROM runs r JOIN a ON r.id=a.parent_id) "
                        "SELECT id FROM a WHERE parent_id IS NULL", (cursor["run_id"],)).fetchone()
                    if root and root["id"] not in cost_roots:
                        spent += float(connection.execute(
                            "WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT r.id FROM runs r JOIN sub ON r.parent_id=sub.id) "
                            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id IN (SELECT id FROM sub)",
                            (root["id"],)).fetchone()[0])
                        cost_roots.add(root["id"])
                task_input = json.loads(cursor["input"] or "{}")
                cursor = by_task_id.get(task_input.get("previous_id"))
            pursuit_llm = {"spent_usd": round(spent, 6)}
    finally:
        connection.close()

    for generation in generations:
        proof = evidence.get(generation["evidence_id"])
        valid_proof = (proof and proof["business"] == generation["business"] and
                       proof["status"] == "active" and proof["nature"] == "observed" and
                       proof["source_type"] == "file" and proof["source_ref"] == generation["output_path"])
        generation["verified"] = False
        generation["verification_reason"] = "Preuve active ou fichier absent"
        if generation["status"] == "done" and valid_proof and generation["output_path"] and generation["sha256"]:
            result = agnes.verify_mp4(Path(generation["output_path"]))
            generation["verified"] = bool(result.get("verified") and result.get("sha256") == generation["sha256"])
            generation["verification_reason"] = ("MP4 décodable et SHA-256 conforme" if generation["verified"]
                                                  else result.get("reason", "SHA-256 différent"))

    by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        task["result"] = json.loads(task.get("output") or "{}") or {}
        task["technical_obsolete"] = any(r["task_id"] == task["id"] and r["technical_obsolete"] for r in requests) \
            and not any(r["task_id"] == task["id"] and r["status"] == "pending" and not r["technical_obsolete"] for r in requests)
    for objective in objectives:
        linked = [by_id[link["to_id"]] for link in links if link["business"] == objective["business"]
                  and link["from_id"] == objective["id"] and link["to_id"] in by_id
                  and link["from_type"] == "objective" and link["to_type"] == "task"
                  and link["relation"] == "executed_by"]
        objective["work_tasks"] = sorted(linked, key=lambda task: task["id"])
        objective["generations"] = [item for item in generations
                                    if item["business"] == objective["business"] and
                                    item["objective_id"] == objective["id"]]
        objective["authorized"] = any(
            link["business"] == objective["business"] and link["from_type"] == "decision" and
            link["to_type"] == "objective" and link["to_id"] == objective["id"] and
            link["relation"] == "authorizes" and
            (decision := decisions.get(link["from_id"])) is not None and
            decision["status"] == "approved" and decision["created_by"] == "human" and
            decision["decided_by"] == "human" and decision["decision"] == "agnes_generate_video" and
            decision["resulting_action"] == "prompt_sha256:" +
            hashlib.sha256(objective["statement"].encode("utf-8")).hexdigest()
            for link in links)

    for event in events:
        try:
            data = json.loads(event["data"] or "{}")
            event["data"] = data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            event["data"] = {}
    state = {"objectives": objectives, "tasks": tasks, "generations": generations,
            "events": events, "requests": requests, "channels": channels,
            "accounts": account_rows, "mandates": mandate_rows,
            "allowances": allowances, "ledger": ledger, "decisions": list(decisions.values()),
            "evidence": list(evidence.values()), "browser": browser,
            "businesses": sorted(known),
            "token_cost_usd": token_cost, "llm_calls": llm_calls, "pursuit_llm": pursuit_llm,
            "agnes_api_cost": None,
            "agnes_health": _agnes_health(channels) if check_health else "Non sondé"}
    state["activities"] = _activity_rows(state, declared, business, costs)
    return state


def mission_state(objective: dict) -> tuple[str, str]:
    if objective["status"] == "achieved":
        if objective["success_criteria"] == "kept_video_files>=1" and not any(
                g["verified"] for g in objective["generations"]):
            return "À examiner", "Objectif historiquement atteint, mais le MP4 n'est plus vérifiable."
        return "Objectif atteint", "Le Supervisor a clos l'objectif après vérification de la preuve."
    if objective["status"] == "paused":
        latest = (objective.get("work_tasks") or [{}])[-1]
        return "En pause", (latest.get("result") or {}).get("reason") or latest.get("error") or "Reprendre pour réexaminer l'état dans les limites existantes."
    tasks = objective["work_tasks"]
    if not tasks:
        return "Planification", "L'objectif est autorisé. Le travail n'est pas encore planifié."
    latest = tasks[-1]
    if latest["status"] in ("done", "done_degraded"):
        if any(g["verified"] for g in objective["generations"]):
            return "Vérification", "Le MP4 est vérifié. Le Supervisor doit encore clore l'objectif."
        if objective.get("success_criteria") == "kept_video_files>=1":
            return "Vérification", "La tâche est terminée. La preuve MP4 reste à confirmer."
        return "Travail terminé", "Résultats disponibles. Aucun résultat économique n'est déduit du statut de la tâche."
    if latest["status"] == "waiting_human":
        if latest.get("technical_obsolete"):
            return "Reprise technique disponible", "Ancienne source invalide. Cliquer sur Reprendre réconciliera la demande sans accorder de droits."
        return "Votre réponse attendue", latest.get("error") or "Une autorisation ou une réponse est requise."
    if latest["status"] in ("failed", "cancelled"):
        return "À examiner", latest.get("error") or "La tâche s'est interrompue."
    if latest["status"] == "running":
        return "En cours", "Le Worker traite la tâche."
    return "En attente du Worker", "La tâche est prête dans la file de travail."
