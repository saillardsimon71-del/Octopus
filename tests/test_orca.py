"""Tests du pont Orca sans nécessiter l'installation d'Orca."""
from __future__ import annotations

import pytest

from agents import orca


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OCTOPUS_ORCA_ENABLED", raising=False)
    assert orca.enabled() is False
    with pytest.raises(orca.OrcaError, match="Pont Orca désactivé"):
        orca.status()


def test_decode_json_pretty_and_json_line():
    assert orca._decode_json('{\n  "ok": true\n}') == {"ok": True}
    assert orca._decode_json('log\n{"ok":true}\n') == {"ok": True}
    assert orca._decode_json("plain output") == {"raw": "plain output"}


def test_command_uses_project_root_and_no_shell(monkeypatch, tmp_path):
    monkeypatch.setenv("OCTOPUS_ORCA_ENABLED", "1")
    monkeypatch.setenv("OCTOPUS_ORCA_CLI", "orca")
    monkeypatch.setattr(orca.shutil, "which", lambda _: str(tmp_path / "orca"))
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return type("Completed", (), {"returncode": 0, "stdout": '{"ok":true}', "stderr": ""})()

    monkeypatch.setattr(orca.subprocess, "run", fake_run)
    assert orca.run(["status", "--json"]) == {"ok": True}
    assert seen["argv"][1:] == ["status", "--json"]
    assert seen.get("shell", False) is False
    assert seen["cwd"] == str(orca.config.PROJECT_ROOT)


def test_unavailable_cli_is_explicit(monkeypatch):
    monkeypatch.setenv("OCTOPUS_ORCA_ENABLED", "1")
    monkeypatch.setenv("OCTOPUS_ORCA_CLI", "missing-orca")
    monkeypatch.setattr(orca.shutil, "which", lambda _: None)
    with pytest.raises(orca.OrcaUnavailable, match="CLI Orca introuvable"):
        orca.status()


def test_command_error_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("OCTOPUS_ORCA_ENABLED", "1")
    monkeypatch.setenv("OCTOPUS_ORCA_CLI", "orca")
    monkeypatch.setattr(orca.shutil, "which", lambda _: str(tmp_path / "orca"))

    def fake_run(*args, **kwargs):
        return type("Completed", (), {"returncode": 2, "stdout": "", "stderr": "bad spec"})()

    monkeypatch.setattr(orca.subprocess, "run", fake_run)
    with pytest.raises(orca.OrcaCommandError, match="bad spec"):
        orca.run(["orchestration", "worker-list", "--json"])


def test_start_development_task_chains_run_task_worker(monkeypatch):
    calls = []

    monkeypatch.setattr(orca, "run_create", lambda objective: calls.append(("run", objective)) or {"run":{"id": "run-1"}})
    monkeypatch.setattr(orca, "task_create", lambda spec, *, run_id: calls.append(("task", spec, run_id)) or {"task_id": "task-1"})
    monkeypatch.setattr(
        orca,
        "worker_start",
        lambda task_id, **kwargs: calls.append(("worker", task_id, kwargs)) or {"dispatch": "dispatch-1"},
    )

    result = orca.start_development_task(
        objective="corriger les tests",
        spec="Réparer les tests unitaires concernés.",
        agent="codex",
        worktree="current",
        effort="medium",
    )

    assert [c[0] for c in calls] == ["run", "task", "worker"]
    assert calls[1][2] == "run-1"
    assert calls[2][1] == "task-1"
    assert calls[2][2]["agent"] == "codex"
    assert calls[2][2]["run_id"] == "run-1"
    assert result["run_id"] == "run-1"
    assert result["worker"]["dispatch"] == "dispatch-1"


def test_missing_run_id_refuses_to_dispatch(monkeypatch):
    monkeypatch.setattr(orca, "run_create", lambda _: {"ok": True})
    monkeypatch.setattr(orca, "task_create", lambda *a, **k: pytest.fail("task must not start"))
    with pytest.raises(orca.OrcaCommandError, match="run_id"):
        orca.start_development_task(objective="x", spec="y", agent="codex")


def test_missing_task_id_refuses_to_dispatch(monkeypatch):
    monkeypatch.setattr(orca, "run_create", lambda _: {"id": "run-1"})
    monkeypatch.setattr(orca, "task_create", lambda spec, *, run_id: {"ok": True})
    monkeypatch.setattr(orca, "worker_start", lambda *a, **k: pytest.fail("worker must not start"))
    with pytest.raises(orca.OrcaCommandError, match="task_id"):
        orca.start_development_task(objective="x", spec="y", agent="codex")


def test_liveness_keeps_orca_safety_vocabulary():
    assert orca.normalize_liveness("live") == "live"
    assert orca.normalize_liveness("exited") == "exited"
    assert orca.normalize_liveness("agentWait") == "unverifiable"
