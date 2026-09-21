"""Bounded unattended development canaries for OCTOPUS.

The default policy remains documentation-only. python_canary keeps the tightly
bounded single-file path. product_ticket is an explicit supervised path for real
multi-file product work: Docker-isolated deterministic oracles, exact write scope,
strict preflight, bounded diff radius, no declarative fallback, and local commits only.
Nothing is pushed or merged into main.
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
DEFAULT_MAX_STEPS = 20
NIGHT_POLICY = "docs_only"
NIGHT_POLICIES = frozenset({"docs_only", "python_canary", "product_ticket"})
PROTECTED_DOCS = {
    "AGENTS.md",
    "docs/ACCEPTANCE_GATES.md",
}
PYTHON_CANARY_ORACLES = dev_worker.PYTHON_CANARY_ORACLES
PYTHON_CANARY_ALLOWED = frozenset(PYTHON_CANARY_ORACLES)


def stop_file() -> Path:
    return paths.data_dir() / "NIGHT_SHIFT_STOP"


def stop_requested() -> bool:
    return stop_file().exists()


def request_stop() -> Path:
    target = stop_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stop\n", encoding="utf-8")
    return target


def clear_stop() -> bool:
    target = stop_file()
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    return True


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


def _validate_ticket(raw: dict, index: int, policy: str = NIGHT_POLICY) -> dict:
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
        if policy == "docs_only":
            if not path.lower().endswith(".md"):
                raise NightShiftError(
                    f"ticket #{index}: policy docs_only refuse le chemin: {path}"
                )
            if path in PROTECTED_DOCS:
                raise NightShiftError(f"ticket #{index}: document de gouvernance protégé: {path}")
        elif policy == "python_canary":
            if not path.lower().endswith(".py"):
                raise NightShiftError(f"ticket #{index}: python_canary exige des fichiers .py: {path}")
            if path.startswith("tests/") or path.endswith("/conftest.py") or path == "conftest.py":
                raise NightShiftError(f"ticket #{index}: tests existants non modifiables: {path}")
            if path.endswith("/__init__.py") or path == "__init__.py":
                raise NightShiftError(f"ticket #{index}: __init__.py protégé en python_canary: {path}")
            if path not in PYTHON_CANARY_ALLOWED:
                raise NightShiftError(f"ticket #{index}: fichier hors allowlist python_canary: {path}")
        elif policy == "product_ticket":
            if path in dev_worker.OCTOPUS_PRODUCT_PROTECTED_PATHS:
                raise NightShiftError(f"ticket #{index}: noyau product_ticket protégé: {path}")
            if path.startswith("tests/") or path.endswith("/conftest.py") or path == "conftest.py":
                raise NightShiftError(f"ticket #{index}: oracles de test non modifiables: {path}")
            try:
                dev_worker._validate_allowed_paths([path])
            except dev_worker.DevWorkerError as exc:
                raise NightShiftError(f"ticket #{index}: périmètre product_ticket refusé: {exc}") from exc
        if path not in allowed_paths:
            allowed_paths.append(path)
    if policy == "python_canary" and len(allowed_paths) != 1:
        raise NightShiftError(f"ticket #{index}: python_canary exige exactement un fichier source")

    targets_raw = raw.get("test_targets")
    if policy == "product_ticket" and not targets_raw:
        raise NightShiftError(f"ticket #{index}: product_ticket exige test_targets explicite")
    targets_raw = targets_raw or ["tests/test_dev_worker.py"]
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

    if policy == "python_canary":
        required_targets = []
        for source_path in allowed_paths:
            for target in PYTHON_CANARY_ORACLES[source_path]:
                if target not in required_targets:
                    required_targets.append(target)
        if test_targets != required_targets:
            raise NightShiftError(
                f"ticket #{index}: oracle python_canary attendu {required_targets}, reçu {test_targets}"
            )

    max_steps = int(raw.get("max_steps", DEFAULT_MAX_STEPS))
    step_cap = 25 if policy == "docs_only" else 30
    if not 1 <= max_steps <= step_cap:
        raise NightShiftError(f"ticket #{index}: max_steps doit être compris entre 1 et {step_cap}")
    acceptance_raw = raw.get("acceptance_criteria") or []
    if not isinstance(acceptance_raw, list):
        raise NightShiftError(f"ticket #{index}: acceptance_criteria doit être une liste")
    acceptance_criteria = []
    for value in acceptance_raw:
        if not isinstance(value, str) or not value.strip():
            raise NightShiftError(f"ticket #{index}: acceptance_criteria invalide")
        acceptance_criteria.append(" ".join(value.split()))

    if policy in {"python_canary", "product_ticket"}:
        test_sandbox = "docker"
        requested_image = str(
            raw.get("test_sandbox_image") or dev_worker.DEFAULT_TEST_SANDBOX_IMAGE
        ).strip()
        if requested_image != dev_worker.DEFAULT_TEST_SANDBOX_IMAGE:
            raise NightShiftError(
                f"ticket #{index}: image sandbox {policy} imposée: {dev_worker.DEFAULT_TEST_SANDBOX_IMAGE}"
            )
        sandbox_image = dev_worker.DEFAULT_TEST_SANDBOX_IMAGE
        if policy == "python_canary":
            max_files_changed = int(raw.get("max_files_changed", 1))
            max_lines_added = int(raw.get("max_lines_added", 80))
            max_lines_deleted = int(raw.get("max_lines_deleted", 80))
            if max_files_changed != 1:
                raise NightShiftError(f"ticket #{index}: python_canary exige max_files_changed=1")
            if not 1 <= max_lines_added <= 80 or not 1 <= max_lines_deleted <= 80:
                raise NightShiftError(f"ticket #{index}: rayon de lignes python_canary trop large (maximum 80)")
        else:
            max_files_changed = int(raw.get("max_files_changed", len(allowed_paths)))
            max_lines_added = int(raw.get("max_lines_added", 2500))
            max_lines_deleted = int(raw.get("max_lines_deleted", 2500))
            if not 1 <= max_files_changed <= min(dev_worker.PRODUCT_TICKET_MAX_FILES, len(allowed_paths)):
                raise NightShiftError(
                    f"ticket #{index}: max_files_changed product_ticket doit être entre 1 et "
                    f"{min(dev_worker.PRODUCT_TICKET_MAX_FILES, len(allowed_paths))}"
                )
            if not 1 <= max_lines_added <= dev_worker.PRODUCT_TICKET_MAX_LINES:
                raise NightShiftError(
                    f"ticket #{index}: max_lines_added product_ticket maximum "
                    f"{dev_worker.PRODUCT_TICKET_MAX_LINES}"
                )
            if not 1 <= max_lines_deleted <= dev_worker.PRODUCT_TICKET_MAX_LINES:
                raise NightShiftError(
                    f"ticket #{index}: max_lines_deleted product_ticket maximum "
                    f"{dev_worker.PRODUCT_TICKET_MAX_LINES}"
                )
    else:
        test_sandbox = "host"
        sandbox_image = dev_worker.DEFAULT_TEST_SANDBOX_IMAGE
        max_files_changed = len(allowed_paths)
        max_lines_added = int(raw.get("max_lines_added", 500))
        max_lines_deleted = int(raw.get("max_lines_deleted", 500))

    return {
        "goal": goal,
        "allowed_paths": allowed_paths,
        "test_targets": test_targets,
        "max_steps": max_steps,
        "acceptance_criteria": acceptance_criteria,
        "noop_allowed": bool(raw.get("noop_allowed", False)),
        "test_sandbox": test_sandbox,
        "test_sandbox_image": sandbox_image,
        "max_files_changed": max_files_changed,
        "max_lines_added": max_lines_added,
        "max_lines_deleted": max_lines_deleted,
    }


def validate_plan(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise NightShiftError("plan JSON attendu")
    policy = str(raw.get("policy") or NIGHT_POLICY)
    if policy not in NIGHT_POLICIES:
        raise NightShiftError(f"politique night-shift non supportée: {policy}")
    tickets_raw = raw.get("tickets")
    if not isinstance(tickets_raw, list) or not tickets_raw:
        raise NightShiftError("tickets non vide requis")
    if len(tickets_raw) > MAX_TICKETS:
        raise NightShiftError(f"maximum {MAX_TICKETS} tickets par night shift")
    tickets = [_validate_ticket(item, index + 1, policy) for index, item in enumerate(tickets_raw)]
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


def _fast_forward(
        night_worktree: Path, commit: str, source_repo: Path | None = None,
        source_branch: str | None = None) -> None:
    current = _git(night_worktree, "rev-parse", "HEAD")
    if source_repo is not None:
        if not source_repo.is_dir() or not source_branch:
            raise NightShiftError("source de commit isolé invalide")
        _git(night_worktree, "fetch", "--no-tags", str(source_repo), source_branch)
        fetched = _git(night_worktree, "rev-parse", "FETCH_HEAD")
        if fetched != commit:
            raise NightShiftError(
                f"commit importé inattendu: attendu {commit[:12]}, obtenu {fetched[:12]}"
            )
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
    resolved_images = {}
    if plan["policy"] in {"python_canary", "product_ticket"}:
        try:
            for image in sorted({ticket["test_sandbox_image"] for ticket in plan["tickets"][:max_tasks]}):
                image_id, _ = dev_worker._docker_sandbox_probe(Path(ready["repository"]), image)
                resolved_images[image] = image_id
        except (dev_worker.DevWorkerError, OSError, subprocess.SubprocessError) as exc:
            raise NightShiftError(
                f"{plan['policy']} refuse de démarrer: probe Docker réel échoué: {exc}"
            ) from exc
        for ticket in plan["tickets"][:max_tasks]:
            ticket["test_sandbox_image"] = resolved_images[ticket["test_sandbox_image"]]
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    report = {
        "run_id": run_id,
        "plan": plan["name"],
        "policy": plan["policy"],
        "base_repository": ready["repository"],
        "base_head": ready["base_head"],
        "model": dev_worker.KILO_MODEL,
        "sandbox_images": resolved_images,
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

    try:
        for index, ticket in enumerate(plan["tickets"][:max_tasks], start=1):
            if time.monotonic() - started >= max_hours * 3600:
                stop_reason = "time_limit"
                break
            if stop_requested():
                stop_reason = "kill_switch"
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
                    "acceptance_criteria": ticket["acceptance_criteria"],
                    "noop_allowed": ticket["noop_allowed"],
                    "test_sandbox": ticket["test_sandbox"],
                    "test_sandbox_image": ticket["test_sandbox_image"],
                    "max_files_changed": ticket["max_files_changed"],
                    "max_lines_added": ticket["max_lines_added"],
                    "max_lines_deleted": ticket["max_lines_deleted"],
                    "strict_repository_preflight": plan["policy"] in {"python_canary", "product_ticket"},
                    "require_baseline_oracle": plan["policy"] in {"python_canary", "product_ticket"},
                    "python_canary_ast": plan["policy"] == "python_canary",
                    "allow_declarative_fallback": False,
                    "self_modification_policy": (
                        "product_ticket" if plan["policy"] == "product_ticket" else "python_canary"
                    ),
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
                if output.get("noop"):
                    entry["noop"] = True
                    entry["night_head"] = _git(night_worktree, "rev-parse", "HEAD")
                    consecutive_failures = 0
                    continue
                commit = str(output.get("commit") or "")
                if not commit:
                    stop_reason = "missing_commit"
                    break
                _fast_forward(
                    night_worktree,
                    commit,
                    Path(str(output.get("worktree") or "")),
                    str(output.get("branch") or ""),
                )
                entry["night_head"] = _git(night_worktree, "rev-parse", "HEAD")
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    stop_reason = "consecutive_failures"
                    break
    
        report["status"] = stop_reason
    except BaseException as exc:
        report["status"] = f"crash:{type(exc).__name__}"
        report["error"] = repr(exc)
        raise
    finally:
        try:
            report["final_head"] = _git(night_worktree, "rev-parse", "HEAD")
        except Exception as final_exc:
            report["final_head"] = None
            report["final_head_error"] = repr(final_exc)
        report["finished_at"] = time.time()
        report["elapsed_s"] = round(time.monotonic() - started, 3)
        target = _write_report(report)
        report["report_path"] = str(target)
        log(f"[night] terminé: {report['status']}; rapport: {target}")
    return report
