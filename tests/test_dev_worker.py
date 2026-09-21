from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from octopus import tasks, worker


@pytest.fixture(autouse=True)
def load_dev_handler(tmp_path, monkeypatch):
    worker.load_handlers(["octopus.dev_worker"])
    clone_root = tmp_path / "isolated-dev-clones"
    monkeypatch.setenv("OCTOPUS_DEV_WORKTREE_ROOT", str(clone_root))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def repository(tmp_path: Path, *, passing: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "devworker@example.invalid")
    git(repo, "config", "user.name", "DevWorker Test")
    (repo / "calc.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    expected = 1 if passing else 2
    (repo / "test_calc.py").write_text(
        f"from calc import answer\n\ndef test_answer():\n    assert answer() == {expected}\n", encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def scripted(monkeypatch, dev_worker, actions):
    calls = []
    iterator = iter(actions)

    def complete(task, messages, **kwargs):
        calls.append((task, messages, kwargs))
        action = next(iterator)
        return SimpleNamespace(data=action, text=json.dumps(action))

    monkeypatch.setattr(dev_worker.llm, "complete", complete)
    return calls


def test_development_task_edits_tests_and_commits_in_isolated_worktree(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    source_head = git(repo, "rev-parse", "HEAD")
    patch = """diff --git a/calc.py b/calc.py
index 4c47471..f208b75 100644
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def answer():
-    return 1
+    return 2
"""
    calls = scripted(monkeypatch, dev_worker, [
        {"action": "read", "path": "calc.py"},
        {"action": "patch", "patch": patch},
        {"action": "test"},
        {"action": "commit", "message": "fix: return expected answer"},
    ])
    task_id = worker.enqueue("octopus", "development.task", {
        "repository": str(repo), "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]], "max_steps": 4,
        "backend": "declarative",
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    output = result["output"]
    worktree = Path(output["worktree"])
    assert output["commit"] == git(worktree, "rev-parse", "HEAD")
    assert output["commit"] != source_head
    assert git(repo, "rev-parse", "HEAD") == source_head
    assert (repo / "calc.py").read_text(encoding="utf-8").endswith("return 1\n")
    assert git(worktree, "status", "--porcelain") == ""
    assert all(task == "development.step" and kwargs["profile"] == "zero_cost" for task, _, kwargs in calls)
    assert all(kwargs["json_schema"] == dev_worker.DEV_ACTION_SCHEMA for _, _, kwargs in calls)
    assert all(kwargs["tool_schemas"] == dev_worker.DEV_ACTION_TOOLS for _, _, kwargs in calls)
    schema_json = json.dumps(dev_worker.DEV_ACTION_SCHEMA, separators=(",", ":"))
    assert all(schema_json in messages[0]["content"] for _, messages, _ in calls)
    assert all("do not execute anything provider-side" in messages[0]["content"] for _, messages, _ in calls)
    assert all("Do not repeat the same action" in messages[0]["content"] for _, messages, _ in calls)
    assert all("unified diff with ---/+++ paths" in messages[0]["content"] for _, messages, _ in calls)
    assert all("smallest change" in messages[0]["content"] for _, messages, _ in calls)
    for _, messages, _ in calls[2:]:
        history = json.loads(messages[1]["content"])["history"]
        assert all(item["action"] != "read" for item in history)
    assert tasks.get(task_id)["output"]["branch"].startswith("codex/devtask-")


def test_devworker_rejects_paths_outside_worktree(tmp_path):
    from octopus.dev_worker import DevWorkerError, resolve_path

    root = tmp_path / "worktree"
    root.mkdir()
    with pytest.raises(DevWorkerError, match="hors worktree"):
        resolve_path(root, "../secret.txt")


@pytest.mark.parametrize("header", [
    "rename from calc.py",
    "rename to .forbidden/calc.py",
    "copy from calc.py",
    "copy to .forbidden/calc.py",
    "old mode 100644",
    "new mode 100755",
    "new file mode 120000",
    "deleted file mode 120000",
    "GIT binary patch",
])
def test_devworker_rejects_unsupported_patch_headers(tmp_path, header):
    from octopus.dev_worker import DevWorkerError, _check_patch_paths

    root = tmp_path / "worktree"
    root.mkdir()
    patch = f"diff --git a/calc.py b/calc.py\n{header}\n"
    with pytest.raises(DevWorkerError, match="en-tête de patch interdit"):
        _check_patch_paths(root, patch)


def test_sanitized_test_env_is_allowlist_based(monkeypatch, tmp_path):
    from octopus import dev_worker

    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "secret")
    monkeypatch.setenv("UNRELATED_VALUE", "do-not-pass")
    env = dev_worker._sanitized_test_env(str(tmp_path))

    assert env["PATH"] == "safe-path"
    assert "AZURE_SPEECH_KEY" not in env
    assert "UNRELATED_VALUE" not in env
    assert env["HOME"] == str(tmp_path)
    assert env["USERPROFILE"] == str(tmp_path)
    assert env["XDG_CONFIG_HOME"] == str(tmp_path)
    assert env["PYTEST_ADDOPTS"] == "-p no:cacheprovider"


def test_devworker_rejects_unapproved_test_commands():
    from octopus.dev_worker import DevWorkerError, validate_test_commands

    with pytest.raises(DevWorkerError, match="pytest"):
        validate_test_commands([["powershell", "-Command", "Write-Host unsafe"]])


def test_devworker_subprocess_input_is_utf8(tmp_path):
    from octopus.dev_worker import _run

    result = _run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        tmp_path,
        input_text="tiretâ€‘insÃ©cable",
    )

    assert result.returncode == 0
    assert result.stdout == "tiretâ€‘insÃ©cable"


def test_devworker_strict_schema_builds_structured_output_request():
    from octopus import dev_worker, llm

    request = llm._build_request(
        {"api_model": "dummy", "capabilities": ["json"], "params": {}},
        [{"role": "user", "content": "next"}],
        100,
        False,
        None,
        dev_worker.DEV_ACTION_SCHEMA,
    )

    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "octopus_response",
            "strict": True,
            "schema": dev_worker.DEV_ACTION_SCHEMA,
        },
    }


def test_devworker_groq_uses_tool_call_with_local_validation(monkeypatch):
    from octopus import catalog, dev_worker, llm

    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    model = catalog.load().model("omniroute/devworker-groq")
    request = llm._build_request(
        model,
        [{"role": "user", "content": "next"}],
        100,
        False,
        None,
        dev_worker.DEV_ACTION_SCHEMA,
        tool_schemas=dev_worker.DEV_ACTION_TOOLS,
    )

    assert "response_format" not in request
    assert request["tools"] == dev_worker.DEV_ACTION_TOOLS
    assert [tool["function"]["name"] for tool in request["tools"]] == [
        "read", "search", "patch", "test", "commit",
    ]
    assert request["tool_choice"] == "required"
    for tool in request["tools"]:
        parameters = tool["function"]["parameters"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
    search = request["tools"][1]["function"]["parameters"]
    assert search["required"] == ["query"]
    assert search["properties"]["path"] == {"type": ["string", "null"]}
    assert request["reasoning_effort"] == "low"


def test_devworker_uses_direct_groq_route_with_tools(
        monkeypatch, providers_up, transport):
    from octopus import dev_worker, llm

    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")

    def reply(provider, request):
        assert request["tools"] == dev_worker.DEV_ACTION_TOOLS
        assert request["tool_choice"] == "required"
        return llm.TransportResult(
            text='{"action":"search","query":"needle"}',
            usage=llm.Usage(prompt_tokens=10, completion_tokens=5),
            requested_model=request["model"],
            resolved_model="groq/openai/gpt-oss-120b",
            resolved_provider="groq",
            provider_cost_usd=0.0,
        )

    transport.handler = reply
    completion = llm.complete(
        "development.step",
        [{"role": "user", "content": "next"}],
        agent="DEVWORKER",
        profile="zero_cost",
        json_schema=dev_worker.DEV_ACTION_SCHEMA,
        tool_schemas=dev_worker.DEV_ACTION_TOOLS,
        validate=dev_worker._parse_action,
    )

    assert transport.models == ["groq/openai/gpt-oss-120b"]
    assert completion.data == {
        "action": "search", "path": None, "query": "needle", "patch": None, "message": None,
    }


def test_devworker_local_validation_normalizes_unused_nullable_fields():
    from octopus.dev_worker import _parse_action

    assert _parse_action('{"action":"test"}') == {
        "action": "test", "path": None, "query": None, "patch": None, "message": None,
    }


def test_devworker_bounds_history_results_for_free_provider_tpm(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path)
    calls = scripted(monkeypatch, dev_worker, [
        {"action": "search", "query": "worktree"},
        {"action": "read", "path": "octopus/dev_worker.py"},
        {"action": "commit", "message": "fix: bounded history"},
    ])

    def fake_tool(action, worktree, tests, tests_passed):
        if action["action"] == "commit":
            return "fake-commit", True, "fake-commit"
        return action["action"] * 12_000, tests_passed, None

    monkeypatch.setattr(dev_worker, "_tool", fake_tool)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Keep the free-provider prompt bounded.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 3,
        "backend": "declarative",
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    history = json.loads(calls[2][1][1]["content"])["history"]
    assert sum(len(item["result"]) for item in history) <= dev_worker.DEV_HISTORY_CHARS
    assert history[-1]["action"] == "read"
    assert history[-1]["arguments"] == {"path": "octopus/dev_worker.py"}


def test_devworker_refuses_excess_prepatch_inspection(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path)
    actions = [
        {"action": "search", "query": f"query-{index}"}
        for index in range(dev_worker.DEV_INSPECTION_LIMIT + 1)
    ] + [{"action": "commit", "message": "fix: bounded inspection"}]
    calls = scripted(monkeypatch, dev_worker, actions)
    executed = []

    def fake_tool(action, worktree, tests, tests_passed):
        executed.append(action)
        if action["action"] == "commit":
            return "fake-commit", True, "fake-commit"
        return "search result", tests_passed, None

    monkeypatch.setattr(dev_worker, "_tool", fake_tool)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Stop inspecting and make the requested change.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": dev_worker.DEV_INSPECTION_LIMIT + 2,
        "backend": "declarative",
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert len([action for action in executed if action["action"] == "search"]) == dev_worker.DEV_INSPECTION_LIMIT
    state = json.loads(calls[-1][1][1]["content"])
    assert state["inspection_actions_remaining"] == 0
    assert "inspection épuisé" in state["history"][-1]["result"]


def test_devworker_retries_two_bounded_provider_rate_limits(tmp_path, monkeypatch):
    from octopus import dev_worker

    class RateLimitError(Exception):
        def __init__(self, delay):
            super().__init__(f"try again in {delay}s")
            self.response = SimpleNamespace(headers={"retry-after": str(delay)})

    repo = repository(tmp_path)
    calls = []
    sleeps = []

    def complete(task, messages, **kwargs):
        calls.append((task, messages, kwargs))
        if len(calls) <= 2:
            raise RateLimitError(len(calls) / 100)
        action = {"action": "commit", "message": "fix: retry bounded rate limit"}
        return SimpleNamespace(data=action, text=json.dumps(action))

    monkeypatch.setattr(dev_worker.llm, "complete", complete)
    monkeypatch.setattr(dev_worker, "_sleep", sleeps.append)
    monkeypatch.setattr(
        dev_worker, "_tool",
        lambda action, worktree, tests, tests_passed: ("fake-commit", True, "fake-commit"),
    )
    task_id = worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Retry one bounded transient provider limit.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 1,
        "backend": "declarative",
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert len(calls) == 3
    assert sleeps == [0.01, 0.02]
    assert any(event["type"] == "development.rate_limited" for event in tasks.events(task_id=task_id))


def test_devworker_extracts_observed_groq_retry_delay():
    from octopus import dev_worker

    RateLimitError = type("RateLimitError", (Exception,), {})

    assert dev_worker._rate_limit_delay(RateLimitError("Please try again in 26.5425s.")) == 26.5425
    assert dev_worker._rate_limit_delay(RateLimitError("Try again in 15 seconds.")) == 15
    assert dev_worker._rate_limit_delay(RateLimitError("Please try again in 61s.")) is None
    assert dev_worker._rate_limit_delay(RuntimeError("Please try again in 1s.")) is None


def test_devworker_paces_omniroute_step_starts(monkeypatch):
    from octopus import dev_worker

    sleeps = []
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setattr(dev_worker.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(dev_worker, "_sleep", sleeps.append)

    started = dev_worker._wait_for_llm_slot(95.0)

    assert sleeps == [8.0]
    assert started == 108.0

    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    assert dev_worker._wait_for_llm_slot(95.0) == 100.0
    assert sleeps == [8.0]


def test_devworker_retries_one_malformed_provider_tool_call(tmp_path, monkeypatch):
    from octopus import dev_worker

    BadRequestError = type("BadRequestError", (Exception,), {})
    repo = repository(tmp_path)
    calls = []

    def complete(task, messages, **kwargs):
        calls.append((task, messages, kwargs))
        if len(calls) == 1:
            raise BadRequestError("Failed to parse tool call arguments as JSON")
        action = {"action": "commit", "message": "fix: retry malformed tool call"}
        return SimpleNamespace(data=action, text=json.dumps(action))

    monkeypatch.setattr(dev_worker.llm, "complete", complete)
    monkeypatch.setattr(
        dev_worker, "_tool",
        lambda action, worktree, tests, tests_passed: ("fake-commit", True, "fake-commit"),
    )
    task_id = worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Retry one malformed declarative tool call.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 1,
        "backend": "declarative",
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert len(calls) == 2
    assert "valid JSON" in calls[1][1][-1]["content"]
    assert any(event["type"] == "development.structured_retry" for event in tasks.events(task_id=task_id))
    assert dev_worker._malformed_tool_call(
        BadRequestError("attempted to call tool 'search' which was not in request.tools"),
    ) is False


@pytest.mark.parametrize("payload", [
    {"action": "read", "query": None, "patch": None, "message": None},
    {"action": "test", "path": 7, "query": None, "patch": None, "message": None},
    {"action": "test", "path": None, "query": None, "patch": None, "message": None, "extra": True},
])
def test_devworker_local_validation_enforces_full_schema(payload):
    from octopus.dev_worker import _parse_action

    with pytest.raises(ValueError):
        _parse_action(json.dumps(payload))


def test_devworker_search_null_path_defaults_to_worktree(tmp_path, monkeypatch):
    from octopus import dev_worker

    seen = {}

    def fake_run(args, cwd, **kwargs):
        seen["args"] = args
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(dev_worker, "_run", fake_run)
    output, passed, commit = dev_worker._tool(
        {"action": "search", "query": "needle", "path": None},
        tmp_path,
        [],
        False,
    )

    assert seen["args"][-1] == str(tmp_path.resolve())
    assert output == "aucun r\u00e9sultat"
    assert passed is False
    assert commit is None


@pytest.mark.parametrize("patch", [
    """--- a/calc.py
+++ b/calc.py
@@
 def answer():
-    return 1
+    return 2
""",
    """*** Begin Patch
*** Update File: calc.py
@@
 def answer():
-    return 1
+    return 2
*** End Patch
""",
])
def test_devworker_applies_safe_context_patches_without_hunk_numbers(tmp_path, patch):
    from octopus import dev_worker

    repo = repository(tmp_path)

    output, passed, commit = dev_worker._tool(
        {"action": "patch", "patch": patch}, repo, [], True,
    )

    assert (repo / "calc.py").read_text(encoding="utf-8") == "def answer():\n    return 2\n"
    assert "calc.py" in output
    assert "+    return 2" in output
    assert passed is False
    assert commit is None


def test_devworker_context_patch_rejects_ambiguous_match(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path)
    (repo / "calc.py").write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    patch = """--- a/calc.py
+++ b/calc.py
@@
-value = 1
+value = 2
"""

    with pytest.raises(dev_worker.DevWorkerError, match="unique"):
        dev_worker._tool({"action": "patch", "patch": patch}, repo, [], False)


def test_devworker_context_patch_tolerates_hybrid_wrapper_and_unprefixed_addition(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path)
    patch = """--- a/calc.py
+++ b/calc.py
@@
-def answer():
+def answer(
):
     return 1
*** End Patch
"""

    dev_worker._tool({"action": "patch", "patch": patch}, repo, [], False)

    assert (repo / "calc.py").read_text(encoding="utf-8") == "def answer(\n):\n    return 1\n"


def test_devworker_wrapped_patch_rejects_path_outside_worktree(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: ../secret.txt
@@
-secret
+changed
*** End Patch
"""

    with pytest.raises(dev_worker.DevWorkerError, match="hors worktree"):
        dev_worker._tool({"action": "patch", "patch": patch}, repo, [], False)
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_devworker_cannot_commit_after_failed_tests(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    source_head = git(repo, "rev-parse", "HEAD")
    scripted(monkeypatch, dev_worker, [
        {"action": "test"},
        {"action": "commit", "message": "bad commit"},
    ])
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo), "goal": "Do not bypass tests.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]], "max_steps": 2,
        "backend": "declarative",
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    worktrees = list((Path(os.environ["OCTOPUS_DEV_WORKTREE_ROOT"])).glob("*"))
    assert len(worktrees) == 1
    assert git(worktrees[0], "rev-parse", "HEAD") == source_head


def test_devworker_kilo_run_is_inline_configured_and_deny_by_default(tmp_path, monkeypatch):
    from octopus import dev_worker

    monkeypatch.setenv("AZURE_SPEECH_KEY", "must-not-leak")
    monkeypatch.setenv("OCTOPUS_SUPER_SECRET", "must-not-leak")
    monkeypatch.setenv("KILO_API_KEY", "allowed-auth")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    captured = {}

    def fake_run_kilo_process(args, cwd, env):
        captured.update(args=args, cwd=str(cwd), env=env)
        return SimpleNamespace(returncode=0, stdout='{"type":"text","text":"done"}\n', stderr="")

    monkeypatch.setattr(dev_worker, "_run_kilo_process", fake_run_kilo_process)
    output = dev_worker._run_kilo(
        worktree,
        "Change only calc.py.",
        [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        4,
    )

    args = captured["args"]
    assert args[:2] == ["kilo.cmd" if os.name == "nt" else "kilo", "run"]
    assert "--auto" in args
    assert "--pure" not in args
    assert args[args.index("--dir") + 1] == str(worktree)
    assert args[args.index("--model") + 1] == "kilo/stepfun/step-3.7-flash:free"
    assert args[args.index("--agent") + 1] == "octopus-devworker"
    assert args[args.index("--format") + 1] == "json"
    assert "Change only calc.py." in args[-1]
    assert "test_calc.py" in args[-1]
    assert "\\n" not in args[-1]
    assert "\\r" not in args[-1]
    assert "OBJECTIVE: Change only calc.py." in args[-1]
    assert "Explore enough repository context" in args[-1]
    assert "Do not survey the entire repository." not in args[-1]
    assert "Prefer grep or glob before reading files." not in args[-1]
    assert captured["cwd"] == str(worktree)
    assert output.endswith("done\"}\n")

    env = captured["env"]
    assert env["KILO_DISABLE_PROJECT_CONFIG"] == "1"
    assert env["KILO_PURE"] == "1"
    assert env["KILO_TELEMETRY_LEVEL"] == "off"
    assert env["KILO_API_KEY"] == "allowed-auth"
    assert "AZURE_SPEECH_KEY" not in env
    assert "OCTOPUS_SUPER_SECRET" not in env
    assert env["XDG_CONFIG_HOME"]
    assert "KILO_CONFIG" not in env
    assert "KILO_CONFIG_DIR" not in env

    config = json.loads(env["KILO_CONFIG_CONTENT"])
    assert config["plugin"] == []
    assert config["mcp"] == {}
    assert config["compaction"] == {"auto": True, "prune": True, "threshold_percent": 65}
    agent = config["agent"]["octopus-devworker"]
    assert agent["mode"] == "primary"
    assert agent["steps"] == 4
    permissions = agent["permission"]
    assert permissions["*"] == "deny"
    assert set(permissions) >= {
        "*", "read", "glob", "grep", "list", "edit", "write", "bash", "task", "agent_manager",
        "skill", "websearch", "webfetch", "external_directory",
    }
    for name in ("bash", "task", "agent_manager", "skill", "websearch", "webfetch", "external_directory"):
        assert permissions[name] == "deny"
    for name in ("read", "glob", "grep", "list", "edit", "write"):
        assert permissions[name] != "deny"
    for name in ("read", "edit", "write"):
        assert permissions[name]["*"] == "allow"
        assert permissions[name]["**/.env"] == "deny"
        assert permissions[name]["**/.env.*"] == "deny"
        assert permissions[name]["**/.git/**"] == "deny"
        assert permissions[name]["**/*credential*"] == "deny"
        assert permissions[name]["**/*secret*"] == "deny"


def test_run_kilo_process_starts_isolated_process_group(tmp_path, monkeypatch):
    from octopus import dev_worker

    captured = {}

    class FakeProcess:
        pid = 4321
        returncode = 0

        def communicate(self, timeout):
            captured["timeout"] = timeout
            return "ok", ""

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(dev_worker.subprocess, "Popen", fake_popen)

    result = dev_worker._run_kilo_process(["kilo", "run"], tmp_path, {"PATH": "x"})

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert 0 < captured["timeout"] <= dev_worker.KILO_POLL_S
    if os.name == "nt":
        assert captured["kwargs"]["creationflags"] == getattr(dev_worker.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        assert "start_new_session" not in captured["kwargs"]
    else:
        assert captured["kwargs"]["start_new_session"] is True
        assert "creationflags" not in captured["kwargs"]


def test_run_kilo_process_timeout_kills_process_tree(tmp_path, monkeypatch):
    from octopus import dev_worker

    killed = []

    class FakeProcess:
        pid = 4321
        returncode = None

        def communicate(self, timeout):
            raise dev_worker.subprocess.TimeoutExpired(["kilo", "run"], timeout)

    monkeypatch.setattr(dev_worker.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(dev_worker, "_terminate_process_tree", lambda process: killed.append(process.pid))
    monkeypatch.setattr(dev_worker, "KILO_TIMEOUT_S", 0.01)
    monkeypatch.setattr(dev_worker, "KILO_POLL_S", 0.01)

    with pytest.raises(dev_worker.DevWorkerError, match="arbre de processus arrêté"):
        dev_worker._run_kilo_process(["kilo", "run"], tmp_path, {"PATH": "x"})

    assert killed == [4321]


def test_run_kilo_process_live_kill_switch_stops_tree(tmp_path, monkeypatch):
    from octopus import dev_worker

    killed = []
    data = tmp_path / "data"
    data.mkdir()
    (data / "NIGHT_SHIFT_STOP").write_text("stop\n", encoding="utf-8")

    class FakeProcess:
        pid = 9876
        returncode = None

        def communicate(self, timeout):
            raise AssertionError("communicate must not run after kill switch")

    monkeypatch.setattr(dev_worker.paths, "data_dir", lambda: data)
    monkeypatch.setattr(dev_worker.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(dev_worker, "_terminate_process_tree", lambda process: killed.append(process.pid))

    with pytest.raises(dev_worker.DevWorkerError, match="NIGHT_SHIFT_STOP"):
        dev_worker._run_kilo_process(["kilo", "run"], tmp_path, {"PATH": "x"})

    assert killed == [9876]


def test_kilo_permissions_restrict_writes_to_allowed_paths():
    from octopus import dev_worker

    permissions = dev_worker._kilo_permissions(["octopus/capabilities.py"])

    assert permissions["edit"]["*"] == "deny"
    assert permissions["write"]["*"] == "deny"
    assert permissions["edit"]["octopus/capabilities.py"] == "allow"
    assert permissions["write"]["octopus/capabilities.py"] == "allow"
    assert permissions["edit"]["**/.git/**"] == "deny"
    assert permissions["write"]["*.pem"] == "deny"


def test_devworker_kilo_failure_includes_bounded_cli_error(tmp_path, monkeypatch):
    from octopus import dev_worker

    monkeypatch.setattr(
        dev_worker,
        "_run_kilo_process",
        lambda *args, **kwargs: SimpleNamespace(returncode=7, stdout="partial", stderr="gateway failed"),
    )

    with pytest.raises(dev_worker.DevWorkerError, match="gateway failed"):
        dev_worker._run_kilo(
            tmp_path, "change", [[sys.executable, "-m", "pytest", "-q", "test_x.py"]], 2,
        )


def test_run_kilo_preserves_supplied_retry_prompt(tmp_path, monkeypatch):
    from octopus import dev_worker

    seen = {}

    def fake_run_kilo_process(args, cwd, env):
        seen["args"] = args
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(dev_worker, "_run_kilo_process", fake_run_kilo_process)

    dev_worker._run_kilo(
        tmp_path,
        "change",
        [[sys.executable, "-m", "pytest", "-q", "test_x.py"]],
        2,
        prompt="SENTINEL_RETRY_PROMPT",
    )

    assert seen["args"][-1] == "SENTINEL_RETRY_PROMPT"


def test_development_task_retries_kilo_after_diff_check_failure(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    prompts = []

    def repairing_kilo(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            (worktree / "calc.py").write_text(
                "def answer():  \n    return 2\n",
                encoding="utf-8",
            )
        else:
            (worktree / "calc.py").write_text(
                "def answer():\n    return 2\n",
                encoding="utf-8",
            )
        return "edited"

    monkeypatch.setattr(dev_worker, "_run_kilo", repairing_kilo)

    task_id = worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 7,
        "allowed_paths": ["calc.py"],
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert result["output"]["backend"] == "kilo"
    assert len(prompts) == 2
    assert "PREVIOUS_FEEDBACK:" in prompts[1]
    assert "diff_check:" in prompts[1]
    assert "trailing whitespace" in prompts[1]
    assert any(event["type"] == "development.kilo_retry" for event in tasks.events(task_id=task_id))


def test_deterministic_diff_check_fix_repairs_only_valid_json_added_lines(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path)
    (repo / "config.json").write_text('{"value": 1}\n', encoding="utf-8")
    git(repo, "add", "config.json")
    git(repo, "commit", "-qm", "add config")
    original_head = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current")

    (repo / "config.json").write_text('{"value": 2}  \n', encoding="utf-8")

    assert dev_worker._validate_kilo_result(
        repo, original_head, branch, allowed_paths=["config.json"], check_diff=False,
    ) == ["config.json"]

    fixes = dev_worker._deterministic_diff_check_fixes(repo, ["config.json"])

    assert fixes == [{
        "fixer_id": "json_trailing_whitespace_v1",
        "file": "config.json",
        "lines": [1],
    }]
    assert (repo / "config.json").read_text(encoding="utf-8") == '{"value": 2}\n'
    assert dev_worker._validate_kilo_result(
        repo, original_head, branch, allowed_paths=["config.json"],
    ) == ["config.json"]


def test_deterministic_diff_check_fix_preserves_markdown_hard_break_and_guides_retry(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path)
    docs = repo / "docs"
    docs.mkdir()
    gui = docs / "GUI.md"
    gui.write_text("Before\n", encoding="utf-8")
    git(repo, "add", "docs/GUI.md")
    git(repo, "commit", "-qm", "add docs")

    gui.write_text("Sous Operate : Missions et Agents.  \n", encoding="utf-8")
    before = gui.read_bytes()

    assert dev_worker._deterministic_diff_check_fixes(repo, ["docs/GUI.md"]) == []
    assert gui.read_bytes() == before

    feedback = dev_worker._augment_diff_check_feedback(
        "docs/GUI.md:1: trailing whitespace.\n+Sous Operate : Missions et Agents.  "
    )
    assert "MARKDOWN_TRAILING_WHITESPACE_GUIDANCE" in feedback
    assert "<br>" in feedback
    assert "do not repeat the same invalid diff" in feedback


def test_development_task_repairs_json_whitespace_without_spending_second_kilo_pass(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path)
    (repo / "config.json").write_text('{"value": 1}\n', encoding="utf-8")
    git(repo, "add", "config.json")
    git(repo, "commit", "-qm", "add config")
    prompts = []

    def kilo_once(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        prompts.append(prompt)
        (worktree / "config.json").write_text('{"value": 2}  \n', encoding="utf-8")
        return '{"type":"text","text":"updated config"}\n'

    monkeypatch.setattr(dev_worker, "_run_kilo", kilo_once)

    task_id = worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Update the JSON value.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 7,
        "allowed_paths": ["config.json"],
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert len(prompts) == 1
    assert result["output"]["deterministic_fixes"] == [{
        "fixer_id": "json_trailing_whitespace_v1",
        "file": "config.json",
        "lines": [1],
    }]
    worktree = Path(result["output"]["worktree"])
    assert (worktree / "config.json").read_text(encoding="utf-8") == '{"value": 2}\n'
    events = tasks.events(task_id=task_id)
    assert any(event["type"] == "development.deterministic_fix" for event in events)


def test_validate_allowed_paths_rejects_absolute_parent_and_sensitive_paths():
    from octopus import dev_worker

    for value in ([r"C:\\outside.py"], ["../outside.py"], [".env"]):
        with pytest.raises(dev_worker.DevWorkerError):
            dev_worker._validate_allowed_paths(value)


def _octopus_self_repo(tmp_path):
    repo = tmp_path / "octopus-self"
    (repo / "octopus").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    (repo / "octopus" / "dev_worker.py").write_text("# marker\n", encoding="utf-8")
    (repo / "octopus" / "night_shift.py").write_text("# marker\n", encoding="utf-8")
    (repo / "octopus" / "capabilities.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_capabilities.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    return repo


def _self_policy_kwargs(repo):
    return {
        "repository": repo,
        "backend": "kilo",
        "allowed_paths": ["octopus/capabilities.py"],
        "tests": [[sys.executable, "-m", "pytest", "-q", "tests/test_capabilities.py"]],
        "test_sandbox": "docker",
        "test_sandbox_image": "octopus-test-sandbox:py311",
        "max_files_changed": 1,
        "max_lines_added": 20,
        "max_lines_deleted": 20,
        "strict_repository_preflight": True,
        "require_baseline_oracle": True,
        "python_canary_ast": True,
        "allow_declarative_fallback": False,
        "product_ticket": False,
    }


def test_octopus_self_policy_accepts_only_registered_python_canary(tmp_path):
    from octopus import dev_worker

    values = _self_policy_kwargs(_octopus_self_repo(tmp_path))

    assert dev_worker._validate_octopus_self_modification_policy(**values) is True


@pytest.mark.parametrize("change, message", [
    ({"backend": "declarative"}, "backend=kilo"),
    ({"allowed_paths": None}, "allowed_paths explicite"),
    ({"allowed_paths": ["octopus/economy.py"]}, "surface Python non approuvée"),
    ({"tests": [[sys.executable, "-m", "pytest", "-q", "tests/test_resources.py"]]}, "oracle Python attendu"),
    ({"test_sandbox": "host"}, "test_sandbox=docker"),
    ({"test_sandbox_image": "custom:latest"}, "image sandbox Python OCTOPUS non approuvée"),
    ({"max_files_changed": 2}, "max_files_changed=1"),
    ({"max_lines_added": 81}, "max_lines_added entre 1 et 80"),
    ({"max_lines_deleted": None}, "max_lines_deleted entre 1 et 80"),
    ({"strict_repository_preflight": False}, "strict_repository_preflight"),
    ({"require_baseline_oracle": False}, "require_baseline_oracle"),
    ({"python_canary_ast": False}, "python_canary_ast"),
    ({"allow_declarative_fallback": True}, "fallback declarative interdit"),
])
def test_octopus_self_policy_rejects_python_bypasses(tmp_path, change, message):
    from octopus import dev_worker

    values = _self_policy_kwargs(_octopus_self_repo(tmp_path))
    values.update(change)

    with pytest.raises(dev_worker.DevWorkerError, match=message):
        dev_worker._validate_octopus_self_modification_policy(**values)


def test_octopus_self_policy_accepts_scoped_product_ticket(tmp_path):
    from octopus import dev_worker

    values = _self_policy_kwargs(_octopus_self_repo(tmp_path))
    values.update(
        allowed_paths=["agents/gui/intelligence.py", "agents/gui/workbench.py"],
        tests=[
            [sys.executable, "-m", "pytest", "-q", "tests/test_gui.py"],
            [sys.executable, "-m", "pytest", "-q", "tests/test_gui_intelligence.py"],
        ],
        test_sandbox="docker",
        max_files_changed=2,
        max_lines_added=1200,
        max_lines_deleted=1200,
        strict_repository_preflight=True,
        require_baseline_oracle=True,
        python_canary_ast=False,
        product_ticket=True,
    )

    assert dev_worker._validate_octopus_self_modification_policy(**values) is False


def test_octopus_product_ticket_allows_scoped_non_python_product_files(tmp_path):
    from octopus import dev_worker

    values = _self_policy_kwargs(_octopus_self_repo(tmp_path))
    values.update(
        allowed_paths=["octopus/config/catalog.json"],
        tests=[[sys.executable, "-m", "pytest", "-q", "tests/test_pricing_catalog.py"]],
        test_sandbox="docker",
        max_files_changed=1,
        max_lines_added=500,
        max_lines_deleted=500,
        strict_repository_preflight=True,
        require_baseline_oracle=True,
        python_canary_ast=False,
        product_ticket=True,
    )

    assert dev_worker._validate_octopus_self_modification_policy(**values) is False


@pytest.mark.parametrize("change, message", [
    ({"allowed_paths": ["octopus/dev_worker.py"]}, "frontière de sécurité"),
    ({"allowed_paths": ["agents/web_guard.py"]}, "frontière de sécurité"),
    ({"allowed_paths": ["agents/browser.py"]}, "frontière de sécurité"),
    ({"allowed_paths": ["agents/publish.py"]}, "frontière de sécurité"),
    ({"allowed_paths": ["docker/dev-sandbox.Dockerfile"]}, "frontière de sécurité"),
    ({"allowed_paths": ["tests/test_gui.py"]}, "oracles de test"),
    ({"test_sandbox": "host"}, "test_sandbox=docker"),
    ({"require_baseline_oracle": False}, "require_baseline_oracle"),
    ({"max_files_changed": 21}, "max_files_changed"),
    ({"max_lines_added": 5001}, "max_lines_added"),
    ({"python_canary_ast": True}, "n'utilise pas python_canary_ast"),
])
def test_octopus_product_ticket_rejects_boundary_bypasses(tmp_path, change, message):
    from octopus import dev_worker

    values = _self_policy_kwargs(_octopus_self_repo(tmp_path))
    values.update(
        allowed_paths=["agents/gui/intelligence.py", "agents/gui/workbench.py"],
        tests=[[sys.executable, "-m", "pytest", "-q", "tests/test_gui.py"]],
        test_sandbox="docker",
        max_files_changed=2,
        max_lines_added=1200,
        max_lines_deleted=1200,
        strict_repository_preflight=True,
        require_baseline_oracle=True,
        python_canary_ast=False,
        product_ticket=True,
    )
    values.update(change)

    with pytest.raises(dev_worker.DevWorkerError, match=message):
        dev_worker._validate_octopus_self_modification_policy(**values)


def test_octopus_self_policy_allows_scoped_docs_but_protects_governance(tmp_path):
    from octopus import dev_worker

    repo = _octopus_self_repo(tmp_path)
    values = _self_policy_kwargs(repo)
    values.update(
        allowed_paths=["docs/CURRENT_STATE.md"],
        tests=[[sys.executable, "-m", "pytest", "-q", "tests/test_capabilities.py"]],
        test_sandbox="host",
        max_files_changed=None,
        max_lines_added=None,
        max_lines_deleted=None,
        strict_repository_preflight=False,
        require_baseline_oracle=False,
        python_canary_ast=False,
    )
    assert dev_worker._validate_octopus_self_modification_policy(**values) is False

    values["allowed_paths"] = ["docs/ACCEPTANCE_GATES.md"]
    with pytest.raises(dev_worker.DevWorkerError, match="gouvernance protégé"):
        dev_worker._validate_octopus_self_modification_policy(**values)


def test_development_task_kilo_requires_explicit_write_scope(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "No implicit write scope.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "backend": "kilo",
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    assert "allowed_paths explicite" in result["error"]


def test_development_task_rejects_kilo_change_outside_allowed_paths(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)

    def out_of_scope_kilo(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        (worktree / "calc.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
        (worktree / "extra.py").write_text("x = 1\n", encoding="utf-8")
        return "edited"

    monkeypatch.setattr(dev_worker, "_run_kilo", out_of_scope_kilo)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Only change calc.py.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "allowed_paths": ["calc.py"],
        "allow_declarative_fallback": False,
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    assert "hors périmètre autorisé" in result["error"]


def test_development_task_disables_declarative_fallback_when_requested(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    declarative_calls = []

    monkeypatch.setattr(
        dev_worker,
        "_run_kilo",
        lambda *args, **kwargs: (_ for _ in ()).throw(dev_worker.DevWorkerError("Kilo unavailable")),
    )
    monkeypatch.setattr(
        dev_worker,
        "_run_declarative_backend",
        lambda *args, **kwargs: declarative_calls.append((args, kwargs)),
    )
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "backend": "kilo",
        "allowed_paths": ["calc.py"],
        "allow_declarative_fallback": False,
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    assert declarative_calls == []
    assert "fallback declarative désactivé" in result["error"]


def test_sanitized_test_env_hides_credentials_and_uses_temp_home(monkeypatch, tmp_path):
    from octopus import dev_worker

    monkeypatch.setenv("OMNIROUTE_API_KEY", "secret")
    monkeypatch.setenv("SOME_TOKEN", "secret")
    monkeypatch.setenv("NORMAL_VALUE", "kept")

    env = dev_worker._sanitized_test_env(str(tmp_path))

    assert "OMNIROUTE_API_KEY" not in env
    assert "SOME_TOKEN" not in env
    assert "NORMAL_VALUE" not in env
    assert env["HOME"] == str(tmp_path)
    assert env["USERPROFILE"] == str(tmp_path)
    assert env["XDG_CONFIG_HOME"] == str(tmp_path)
    assert env["PYTEST_ADDOPTS"] == "-p no:cacheprovider"


def test_docker_test_args_are_networkless_read_only_and_secret_free(tmp_path):
    from octopus import dev_worker

    args = dev_worker._docker_test_args(
        tmp_path,
        "octopus-test-sandbox:py311",
        [sys.executable, "-m", "pytest", "-q", "tests/test_capabilities.py"],
    )

    assert args[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == args[args.index("--network"):args.index("--network") + 2]
    assert "--read-only" in args
    assert "--pull" in args and "never" in args
    assert "--init" in args
    assert ["--cap-drop", "ALL"] == args[args.index("--cap-drop"):args.index("--cap-drop") + 2]
    assert ["--security-opt", "no-new-privileges"] == args[
        args.index("--security-opt"):args.index("--security-opt") + 2
    ]
    assert ["--user", "10001:10001"] == args[args.index("--user"):args.index("--user") + 2]
    assert ["--memory-swap", "768m"] == args[
        args.index("--memory-swap"):args.index("--memory-swap") + 2
    ]
    mount = args[args.index("--mount") + 1]
    assert mount.startswith("type=bind,source=")
    assert mount.endswith(",target=/workspace,readonly")
    assert "octopus-test-sandbox:py311" in args
    assert "-rA" in args
    joined = " ".join(args)
    assert "OMNIROUTE_API_KEY" not in joined
    assert "TOKEN" not in joined
    assert "SECRET" not in joined


def test_run_tests_docker_fails_closed_without_image(tmp_path, monkeypatch):
    from octopus import dev_worker

    calls = []

    def fake_run(args, cwd, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "info", "--format"]:
            return SimpleNamespace(returncode=0, stdout="linux\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="missing")

    monkeypatch.setattr(dev_worker, "_run", fake_run)

    with pytest.raises(dev_worker.DevWorkerError, match="image sandbox Docker absente"):
        dev_worker._run_tests(
            tmp_path,
            [[sys.executable, "-m", "pytest", "-q", "tests/test_capabilities.py"]],
            sandbox="docker",
            sandbox_image="octopus-test-sandbox:py311",
        )

    assert calls[0][:2] == ["docker", "info"]
    assert calls[1][:3] == ["docker", "image", "inspect"]


def test_run_tests_docker_uses_stable_tag_and_verifies_pinned_identity(tmp_path, monkeypatch):
    from octopus import dev_worker

    expected = "sha256:" + "a" * 64
    calls = []

    def fake_run(args, cwd, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "info", "--format"]:
            return SimpleNamespace(returncode=0, stdout="linux\n", stderr="")
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=expected + "\n", stderr="")
        if args[:2] == ["docker", "run"]:
            return SimpleNamespace(returncode=0, stdout="PASSED test_x.py::test_x\n", stderr="")
        if args[:3] == ["docker", "rm", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(dev_worker, "_run", fake_run)

    output, green = dev_worker._run_tests(
        tmp_path,
        [[sys.executable, "-m", "pytest", "-q", "test_x.py"]],
        sandbox="docker",
        sandbox_image="octopus-test-sandbox:py311",
        expected_image_id=expected,
    )

    assert green is True
    run_args = next(args for args in calls if args[:2] == ["docker", "run"])
    assert "octopus-test-sandbox:py311" in run_args
    assert expected not in run_args
    assert "PASSED" in output


def test_run_tests_docker_fails_closed_if_tag_identity_changes(tmp_path, monkeypatch):
    from octopus import dev_worker

    expected = "sha256:" + "a" * 64
    actual = "sha256:" + "b" * 64

    def fake_run(args, cwd, **kwargs):
        if args[:3] == ["docker", "info", "--format"]:
            return SimpleNamespace(returncode=0, stdout="linux\n", stderr="")
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=actual + "\n", stderr="")
        raise AssertionError("docker run must not start after identity mismatch")

    monkeypatch.setattr(dev_worker, "_run", fake_run)

    with pytest.raises(dev_worker.DevWorkerError, match="modifiée depuis le pré-vol"):
        dev_worker._run_tests(
            tmp_path,
            [[sys.executable, "-m", "pytest", "-q", "test_x.py"]],
            sandbox="docker",
            sandbox_image="octopus-test-sandbox:py311",
            expected_image_id=expected,
        )


def test_validate_kilo_result_enforces_diff_radius(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)
    original_head = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current")
    (repo / "calc.py").write_text(
        "def answer():\n    value = 1\n    return value\n",
        encoding="utf-8",
    )

    with pytest.raises(dev_worker.DevWorkerError, match="rayon de modification dépassé"):
        dev_worker._validate_kilo_result(
            repo,
            original_head,
            branch,
            allowed_paths=["calc.py"],
            max_files_changed=1,
            max_lines_added=1,
            max_lines_deleted=10,
        )


def test_development_task_can_use_docker_test_sandbox(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)

    def fake_kilo(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        (worktree / "calc.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
        return "edited"

    seen = []

    def fake_run_tests(
            worktree, tests, *, sandbox="host", sandbox_image=dev_worker.DEFAULT_TEST_SANDBOX_IMAGE,
            expected_image_id=None):
        seen.append((sandbox, sandbox_image, expected_image_id, tests))
        return "green", True

    monkeypatch.setattr(dev_worker, "_run_kilo", fake_kilo)
    monkeypatch.setattr(dev_worker, "_run_tests", fake_run_tests)
    monkeypatch.setattr(dev_worker, "_docker_image_id", lambda *args, **kwargs: "sha256:fixed")

    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 12,
        "backend": "kilo",
        "allowed_paths": ["calc.py"],
        "test_sandbox": "docker",
        "test_sandbox_image": "octopus-test-sandbox:py311",
        "max_files_changed": 1,
        "max_lines_added": 10,
        "max_lines_deleted": 10,
        "allow_declarative_fallback": False,
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert result["output"]["test_sandbox"] == "docker"
    assert seen
    assert seen[0][0] == "docker"
    assert seen[0][1] == "octopus-test-sandbox:py311"
    assert seen[0][2] == "sha256:fixed"
    assert result["output"]["test_sandbox_image"] == "sha256:fixed"
    assert result["output"]["test_sandbox_image_ref"] == "octopus-test-sandbox:py311"


def test_task_clone_is_independent_and_outside_source_repo(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)
    clone, branch = dev_worker._create_worktree(repo, 999)

    assert clone.is_dir()
    assert repo.resolve() not in clone.resolve().parents
    assert git(clone, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert git(clone, "branch", "--show-current") == branch
    assert git(clone, "remote") == ""


def test_validate_kilo_result_rejects_ignored_file(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)
    (repo / ".gitignore").write_text("ignored.tmp\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore")
    original_head = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current")
    (repo / "ignored.tmp").write_text("hidden\n", encoding="utf-8")

    with pytest.raises(dev_worker.DevWorkerError, match="fichier ignoré"):
        dev_worker._validate_kilo_result(
            repo, original_head, branch, allowed_paths=["calc.py"],
        )


def test_strict_repository_preflight_rejects_tracked_secret_like_file(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)
    (repo / "server.pem").write_text("fake\n", encoding="utf-8")
    git(repo, "add", "server.pem")
    git(repo, "commit", "-qm", "tracked secret")

    with pytest.raises(dev_worker.DevWorkerError, match="fichiers sensibles suivis"):
        dev_worker._strict_repository_preflight(repo)


def test_python_canary_ast_rejects_new_import_and_module_side_effect(tmp_path):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)
    original_head = git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text(
        "import os\n\nos._exit(0)\n\ndef answer():\n    return 1\n",
        encoding="utf-8",
    )

    with pytest.raises(dev_worker.KiloRepairableError, match="nouvel import"):
        dev_worker._validate_python_canary_ast(repo, original_head, ["calc.py"])


def test_oracle_signature_requires_real_report_lines():
    from octopus import dev_worker

    assert dev_worker._pytest_oracle_signature("all good\n1 passed in 0.01s\n") == ()
    assert dev_worker._pytest_oracle_signature(
        "PASSED tests/test_x.py::test_a\nPASSED tests/test_x.py::test_b\n"
    ) == (
        ("PASSED", "tests/test_x.py::test_a"),
        ("PASSED", "tests/test_x.py::test_b"),
    )


def test_kilo_prompt_allows_broad_reading_but_keeps_write_scope():
    from octopus import dev_worker

    prompt = dev_worker._build_kilo_prompt(
        "Update the documented state.",
        [[sys.executable, "-m", "pytest", "-q", "tests/test_dev_worker.py"]],
        20,
        allowed_paths=["docs/CURRENT_STATE.md"],
        acceptance_criteria=["remove stale branch reference", "preserve project rules"],
        noop_allowed=True,
        repository_context="branch=topic; head=abc; recent_commits=abc latest",
    )

    assert "All other tracked repository files may be read as needed" in prompt
    assert "MODIFIABLE: docs/CURRENT_STATE.md" in prompt
    assert "SUCCESS_CRITERIA: remove stale branch reference ; preserve project rules." in prompt
    assert "BUDGET: 20 steps" in prompt
    assert "REPOSITORY_FACTS: branch=topic; head=abc" in prompt
    assert "NO_CHANGE_NEEDED" in prompt
    assert "Do not survey the entire repository" not in prompt


def test_development_task_accepts_justified_noop_when_allowed(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)

    monkeypatch.setattr(
        dev_worker,
        "_run_kilo",
        lambda *args, **kwargs: (
            '{"type":"text","text":"Verified the requested state from the repository.\\nNO_CHANGE_NEEDED"}\n'
        ),
    )
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Review the current documentation.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 12,
        "backend": "kilo",
        "allowed_paths": ["calc.py"],
        "noop_allowed": True,
        "allow_declarative_fallback": False,
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert result["output"]["noop"] is True
    assert result["output"]["commit"] is None
    assert result["output"]["changed_paths"] == []


def test_no_changes_without_explicit_noop_remains_repairable(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=True)
    calls = []

    def no_edit(*args, **kwargs):
        calls.append(kwargs.get("prompt"))
        return '{"type":"text","text":"I did not edit anything."}\n'

    monkeypatch.setattr(dev_worker, "_run_kilo", no_edit)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make a required edit.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 12,
        "backend": "kilo",
        "allowed_paths": ["calc.py"],
        "noop_allowed": True,
        "allow_declarative_fallback": False,
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    assert len(calls) == dev_worker.KILO_MAX_PASSES
    assert "PREVIOUS_FINAL:" in calls[1]


def test_development_task_uses_kilo_then_validates_tests_and_commits(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    source_head = git(repo, "rev-parse", "HEAD")
    seen = {}

    def fake_kilo(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        seen.update(worktree=worktree, goal=goal, tests=tests, max_steps=max_steps)
        (worktree / "calc.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
        return "edited"

    monkeypatch.setattr(dev_worker, "_run_kilo", fake_kilo)
    monkeypatch.setattr(
        dev_worker.llm,
        "complete",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("declarative backend not expected")),
    )
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 7,
        "allowed_paths": ["calc.py"],
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    output = result["output"]
    worktree = Path(output["worktree"])
    assert seen["worktree"] == worktree
    assert seen["max_steps"] == 7
    assert output["backend"] == "kilo"
    assert output["changed_paths"] == ["calc.py"]
    assert output["commit"] == git(worktree, "rev-parse", "HEAD")
    assert output["commit"] != source_head
    assert git(repo, "rev-parse", "HEAD") == source_head
    assert git(worktree, "status", "--porcelain") == ""


def test_development_task_falls_back_only_when_kilo_left_worktree_clean(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    patch = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def answer():
-    return 1
+    return 2
"""
    monkeypatch.setattr(
        dev_worker,
        "_run_kilo",
        lambda *args, **kwargs: (_ for _ in ()).throw(dev_worker.DevWorkerError("Kilo unavailable")),
    )
    scripted(monkeypatch, dev_worker, [
        {"action": "patch", "patch": patch},
        {"action": "test"},
        {"action": "commit", "message": "fix: complete clean fallback"},
    ])
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 3,
        "allowed_paths": ["calc.py"],
        "allow_declarative_fallback": True,
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "done"
    assert result["output"]["backend"] == "declarative"
    assert result["output"]["fallback_from"] == "kilo"


def test_development_task_does_not_fallback_after_kilo_modification(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    source_head = git(repo, "rev-parse", "HEAD")
    declarative_calls = []

    def dirty_failure(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        (worktree / "calc.py").write_text("def answer():\n    return 3\n", encoding="utf-8")
        raise dev_worker.DevWorkerError("Kilo failed after editing")

    monkeypatch.setattr(dev_worker, "_run_kilo", dirty_failure)
    monkeypatch.setattr(dev_worker.llm, "complete", lambda *args, **kwargs: declarative_calls.append(args))
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "allowed_paths": ["calc.py"],
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    assert declarative_calls == []
    worktree = next((Path(os.environ["OCTOPUS_DEV_WORKTREE_ROOT"])).glob("*"))
    assert git(worktree, "rev-parse", "HEAD") == source_head
    assert "calc.py" in git(worktree, "status", "--porcelain")


def test_development_task_rejects_commit_created_by_kilo(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    source_head = git(repo, "rev-parse", "HEAD")

    def committing_kilo(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        (worktree / "calc.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
        git(worktree, "add", "calc.py")
        git(worktree, "commit", "-qm", "forbidden Kilo commit")
        return "committed"

    monkeypatch.setattr(dev_worker, "_run_kilo", committing_kilo)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "allowed_paths": ["calc.py"],
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    worktree = next((Path(os.environ["OCTOPUS_DEV_WORKTREE_ROOT"])).glob("*"))
    assert git(worktree, "rev-parse", "HEAD") != source_head


def test_development_task_rejects_forbidden_kilo_path(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    source_head = git(repo, "rev-parse", "HEAD")

    def secret_edit(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        (worktree / ".env").write_text("TOKEN=forbidden\n", encoding="utf-8")
        return "edited secret"

    monkeypatch.setattr(dev_worker, "_run_kilo", secret_edit)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "allowed_paths": ["calc.py"],
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    worktree = next((Path(os.environ["OCTOPUS_DEV_WORKTREE_ROOT"])).glob("*"))
    assert git(worktree, "rev-parse", "HEAD") == source_head
    assert (worktree / ".env").is_file()


def test_development_task_does_not_commit_when_kilo_tests_fail(tmp_path, monkeypatch):
    from octopus import dev_worker

    repo = repository(tmp_path, passing=False)
    source_head = git(repo, "rev-parse", "HEAD")

    def wrong_edit(worktree, goal, tests, max_steps, prompt=None, allowed_paths=None):
        (worktree / "calc.py").write_text("def answer():\n    return 3\n", encoding="utf-8")
        return "edited"

    monkeypatch.setattr(dev_worker, "_run_kilo", wrong_edit)
    worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Make the deterministic test pass.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "allowed_paths": ["calc.py"],
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    worktree = next((Path(os.environ["OCTOPUS_DEV_WORKTREE_ROOT"])).glob("*"))
    assert git(worktree, "rev-parse", "HEAD") == source_head
    assert "calc.py" in git(worktree, "status", "--porcelain")

