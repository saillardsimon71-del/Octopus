from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from octopus import tasks, worker


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
        calls.append((task, kwargs))
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
    assert all(task == "development.step" and kwargs["profile"] == "zero_cost" for task, kwargs in calls)
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
