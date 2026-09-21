"""DevTask minimal: worktree isolé, outils bornés, tests déterministes, commit local."""
from __future__ import annotations

import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
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
_KILO_ENV_KEEP = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "KILO_API_KEY", "KILOCODE_API_KEY", "KILO_PROVIDER",
})
KILO_COMMAND = "kilo.cmd" if os.name == "nt" else "kilo"
KILO_TIMEOUT_S = 600
KILO_POLL_S = 1.0
KILO_TEST_FEEDBACK_CHARS = 4000
KILO_MAX_PASSES = 3
DEFAULT_TEST_SANDBOX_IMAGE = "octopus-test-sandbox:py311"
PYTHON_CANARY_ORACLES = {
    "octopus/businesses.py": ("tests/test_businesses.py",),
    "octopus/capabilities.py": ("tests/test_capabilities.py",),
    "octopus/connectors.py": ("tests/test_connectors.py",),
    "octopus/resources.py": ("tests/test_resources.py",),
}
OCTOPUS_SELF_MARKERS = (
    "octopus/dev_worker.py",
    "octopus/night_shift.py",
)
OCTOPUS_SELF_PROTECTED_PATHS = frozenset({
    "AGENTS.md",
    "octopus/AGENTS.md",
    "docs/ACCEPTANCE_GATES.md",
})
# Product tickets may change application code, but never the self-development, cost,
# outbound-action, or web-safety boundaries that supervise those tickets.
OCTOPUS_PRODUCT_PROTECTED_PATHS = OCTOPUS_SELF_PROTECTED_PATHS | frozenset({
    "octopus/dev_worker.py",
    "octopus/night_shift.py",
    "octopus/promotion.py",
    "octopus/worker.py",
    "octopus/tasks.py",
    "octopus/compute_finance.py",
    "octopus/economy.py",
    "octopus/actions.py",
    "octopus/browser_actions.py",
    "octopus/smtp_executor.py",
    "octopus/web_guard.py",
})
PRODUCT_TICKET_MAX_FILES = 12
PRODUCT_TICKET_MAX_LINES = 2500
_RETRY_DELAY_RE = re.compile(r"try again in ([0-9]+(?:\.[0-9]+)?)\s*(?:s|seconds?)\b", re.IGNORECASE)

_KILO_SENSITIVE_PATTERNS = (
    ".env", ".env.*", "**/.env", "**/.env.*",
    "*.pem", "**/*.pem", "*.key", "**/*.key", "*.p12", "**/*.p12", "*.pfx", "**/*.pfx",
    "*.kdbx", "**/*.kdbx", "*.tfstate", "**/*.tfstate", "*.tfvars", "**/*.tfvars",
    "id_rsa*", "**/id_rsa*", "credentials*", "**/credentials*", "*credential*", "**/*credential*",
    "secrets*", "**/secrets*", "*secret*", "**/*secret*", "service-account*.json", "**/service-account*.json",
    ".aws/**", "**/.aws/**", ".ssh/**", "**/.ssh/**", ".docker/**", "**/.docker/**",
    ".git-credentials", "**/.git-credentials",
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
    "docs/ACCEPTANCE_GATES.md",
)


def _kilo_permissions(allowed_paths: list[str] | None = None) -> dict:
    read = {"*": "allow"}
    if allowed_paths:
        edit = {"*": "deny", **{path: "allow" for path in allowed_paths}}
        write = {"*": "deny", **{path: "allow" for path in allowed_paths}}
    else:
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
        "list": "allow",
        "edit": edit,
        "write": write,
    }
    for name in (
        "bash", "task", "agent_manager", "skill", "websearch", "webfetch", "external_directory",
        "lsp", "question", "todowrite", "todoread", "doom_loop", "mcp",
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


def _kilo_environment(config_root: str, config: dict) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _KILO_ENV_KEEP
    }
    env["XDG_CONFIG_HOME"] = config_root
    env["KILO_CONFIG_CONTENT"] = json.dumps(config, separators=(",", ":"))
    env["KILO_DISABLE_PROJECT_CONFIG"] = "1"
    env["KILO_PURE"] = "1"
    env["KILO_TELEMETRY_LEVEL"] = "off"
    return env


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.poll() is None:
                process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _run_kilo_process(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    kwargs = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(args, **kwargs)
    deadline = time.monotonic() + KILO_TIMEOUT_S
    while True:
        if (paths.data_dir() / "NIGHT_SHIFT_STOP").exists():
            _terminate_process_tree(process)
            raise DevWorkerError("Kilo arrêté par NIGHT_SHIFT_STOP; arbre de processus arrêté")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            raise DevWorkerError(f"Kilo CLI timeout après {KILO_TIMEOUT_S}s; arbre de processus arrêté")
        try:
            stdout, stderr = process.communicate(timeout=min(KILO_POLL_S, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _run_kilo(
        worktree: Path, goal: str, tests: list[list[str]], max_steps: int,
        prompt: str | None = None, allowed_paths: list[str] | None = None) -> str:
    if prompt is None:
        prompt = _build_kilo_prompt(goal, tests, max_steps, allowed_paths=allowed_paths)
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
                "permission": _kilo_permissions(allowed_paths),
            },
        },
    }
    with tempfile.TemporaryDirectory(prefix="octopus-kilo-config-") as config_root:
        env = _kilo_environment(config_root, config)
        result = _run_kilo_process(
            [
                KILO_COMMAND, "run", "--auto", "--dir", str(worktree), "--model", KILO_MODEL,
                "--agent", KILO_AGENT, "--format", "json", prompt,
            ],
            worktree,
            env,
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


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _wait_for_llm_slot(previous_started: float) -> float:
    now = time.monotonic()
    if os.environ.get("OMNIROUTE_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return now
    delay = previous_started + DEV_MIN_LLM_INTERVAL_S - now
    if delay > 0:
        _sleep(delay)
        now += delay
    return now


def _check_patch_paths(worktree: Path, patch: str) -> None:
    unsupported = (
        "rename from ", "rename to ", "copy from ", "copy to ",
        "old mode ", "new mode ", "new file mode 120000", "deleted file mode 120000",
        "GIT binary patch",
    )
    targets = []
    for line in patch.splitlines():
        if line.startswith(unsupported):
            raise DevWorkerError(f"en-tête de patch interdit: {line}")
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


def _task_clone_root() -> Path:
    configured = os.environ.get("OCTOPUS_DEV_WORKTREE_ROOT", "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path(tempfile.gettempdir()) / "octopus-dev-clones").resolve()


def _create_worktree(repository: Path, task_id: int) -> tuple[Path, str]:
    """Create an independent clone outside the source repository.

    Kilo never receives a linked Git worktree, so resolving the common Git dir
    cannot widen its filesystem boundary back to the parent checkout.
    """
    root_text = _git(repository, "rev-parse", "--show-toplevel")
    source = Path(root_text).resolve()
    source_head = _git(source, "rev-parse", "HEAD")
    common_git_raw = _git(source, "rev-parse", "--git-common-dir")
    common_git = Path(common_git_raw)
    if not common_git.is_absolute():
        common_git = (source / common_git).resolve()
    else:
        common_git = common_git.resolve()
    if not common_git.is_dir():
        raise DevWorkerError(f"git-common-dir introuvable: {common_git}")
    suffix = uuid.uuid4().hex[:8]
    branch = f"codex/devtask-{task_id}-{suffix}"
    clone_root = _task_clone_root()
    clone_root.mkdir(parents=True, exist_ok=True)
    target = (clone_root / f"task-{task_id}-{suffix}").resolve()
    if source == target or source in target.parents:
        raise DevWorkerError("clone de tâche doit être hors du dépôt source")
    result = _run(
        ["git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(common_git), str(target)],
        source.parent,
        timeout=300,
    )
    if result.returncode:
        raise DevWorkerError((result.stderr or result.stdout or "git clone failed")[-2000:])
    _git(target, "checkout", "-b", branch, source_head)
    _git(target, "remote", "remove", "origin")
    _git(target, "config", "core.longpaths", "true")
    _git(target, "config", "user.name", "OCTOPUS DevWorker")
    _git(target, "config", "user.email", "octopus-devworker@localhost.invalid")
    empty_hooks = (target / ".git" / "octopus-empty-hooks").resolve()
    empty_hooks.mkdir(parents=True, exist_ok=True)
    _git(target, "config", "core.hooksPath", str(empty_hooks))
    if _status_entries(target):
        raise DevWorkerError(f"clone de tâche non propre: {target}")
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


def _is_octopus_self_repository(repository: Path) -> bool:
    root = repository.resolve()
    return all((root / marker).is_file() for marker in OCTOPUS_SELF_MARKERS)


def _pytest_targets(commands: list[list[str]]) -> list[str]:
    targets = []
    for command in commands:
        targets.extend(arg for arg in command[3:] if not arg.startswith("-"))
    return targets


def _validate_octopus_self_modification_policy(
        repository: Path, backend: str, allowed_paths: list[str] | None, tests: list[list[str]],
        test_sandbox: str, test_sandbox_image: str, max_files_changed: int | None,
        max_lines_added: int | None, max_lines_deleted: int | None,
        strict_repository_preflight: bool, require_baseline_oracle: bool,
        python_canary_ast: bool, allow_declarative_fallback: bool,
        product_ticket: bool = False) -> bool:
    """Fail closed for OCTOPUS modifying its own repository.

    Returns True only for the tightly bounded Python-canary path.
    """
    if not _is_octopus_self_repository(repository):
        return False
    if backend != "kilo":
        raise DevWorkerError("auto-modification OCTOPUS exige backend=kilo")
    if not allowed_paths:
        raise DevWorkerError("auto-modification OCTOPUS exige allowed_paths explicite")
    if allow_declarative_fallback:
        raise DevWorkerError("fallback declarative interdit pour auto-modification OCTOPUS")

    protected = sorted(set(allowed_paths) & OCTOPUS_SELF_PROTECTED_PATHS)
    if protected:
        raise DevWorkerError("chemin de gouvernance protégé: " + ", ".join(protected))

    python_paths = [path for path in allowed_paths if path.endswith(".py")]
    unsupported = [
        path for path in allowed_paths
        if not path.endswith(".py") and not path.lower().endswith(".md")
    ]
    if unsupported:
        raise DevWorkerError(
            "auto-modification OCTOPUS limitée aux docs Markdown et surfaces Python approuvées: "
            + ", ".join(unsupported)
        )
    if not python_paths:
        if product_ticket:
            raise DevWorkerError("product_ticket OCTOPUS exige au moins un fichier Python")
        return False

    if product_ticket:
        product_protected = sorted(set(allowed_paths) & OCTOPUS_PRODUCT_PROTECTED_PATHS)
        if product_protected:
            raise DevWorkerError(
                "product_ticket refuse une frontière de sécurité: " + ", ".join(product_protected)
            )
        if len(allowed_paths) > PRODUCT_TICKET_MAX_FILES:
            raise DevWorkerError(
                f"product_ticket OCTOPUS limité à {PRODUCT_TICKET_MAX_FILES} chemins autorisés"
            )
        if any(path.startswith("tests/") for path in allowed_paths):
            raise DevWorkerError("product_ticket OCTOPUS ne peut pas modifier ses oracles de test")
        actual_targets = _pytest_targets(tests)
        if not actual_targets or any(
            not target.startswith("tests/") or not target.endswith(".py")
            for target in actual_targets
        ):
            raise DevWorkerError("product_ticket OCTOPUS exige uniquement des cibles pytest sous tests/")
        if test_sandbox != "docker":
            raise DevWorkerError("product_ticket OCTOPUS exige test_sandbox=docker")
        if (
            test_sandbox_image != DEFAULT_TEST_SANDBOX_IMAGE
            and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", test_sandbox_image) is None
        ):
            raise DevWorkerError("image sandbox product_ticket OCTOPUS non approuvée")
        if (
            max_files_changed is None
            or not 1 <= max_files_changed <= min(PRODUCT_TICKET_MAX_FILES, len(allowed_paths))
        ):
            raise DevWorkerError(
                "product_ticket OCTOPUS exige max_files_changed entre 1 et la taille de allowed_paths"
            )
        for name, value in (
            ("max_lines_added", max_lines_added),
            ("max_lines_deleted", max_lines_deleted),
        ):
            if value is None or not 1 <= value <= PRODUCT_TICKET_MAX_LINES:
                raise DevWorkerError(
                    f"product_ticket OCTOPUS exige {name} entre 1 et {PRODUCT_TICKET_MAX_LINES}"
                )
        if not strict_repository_preflight:
            raise DevWorkerError("product_ticket OCTOPUS exige strict_repository_preflight")
        if not require_baseline_oracle:
            raise DevWorkerError("product_ticket OCTOPUS exige require_baseline_oracle")
        if python_canary_ast:
            raise DevWorkerError("product_ticket OCTOPUS n'utilise pas python_canary_ast")
        return False

    if len(allowed_paths) != 1 or len(python_paths) != 1:
        raise DevWorkerError("python_canary OCTOPUS exige exactement un fichier Python")
    source = python_paths[0]
    expected_oracle = PYTHON_CANARY_ORACLES.get(source)
    if expected_oracle is None:
        raise DevWorkerError(f"surface Python non approuvée pour auto-modification OCTOPUS: {source}")
    actual_targets = _pytest_targets(tests)
    if actual_targets != list(expected_oracle):
        raise DevWorkerError(
            f"oracle Python attendu {list(expected_oracle)}, reçu {actual_targets}"
        )
    if test_sandbox != "docker":
        raise DevWorkerError("auto-modification Python OCTOPUS exige test_sandbox=docker")
    if (
        test_sandbox_image != DEFAULT_TEST_SANDBOX_IMAGE
        and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", test_sandbox_image) is None
    ):
        raise DevWorkerError("image sandbox Python OCTOPUS non approuvée")
    if max_files_changed != 1:
        raise DevWorkerError("auto-modification Python OCTOPUS exige max_files_changed=1")
    for name, value in (
        ("max_lines_added", max_lines_added),
        ("max_lines_deleted", max_lines_deleted),
    ):
        if value is None or not 1 <= value <= 80:
            raise DevWorkerError(f"auto-modification Python OCTOPUS exige {name} entre 1 et 80")
    if not strict_repository_preflight:
        raise DevWorkerError("auto-modification Python OCTOPUS exige strict_repository_preflight")
    if not require_baseline_oracle:
        raise DevWorkerError("auto-modification Python OCTOPUS exige require_baseline_oracle")
    if not python_canary_ast:
        raise DevWorkerError("auto-modification Python OCTOPUS exige python_canary_ast")
    return True


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


def _tracked_sensitive_path(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    lowered = [part.lower() for part in parts]
    name = lowered[-1]
    if any(part in {".aws", ".ssh", ".docker"} for part in lowered):
        return True
    if name in {".git-credentials", ".netrc", ".npmrc", ".pypirc"}:
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if name.startswith("id_rsa"):
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".kdbx", ".tfstate", ".tfvars")):
        return True
    if name.startswith(("credentials", "secrets", "service-account")):
        return True
    return False


def _assert_safe_allowed_paths(worktree: Path, allowed_paths: list[str] | None) -> None:
    if not allowed_paths:
        return
    root = worktree.resolve()
    for relative in allowed_paths:
        candidate = (root / relative).resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            raise DevWorkerError(f"allowed_path résout hors clone isolé: {relative}")
        current = root
        for part in Path(relative).parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise DevWorkerError(f"parent symlink interdit dans allowed_path: {relative}")
        tracked = _git(worktree, "ls-files", "-s", "--", relative, check=False)
        if tracked:
            mode = tracked.split(None, 1)[0]
            if mode == "120000":
                raise DevWorkerError(f"symlink Git interdit dans allowed_path: {relative}")


def _strict_repository_preflight(worktree: Path) -> None:
    entries = _status_entries(worktree)
    if entries:
        raise DevWorkerError(f"clone isolé non propre au pré-vol: {entries[:8]}")
    tracked = _git(worktree, "ls-files", "-z")
    risky = sorted(path for path in tracked.split("\0") if path and _tracked_sensitive_path(path))
    if risky:
        raise DevWorkerError("fichiers sensibles suivis interdits dans clone Kilo: " + ", ".join(risky[:20]))
    secret_regex = (
        r"(-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|"
        r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"
    )
    scan = _run(["git", "grep", "-I", "-l", "-E", secret_regex, "HEAD", "--"], worktree)
    if scan.returncode not in {0, 1}:
        raise DevWorkerError((scan.stderr or scan.stdout or "scan secrets Git échoué")[-2000:])
    if scan.returncode == 0 and scan.stdout.strip():
        names = sorted(set(scan.stdout.splitlines()))
        raise DevWorkerError(
            "contenu secret à haute confiance détecté dans clone Kilo: " + ", ".join(names[:20])
        )
    staged = _git(worktree, "ls-files", "-s")
    symlinks = []
    submodules = []
    for line in staged.splitlines():
        parts = line.split(None, 3)
        if len(parts) != 4:
            continue
        if parts[0] == "120000":
            symlinks.append(parts[3])
        elif parts[0] == "160000":
            submodules.append(parts[3])
    if symlinks:
        raise DevWorkerError("symlinks Git interdits en python_canary: " + ", ".join(symlinks[:20]))
    if submodules:
        raise DevWorkerError("submodules Git interdits en python_canary: " + ", ".join(submodules[:20]))
    flags = _git(worktree, "ls-files", "-v")
    suspicious_flags = []
    for line in flags.splitlines():
        if not line:
            continue
        tag = line[0]
        if tag == "S" or tag.islower():
            suspicious_flags.append(line[2:])
    if suspicious_flags:
        raise DevWorkerError(
            "index Git avec skip-worktree/assume-unchanged interdit: " + ", ".join(suspicious_flags[:20])
        )


def _validate_positive_limit(raw, name: str, *, maximum: int) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise DevWorkerError(f"{name} invalide")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise DevWorkerError(f"{name} invalide") from exc
    if not 1 <= value <= maximum:
        raise DevWorkerError(f"{name} doit être compris entre 1 et {maximum}")
    return value


def _diff_radius(worktree: Path) -> dict[str, int]:
    result = _run(["git", "diff", "--numstat", "HEAD", "--"], worktree)
    if result.returncode:
        raise DevWorkerError((result.stderr or result.stdout or "git diff --numstat failed")[-2000:])
    files = 0
    added = 0
    deleted = 0
    tracked_paths = set()
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        raw_add, raw_del, path = parts
        files += 1
        tracked_paths.add(path)
        if raw_add == "-" or raw_del == "-":
            raise DevWorkerError(f"diff binaire interdit dans rayon de modification: {path}")
        if not raw_add.isdigit() or not raw_del.isdigit():
            raise DevWorkerError(f"numstat Git invalide: {line}")
        added += int(raw_add)
        deleted += int(raw_del)

    for status, path in _status_entries(worktree):
        if status != "??" or path in tracked_paths:
            continue
        files += 1
        candidate = resolve_path(worktree, path)
        if candidate.is_file():
            if candidate.stat().st_size > 1_000_000:
                raise DevWorkerError(f"fichier untracked trop volumineux: {path}")
            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                raise DevWorkerError(f"fichier untracked non UTF-8/refusé: {path}") from exc
            if any(len(line.encode("utf-8")) > 16_384 for line in text.splitlines()):
                raise DevWorkerError(f"ligne untracked trop volumineuse: {path}")
            added += len(text.splitlines())
    return {"files": files, "added": added, "deleted": deleted}


def _enforce_diff_radius(
        worktree: Path, *, max_files_changed: int | None = None,
        max_lines_added: int | None = None, max_lines_deleted: int | None = None) -> None:
    radius = _diff_radius(worktree)
    failures = []
    if max_files_changed is not None and radius["files"] > max_files_changed:
        failures.append(f"files={radius['files']}>{max_files_changed}")
    if max_lines_added is not None and radius["added"] > max_lines_added:
        failures.append(f"added={radius['added']}>{max_lines_added}")
    if max_lines_deleted is not None and radius["deleted"] > max_lines_deleted:
        failures.append(f"deleted={radius['deleted']}>{max_lines_deleted}")
    if failures:
        raise DevWorkerError("rayon de modification dépassé: " + ", ".join(failures))


_TEST_ENV_KEEP = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "PYTHONUTF8", "PYTHONIOENCODING",
    "CI", "GITHUB_ACTIONS",
})


def _sanitized_test_env(home: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _TEST_ENV_KEEP
    }
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["XDG_CONFIG_HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    return env


def _validate_test_sandbox(raw) -> str:
    value = str(raw or "host").strip().lower()
    if value not in {"host", "docker"}:
        raise DevWorkerError("test_sandbox attendu: host ou docker")
    return value


def _docker_image_id(worktree: Path, image: str) -> str:
    info = _run(["docker", "info", "--format", "{{.OSType}}"], worktree, timeout=30)
    if info.returncode or info.stdout.strip().lower() != "linux":
        detail = (info.stderr or info.stdout or "Docker indisponible / mode non-Linux")[-1000:]
        raise DevWorkerError(f"sandbox Docker exige Docker Linux: {detail}")
    inspect = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image], worktree, timeout=30)
    image_id = inspect.stdout.strip()
    if inspect.returncode or not image_id.startswith("sha256:"):
        raise DevWorkerError(
            f"image sandbox Docker absente/invalide: {image}; construire l'image avant ce ticket"
        )
    return image_id


def _docker_mount_source(worktree: Path) -> str:
    source = worktree.resolve()
    if not source.is_dir():
        raise DevWorkerError(f"source Docker inexistante: {source}")
    raw = str(source)
    if any(ch in raw for ch in (",", "\n", "\r", '"')):
        raise DevWorkerError(f"chemin source Docker non sûr: {raw!r}")
    return raw


def _docker_test_args(
        worktree: Path, image: str, command: list[str], *, container_name: str | None = None) -> list[str]:
    inner = ["python", *command[1:]]
    if "-rA" not in inner:
        inner.append("-rA")
    name = container_name or f"octopus-t-{uuid.uuid4().hex[:12]}"
    mount = f"type=bind,source={_docker_mount_source(worktree)},target=/workspace,readonly"
    return [
        "docker", "run", "--rm",
        "--name", name,
        "--label", "octopus.test=1",
        "--pull", "never",
        "--init",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "10001:10001",
        "--pids-limit", "128",
        "--memory", "768m",
        "--memory-swap", "768m",
        "--cpus", "1.0",
        "--ulimit", "nofile=1024:1024",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
        "-e", "HOME=/tmp",
        "-e", "USERPROFILE=/tmp",
        "-e", "XDG_CONFIG_HOME=/tmp",
        "-e", "PYTHONUTF8=1",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "TZ=UTC",
        "-e", "PYTEST_ADDOPTS=-p no:cacheprovider",
        "--mount", mount,
        "-w", "/workspace",
        image,
        *inner,
    ]


def _cleanup_docker_container(worktree: Path, container_name: str) -> None:
    try:
        _run(["docker", "rm", "-f", container_name], worktree, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _run_tests(
        worktree: Path, tests: list[list[str]], *, sandbox: str = "host",
        sandbox_image: str = DEFAULT_TEST_SANDBOX_IMAGE) -> tuple[str, bool]:
    outputs = []
    if sandbox == "docker":
        image_id = _docker_image_id(worktree, sandbox_image)
        for command in tests:
            container_name = f"octopus-t-{uuid.uuid4().hex[:12]}"
            args = _docker_test_args(worktree, image_id, command, container_name=container_name)
            try:
                result = _run(args, worktree, timeout=300)
            except subprocess.TimeoutExpired as exc:
                _cleanup_docker_container(worktree, container_name)
                raise DevWorkerError("sandbox Docker timeout; conteneur forcé à l'arrêt") from exc
            finally:
                _cleanup_docker_container(worktree, container_name)
            combined = (result.stdout or "") + (result.stderr or "")
            if len(combined.encode("utf-8", errors="replace")) > 2_000_000:
                raise DevWorkerError("sortie sandbox Docker trop volumineuse")
            outputs.append(f"$ docker sandbox :: {' '.join(command)}\n{combined}"[-12000:])
            if result.returncode in {125, 126, 127, 137}:
                raise DevWorkerError(
                    f"échec infrastructure Docker code={result.returncode}: {combined[-2000:]}"
                )
            if result.returncode == 5:
                raise DevWorkerError("pytest n'a collecté aucun test (code 5)")
            if result.returncode:
                return "\n".join(outputs), False
        return "\n".join(outputs), True

    with tempfile.TemporaryDirectory(prefix="octopus-test-home-") as test_home:
        test_env = _sanitized_test_env(test_home)
        for command in tests:
            result = _run(command, worktree, env=test_env)
            outputs.append(f"$ {' '.join(command)}\n{result.stdout}{result.stderr}"[-12000:])
            if result.returncode:
                return "\n".join(outputs), False
    return "\n".join(outputs), True


_ORACLE_LINE_RE = re.compile(r"^(PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)\s+([^\s]+)")


def _pytest_oracle_signature(output: str) -> tuple[tuple[str, str], ...]:
    signature = []
    for line in output.splitlines():
        match = _ORACLE_LINE_RE.match(line.strip())
        if match:
            signature.append((match.group(1), match.group(2)))
    return tuple(sorted(signature))


def _docker_sandbox_probe(base_dir: Path, image: str) -> tuple[str, str]:
    image_id = _docker_image_id(base_dir, image)
    probe_root = paths.data_dir()
    probe_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="octopus-docker-probe-", dir=str(probe_root)) as tmp:
        probe = Path(tmp)
        (probe / "test_probe.py").write_text(
            "import os, socket\n"
            "from pathlib import Path\n\n"
            "def test_sandbox_invariants():\n"
            "    assert os.environ.get('OCTOPUS_TEST_SECRET') is None\n"
            "    assert not Path('/var/run/docker.sock').exists()\n"
            "    if hasattr(os, 'getuid'):\n"
            "        assert os.getuid() != 0\n"
            "    status = Path('/proc/self/status').read_text(encoding='utf-8')\n"
            "    assert 'NoNewPrivs:\\t1' in status\n"
            "    try:\n"
            "        Path('/workspace/SHOULD_NOT_WRITE').write_text('x', encoding='utf-8')\n"
            "    except OSError:\n"
            "        pass\n"
            "    else:\n"
            "        raise AssertionError('workspace unexpectedly writable')\n"
            "    sock = socket.socket()\n"
            "    sock.settimeout(0.2)\n"
            "    try:\n"
            "        sock.connect(('1.1.1.1', 53))\n"
            "    except OSError:\n"
            "        pass\n"
            "    else:\n"
            "        raise AssertionError('network unexpectedly reachable')\n",
            encoding="utf-8",
        )
        output, green = _run_tests(
            probe,
            [[sys.executable, "-m", "pytest", "-q", "test_probe.py"]],
            sandbox="docker",
            sandbox_image=image_id,
        )
        signature = _pytest_oracle_signature(output)
        if not green or not any(status == "PASSED" for status, _ in signature):
            raise DevWorkerError("probe Docker réel non concluant: " + output[-2000:])
        return image_id, output[-2000:]


def _tool(
        action: dict, worktree: Path, tests: list[list[str]], tests_passed: bool, *,
        test_sandbox: str = "host", test_sandbox_image: str = DEFAULT_TEST_SANDBOX_IMAGE,
) -> tuple[str, bool, str | None]:
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
        output, passed = _run_tests(
            worktree, tests, sandbox=test_sandbox, sandbox_image=test_sandbox_image,
        )
        return output, passed, None
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
        any(part in {".git", ".kilo", ".kilocode", ".aws", ".ssh", ".docker"} for part in lowered)
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".kdbx", ".tfstate", ".tfvars"))
        or name.startswith(("credentials", "secrets", "id_rsa", "service-account"))
        or name in {
            ".git-credentials", ".netrc", ".npmrc", ".pypirc",
            "kilo.json", "kilo.jsonc", "opencode.json", "opencode.jsonc", "agents.md",
        }
    )


def _call_name(node: ast.Call) -> str:
    func = node.func
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _validate_python_canary_ast(worktree: Path, original_head: str, changed_paths: list[str]) -> None:
    forbidden_prefixes = (
        "os._exit", "subprocess.", "ctypes.", "importlib.", "socket.",
    )
    forbidden_exact = {"exec", "eval", "compile", "__import__"}
    for relative in changed_paths:
        if not relative.endswith(".py"):
            continue
        try:
            before = ast.parse(_git(worktree, "show", f"{original_head}:{relative}"), filename=relative)
            after = ast.parse((worktree / relative).read_text(encoding="utf-8"), filename=relative)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            raise KiloRepairableError("python_policy", f"AST Python invalide: {relative}: {exc}") from exc

        old_imports = Counter(
            ast.dump(node, include_attributes=False)
            for node in ast.walk(before)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        new_imports = Counter(
            ast.dump(node, include_attributes=False)
            for node in ast.walk(after)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        if new_imports - old_imports:
            raise KiloRepairableError("python_policy", f"nouvel import interdit en python_canary: {relative}")

        old_forbidden = Counter(
            _call_name(node) for node in ast.walk(before)
            if isinstance(node, ast.Call)
            and (_call_name(node) in forbidden_exact or _call_name(node).startswith(forbidden_prefixes))
        )
        new_forbidden = Counter(
            _call_name(node) for node in ast.walk(after)
            if isinstance(node, ast.Call)
            and (_call_name(node) in forbidden_exact or _call_name(node).startswith(forbidden_prefixes))
        )
        if new_forbidden - old_forbidden:
            raise KiloRepairableError(
                "python_policy", f"nouvel appel dangereux interdit en python_canary: {relative}"
            )

        def top_level_effects(module: ast.Module) -> Counter:
            effects = Counter()
            for node in module.body:
                value = None
                if isinstance(node, ast.Expr):
                    value = node.value
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = node.value
                if value is not None and any(isinstance(child, ast.Call) for child in ast.walk(value)):
                    effects[ast.dump(node, include_attributes=False)] += 1
            return effects

        if top_level_effects(after) - top_level_effects(before):
            raise KiloRepairableError(
                "python_policy", f"nouvel effet de bord niveau module interdit: {relative}"
            )


def _validate_kilo_result(
        worktree: Path, original_head: str, original_branch: str,
        allowed_paths: list[str] | None = None, *,
        max_files_changed: int | None = None,
        max_lines_added: int | None = None,
        max_lines_deleted: int | None = None) -> list[str]:
    if _git(worktree, "rev-parse", "HEAD") != original_head:
        raise DevWorkerError(f"Kilo a créé un commit; worktree conservé: {worktree}")
    if _git(worktree, "branch", "--show-current") != original_branch:
        raise DevWorkerError(f"Kilo a changé de branche; worktree conservé: {worktree}")
    entries = _status_entries(worktree)
    ignored = sorted({path for status, path in entries if status == "!!"})
    if ignored:
        raise DevWorkerError(
            "fichier ignoré créé/modifié par Kilo interdit: " + ", ".join(ignored[:20])
            + f"; clone conservé: {worktree}"
        )
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
    _enforce_diff_radius(
        worktree,
        max_files_changed=max_files_changed,
        max_lines_added=max_lines_added,
        max_lines_deleted=max_lines_deleted,
    )
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
                    _sleep(delay)
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
    test_sandbox = _validate_test_sandbox(ctx.input.get("test_sandbox"))
    test_sandbox_image = str(
        ctx.input.get("test_sandbox_image") or DEFAULT_TEST_SANDBOX_IMAGE
    ).strip()
    if not test_sandbox_image or len(test_sandbox_image) > 200:
        raise DevWorkerError("test_sandbox_image invalide")
    max_files_changed = _validate_positive_limit(
        ctx.input.get("max_files_changed"), "max_files_changed", maximum=20,
    )
    max_lines_added = _validate_positive_limit(
        ctx.input.get("max_lines_added"), "max_lines_added", maximum=5000,
    )
    max_lines_deleted = _validate_positive_limit(
        ctx.input.get("max_lines_deleted"), "max_lines_deleted", maximum=5000,
    )
    strict_repository_preflight = bool(ctx.input.get("strict_repository_preflight", False))
    require_baseline_oracle = bool(ctx.input.get("require_baseline_oracle", False))
    python_canary_ast = bool(ctx.input.get("python_canary_ast", False))
    allow_declarative_fallback = bool(ctx.input.get("allow_declarative_fallback", False))
    self_modification_policy = str(
        ctx.input.get("self_modification_policy") or "python_canary"
    ).strip().lower()
    if self_modification_policy not in {"python_canary", "product_ticket"}:
        raise DevWorkerError("self_modification_policy attendu: python_canary ou product_ticket")
    product_ticket = self_modification_policy == "product_ticket"
    if backend == "kilo" and allowed_paths is None:
        raise DevWorkerError("backend=kilo exige allowed_paths explicite")
    if backend == "declarative" and allowed_paths is not None:
        raise DevWorkerError("allowed_paths exige backend=kilo")
    octopus_python_canary = _validate_octopus_self_modification_policy(
        repository, backend, allowed_paths, tests, test_sandbox, test_sandbox_image,
        max_files_changed, max_lines_added, max_lines_deleted,
        strict_repository_preflight, require_baseline_oracle,
        python_canary_ast, allow_declarative_fallback,
        product_ticket,
    )

    worktree, branch = _create_worktree(repository, ctx.id)
    _assert_safe_allowed_paths(worktree, allowed_paths)
    if strict_repository_preflight:
        _strict_repository_preflight(worktree)
    if backend == "declarative":
        return _run_declarative_backend(ctx, goal, worktree, branch, tests, max_steps)

    effective_test_image = test_sandbox_image
    if octopus_python_canary:
        trusted_image_id, _ = _docker_sandbox_probe(worktree, DEFAULT_TEST_SANDBOX_IMAGE)
        if test_sandbox_image.startswith("sha256:") and test_sandbox_image.lower() != trusted_image_id.lower():
            raise DevWorkerError("image sandbox résolue différente de l'image OCTOPUS approuvée")
        effective_test_image = trusted_image_id
    elif test_sandbox == "docker":
        effective_test_image = _docker_image_id(worktree, test_sandbox_image)

    baseline_signature: tuple[tuple[str, str], ...] | None = None
    if require_baseline_oracle:
        signatures = []
        for baseline_attempt in range(2):
            baseline_output, baseline_green = _run_tests(
                worktree, tests, sandbox=test_sandbox, sandbox_image=effective_test_image,
            )
            signature = _pytest_oracle_signature(baseline_output)
            if not baseline_green or not signature or any(status != "PASSED" for status, _ in signature):
                raise DevWorkerError(
                    f"oracle baseline invalide avant Kilo (run {baseline_attempt + 1}): "
                    + baseline_output[-2000:]
                )
            signatures.append(signature)
        if signatures[0] != signatures[1]:
            raise DevWorkerError("oracle baseline instable entre deux exécutions")
        baseline_signature = signatures[0]
        ctx.emit("development.baseline", {
            "tests": len(baseline_signature),
            "sandbox": test_sandbox,
            "image": effective_test_image if test_sandbox == "docker" else None,
        })

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
            kilo_output = _run_kilo(
                worktree, goal, tests, max_steps, prompt=prompt, allowed_paths=allowed_paths,
            )
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
            changed_paths = _validate_kilo_result(
                worktree, original_head, branch, allowed_paths=allowed_paths,
                max_files_changed=max_files_changed,
                max_lines_added=max_lines_added,
                max_lines_deleted=max_lines_deleted,
            )
            if python_canary_ast:
                _validate_python_canary_ast(worktree, original_head, changed_paths)
        except KiloRepairableError as exc:
            if exc.kind == "no_changes" and noop_allowed and _kilo_declares_noop(kilo_output):
                test_output, tests_passed, _ = _tool(
                    {"action": "test"}, worktree, tests, False,
                    test_sandbox=test_sandbox, test_sandbox_image=effective_test_image,
                )
                if tests_passed and baseline_signature is not None:
                    noop_signature = _pytest_oracle_signature(test_output)
                    tests_passed = noop_signature == baseline_signature
                    if not tests_passed:
                        test_output += "\nORACLE_SIGNATURE_MISMATCH"
                ctx.emit("development.tool", {"action": "test", "ok": tests_passed, "noop": True})
                if tests_passed:
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
                        "self_policy": "product_ticket" if product_ticket else ("python_canary" if octopus_python_canary else "scoped_kilo"),
                    }
                    ctx.emit("development.noop", result)
                    return result
                last_test_output = "NOOP_BASELINE_TEST_FAILURE: " + test_output[-KILO_TEST_FEEDBACK_CHARS:]
                if attempt < KILO_MAX_PASSES - 1:
                    ctx.emit("development.kilo_retry", {
                        "attempt": attempt + 1,
                        "max_passes": KILO_MAX_PASSES,
                        "test_output": last_test_output,
                    })
                    continue
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
        test_output, tests_passed, _ = _tool(
            {"action": "test"}, worktree, tests, False,
            test_sandbox=test_sandbox, test_sandbox_image=effective_test_image,
        )
        if tests_passed and baseline_signature is not None:
            post_signature = _pytest_oracle_signature(test_output)
            tests_passed = post_signature == baseline_signature
            if not tests_passed:
                test_output += "\nORACLE_SIGNATURE_MISMATCH"
        ctx.emit("development.tool", {"action": "test", "ok": tests_passed})
        if tests_passed:
            changed_paths = _validate_kilo_result(
                worktree, original_head, branch, allowed_paths=allowed_paths,
                max_files_changed=max_files_changed,
                max_lines_added=max_lines_added,
                max_lines_deleted=max_lines_deleted,
            )
            if python_canary_ast:
                _validate_python_canary_ast(worktree, original_head, changed_paths)
            commit_message = f"chore: complete development task {ctx.id}"
            _, _, commit = _tool(
                {"action": "commit", "message": commit_message}, worktree, tests, tests_passed,
                test_sandbox=test_sandbox, test_sandbox_image=effective_test_image,
            )
            result = {
                "commit": commit,
                "branch": branch,
                "worktree": str(worktree),
                "tests": tests,
                "backend": "kilo",
                "model": KILO_MODEL,
                "changed_paths": changed_paths,
                "test_sandbox": test_sandbox,
                "test_sandbox_image": effective_test_image if test_sandbox == "docker" else None,
                "oracle_tests": len(baseline_signature or ()),
                "self_policy": "product_ticket" if product_ticket else ("python_canary" if octopus_python_canary else "scoped_kilo"),
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
