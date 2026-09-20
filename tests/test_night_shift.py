from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_night_plan_is_docs_only_and_bounded():
    from octopus import night_shift

    plan = night_shift.load_plan()

    assert plan["policy"] == "docs_only"
    assert 1 <= len(plan["tickets"]) <= night_shift.MAX_TICKETS
    for ticket in plan["tickets"]:
        assert ticket["allowed_paths"]
        assert all(path.endswith(".md") for path in ticket["allowed_paths"])
        assert all(path not in night_shift.PROTECTED_DOCS for path in ticket["allowed_paths"])
        assert 1 <= ticket["max_steps"] <= 25
        assert isinstance(ticket["acceptance_criteria"], list)
        assert isinstance(ticket["noop_allowed"], bool)


@pytest.mark.parametrize(
    "allowed_paths",
    [
        ["octopus/actions.py"],
        ["tests/test_dev_worker.py"],
        ["AGENTS.md"],
        ["docs/ACCEPTANCE_GATES.md"],
        ["../README.md"],
    ],
)
def test_night_plan_rejects_unsafe_edit_scope(allowed_paths):
    from octopus import night_shift

    raw = {
        "policy": "docs_only",
        "tickets": [{
            "goal": "unsafe",
            "allowed_paths": allowed_paths,
            "test_targets": ["tests/test_dev_worker.py"],
        }],
    }

    with pytest.raises(night_shift.NightShiftError):
        night_shift.validate_plan(raw)


def test_night_run_forces_kilo_scope_no_fallback_and_fast_forwards(tmp_path, monkeypatch):
    from octopus import night_shift

    repository = tmp_path / "repo"
    repository.mkdir()
    plan = {
        "name": "unit-night",
        "policy": "docs_only",
        "tickets": [{
            "goal": "Improve README wording.",
            "allowed_paths": ["README.md"],
            "test_targets": ["tests/test_dev_worker.py"],
            "max_steps": 20,
            "acceptance_criteria": ["README documents the worker command"],
            "noop_allowed": False,
        }],
    }

    monkeypatch.setattr(
        night_shift,
        "preflight",
        lambda repo: {"repository": str(repository), "base_head": "base"},
    )
    monkeypatch.setattr(night_shift.worker, "load_handlers", lambda: {})
    monkeypatch.setattr(
        night_shift,
        "_create_night_worktree",
        lambda repo, run_id: (repository, f"octopus/night-{run_id}"),
    )

    enqueued = {}

    def fake_enqueue(business, kind, input, **kwargs):
        enqueued.update(business=business, kind=kind, input=input, kwargs=kwargs)
        return 42

    monkeypatch.setattr(night_shift.worker, "enqueue", fake_enqueue)
    monkeypatch.setattr(
        night_shift.worker,
        "run_one",
        lambda **kwargs: {
            "id": 42,
            "status": "done",
            "output": {"commit": "abc123", "backend": "kilo"},
        },
    )
    forwarded = []
    monkeypatch.setattr(
        night_shift,
        "_fast_forward",
        lambda repo, commit: forwarded.append((repo, commit)),
    )
    monkeypatch.setattr(
        night_shift,
        "_git",
        lambda repo, *args, **kwargs: "abc123" if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(
        night_shift,
        "_write_report",
        lambda report: tmp_path / "report.json",
    )

    result = night_shift.run(repository, plan, max_tasks=1, max_hours=1, max_failures=1)

    assert result["status"] == "backlog_complete"
    assert enqueued["business"] == "octopus"
    assert enqueued["kind"] == "development.task"
    task_input = enqueued["input"]
    assert task_input["backend"] == "kilo"
    assert task_input["allowed_paths"] == ["README.md"]
    assert task_input["allow_declarative_fallback"] is False
    assert task_input["max_steps"] == 20
    assert task_input["acceptance_criteria"] == ["README documents the worker command"]
    assert task_input["noop_allowed"] is False
    assert task_input["tests"][0][0]
    assert task_input["tests"][0][1:] == ["-m", "pytest", "-q", "tests/test_dev_worker.py"]
    assert forwarded == [(repository, "abc123")]


def test_night_run_accepts_justified_noop_without_fast_forward(tmp_path, monkeypatch):
    from octopus import night_shift

    repository = tmp_path / "repo"
    repository.mkdir()
    plan = {
        "name": "noop-night",
        "policy": "docs_only",
        "tickets": [{
            "goal": "Review README.",
            "allowed_paths": ["README.md"],
            "test_targets": ["tests/test_dev_worker.py"],
            "max_steps": 20,
            "acceptance_criteria": ["README is already accurate"],
            "noop_allowed": True,
        }],
    }

    monkeypatch.setattr(
        night_shift,
        "preflight",
        lambda repo: {"repository": str(repository), "base_head": "base"},
    )
    monkeypatch.setattr(night_shift.worker, "load_handlers", lambda: {})
    monkeypatch.setattr(
        night_shift,
        "_create_night_worktree",
        lambda repo, run_id: (repository, f"octopus/night-{run_id}"),
    )
    monkeypatch.setattr(night_shift.worker, "enqueue", lambda *args, **kwargs: 50)
    monkeypatch.setattr(
        night_shift.worker,
        "run_one",
        lambda **kwargs: {
            "id": 50,
            "status": "done",
            "output": {"commit": None, "backend": "kilo", "noop": True},
        },
    )
    forwarded = []
    monkeypatch.setattr(
        night_shift,
        "_fast_forward",
        lambda repo, commit: forwarded.append((repo, commit)),
    )
    monkeypatch.setattr(
        night_shift,
        "_git",
        lambda repo, *args, **kwargs: "base" if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(night_shift, "_write_report", lambda report: tmp_path / "report.json")

    result = night_shift.run(repository, plan, max_tasks=1, max_hours=1, max_failures=1)

    assert result["status"] == "backlog_complete"
    assert result["tickets"][0]["noop"] is True
    assert forwarded == []


def test_night_run_stops_after_consecutive_failures(tmp_path, monkeypatch):
    from octopus import night_shift

    repository = tmp_path / "repo"
    repository.mkdir()
    ticket = {
        "goal": "Improve docs.",
        "allowed_paths": ["README.md"],
        "test_targets": ["tests/test_dev_worker.py"],
        "max_steps": 12,
        "acceptance_criteria": [],
        "noop_allowed": False,
    }
    plan = {
        "name": "failure-night",
        "policy": "docs_only",
        "tickets": [ticket, ticket, ticket],
    }

    monkeypatch.setattr(
        night_shift,
        "preflight",
        lambda repo: {"repository": str(repository), "base_head": "base"},
    )
    monkeypatch.setattr(night_shift.worker, "load_handlers", lambda: {})
    monkeypatch.setattr(
        night_shift,
        "_create_night_worktree",
        lambda repo, run_id: (repository, f"octopus/night-{run_id}"),
    )
    ids = iter([10, 11, 12])
    monkeypatch.setattr(night_shift.worker, "enqueue", lambda *args, **kwargs: next(ids))
    results = iter([
        {"id": 10, "status": "failed", "error": "one"},
        {"id": 11, "status": "failed", "error": "two"},
    ])
    monkeypatch.setattr(night_shift.worker, "run_one", lambda **kwargs: next(results))
    monkeypatch.setattr(
        night_shift,
        "_git",
        lambda repo, *args, **kwargs: "base" if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(
        night_shift,
        "_write_report",
        lambda report: tmp_path / "report.json",
    )

    result = night_shift.run(repository, plan, max_tasks=3, max_hours=1, max_failures=2)

    assert result["status"] == "consecutive_failures"
    assert len(result["tickets"]) == 2


def test_fast_forward_requires_direct_child(tmp_path):
    from octopus import night_shift

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "x.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "x.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    subprocess.run(["git", "checkout", "-qb", "child"], cwd=repo, check=True)
    (repo / "x.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "two"], cwd=repo, check=True)
    child = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "checkout", "-q", "--detach", base], cwd=repo, check=True)

    night_shift._fast_forward(repo, child)

    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == child
