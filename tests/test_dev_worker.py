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
def load_dev_handler():
    worker.load_handlers(["octopus.dev_worker"])


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


def test_devworker_rejects_unapproved_test_commands():
    from octopus.dev_worker import DevWorkerError, validate_test_commands

    with pytest.raises(DevWorkerError, match="pytest"):
        validate_test_commands([["powershell", "-Command", "Write-Host unsafe"]])


def test_devworker_subprocess_input_is_utf8(tmp_path):
    from octopus.dev_worker import _run

    result = _run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        tmp_path,
        input_text="tiret‑insécable",
    )

    assert result.returncode == 0
    assert result.stdout == "tiret‑insécable"


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


def test_devworker_falls_back_from_gemini_429_to_groq_tools(
        monkeypatch, providers_up, transport):
    from octopus import dev_worker, llm

    class RateLimitError(Exception):
        pass

    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")

    def reply(provider, request):
        if request["model"] == "octopus-free-devworker-gemini":
            return RateLimitError("429 quota exhausted")
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

    assert transport.models == [
        "octopus-free-devworker-gemini", "octopus-free-devworker-groq",
    ]
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
    monkeypatch.setattr(dev_worker.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        dev_worker, "_tool",
        lambda action, worktree, tests, tests_passed: ("fake-commit", True, "fake-commit"),
    )
    task_id = worker.enqueue("octopus", "development.task", {
        "repository": str(repo),
        "goal": "Retry one bounded transient provider limit.",
        "tests": [[sys.executable, "-m", "pytest", "-q", "test_calc.py"]],
        "max_steps": 1,
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
    })

    result = worker.run_one("dev", kinds=["development.task"], log=lambda _: None)

    assert result["status"] == "failed"
    worktrees = list((Path(os.environ["OCTOPUS_HOME"]) / "data" / "dev-worktrees").glob("*"))
    assert len(worktrees) == 1
    assert git(worktrees[0], "rev-parse", "HEAD") == source_head
