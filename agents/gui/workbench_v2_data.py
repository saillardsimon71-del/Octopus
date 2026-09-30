"""Read-only view of the OCTOPUS journal for the Workbench."""
from __future__ import annotations

import json
import hashlib
import http.client
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from octopus import agnes, paths


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
    path = paths.journal_path()
    if not path.is_file():
        return {"objectives": [], "tasks": [], "generations": [], "events": [],
                "requests": [], "channels": [], "allowances": [], "ledger": [],
                "businesses": [], "token_cost_usd": 0.0, "agnes_api_cost": None,
                "agnes_health": "Non sondé"}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
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
        objectives = rows("strategy_objectives")
        tasks = rows("tasks")
        generations = rows("agnes_video_generations")
        events = rows("events", limit=100)
        requests = rows("human_requests")
        channels = rows("economic_channels")
        allowances = rows("spend_allowances")
        ledger = rows("ledger_entries")
        evidence = {row["id"]: row for row in rows("strategy_evidence", "id, business, status, nature, source_type, source_ref")}
        links = rows("strategy_links", "business, from_type, from_id, to_type, to_id, relation")
        decisions = {row["id"]: row for row in rows("strategy_decisions")}
        if "llm_calls" in present:
            token_sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls" + clause
            token_cost = round(float(connection.execute(token_sql, args).fetchone()[0]), 6)
        else:
            token_cost = 0.0
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
    return {"objectives": objectives, "tasks": tasks, "generations": generations,
            "events": events, "requests": requests, "channels": channels,
            "allowances": allowances, "ledger": ledger,
            "businesses": sorted({item["business"] for group in
                                   (objectives, tasks, generations, channels, ledger) for item in group}),
            "token_cost_usd": token_cost, "agnes_api_cost": None,
            "agnes_health": _agnes_health(channels) if check_health else "Non sondé"}


def mission_state(objective: dict) -> tuple[str, str]:
    if objective["status"] == "achieved":
        if objective["success_criteria"] == "kept_video_files>=1" and not any(
                g["verified"] for g in objective["generations"]):
            return "À examiner", "Objectif historiquement atteint, mais le MP4 n'est plus vérifiable."
        return "Objectif atteint", "Le Supervisor a clos l'objectif après vérification de la preuve."
    if objective["status"] == "paused":
        return "En pause", "Une intervention ou une nouvelle décision est nécessaire."
    tasks = objective["work_tasks"]
    if not tasks:
        return "Planification", "L'objectif est autorisé. Le travail n'est pas encore planifié."
    latest = tasks[-1]
    if latest["status"] == "done":
        if any(g["verified"] for g in objective["generations"]):
            return "Vérification", "Le MP4 est vérifié. Le Supervisor doit encore clore l'objectif."
        return "Vérification", "La tâche est terminée. La preuve MP4 reste à confirmer."
    if latest["status"] == "waiting_human":
        return "Votre réponse attendue", latest.get("error") or "Une autorisation ou une réponse est requise."
    if latest["status"] in ("failed", "cancelled"):
        return "À examiner", latest.get("error") or "La tâche s'est interrompue."
    if latest["status"] == "running":
        return "En cours", "Le Worker traite la tâche."
    return "En attente du Worker", "La tâche est prête dans la file de travail."
