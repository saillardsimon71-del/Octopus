"""Bounded unattended development canary for OCTOPUS.

The first version is deliberately documentation-only. Each ticket is executed by
`development.task` with Kilo, an exact path allowlist, no declarative fallback,
and a hard zero-cost model requirement. Successful task commits are fast-forwarded
onto one local night branch. Nothing is pushed or merged into main.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable

from . import dev_worker, paths, tasks, worker


class NightShiftError(RuntimeError):
    pass


MAX_TICKETS = 5
MAX_HOURS = 8.0
MAX_CONSECUTIVE_FAILURES = 2
DEFAULT_MAX_STEPS = 6
NIGHT_POLICY = "docs_only"
PROTECTED_DOCS = {
    "AGENTS.md",
    "docs/ACCEPTANCE_GATES.md",
}


def default_plan_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "night_shift.json"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode:
        raise NightShiftError((result.stderr or result.stdout or "git failed")[-2000:])
    return result.stdout.strip()


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise NightShiftError(f"chemin invalide: {value!r}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise NightShiftError(f"chemin invalide: {value!r}")
    return normalized


def _validate_ticket(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise NightShiftError(f"ticket #{index}: objet JSON attendu")
    goal = str(raw.get("goal") or "").strip()
    if not goal:
        raise NightShiftError(f"ticket #{index}: goal requis")

    allowed_raw = raw.get("allowed_paths")
    if not isinstance(allowed_raw, list) or not allowed_raw:
        raise NightShiftError(f"ticket #{index}: allowed_paths non vide requis")
    allowed_paths = []
    for value in allowed_raw:
        if not isinstance(value, str):
            raise NightShiftError(f"ticket #{index}: allowed_paths doit contenir des chaînes")
        path = _normalize_relative_path(value)
        if not path.lower().endswith(".md"):
            raise NightShiftError(
                f"ticket #{index}: night canary v1 est documentation-only; chemin refusé: {path}"
            )
        if path in PROTECTED_DOCS:
            raise NightShiftError(f"ticket #{index}: document de gouvernance protégé: {path}")
        if path not in allowed_paths:
            allowed_paths.append(path)

    targets_raw = raw.get("test_targets") or ["tests/test_dev_worker.py"]
    if not isinstance(targets_raw, list) or not targets_raw:
        raise NightShiftError(f"ticket #{index}: test_targets non vide requis")
    test_targets = []
    for value in targets_raw:
        if not isinstance(value, str):
            raise NightShiftError(f"ticket #{index}: test_targets doit contenir des chaînes")
        target = _normalize_relative_path(value.split("::", 1)[0])
        if not target.startswith("tests/") or not target.endswith(".py"):
            raise NightShiftError(f"ticket #{index}: cible pytest refusée: {value}")
        test_targets.append(value)

    max_steps = int(raw.get("max_steps", DEFAULT_MAX_STEPS))
    if not 1 <= max_steps <= 8:
        raise NightShiftError(f"ticket #{index}: max_steps doit être compris entre 1 et 8")

    return {
        "goal": goal,
        "allowed_paths": allowed_paths,
        "test_targets": test_targets,
        "max_steps": max_steps,
    }


def validate_plan(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise NightShiftError("plan JSON attendu")
    policy = str(raw.get("policy") or NIGHT_POLICY)
    if policy != NIGHT_POLICY:
        raise NightShiftError(f"politique night-shift non supportée: {policy}")
    tickets_raw = raw.get("tickets")
    if not isinstance(tickets_raw, list) or not tickets_raw:
        raise NightShiftError("tickets non vide requis")
    if len(tickets_raw) > MAX_TICKETS:
        raise NightShiftError(f"maximum {MAX_TICKETS} tickets par night shift")
    tickets = [_validate_ticket(item, index + 1) for index, item in enumerate(tickets_raw)]
    return {
        "name": str(raw.get("name") or "night-canary-v1"),
        "policy": policy,
        "tickets": tickets,
    }


def load_plan(path: Path | None = None) -> dict:
    source = path or default_plan_path()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NightShiftError(f"plan illisible: {source}: {exc}") from exc
    return validate_plan(raw)


def _active_development_tasks() -> list[dict]:
    rows = []
    for status in ("queued", "running", "waiting_human"):
        rows.extend(
            task for task in tasks.list_tasks(status=status, limit=200)
            if task["kind"] == "development.task"
        )
    return rows


def preflight(repository: Path) -> dict:
    repository = repository.resolve()
    if not repository.is_dir():
        raise NightShiftError(f"repository introuvable: {repository}")
    root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
    if root != repository:
        raise NightShiftError(f"lancer night-shift depuis la racine Git: {root}")
    if _git(repository, "status", "--porcelain"):
        raise NightShiftError("repository non propre; night-shift refuse de démarrer")
    if not dev_worker.KILO_MODEL.endswith(":free"):
        raise NightShiftError(f"modèle Kilo non attesté gratuit: {dev_worker.KILO_MODEL}")
    if shutil.which(dev_worker.KILO_COMMAND) is None:
        raise NightShiftError(f"CLI Kilo introuvable: {dev_worker.KILO_COMMAND}")
    active = _active_development_tasks()
    if active:
        ids = ", ".join(f"#{task['id']}:{task['status']}" for task in active)
        raise NightShiftError(f"development.task déjà actif/en attente: {ids}")
    scheduled = [
        item for item in tasks.schedules()
        if item["kind"] == "development.task" and item["enabled"]
    ]
    if scheduled:
        raise NightShiftError("planification development.task active: désactiver avant night-shift")
    return {
        "repository": str(repository),
        "base_head": _git(repository, "rev-parse", "HEAD"),
    }


def _create_night_worktree(repository: Path, run_id: str) -> tuple[Path, str]:
    branch = f"octopus/night-{run_id}"
    root = paths.data_dir() / "night-worktrees"
    root.mkdir(parents=True, exist_ok=True)
    target = (root / run_id).resolve()
    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(target), "HEAD"],
        cwd=str(repository),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise NightShiftError((result.stderr or result.stdout or "git worktree add failed")[-2000:])
    return target, branch


def _fast_forward(night_worktree: Path, commit: str) -> None:
    current = _git(night_worktree, "rev-parse", "HEAD")
    parent = _git(night_worktree, "rev-parse", f"{commit}^")
    if parent != current:
        raise NightShiftError(
            f"commit {commit[:12]} n'est pas enfant direct de la branche night-shift"
        )
    _git(night_worktree, "merge", "--ff-only", commit)
    if _git(night_worktree, "status", "--porcelain"):
        raise NightShiftError("branche night-shift non propre après fast-forward")


def _report_path(run_id: str) -> Path:
    root = paths.data_dir() / "night-shift-reports"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{run_id}.json"


def _write_report(report: dict) -> Path:
    target = _report_path(report["run_id"])
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return target


def run(
    repository: Path,
    plan: dict,
    *,
    max_tasks: int = MAX_TICKETS,
    max_hours: float = MAX_HOURS,
    max_failures: int = MAX_CONSECUTIVE_FAILURES,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    if not 1 <= max_tasks <= MAX_TICKETS:
        raise NightShiftError(f"max_tasks doit être compris entre 1 et {MAX_TICKETS}")
    if not 0 < max_hours <= MAX_HOURS:
        raise NightShiftError(f"max_hours doit être > 0 et <= {MAX_HOURS}")
    if not 1 <= max_failures <= MAX_CONSECUTIVE_FAILURES:
        raise NightShiftError(
            f"max_failures doit être compris entre 1 et {MAX_CONSECUTIVE_FAILURES}"
        )

    plan = validate_plan(plan)
    ready = preflight(repository)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    report = {
        "run_id": run_id,
        "plan": plan["name"],
        "policy": plan["policy"],
        "base_repository": ready["repository"],
        "base_head": ready["base_head"],
        "model": dev_worker.KILO_MODEL,
        "max_tasks": max_tasks,
        "max_hours": max_hours,
        "max_failures": max_failures,
        "started_at": time.time(),
        "status": "dry_run" if dry_run else "running",
        "night_branch": None,
        "night_worktree": None,
        "tickets": [],
    }
    if dry_run:
        report["tickets"] = plan["tickets"][:max_tasks]
        report["finished_at"] = time.time()
        return report

    worker.load_handlers()
    night_worktree, night_branch = _create_night_worktree(Path(ready["repository"]), run_id)
    report["night_branch"] = night_branch
    report["night_worktree"] = str(night_worktree)
    started = time.monotonic()
    consecutive_failures = 0
    stop_reason = "backlog_complete"

    for index, ticket in enumerate(plan["tickets"][:max_tasks], start=1):
        if time.monotonic() - started >= max_hours * 3600:
            stop_reason = "time_limit"
            break

        log(f"[night] ticket {index}/{min(len(plan['tickets']), max_tasks)}")
        tests = [
            [sys.executable, "-m", "pytest", "-q", target]
            for target in ticket["test_targets"]
        ]
        task_id = worker.enqueue(
            "octopus",
            "development.task",
            {
                "repository": str(night_worktree),
                "goal": ticket["goal"],
                "tests": tests,
                "max_steps": ticket["max_steps"],
                "backend": "kilo",
                "allowed_paths": ticket["allowed_paths"],
                "allow_declarative_fallback": False,
            },
            priority=10_000,
            idempotency_key=f"night:{run_id}:{index}",
        )
        result = worker.run_one(
            owner=f"night-{run_id}",
            kinds=["development.task"],
            log=log,
        )
        entry = {
            "index": index,
            "task_id": task_id,
            "goal": ticket["goal"],
            "allowed_paths": ticket["allowed_paths"],
            "result": result,
        }

        if result is None or result.get("id") != task_id:
            entry["status"] = "runner_error"
            report["tickets"].append(entry)
            stop_reason = "unexpected_task_claim"
            break

        entry["status"] = result["status"]
        report["tickets"].append(entry)
        if result["status"] == "done":
            output = result.get("output") or {}
            commit = str(output.get("commit") or "")
            if not commit:
                stop_reason = "missing_commit"
                break
            _fast_forward(night_worktree, commit)
            entry["night_head"] = _git(night_worktree, "rev-parse", "HEAD")
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= max_failures:
                stop_reason = "consecutive_failures"
                break

    report["status"] = stop_reason
    report["final_head"] = _git(night_worktree, "rev-parse", "HEAD")
    report["finished_at"] = time.time()
    report["elapsed_s"] = round(time.monotonic() - started, 3)
    target = _write_report(report)
    report["report_path"] = str(target)
    log(f"[night] terminé: {stop_reason}; rapport: {target}")
    return report
