"""DevTask minimal: worktree isolé, outils bornés, tests déterministes, commit local."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
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

DEV_ACTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read one UTF-8 file inside the task worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search for a fixed string inside the task worktree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": ["string", "null"]},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "Apply a UTF-8 unified diff inside the task worktree.",
            "parameters": {
                "type": "object",
                "properties": {"patch": {"type": "string"}},
                "required": ["patch"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test",
            "description": "Run the pre-approved deterministic pytest commands.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit",
            "description": "Commit task worktree changes after tests pass.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
]
DEV_HISTORY_CHARS = 10_000
_RETRY_DELAY_RE = re.compile(r"try again in ([0-9]+(?:\.[0-9]+)?)\s*(?:s|seconds?)\b", re.IGNORECASE)


def _run(args: list[str], cwd: Path, *, input_text: str | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd), input=input_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


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


def _rate_limit_delay(exc: Exception) -> float | None:
    if type(exc).__name__ != "RateLimitError":
        return None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) if response is not None else {}
    raw = headers.get("retry-after") if headers is not None else None
    if raw is None:
        match = _RETRY_DELAY_RE.search(str(exc))
        raw = match.group(1) if match else None
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        return None
    return delay if 0 < delay <= 60 else None


def _malformed_tool_call(exc: Exception) -> bool:
    return type(exc).__name__ == "BadRequestError" and "Failed to parse tool call arguments as JSON" in str(exc)


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
        elif line.startswith("*** Update File: "):
            relative = line.removeprefix("*** Update File: ").strip()
            resolve_path(worktree, relative)
            targets.append(relative)
    if not targets:
        raise DevWorkerError("patch sans chemin de fichier")


def _context_patch_sections(patch: str) -> list[tuple[str, list[str]]]:
    lines = patch.splitlines()
    sections = []
    if lines and lines[0] == "*** Begin Patch":
        if lines[-1:] != ["*** End Patch"]:
            raise DevWorkerError("patch encapsulé sans fin")
        index = 1
        while index < len(lines) - 1:
            marker = lines[index]
            if not marker.startswith("*** Update File: "):
                raise DevWorkerError(f"opération de patch non prise en charge: {marker}")
            relative = marker.removeprefix("*** Update File: ").strip()
            index += 1
            body = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                body.append(lines[index])
                index += 1
            sections.append((relative, body))
        return sections

    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise DevWorkerError("en-têtes ---/+++ incomplets")
        raw = lines[index + 1][4:].split("\t", 1)[0]
        if raw == "/dev/null":
            raise DevWorkerError("création ou suppression de fichier interdite dans ce format")
        relative = raw[2:] if raw.startswith(("a/", "b/")) else raw
        index += 2
        body = []
        while index < len(lines) and not lines[index].startswith(("diff --git ", "--- ")):
            body.append(lines[index])
            index += 1
        sections.append((relative, body))
    if not sections:
        raise DevWorkerError("format de patch contextuel invalide")
    return sections


def _apply_context_patch(worktree: Path, patch: str) -> None:
    prepared: dict[Path, tuple[list[str], str, bool]] = {}
    for relative, body in _context_patch_sections(patch):
        path = resolve_path(worktree, relative)
        if not path.is_file() or path.stat().st_size > 100_000:
            raise DevWorkerError(f"fichier absent ou trop grand: {relative}")
        if path not in prepared:
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DevWorkerError(f"fichier non UTF-8: {relative}") from exc
            newline = "\r\n" if "\r\n" in text else "\n"
            normalized = text.replace("\r\n", "\n")
            prepared[path] = (normalized.splitlines(), newline, normalized.endswith("\n"))
        source, newline, final_newline = prepared[path]
        hunks: list[list[str]] = []
        current: list[str] | None = None
        for line in body:
            if line.startswith("@@"):
                if current is not None:
                    hunks.append(current)
                current = []
            elif current is not None:
                current.append(line)
            elif line:
                raise DevWorkerError(f"contenu avant le premier hunk: {relative}")
        if current is not None:
            hunks.append(current)
        if not hunks:
            raise DevWorkerError(f"patch sans hunk: {relative}")
        for hunk in hunks:
            old, new = [], []
            for line in hunk:
                if line in {"\\ No newline at end of file", "*** End Patch"}:
                    continue
                if not line or line[0] not in {" ", "+", "-"}:
                    new.append(line)
                    continue
                if line[0] in {" ", "-"}:
                    old.append(line[1:])
                if line[0] in {" ", "+"}:
                    new.append(line[1:])
            if not old:
                raise DevWorkerError(f"insertion sans contexte interdite: {relative}")
            matches = [i for i in range(len(source) - len(old) + 1) if source[i:i + len(old)] == old]
            if len(matches) != 1:
                raise DevWorkerError(f"contexte non unique ({len(matches)} correspondances): {relative}")
            start = matches[0]
            source = source[:start] + new + source[start + len(old):]
        prepared[path] = (source, newline, final_newline)

    for path, (lines, newline, final_newline) in prepared.items():
        text = newline.join(lines) + (newline if final_newline else "")
        path.write_bytes(text.encode("utf-8"))


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
            _apply_context_patch(worktree, patch)
        else:
            applied = _run(["git", "apply", "--whitespace=nowarn", "-"], worktree, input_text=patch)
            if applied.returncode:
                raise DevWorkerError((applied.stderr or applied.stdout)[-2000:])
        return _git(worktree, "diff", "--")[-12000:], False, None
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
        "You are DevWorker. Choose the next useful action through the structured response mechanism provided. "
        "If declarative action tools are available, call exactly one; they only represent the response and do not "
        "execute anything provider-side. Return an action matching this schema exactly: "
        f"{schema_json}. "
        "Inspect before modifying. Return one action per response. Never request shell, push, PR, or agent. "
        "Make the smallest change needed and never reformat unrelated lines. "
        "For patch actions, patch must be a UTF-8 unified diff with ---/+++ paths accepted by git apply; "
        "never use Begin Patch or SEARCH/REPLACE markers. "
        "Commit only after tests pass."
    )
    for _ in range(max_steps):
        ctx.check_cancel()
        state = _git(worktree, "status", "--short") or "clean"
        history = []
        remaining = DEV_HISTORY_CHARS
        for entry in reversed(transcript[-8:]):
            if remaining <= 0:
                break
            result = entry["result"][-remaining:]
            history.append({"action": entry["action"], "result": result})
            remaining -= len(result)
        history.reverse()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "goal": goal, "worktree_status": state, "tests": tests, "history": history,
            }, ensure_ascii=False)},
        ]
        completion_messages = messages
        rate_limit_retried = False
        structured_retried = False
        while True:
            try:
                completion = llm.complete(
                    "development.step", completion_messages, agent="DEVWORKER", business=ctx.business,
                    profile="zero_cost", json_schema=DEV_ACTION_SCHEMA, tool_schemas=DEV_ACTION_TOOLS,
                    max_tokens=1600, validate=_parse_action,
                )
                break
            except Exception as exc:
                delay = _rate_limit_delay(exc)
                if not rate_limit_retried and delay is not None:
                    rate_limit_retried = True
                    ctx.emit("development.rate_limited", {"retry_after_s": delay})
                    time.sleep(delay)
                    continue
                if not structured_retried and _malformed_tool_call(exc):
                    structured_retried = True
                    ctx.emit("development.structured_retry", {})
                    completion_messages = [*messages, {
                        "role": "user",
                        "content": "The previous tool arguments were rejected. Return exactly one declarative "
                                   "tool call with valid JSON; escape every newline and quote inside strings.",
                    }]
                    continue
                raise
        action = completion.data if completion.data is not None else _parse_action(completion.text)
        tool_failed = False
        try:
            output, tests_passed, commit = _tool(action, worktree, tests, tests_passed)
        except (DevWorkerError, OSError, subprocess.SubprocessError) as exc:
            output, commit = f"outil refusé/échoué: {type(exc).__name__}: {exc}", None
            tool_failed = True
        if action["action"] == "patch" and not tool_failed:
            transcript = [entry for entry in transcript if entry["action"] != "read"]
        transcript.append({"action": action["action"], "result": output[-12000:]})
        ctx.emit("development.tool", {"action": action["action"], "ok": not output.startswith("outil refusé")})
        if commit:
            result = {"commit": commit, "branch": branch, "worktree": str(worktree), "tests": tests}
            ctx.emit("development.committed", result)
            return result
    raise DevWorkerError(f"DevTask sans commit après {max_steps} étapes; worktree conservé: {worktree}")
