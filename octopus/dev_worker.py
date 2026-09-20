"""DevTask minimal: worktree isolé, outils bornés, tests déterministes, commit local."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import llm, paths
from .worker import handler


class DevWorkerError(RuntimeError):
    retryable = False


class KiloRepairableError(DevWorkerError):
    """Failure that a later Kilo pass may safely repair inside the same worktree."""

    def __init__(self, kind: str, feedback: str):
        self.kind = kind
        self.feedback = feedback
        super().__init__(feedback)


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
DEV_INSPECTION_LIMIT = 6
DEV_MIN_LLM_INTERVAL_S = 13.0
KILO_MODEL = "kilo/stepfun/step-3.7-flash:free"
KILO_AGENT = "octopus-devworker"
KILO_COMMAND = "kilo.cmd" if os.name == "nt" else "kilo"
KILO_TIMEOUT_S = 600
KILO_TEST_FEEDBACK_CHARS = 4000
KILO_MAX_PASSES = 3
_RETRY_DELAY_RE = re.compile(r"try again in ([0-9]+(?:\.[0-9]+)?)\s*(?:s|seconds?)\b", re.IGNORECASE)

_KILO_SENSITIVE_PATTERNS = (
    ".env", ".env.*", "**/.env", "**/.env.*", "**/*.pem", "**/*.key",
    "**/credentials*", "**/*credential*", "**/secrets*", "**/*secret*",
    ".netrc", "**/.netrc", ".npmrc", "**/.npmrc", ".pypirc", "**/.pypirc",
    ".git", ".git/**", "**/.git", "**/.git/**",
    ".kilo/**", "**/.kilo/**", ".kilocode/**", "**/.kilocode/**",
    "kilo.json", "kilo.jsonc", "opencode.json", "opencode.jsonc",
    "**/kilo.json", "**/kilo.jsonc", "**/opencode.json", "**/opencode.jsonc",
)
_KILO_PROTECTED_PATTERNS = (
    ".git/**", "**/.git/**", ".kilo/**", "**/.kilo/**", ".kilocode/**", "**/.kilocode/**",
    "kilo.json", "kilo.jsonc", "opencode.json", "opencode.jsonc", "**/kilo.json", "**/kilo.jsonc",
    "**/opencode.json", "**/opencode.jsonc", "AGENTS.md", "**/AGENTS.md",
)


def _kilo_permissions() -> dict:
    read = {"*": "allow"}
    edit = {"*": "allow"}
    write = {"*": "allow"}
    for pattern in _KILO_SENSITIVE_PATTERNS:
        read[pattern] = "deny"
        edit[pattern] = "deny"
        write[pattern] = "deny"
    for pattern in _KILO_PROTECTED_PATTERNS:
        edit[pattern] = "deny"
        write[pattern] = "deny"
    permissions = {
        "*": "deny",
        "read": read,
        "glob": "allow",
        "grep": "allow",
        "edit": edit,
        "write": write,
    }
    for name in (
        "bash", "task", "agent_manager", "skill", "websearch", "webfetch", "external_directory",
        "lsp", "list", "question", "todowrite", "todoread", "doom_loop", "mcp",
    ):
        permissions[name] = "deny"
    return permissions


def _validate_acceptance_criteria(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DevWorkerError("acceptance_criteria doit être une liste")
    out = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise DevWorkerError("acceptance_criteria contient une valeur invalide")
        text = " ".join(value.split())
        if text not in out:
            out.append(text)
    return out


def _repository_context(worktree: Path) -> str:
    head = _git(worktree, "rev-parse", "HEAD")
    branch = _git(worktree, "branch", "--show-current")
    recent = _git(worktree, "log", "-5", "--oneline").replace("\n", " | ")
    return f"branch={branch}; head={head}; recent_commits={recent}"


def _kilo_output_summary(output: str) -> dict:
    counts: dict[str, int] = {}
    texts = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        type_ = str(event.get("type") or "unknown")
        counts[type_] = counts.get(type_, 0) + 1
        if type_ == "text" and isinstance(event.get("text"), str):
            texts.append(event["text"])
    final_text = texts[-1].strip() if texts else ""
    return {"event_types": counts, "final_text": final_text[-2000:]}


def _kilo_declares_noop(output: str) -> bool:
    text = _kilo_output_summary(output)["final_text"].strip()
    return bool(text) and text.splitlines()[-1].strip() == "NO_CHANGE_NEEDED"


def _build_kilo_prompt(
        goal: str, tests: list[list[str]], max_steps: int, last_test_output: str = "", attempt: int = 0,
        allowed_paths: list[str] | None = None, acceptance_criteria: list[str] | None = None,
        noop_allowed: bool = False, repository_context: str = "") -> str:
    goal_text = " ".join(goal.split())
    tests_text = " ; ".join(" ".join(command) for command in tests)
    modifiable = ", ".join(allowed_paths) if allowed_paths is not None else "task-relevant files"
    criteria = acceptance_criteria or ["satisfy the stated objective", "leave the repository consistent"]
    criteria_text = " ; ".join(criteria)
    first_edit_target = max(1, int(max_steps * 0.4))
    parts = [
        "Work in this isolated worktree and solve the ticket.",
        f"OBJECTIVE: {goal_text}",
        f"MODIFIABLE: {modifiable}. All other tracked repository files may be read as needed but must not be modified.",
        f"SUCCESS_CRITERIA: {criteria_text}.",
        f"OCTOPUS_WILL_RUN: {tests_text}. Do not run tests yourself; OCTOPUS is the final test authority.",
        f"BUDGET: {max_steps} steps. Aim to make the first useful edit before step {first_edit_target}.",
        "Explore enough repository context to make a correct change; choose your own investigation method.",
        "Do not access secrets, external directories, Kilo configuration, or Git internals. Do not commit, push, or merge.",
        "If a fact cannot be verified from readable repository context or the supplied repository facts, do not invent it.",
        "Finish with a concise final report: files changed, facts verified, and any facts still unverified.",
    ]
    if repository_context:
        parts.append("REPOSITORY_FACTS: " + " ".join(repository_context.split()))
    if noop_allowed:
        parts.append(
            "If verification shows that no edit is actually needed, explain the verified basis briefly and end the final "
            "response with a separate line containing exactly NO_CHANGE_NEEDED."
        )
    if attempt:
        parts.append(
            "RETRY: a previous pass did not satisfy external validation or tests. Use the feedback below to make a "
            "meaningfully different correction; do not merely repeat the previous pass."
        )
    if last_test_output:
        parts.append("PREVIOUS_FEEDBACK: " + " ".join(last_test_output[-KILO_TEST_FEEDBACK_CHARS:].split()))
    return " ".join(parts)


def _run_kilo(worktree: Path, goal: str, tests: list[list[str]], max_steps: int, prompt: str | None = None) -> str:
    if prompt is None:
        prompt = _build_kilo_prompt(goal, tests, max_steps)
    config = {
        "plugin": [],
        "mcp": {},
        "compaction": {"auto": True, "prune": True, "threshold_percent": 65},
        "agent": {
            KILO_AGENT: {
                "description": "Restricted OCTOPUS development worker",
                "mode": "primary",
                "model": KILO_MODEL,
                "steps": max_steps,
                "permission": _kilo_permissions(),
            },
        },
    }
    with tempfile.TemporaryDirectory(prefix="octopus-kilo-config-") as config_root:
        env = dict(os.environ)
        env.pop("KILO_CONFIG", None)
        env.pop("KILO_CONFIG_DIR", None)
        env["XDG_CONFIG_HOME"] = config_root
        env["KILO_CONFIG_CONTENT"] = json.dumps(config, separators=(",", ":"))
        env["KILO_DISABLE_PROJECT_CONFIG"] = "1"
        env["KILO_PURE"] = "1"
        result = subprocess.run(
            [
                KILO_COMMAND, "run", "--auto", "--dir", str(worktree), "--model", KILO_MODEL,
                "--agent", KILO_AGENT, "--format", "json", prompt,
            ],
            cwd=str(worktree), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=KILO_TIMEOUT_S, env=env,
        )
    if result.returncode:
        raise DevWorkerError((result.stderr or result.stdout or "Kilo CLI failed")[-4000:])
    return result.stdout


def _run(args: list[str], cwd: Path, *, input_text: str | None = None, timeout: int = 300,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd), input=input_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=env,
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


def _wait_for_llm_slot(previous_started: float) -> float:
    now = time.monotonic()
    if os.environ.get("OMNIROUTE_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return now
    delay = previous_started + DEV_MIN_LLM_INTERVAL_S - now
    if delay > 0:
        time.sleep(delay)
        now += delay
    return now


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


def _validate_allowed_paths(raw) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise DevWorkerError("allowed_paths doit être une liste non vide de chemins relatifs")
    out = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise DevWorkerError("allowed_paths contient un chemin invalide")
        normalized = value.replace("\\", "/").strip()
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
            raise DevWorkerError(f"allowed_path absolu interdit: {value}")
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise DevWorkerError(f"allowed_path invalide: {value}")
        if _forbidden_kilo_path(normalized):
            raise DevWorkerError(f"allowed_path interdit: {value}")
        if normalized not in out:
            out.append(normalized)
    return out


def _enforce_allowed_paths(changed_paths: list[str], allowed_paths: list[str] | None) -> None:
    if allowed_paths is None:
        return
    allowed = set(allowed_paths)
    outside = sorted(
        path for path in changed_paths
        if path.replace("\\", "/") not in allowed
    )
    if outside:
        raise DevWorkerError(
            "Kilo a modifié un chemin hors périmètre autorisé: "
            + ", ".join(outside)
        )


_TEST_ENV_SECRET_MARKERS = (
    "API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "COOKIE", "SESSION", "AUTH",
)


def _sanitized_test_env(home: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _TEST_ENV_SECRET_MARKERS)
    }
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["XDG_CONFIG_HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    pytest_addopts = env.get("PYTEST_ADDOPTS", "").strip()
    env["PYTEST_ADDOPTS"] = f"{pytest_addopts} -p no:cacheprovider".strip()
    return env


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
        with tempfile.TemporaryDirectory(prefix="octopus-test-home-") as test_home:
            test_env = _sanitized_test_env(test_home)
            for command in tests:
                result = _run(command, worktree, env=test_env)
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


def _status_entries(worktree: Path) -> tuple[tuple[str, str], ...]:
    result = _run(
        [
            "git", "status", "--porcelain=v1", "-z", "--untracked-files=all",
            "--ignored=matching", "--no-renames",
        ],
        worktree,
    )
    if result.returncode:
        raise DevWorkerError((result.stderr or result.stdout or "git status failed")[-2000:])
    entries = []
    for record in result.stdout.split("\0"):
        if record:
            entries.append((record[:2], record[3:]))
    return tuple(entries)


def _changed_paths(worktree: Path) -> list[str]:
    return sorted({path for status, path in _status_entries(worktree) if status != "!!"})


def _forbidden_kilo_path(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    lowered = [part.lower() for part in parts]
    name = lowered[-1]
    return (
        any(part in {".git", ".kilo", ".kilocode"} for part in lowered)
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pem", ".key"))
        or name.startswith(("credentials", "secrets"))
        or name in {"kilo.json", "kilo.jsonc", "opencode.json", "opencode.jsonc", "agents.md"}
    )


def _validate_kilo_result(worktree: Path, original_head: str, original_branch: str,
                          allowed_paths: list[str] | None = None) -> list[str]:
    if _git(worktree, "rev-parse", "HEAD") != original_head:
        raise DevWorkerError(f"Kilo a créé un commit; worktree conservé: {worktree}")
    if _git(worktree, "branch", "--show-current") != original_branch:
        raise DevWorkerError(f"Kilo a changé de branche; worktree conservé: {worktree}")
    entries = _status_entries(worktree)
    forbidden = sorted({path for _, path in entries if _forbidden_kilo_path(path)})
    if forbidden:
        raise DevWorkerError(f"chemin Kilo interdit: {', '.join(forbidden)}; worktree conservé: {worktree}")
    changed = sorted({path for status, path in entries if status != "!!"})
    if not changed:
        raise KiloRepairableError(
            "no_changes",
            f"Kilo n'a produit aucune modification; worktree conservé: {worktree}",
        )
    _enforce_allowed_paths(changed, allowed_paths)
    check = _run(["git", "diff", "--check", "HEAD", "--"], worktree)
    if check.returncode:
        raise KiloRepairableError(
            "diff_check",
            (check.stderr or check.stdout or "diff Kilo invalide")[-2000:],
        )
    return changed


def _run_declarative_backend(ctx, goal: str, worktree: Path, branch: str,
                             tests: list[list[str]], max_steps: int, fallback_from: str | None = None):
    transcript = []
    tests_passed = False
    inspection_actions = 0
    patch_applied = False
    last_llm_started = 0.0
    schema_json = json.dumps(DEV_ACTION_SCHEMA, separators=(",", ":"))
    system = (
        "You are DevWorker. Choose the next useful action through the structured response mechanism provided. "
        "If declarative action tools are available, call exactly one; they only represent the response and do not "
        "execute anything provider-side. Return an action matching this schema exactly: "
        f"{schema_json}. "
        "Inspect before modifying. Return one action per response. Never request shell, push, PR, or agent. "
        "Do not repeat the same action with identical arguments when its result is already in history. "
        f"Before the first successful patch, use at most {DEV_INSPECTION_LIMIT} read/search actions. "
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
            history.append({**{key: value for key, value in entry.items() if key != "result"}, "result": result})
            remaining -= len(result)
        history.reverse()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "goal": goal,
                "worktree_status": state,
                "tests": tests,
                "inspection_actions_remaining": (
                    None if patch_applied else max(0, DEV_INSPECTION_LIMIT - inspection_actions)
                ),
                "history": history,
            }, ensure_ascii=False)},
        ]
        completion_messages = messages
        rate_limit_retries = 0
        structured_retried = False
        while True:
            try:
                last_llm_started = _wait_for_llm_slot(last_llm_started)
                completion = llm.complete(
                    "development.step", completion_messages, agent="DEVWORKER", business=ctx.business,
                    profile="zero_cost", json_schema=DEV_ACTION_SCHEMA, tool_schemas=DEV_ACTION_TOOLS,
                    max_tokens=1600, validate=_parse_action,
                )
                break
            except Exception as exc:
                delay = _rate_limit_delay(exc)
                if rate_limit_retries < 2 and delay is not None:
                    rate_limit_retries += 1
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
        if (not patch_applied and action["action"] in {"read", "search"}
                and inspection_actions >= DEV_INSPECTION_LIMIT):
            output = "outil refusé/échoué: budget d'inspection épuisé; utiliser patch, test ou commit"
            commit = None
            tool_failed = True
        else:
            if not patch_applied and action["action"] in {"read", "search"}:
                inspection_actions += 1
            try:
                output, tests_passed, commit = _tool(action, worktree, tests, tests_passed)
            except (DevWorkerError, OSError, subprocess.SubprocessError) as exc:
                output, commit = f"outil refusé/échoué: {type(exc).__name__}: {exc}", None
                tool_failed = True
        if action["action"] == "patch" and not tool_failed:
            patch_applied = True
            transcript = [entry for entry in transcript if entry["action"] != "read"]
        arguments = {}
        for name in ("path", "query", "patch", "message"):
            value = action.get(name)
            if value is not None:
                arguments[name] = value if len(value) <= 500 else value[:500] + "..."
        transcript.append({"action": action["action"], "arguments": arguments, "result": output[-12000:]})
        ctx.emit("development.tool", {"action": action["action"], "ok": not output.startswith("outil refusé")})
        if commit:
            result = {
                "commit": commit,
                "branch": branch,
                "worktree": str(worktree),
                "tests": tests,
                "backend": "declarative",
            }
            if fallback_from:
                result["fallback_from"] = fallback_from
            ctx.emit("development.committed", result)
            return result
    raise DevWorkerError(f"DevTask sans commit après {max_steps} étapes; worktree conservé: {worktree}")


@handler("development.task", resource="development", max_attempts=1)
def development_task(ctx):
    goal = str(ctx.input.get("goal") or "").strip()
    if not goal:
        raise DevWorkerError("goal requis")
    repository = Path(str(ctx.input.get("repository") or "")).resolve()
    if not repository.is_dir():
        raise DevWorkerError("repository introuvable")
    tests = validate_test_commands(ctx.input.get("tests"))
    max_steps = int(ctx.input.get("max_steps", 15))
    if not 1 <= max_steps <= 30:
        raise DevWorkerError("max_steps doit être compris entre 1 et 30")
    backend = str(ctx.input.get("backend") or "kilo").strip().lower()
    if backend not in {"kilo", "declarative"}:
        raise DevWorkerError("backend attendu: kilo ou declarative")
    allowed_paths = _validate_allowed_paths(ctx.input.get("allowed_paths"))
    acceptance_criteria = _validate_acceptance_criteria(ctx.input.get("acceptance_criteria"))
    noop_allowed = bool(ctx.input.get("noop_allowed", False))
    allow_declarative_fallback = bool(ctx.input.get("allow_declarative_fallback", True))
    if backend == "declarative" and allowed_paths is not None:
        raise DevWorkerError("allowed_paths exige backend=kilo")

    worktree, branch = _create_worktree(repository, ctx.id)
    if backend == "declarative":
        return _run_declarative_backend(ctx, goal, worktree, branch, tests, max_steps)

    original_head = _git(worktree, "rev-parse", "HEAD")
    original_status = _status_entries(worktree)
    last_test_output = ""
    for attempt in range(KILO_MAX_PASSES):
        repository_context = _repository_context(worktree)
        prompt = _build_kilo_prompt(
            goal, tests, max_steps, last_test_output, attempt,
            allowed_paths=allowed_paths,
            acceptance_criteria=acceptance_criteria,
            noop_allowed=noop_allowed,
            repository_context=repository_context,
        )
        try:
            kilo_output = _run_kilo(worktree, goal, tests, max_steps, prompt=prompt)
            summary = _kilo_output_summary(kilo_output)
            ctx.emit("development.kilo_pass", {
                "attempt": attempt + 1,
                "max_passes": KILO_MAX_PASSES,
                "max_steps": max_steps,
                "event_types": summary["event_types"],
                "final_text": summary["final_text"][-1000:],
            })
        except (DevWorkerError, OSError, subprocess.SubprocessError) as exc:
            pristine = (
                _git(worktree, "rev-parse", "HEAD") == original_head
                and _git(worktree, "branch", "--show-current") == branch
                and _status_entries(worktree) == original_status
            )
            if pristine and allow_declarative_fallback:
                ctx.emit("development.kilo_fallback", {"error": f"{type(exc).__name__}: {exc}"[-2000:]})
                return _run_declarative_backend(
                    ctx, goal, worktree, branch, tests, max_steps, fallback_from="kilo",
                )
            if pristine:
                raise DevWorkerError(
                    f"Kilo a échoué sur worktree propre et fallback declarative désactivé: {exc}"
                ) from exc
            raise DevWorkerError(f"Kilo a échoué après modification; worktree conservé: {worktree}: {exc}") from exc

        try:
            changed_paths = _validate_kilo_result(worktree, original_head, branch, allowed_paths=allowed_paths)
        except KiloRepairableError as exc:
            if exc.kind == "no_changes" and noop_allowed and _kilo_declares_noop(kilo_output):
                result = {
                    "commit": None,
                    "branch": branch,
                    "worktree": str(worktree),
                    "tests": tests,
                    "backend": "kilo",
                    "model": KILO_MODEL,
                    "changed_paths": [],
                    "noop": True,
                    "final_text": _kilo_output_summary(kilo_output)["final_text"],
                }
                ctx.emit("development.noop", result)
                return result
            final_text = _kilo_output_summary(kilo_output)["final_text"]
            last_test_output = f"{exc.kind}: {exc.feedback}"
            if final_text:
                last_test_output += f" | PREVIOUS_FINAL: {final_text}"
            if attempt < KILO_MAX_PASSES - 1:
                ctx.emit("development.kilo_retry", {
                    "attempt": attempt + 1,
                    "max_passes": KILO_MAX_PASSES,
                    "test_output": last_test_output[-KILO_TEST_FEEDBACK_CHARS:],
                })
                continue
            raise DevWorkerError(
                f"validation Kilo toujours invalide après {KILO_MAX_PASSES} passes; "
                f"worktree conservé: {worktree}\n"
                f"{last_test_output[-KILO_TEST_FEEDBACK_CHARS:]}"
            ) from exc
        ctx.emit("development.kilo_completed", {"changed_paths": changed_paths})
        test_output, tests_passed, _ = _tool({"action": "test"}, worktree, tests, False)
        ctx.emit("development.tool", {"action": "test", "ok": tests_passed})
        if tests_passed:
            changed_paths = _validate_kilo_result(worktree, original_head, branch, allowed_paths=allowed_paths)
            commit_message = f"chore: complete development task {ctx.id}"
            _, _, commit = _tool(
                {"action": "commit", "message": commit_message}, worktree, tests, tests_passed,
            )
            result = {
                "commit": commit,
                "branch": branch,
                "worktree": str(worktree),
                "tests": tests,
                "backend": "kilo",
                "model": KILO_MODEL,
                "changed_paths": changed_paths,
            }
            ctx.emit("development.committed", result)
            return result

        last_test_output = test_output
        if attempt < KILO_MAX_PASSES - 1:
            ctx.emit("development.kilo_retry", {
                "attempt": attempt + 1,
                "max_passes": KILO_MAX_PASSES,
                "test_output": test_output[-KILO_TEST_FEEDBACK_CHARS:],
            })
    raise DevWorkerError(f"tests déterministes en échec après {KILO_MAX_PASSES} passes Kilo; worktree conservé: {worktree}\n{last_test_output[-KILO_TEST_FEEDBACK_CHARS:]}")
