from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.gpu_agent.agent import ToolError, Toolbox, parse_action, trim_history


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "sample.py").write_text("value = 1\n", encoding="utf-8")
    return tmp_path


def test_parse_plain_and_fenced_json():
    assert parse_action('{"tool":"repo_summary","args":{}}')["tool"] == "repo_summary"
    assert parse_action('```json\n{"final":"done"}\n```')["final"] == "done"


def test_parse_rejects_non_action():
    with pytest.raises(ToolError, match="either 'tool' or 'final'"):
        parse_action('{"answer":"no action"}')


def test_paths_cannot_escape_or_read_git(repo: Path):
    tools = Toolbox(repo)
    with pytest.raises(ToolError, match="escapes"):
        tools.read_file("../outside.txt")
    with pytest.raises(ToolError, match="forbidden"):
        tools.read_file(".git/config")


def test_runtime_source_is_read_only_to_model(repo: Path):
    target = repo / "ops" / "gpu_agent" / "agent.py"
    target.parent.mkdir(parents=True)
    target.write_text("safe = True\n", encoding="utf-8")
    tools = Toolbox(repo)
    with pytest.raises(ToolError, match="forbidden"):
        tools.read_file("ops/gpu_agent/agent.py")


def test_patch_rejects_forbidden_target(repo: Path):
    tools = Toolbox(repo)
    patch = """diff --git a/.env b/.env
new file mode 100644
--- /dev/null
+++ b/.env
@@ -0,0 +1 @@
+SECRET=x
"""
    with pytest.raises(ToolError, match="forbidden"):
        tools.apply_patch(patch)


def test_test_runner_rejects_flags(repo: Path):
    tools = Toolbox(repo)
    with pytest.raises(ToolError, match="invalid test target"):
        tools.run_tests(["--collect-only"])


def test_checkpoint_requires_fresh_full_suite(repo: Path):
    tools = Toolbox(repo)
    with pytest.raises(ToolError, match="fresh successful full test"):
        tools.checkpoint("test: safe checkpoint")


def test_history_trimming_preserves_system_goal_and_recent_messages():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "goal"},
        *({"role": "user", "content": str(i) * 100} for i in range(10)),
    ]
    trimmed = trim_history(messages, max_chars=350)
    assert trimmed[0]["content"] == "system"
    assert trimmed[1]["content"] == "goal"
    assert trimmed[-1]["content"].startswith("9")
    assert len(trimmed) < len(messages)
