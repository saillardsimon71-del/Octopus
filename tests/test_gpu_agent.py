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


def test_replace_text_requires_one_exact_match(repo: Path):
    tools = Toolbox(repo)
    result = tools.replace_text("sample.py", "value = 1\n", "value = 2\n")
    assert result == {"ok": True, "path": "sample.py", "replacements": 1}
    assert (repo / "sample.py").read_text(encoding="utf-8") == "value = 2\n"
    assert not tools.full_tests_passed_after_change

    (repo / "sample.py").write_text("same\nsame\n", encoding="utf-8")
    with pytest.raises(ToolError, match="exactly once; found 2"):
        tools.replace_text("sample.py", "same", "changed")


def test_write_file_creates_without_overwriting(repo: Path):
    tools = Toolbox(repo)
    result = tools.write_file("tests/new_test.py", "def test_new():\n    assert True\n")
    assert result["path"] == "tests/new_test.py"
    assert (repo / "tests/new_test.py").is_file()
    with pytest.raises(ToolError, match="already exists"):
        tools.write_file("tests/new_test.py", "changed\n")


def test_test_runner_rejects_flags(repo: Path):
    tools = Toolbox(repo)
    with pytest.raises(ToolError, match="invalid test target"):
        tools.run_tests(["--collect-only"])


def test_checkpoint_requires_fresh_full_suite(repo: Path):
    tools = Toolbox(repo)
    with pytest.raises(ToolError, match="fresh successful full test"):
        tools.checkpoint("test: safe checkpoint")


def test_build_gate_rejects_early_or_dirty_completion(repo: Path):
    tools = Toolbox(repo)
    ready, reasons = tools.build_ready()
    assert not ready
    assert any("validated checkpoint" in reason for reason in reasons)
    assert any("full test suite" in reason for reason in reasons)
    assert any("working tree is clean" in reason for reason in reasons)


def test_audit_report_writer_uses_only_fixed_path(repo: Path):
    tools = Toolbox(repo)
    result = tools.write_audit_report("# Audit\n" + ("evidence\n" * 150))
    assert result["path"] == "docs/audits/GPU_AUDIT_2026-09-18.md"
    assert (repo / result["path"]).is_file()
    assert not tools.full_tests_passed_after_change


def test_audit_gate_rejects_shallow_uncommitted_report(repo: Path):
    tools = Toolbox(repo)
    tools.write_audit_report("# Audit\n" + ("evidence\n" * 150))
    ready, reasons = tools.audit_ready()
    assert not ready
    assert any("20 distinct files" in reason for reason in reasons)
    assert any("6,000 characters" in reason for reason in reasons)
    assert any("working tree is clean" in reason for reason in reasons)


def test_audit_read_budget_forces_synthesis(repo: Path):
    tools = Toolbox(repo, audit_mode=True)
    tools.read_paths = {f"file-{index}.py" for index in range(40)}
    with pytest.raises(ToolError, match="synthesize evidence"):
        tools.read_file("sample.py")


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
