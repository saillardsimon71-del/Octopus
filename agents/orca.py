"""Pont optionnel vers le CLI Orca pour les tâches de développement.

Orca n'est pas une dépendance du runtime métier. Ce module fournit uniquement
un client subprocess sûr, sans shell libre, pour déléguer du travail de code à
Orca quand le CLI est installé.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import config


class OrcaError(RuntimeError):
    """Erreur d'intégration Orca."""


class OrcaUnavailable(OrcaError):
    """Le CLI Orca n'est pas installé ou n'est pas exécutable."""


class OrcaCommandError(OrcaError):
    """Le CLI Orca a refusé ou n'a pas terminé correctement la commande."""


LIVE = "live"
UNVERIFIABLE = "unverifiable"
EXITED = "exited"


def enabled() -> bool:
    """Active le pont uniquement quand l'utilisateur l'a explicitement demandé."""
    return os.environ.get("OCTOPUS_ORCA_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _cli_path() -> Path:
    configured = os.environ.get("OCTOPUS_ORCA_CLI", "orca").strip()
    if not configured:
        raise OrcaUnavailable("OCTOPUS_ORCA_CLI est vide")
    resolved = shutil.which(configured)
    if resolved:
        return Path(resolved)
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    raise OrcaUnavailable(f"CLI Orca introuvable : {configured}")


def _argv(args: list[str]) -> list[str]:
    """Construit une invocation sans shell global.

    Sur Windows, Orca peut être exposé par un shim .cmd/.bat ; seul ce shim
    passe par cmd.exe, et l'application/les arguments restent construits par
    le code plutôt que par concaténation d'une commande libre.
    """
    path = _cli_path()
    base = [str(path), *args]
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(base)]
    return base


def _decode_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {"raw": text}


def run(args: list[str], *, timeout_s: float = 30.0) -> Any:
    """Exécute une commande Orca et retourne son JSON (ou sa sortie brute)."""
    if not enabled():
        raise OrcaError("Pont Orca désactivé : définis OCTOPUS_ORCA_ENABLED=1 pour l'utiliser")
    argv = _argv(args)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(config.PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OrcaUnavailable(f"exécutable Orca introuvable : {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OrcaCommandError(f"Orca n'a pas répondu dans les {timeout_s:g}s") from exc
    except OSError as exc:
        raise OrcaCommandError(f"impossible de lancer Orca : {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise OrcaCommandError(f"Orca a échoué ({completed.returncode}) : {detail[:1200]}")
    return _decode_json(completed.stdout)


def status(*, timeout_s: float = 15.0) -> Any:
    return run(["status", "--json"], timeout_s=timeout_s)


def run_create(objective: str) -> Any:
    return run(["orchestration", "run-create", "--objective", objective, "--json"])


def task_create(spec: str) -> Any:
    return run(["orchestration", "task-create", "--spec", spec, "--json"])


def worker_start(
    task_id: str,
    *,
    agent: str,
    worktree: str = "current",
    model: str | None = None,
    effort: str | None = None,
) -> Any:
    args = [
        "orchestration",
        "worker-start",
        "--task",
        task_id,
        "--worktree",
        worktree,
        "--agent",
        agent,
        "--json",
    ]
    if model:
        args.extend(["--model", model])
    if effort:
        args.extend(["--effort", effort])
    return run(args)


def worker_list(*, include_remote: bool = True) -> Any:
    args = ["orchestration", "worker-list"]
    if include_remote:
        args.append("--include-remote")
    args.append("--json")
    return run(args)


def check(*, wait: bool = False, timeout_ms: int = 1000) -> Any:
    args = ["orchestration", "check", "--types", "worker_done,escalation,question"]
    if wait:
        args.extend(["--wait", "--timeout-ms", str(timeout_ms)])
    args.append("--json")
    return run(args, timeout_s=max(5.0, timeout_ms / 1000 + 5.0) if wait else 15.0)


def start_development_task(
    *,
    objective: str,
    spec: str,
    agent: str,
    worktree: str = "current",
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """Crée Run → Task → Worker en une opération d'intégration explicite."""
    created_run = run_create(objective)
    created_task = task_create(spec)
    task_id = _extract_task_id(created_task)
    if task_id is None:
        raise OrcaCommandError("Orca a créé la tâche mais aucun task_id exploitable n'a été renvoyé")
    started = worker_start(
        task_id,
        agent=agent,
        worktree=worktree,
        model=model,
        effort=effort,
    )
    return {"run": created_run, "task": created_task, "worker": started}


def _extract_task_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("task_id", "taskId"):
            if payload.get(key) is not None:
                return str(payload[key])
        for key in ("task", "dispatch"):
            nested = payload.get(key)
            found = _extract_task_id(nested)
            if found:
                return found
    return None


def normalize_liveness(value: Any) -> str:
    """Conserve le vocabulaire Orca utile à Octopus sans inventer un état."""
    value = str(value).strip().lower()
    if value == LIVE:
        return LIVE
    if value == EXITED:
        return EXITED
    return UNVERIFIABLE
