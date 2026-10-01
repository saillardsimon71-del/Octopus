"""Inspect a real DataRoot through a copy; simulate resume without executing work.

    python scripts/check_pursuit_resume.py --data-root PATH [--objective ID]

No LLM, browser, worker, service or economic action is started. Source SQLite/WAL
files are copied by journal.readonly_connection, never opened for writing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from octopus import journal, supervisor, tasks  # noqa: E402
from agents.agent_browser import redact  # noqa: E402
from agents.gui.workbench_v2_data import read_snapshot  # noqa: E402


def fingerprints(path: Path) -> dict:
    files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    hashes = {}
    for item in files:
        if item.is_file():
            with item.open("rb") as stream:
                hashes[item.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


def check_resume(data_root: Path, objective_id: int | None = None) -> dict:
    source = data_root.resolve() / "data" / "octopus.db"
    if not source.is_file():
        raise FileNotFoundError(f"Journal absent : {source}")
    before_hashes = fingerprints(source)
    saved_env = {key: os.environ.get(key) for key in (
        "OCTOPUS_HOME", "OCTOPUS_DB", "OCTOPUS_WORKBENCH_READONLY")}
    try:
        with tempfile.TemporaryDirectory(prefix="octopus-resume-check-") as folder:
            target = Path(folder) / "data" / "octopus.db"
            target.parent.mkdir()
            memory = journal.readonly_connection(source)
            copy = sqlite3.connect(target)
            try:
                memory.backup(copy)
            finally:
                memory.close()
                copy.close()
            os.environ["OCTOPUS_HOME"] = folder
            os.environ["OCTOPUS_DB"] = str(target)
            os.environ.pop("OCTOPUS_WORKBENCH_READONLY", None)
            connection = journal.connect()
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()
            snapshot = read_snapshot()
            candidates = [o for o in snapshot["objectives"]
                          if o["business"] == supervisor.DEFAULT_BUSINESS
                          and o["success_criteria"] == supervisor.PURSUIT_CRITERION
                          and o["status"] in {"draft", "active", "paused"}
                          and (o["id"] == objective_id if objective_id else o["created_by"] == "octopus")]
            objective = candidates[0] if candidates else None
            if objective_id and not objective:
                raise ValueError(f"Objectif #{objective_id} non reprenable")
            requests_before = [{"id": r["id"], "task_id": r["task_id"], "key": r["key"],
                                "technical_obsolete": r["technical_obsolete"], "question": redact(r["question"])}
                               for r in snapshot["requests"] if r["status"] == "pending"]
            resumed_id = supervisor.start_pursuit(objective_id=objective["id"] if objective else None)
            after = read_snapshot()
            remaining = [{"id": r["id"], "task_id": r["task_id"], "key": r["key"], "question": redact(r["question"])}
                         for r in after["requests"] if r["status"] == "pending"]
            work = supervisor.work_tasks(supervisor.DEFAULT_BUSINESS, resumed_id)
            output = {
                "integrity": integrity, "source_read_only": True, "real_work_executed": False,
                "objective_id": resumed_id, "existing_objective_reused": objective is not None,
                "objective_status_before": objective["status"] if objective else None,
                "objective_summary": redact(objective["summary"]) if objective else "Objectif neutre créé sur la copie",
                "pending_before": requests_before, "pending_after_copy_resume": remaining,
                "cancelled_technical_request_ids": [r["id"] for r in requests_before
                                                    if r["id"] not in {item["id"] for item in remaining}],
                "next_work": [{"id": t["id"], "status": t["status"], "round": t["input"].get("round"),
                               "budget_usd": t["budget_usd"]} for t in work if t["status"] in tasks.ACTIVE],
                "evidence_preserved": snapshot["evidence"] == after["evidence"],
                "llm_cost_preserved": snapshot["token_cost_usd"] == after["token_cost_usd"],
                "known_llm_cost_usd": snapshot["token_cost_usd"],
                "observations_count": len(snapshot["browser"]),
                "browser_sessions": [{"task_id": o["task_id"], "session": o["session"], "url": redact(o.get("url") or "")}
                                     for o in snapshot["browser"]],
                "last_decisions": [{"id": d["id"], "decision": d["decision"], "rationale": redact(d["rationale"] or "")}
                                   for d in snapshot["decisions"][:5]],
                "local_host_mentions": sum("api.octopus.local" in json.dumps(item, ensure_ascii=False)
                                           for group in (snapshot["decisions"], snapshot["tasks"], snapshot["requests"])
                                           for item in group),
            }
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    output["source_hashes_unchanged"] = before_hashes == fingerprints(source)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--objective", type=int)
    args = parser.parse_args()
    report = check_resume(args.data_root, args.objective)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["integrity"] == "ok" and report["source_hashes_unchanged"] \
        and report["evidence_preserved"] and report["llm_cost_preserved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
