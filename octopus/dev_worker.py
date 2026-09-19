"""DevTask minimal: worktree isolé, outils bornés, tests déterministes, commit local."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from . import llm, paths
from .worker import handler


class DevWorkerError(RuntimeError):
    retryable = False


DEV_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["read", "search", "patch", "test", "commit"]},
        "path": {"type": ["string", "null"]},
        "query": {"type": ["string", "null"]},
        "patch": {"type": ["string", "null"]},
        "message": {"type": ["string", "null"]},
    },
    "required": ["action", "path", "query", "patch", "message"],
    "additionalProperties": False,
}


def _run(args: list[str], cwd: Path, *, input_text: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), input=input_text, capture_output=True, text=True, timeout=timeout)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = _run(["git", *args], repo)
    if check and result.returncode:
        raise DevWorkerError((result.stderr or result.stdout or "git failed")[-2000:])
    return result.stdout.strip()


def resolve_path(worktree: Path, relative: str) -> Path:
    root = worktree.resolve()
    candidate = (root / str(relative)).resolve()
    if candidate != root and root not in candidate.parents:
        raise DevWorkerError(f"chemin hors worktree: {relative}")
    return candidate


def validate_test_commands(commands) -> list[list[str]]:
    if not isinstance(commands, list) or not commands:
        raise DevWorkerError("tests doit contenir au moins une commande pytest")
    allowed_flags = {"-q", "-x", "--disable-warnings", "--maxfail=1"}
    out = []
    python_names = {"python", "python3", "python.exe", Path(sys.executable).name.lower()}
    for raw in commands:
        if not isinstance(raw, list) or not all(isinstance(part, str) and part for part in raw):
            raise DevWorkerError("chaque test doit être une liste d'arguments")
        command = list(raw)
        if len(command) < 4 or Path(command[0]).name.lower() not in python_names or command[1:3] != ["-m", "pytest"]:
            raise DevWorkerError("seules les commandes python -m pytest sont autorisées")
        for arg in command[3:]:
            if arg.startswith("-") and arg not in allowed_flags:
                raise DevWorkerError(f"option pytest non autorisée: {arg}")
            if not arg.startswith("-") and Path(arg.split("::", 1)[0]).is_absolute():
                raise DevWorkerError(f"chemin de test absolu interdit: {arg}")
            if not arg.startswith("-") and ".." in Path(arg.split("::", 1)[0]).parts:
                raise DevWorkerError(f"chemin de test hors worktree: {arg}")
        out.append(command)
    return out


def _parse_action(text: str) -> dict:
    value = llm.parse_json(text)
    fields = set(DEV_ACTION_SCHEMA["properties"])
    unexpected = sorted(set(value) - fields)
    if unexpected:
        raise ValueError(f"champs inattendus: {unexpected}")
    for name in fields:
        value.setdefault(name, None)
    action = value.get("action")
    if action not in {"read", "search", "patch", "test", "commit"}:
        raise ValueError("action attendue: read, search, patch, test ou commit")
    for name in fields - {"action"}:
        if value[name] is not None and not isinstance(value[name], str):
            raise ValueError(f"champ {name} attendu: string ou null")
    required = {"read": "path", "search": "query", "patch": "patch", "commit": "message"}
    field = required.get(action)
    if field and not isinstance(value.get(field), str):
        raise ValueError(f"champ {field} requis")
    return value


def _check_patch_paths(worktree: Path, patch: str) -> None:
    targets = []
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")):
            raw = line[4:].split("\t", 1)[0]
            if raw == "/dev/null":
                continue
            relative = raw[2:] if raw.startswith(("a/", "b/")) else raw
            resolve_path(worktree, relative)
            targets.append(relative)
    if not targets:
        raise DevWorkerError("patch sans chemin de fichier")


def _create_worktree(repository: Path, task_id: int) -> tuple[Path, str]:
    root_text = _git(repository, "rev-parse", "--show-toplevel")
    source = Path(root_text).resolve()
    suffix = uuid.uuid4().hex[:8]
    branch = f"codex/devtask-{task_id}-{suffix}"
    worktree_root = Path(os.environ.get("OCTOPUS_DEV_WORKTREE_ROOT", "").strip() or
                         paths.data_dir() / "dev-worktrees")
    worktree_root.mkdir(parents=True, exist_ok=True)
    target = (worktree_root / f"task-{task_id}-{suffix}").resolve()
    result = _run(["git", "worktree", "add", "-b", branch, str(target), "HEAD"], source)
    if result.returncode:
        raise DevWorkerError((result.stderr or result.stdout)[-2000:])
    return target, branch


def _tool(action: dict, worktree: Path, tests: list[list[str]], tests_passed: bool) -> tuple[str, bool, str | None]:
    name = action["action"]
    if name == "read":
        path = resolve_path(worktree, action["path"])
        if not path.is_file() or path.stat().st_size > 100_000:
            raise DevWorkerError(f"fichier absent ou trop grand: {action['path']}")
        return path.read_text(encoding="utf-8"), tests_passed, None
    if name == "search":
        target = resolve_path(worktree, action.get("path") or ".")
        result = _run(["rg", "-n", "--fixed-strings", "--", action["query"], str(target)], worktree)
        if result.returncode not in {0, 1}:
            raise DevWorkerError((result.stderr or "rg failed")[-2000:])
        return (result.stdout or "aucun résultat")[-12000:], tests_passed, None
    if name == "patch":
        patch = action["patch"]
        _check_patch_paths(worktree, patch)
        check = _run(["git", "apply", "--check", "--whitespace=nowarn", "-"], worktree, input_text=patch)
        if check.returncode:
            raise DevWorkerError((check.stderr or check.stdout)[-2000:])
        applied = _run(["git", "apply", "--whitespace=nowarn", "-"], worktree, input_text=patch)
        if applied.returncode:
            raise DevWorkerError((applied.stderr or applied.stdout)[-2000:])
        return _git(worktree, "diff", "--stat"), False, None
    if name == "test":
        outputs = []
        passed = True
        for command in tests:
            result = _run(command, worktree)
            outputs.append(f"$ {' '.join(command)}\n{result.stdout}{result.stderr}"[-12000:])
            if result.returncode:
                passed = False
                break
        return "\n".join(outputs), passed, None
    if not tests_passed:
        return "commit refusé: les tests déterministes ne sont pas verts depuis le dernier patch", False, None
    message = action["message"].strip()
    if not message or "\n" in message or len(message) > 120:
        raise DevWorkerError("message de commit invalide")
    _git(worktree, "add", "-A")
    if _run(["git", "diff", "--cached", "--quiet"], worktree).returncode == 0:
        raise DevWorkerError("aucune modification à committer")
    _git(worktree, "commit", "-m", message)
    commit = _git(worktree, "rev-parse", "HEAD")
    return commit, True, commit


@handler("development.task", resource="development", max_attempts=1)
def development_task(ctx):
    goal = str(ctx.input.get("goal") or "").strip()
    if not goal:
        raise DevWorkerError("goal requis")
    repository = Path(str(ctx.input.get("repository") or "")).resolve()
    if not repository.is_dir():
        raise DevWorkerError("repository introuvable")
    tests = validate_test_commands(ctx.input.get("tests"))
    max_steps = int(ctx.input.get("max_steps", 12))
    if not 1 <= max_steps <= 20:
        raise DevWorkerError("max_steps doit être compris entre 1 et 20")
    worktree, branch = _create_worktree(repository, ctx.id)
    transcript = []
    tests_passed = False
    schema_json = json.dumps(DEV_ACTION_SCHEMA, separators=(",", ":"))
    system = (
        "You are DevWorker. Do not call tools or emit tool calls. Choose the next useful action and respond only "
        f"with one JSON object matching this schema exactly: {schema_json}. "
        "Inspect before modifying. Return one action per response. Never request shell, push, PR, or agent. "
        "For patch actions, patch must be a UTF-8 unified diff with ---/+++ paths accepted by git apply; "
        "never use Begin Patch or SEARCH/REPLACE markers. "
        "Commit only after tests pass."
    )
    for _ in range(max_steps):
        ctx.check_cancel()
        state = _git(worktree, "status", "--short") or "clean"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "goal": goal, "worktree_status": state, "tests": tests, "history": transcript[-8:],
            }, ensure_ascii=False)},
        ]
        completion = llm.complete(
            "development.step", messages, agent="DEVWORKER", business=ctx.business,
            profile="zero_cost", json_schema=DEV_ACTION_SCHEMA, max_tokens=1600, validate=_parse_action,
        )
        action = completion.data if completion.data is not None else _parse_action(completion.text)
        try:
            output, tests_passed, commit = _tool(action, worktree, tests, tests_passed)
        except (DevWorkerError, OSError, subprocess.SubprocessError) as exc:
            output, commit = f"outil refusé/échoué: {type(exc).__name__}: {exc}", None
        transcript.append({"action": action["action"], "result": output[-12000:]})
        ctx.emit("development.tool", {"action": action["action"], "ok": not output.startswith("outil refusé")})
        if commit:
            result = {"commit": commit, "branch": branch, "worktree": str(worktree), "tests": tests}
            ctx.emit("development.committed", result)
            return result
    raise DevWorkerError(f"DevTask sans commit après {max_steps} étapes; worktree conservé: {worktree}")
