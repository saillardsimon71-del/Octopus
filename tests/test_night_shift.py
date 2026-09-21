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


def test_product_ticket_accepts_wide_scoped_surface_and_real_oracles():
    from octopus import night_shift

    allowed = [
        "agents/gui/workbench.py",
        "agents/gui/intelligence.py",
        "octopus/config/catalog.json",
        "octopus/model_profiles.py",
    ]
    plan = night_shift.validate_plan({
        "name": "gui-product",
        "policy": "product_ticket",
        "tickets": [{
            "goal": "Build one coherent product surface.",
            "allowed_paths": allowed,
            "test_targets": ["tests/test_gui.py", "tests/test_gui_intelligence.py"],
            "max_steps": 30,
            "max_files_changed": 4,
            "max_lines_added": 4000,
            "max_lines_deleted": 3000,
        }],
    })

    ticket = plan["tickets"][0]
    assert ticket["allowed_paths"] == allowed
    assert ticket["test_sandbox"] == "docker"
    assert ticket["max_files_changed"] == 4
    assert ticket["max_lines_added"] == 4000
    assert ticket["max_lines_deleted"] == 3000


@pytest.mark.parametrize("path", [
    "octopus/dev_worker.py",
    "octopus/night_shift.py",
    "octopus/promotion.py",
    "octopus/compute_finance.py",
    "octopus/economy.py",
    "octopus/actions.py",
    "octopus/browser_actions.py",
    "octopus/smtp_executor.py",
    "agents/web_guard.py",
    "agents/browser.py",
    "agents/publish.py",
    "docker/dev-sandbox.Dockerfile",
    "tests/test_gui.py",
    ".env",
])
def test_product_ticket_protects_small_control_kernel(path):
    from octopus import night_shift

    with pytest.raises(night_shift.NightShiftError):
        night_shift.validate_plan({
            "policy": "product_ticket",
            "tickets": [{
                "goal": "unsafe",
                "allowed_paths": [path],
                "test_targets": ["tests/test_gui.py"],
            }],
        })


@pytest.mark.parametrize("override, message", [
    ({"max_files_changed": 21}, "max_files_changed product_ticket"),
    ({"max_lines_added": 5001}, "max_lines_added product_ticket"),
    ({"max_lines_deleted": 5001}, "max_lines_deleted product_ticket"),
])
def test_product_ticket_uses_engine_radius_caps_not_canary_caps(override, message):
    from octopus import night_shift

    ticket = {
        "goal": "wide but bounded",
        "allowed_paths": [f"octopus/product_{i}.py" for i in range(20)],
        "test_targets": ["tests/test_gateway.py"],
        "max_steps": 30,
    }
    ticket.update(override)
    with pytest.raises(night_shift.NightShiftError, match=message):
        night_shift.validate_plan({
            "policy": "product_ticket",
            "tickets": [ticket],
        })


def test_product_ticket_requires_explicit_test_oracle():
    from octopus import night_shift

    with pytest.raises(night_shift.NightShiftError, match="test_targets explicite"):
        night_shift.validate_plan({
            "policy": "product_ticket",
            "tickets": [{
                "goal": "no weak default oracle",
                "allowed_paths": ["agents/gui/workbench.py"],
            }],
        })


def test_night_stop_file_can_be_requested_and_cleared(tmp_path, monkeypatch):
    from octopus import night_shift

    monkeypatch.setattr(night_shift.paths, "data_dir", lambda: tmp_path)

    assert night_shift.stop_requested() is False
    target = night_shift.request_stop()
    assert target == tmp_path / "NIGHT_SHIFT_STOP"
    assert target.read_text(encoding="utf-8") == "stop\n"
    assert night_shift.stop_requested() is True
    assert night_shift.clear_stop() is True
    assert night_shift.stop_requested() is False
    assert night_shift.clear_stop() is False


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


def test_supervised_python_canary_v2_plan_is_valid_and_bounded():
    from octopus import night_shift

    path = Path(__file__).resolve().parents[1] / "octopus" / "config" / "night_shift_python_supervised_v2.json"
    plan = night_shift.load_plan(path)

    assert plan["name"] == "python-canary-supervised-v2"
    assert plan["policy"] == "python_canary"
    assert len(plan["tickets"]) == 3
    for ticket in plan["tickets"]:
        assert ticket["allowed_paths"] == ["octopus/capabilities.py"]
        assert ticket["test_targets"] == ["tests/test_capabilities.py"]
        assert ticket["test_sandbox"] == "docker"
        assert ticket["max_files_changed"] == 1
        assert ticket["max_lines_added"] <= 20
        assert ticket["max_lines_deleted"] <= 20
        assert ticket["noop_allowed"] is False


def test_resources_python_canary_plan_is_valid_and_bounded():
    from octopus import night_shift

    path = Path(__file__).resolve().parents[1] / "octopus" / "config" / "night_shift_python_resources_canary_v1.json"
    plan = night_shift.load_plan(path)

    assert plan["name"] == "python-canary-resources-v1"
    assert plan["policy"] == "python_canary"
    assert len(plan["tickets"]) == 1
    ticket = plan["tickets"][0]
    assert ticket["allowed_paths"] == ["octopus/resources.py"]
    assert ticket["test_targets"] == ["tests/test_resources.py"]
    assert ticket["test_sandbox"] == "docker"
    assert ticket["max_files_changed"] == 1
    assert ticket["max_lines_added"] <= 20
    assert ticket["max_lines_deleted"] <= 20
    assert ticket["noop_allowed"] is False


def test_multimodule_python_canary_v3_plan_is_valid_and_bounded():
    from octopus import night_shift

    path = Path(__file__).resolve().parents[1] / "octopus" / "config" / "night_shift_python_multimodule_v3.json"
    plan = night_shift.load_plan(path)

    assert plan["name"] == "python-canary-multimodule-v3"
    assert plan["policy"] == "python_canary"
    assert len(plan["tickets"]) == 4
    assert [ticket["allowed_paths"] for ticket in plan["tickets"]] == [
        ["octopus/capabilities.py"],
        ["octopus/resources.py"],
        ["octopus/capabilities.py"],
        ["octopus/resources.py"],
    ]
    assert [ticket["test_targets"] for ticket in plan["tickets"]] == [
        ["tests/test_capabilities.py"],
        ["tests/test_resources.py"],
        ["tests/test_capabilities.py"],
        ["tests/test_resources.py"],
    ]
    for ticket in plan["tickets"]:
        assert ticket["test_sandbox"] == "docker"
        assert ticket["max_files_changed"] == 1
        assert ticket["max_lines_added"] <= 20
        assert ticket["max_lines_deleted"] <= 20
        assert ticket["noop_allowed"] is False


def test_connectors_python_canary_plan_is_valid_and_bounded():
    from octopus import night_shift

    path = Path(__file__).resolve().parents[1] / "octopus" / "config" / "night_shift_python_connectors_canary_v1.json"
    plan = night_shift.load_plan(path)

    assert plan["name"] == "python-canary-connectors-v1"
    assert plan["policy"] == "python_canary"
    assert len(plan["tickets"]) == 1
    ticket = plan["tickets"][0]
    assert ticket["allowed_paths"] == ["octopus/connectors.py"]
    assert ticket["test_targets"] == ["tests/test_connectors.py"]
    assert ticket["test_sandbox"] == "docker"
    assert ticket["test_sandbox_image"] == "octopus-test-sandbox:py311"
    assert ticket["max_files_changed"] == 1
    assert ticket["max_lines_added"] <= 16
    assert ticket["max_lines_deleted"] <= 16
    assert ticket["noop_allowed"] is False


def test_businesses_python_canary_plan_is_valid_and_bounded():
    from octopus import night_shift

    path = Path(__file__).resolve().parents[1] / "octopus" / "config" / "night_shift_python_businesses_canary_v1.json"
    plan = night_shift.load_plan(path)

    assert plan["name"] == "python-canary-businesses-v1"
    assert plan["policy"] == "python_canary"
    assert len(plan["tickets"]) == 1
    ticket = plan["tickets"][0]
    assert ticket["allowed_paths"] == ["octopus/businesses.py"]
    assert ticket["test_targets"] == ["tests/test_businesses.py"]
    assert ticket["test_sandbox"] == "docker"
    assert ticket["test_sandbox_image"] == "octopus-test-sandbox:py311"
    assert ticket["max_files_changed"] == 1
    assert ticket["max_lines_added"] <= 16
    assert ticket["max_lines_deleted"] <= 16
    assert ticket["noop_allowed"] is False


def test_python_canary_requires_source_only_and_protects_trust_core():
    from octopus import night_shift

    safe = night_shift.validate_plan({
        "name": "python-safe",
        "policy": "python_canary",
        "tickets": [{
            "goal": "Refactor capability normalization without changing behavior.",
            "allowed_paths": ["octopus/capabilities.py"],
            "test_targets": ["tests/test_capabilities.py"],
            "max_steps": 20,
            "acceptance_criteria": ["existing capability tests remain green"],
            "max_lines_added": 80,
            "max_lines_deleted": 80,
        }],
    })

    ticket = safe["tickets"][0]
    assert ticket["test_sandbox"] == "docker"
    assert ticket["test_sandbox_image"] == "octopus-test-sandbox:py311"
    assert ticket["max_files_changed"] == 1

    for path in (
        "tests/test_capabilities.py",
        "octopus/dev_worker.py",
        "octopus/night_shift.py",
        "octopus/compute_finance.py",
        "octopus/economy.py",
        "octopus/video/service.py",
        "agents/web_guard.py",
        "ops/compute_watchdog.py",
        "octopus/__init__.py",
        "sitecustomize.py",
        "README.md",
    ):
        with pytest.raises(night_shift.NightShiftError):
            night_shift.validate_plan({
                "policy": "python_canary",
                "tickets": [{
                    "goal": "unsafe",
                    "allowed_paths": [path],
                    "test_targets": ["tests/test_capabilities.py"],
                }],
            })


def test_python_canary_rejects_unrelated_oracle():
    from octopus import night_shift

    with pytest.raises(night_shift.NightShiftError, match="oracle python_canary attendu"):
        night_shift.validate_plan({
            "name": "weak-oracle",
            "policy": "python_canary",
            "tickets": [{
                "goal": "Attempt to validate capabilities with an unrelated test.",
                "allowed_paths": ["octopus/capabilities.py"],
                "test_targets": ["tests/test_resources.py"],
            }],
        })


@pytest.mark.parametrize("overrides, message", [
    ({"allowed_paths": ["octopus/capabilities.py", "octopus/resources.py"],
      "test_targets": ["tests/test_capabilities.py", "tests/test_resources.py"]},
     "exactement un fichier source"),
    ({"test_sandbox_image": "custom:test"}, "image sandbox python_canary imposée"),
    ({"max_files_changed": 2}, "max_files_changed=1"),
    ({"max_lines_added": 81}, "maximum 80"),
    ({"max_lines_deleted": 81}, "maximum 80"),
])
def test_python_canary_rejects_wider_execution_radius(overrides, message):
    from octopus import night_shift

    ticket = {
        "goal": "bounded",
        "allowed_paths": ["octopus/capabilities.py"],
        "test_targets": ["tests/test_capabilities.py"],
    }
    ticket.update(overrides)
    with pytest.raises(night_shift.NightShiftError, match=message):
        night_shift.validate_plan({
            "name": "bounded",
            "policy": "python_canary",
            "tickets": [ticket],
        })


def test_python_canary_oracles_are_bound_to_each_surface():
    from octopus import night_shift

    assert night_shift.PYTHON_CANARY_ORACLES == {
        "octopus/businesses.py": ("tests/test_businesses.py",),
        "octopus/capabilities.py": ("tests/test_capabilities.py",),
        "octopus/connectors.py": ("tests/test_connectors.py",),
        "octopus/resources.py": ("tests/test_resources.py",),
    }


def test_python_canary_allowlist_includes_only_supervised_surfaces():
    from octopus import night_shift

    assert night_shift.PYTHON_CANARY_ALLOWED == {
        "octopus/businesses.py",
        "octopus/capabilities.py",
        "octopus/connectors.py",
        "octopus/resources.py",
    }


def test_python_canary_runner_passes_docker_and_radius_to_development_task(tmp_path, monkeypatch):
    from octopus import night_shift

    repository = tmp_path / "repo"
    repository.mkdir()
    plan = {
        "name": "python-canary",
        "policy": "python_canary",
        "tickets": [{
            "goal": "Refactor capability normalization without changing behavior.",
            "allowed_paths": ["octopus/capabilities.py"],
            "test_targets": ["tests/test_capabilities.py"],
            "max_steps": 20,
            "acceptance_criteria": ["existing capability tests remain green"],
            "max_files_changed": 1,
            "max_lines_added": 80,
            "max_lines_deleted": 80,
        }],
    }

    monkeypatch.setattr(
        night_shift,
        "preflight",
        lambda repo: {"repository": str(repository), "base_head": "base"},
    )
    monkeypatch.setattr(
        night_shift.dev_worker,
        "_docker_sandbox_probe",
        lambda *args, **kwargs: ("sha256:probe", "probe green"),
    )
    monkeypatch.setattr(night_shift.worker, "load_handlers", lambda: {})
    monkeypatch.setattr(
        night_shift,
        "_create_night_worktree",
        lambda repo, run_id: (repository, f"octopus/night-{run_id}"),
    )
    captured = {}

    def fake_enqueue(business, kind, input, **kwargs):
        captured.update(input=input, kwargs=kwargs)
        return 77

    monkeypatch.setattr(night_shift.worker, "enqueue", fake_enqueue)
    monkeypatch.setattr(
        night_shift.worker,
        "run_one",
        lambda **kwargs: {
            "id": 77,
            "status": "done",
            "output": {
                "commit": "abc123",
                "backend": "kilo",
                "test_sandbox": "docker",
                "worktree": str(repository),
                "branch": "codex/devtask-77",
            },
        },
    )
    monkeypatch.setattr(night_shift, "_fast_forward", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        night_shift,
        "_git",
        lambda repo, *args, **kwargs: "abc123" if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(night_shift, "_write_report", lambda report: tmp_path / "report.json")

    result = night_shift.run(repository, plan, max_tasks=1, max_hours=1, max_failures=1)

    assert result["status"] == "backlog_complete"
    task_input = captured["input"]
    assert task_input["test_sandbox"] == "docker"
    assert task_input["test_sandbox_image"] == "sha256:probe"
    assert task_input["max_files_changed"] == 1
    assert task_input["max_lines_added"] == 80
    assert task_input["max_lines_deleted"] == 80
    assert task_input["strict_repository_preflight"] is True
    assert task_input["require_baseline_oracle"] is True
    assert task_input["python_canary_ast"] is True
    assert task_input["allow_declarative_fallback"] is False


def test_product_ticket_runner_passes_supervised_policy_to_development_task(tmp_path, monkeypatch):
    from octopus import night_shift

    repository = tmp_path / "repo"
    repository.mkdir()
    plan = {
        "name": "product-gui",
        "policy": "product_ticket",
        "tickets": [{
            "goal": "Redesign the GUI.",
            "allowed_paths": ["agents/gui/workbench.py", "agents/gui/intelligence.py"],
            "test_targets": ["tests/test_gui.py", "tests/test_gui_intelligence.py"],
            "max_steps": 30,
            "max_files_changed": 2,
            "max_lines_added": 1800,
            "max_lines_deleted": 1400,
        }],
    }
    monkeypatch.setattr(
        night_shift, "preflight",
        lambda repo: {"repository": str(repository), "base_head": "base"},
    )
    monkeypatch.setattr(
        night_shift.dev_worker, "_docker_sandbox_probe",
        lambda *args, **kwargs: ("sha256:probe", "probe green"),
    )
    monkeypatch.setattr(night_shift.worker, "load_handlers", lambda: {})
    monkeypatch.setattr(
        night_shift, "_create_night_worktree",
        lambda repo, run_id: (repository, f"octopus/night-{run_id}"),
    )
    captured = {}

    def fake_enqueue(business, kind, input, **kwargs):
        captured.update(input=input, kwargs=kwargs)
        return 88

    monkeypatch.setattr(night_shift.worker, "enqueue", fake_enqueue)
    monkeypatch.setattr(
        night_shift.worker, "run_one",
        lambda **kwargs: {
            "id": 88,
            "status": "done",
            "input": captured.get("input"),
            "output": {
                "commit": "abc123",
                "backend": "kilo",
                "self_policy": "product_ticket",
                "tests_passed": True,
                "baseline_oracle_runs": 2,
                "oracle_tests": 2,
                "post_oracle_tests": 2,
                "test_sandbox": "docker",
                "test_sandbox_image": "sha256:probe",
                "changed_paths": ["agents/gui/workbench.py"],
                "worktree": str(repository),
                "branch": "codex/devtask-88",
            },
        },
    )
    monkeypatch.setattr(night_shift, "_fast_forward", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        night_shift, "_git",
        lambda repo, *args, **kwargs: "abc123" if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(night_shift, "_write_report", lambda report: tmp_path / "report.json")

    result = night_shift.run(repository, plan, max_tasks=1, max_hours=1, max_failures=1)

    assert result["status"] == "backlog_complete"
    task_input = captured["input"]
    assert task_input["self_modification_policy"] == "product_ticket"
    assert task_input["strict_repository_preflight"] is True
    assert task_input["require_baseline_oracle"] is True
    assert task_input["python_canary_ast"] is False
    assert task_input["allow_declarative_fallback"] is False
    assert task_input["test_sandbox"] == "docker"
    assert task_input["test_sandbox_image"] == "sha256:probe"
    assert task_input["max_files_changed"] == 2


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
        night_shift.dev_worker,
        "_run_tests",
        lambda *args, **kwargs: ("baseline green", True),
    )
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
            "output": {
                "commit": "abc123",
                "backend": "kilo",
                "worktree": str(repository),
                "branch": "codex/devtask-42",
            },
        },
    )
    forwarded = []
    monkeypatch.setattr(
        night_shift,
        "_fast_forward",
        lambda repo, commit, source_repo=None, source_branch=None:
            forwarded.append((repo, commit, source_repo, source_branch)),
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
    assert forwarded == [(repository, "abc123", repository, "codex/devtask-42")]


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
        night_shift.dev_worker,
        "_run_tests",
        lambda *args, **kwargs: ("baseline green", True),
    )
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


def test_night_run_writes_report_when_fast_forward_crashes(tmp_path, monkeypatch):
    from octopus import night_shift

    repository = tmp_path / "repo"
    repository.mkdir()
    plan = {
        "name": "crash-night",
        "policy": "docs_only",
        "tickets": [{
            "goal": "Improve README wording.",
            "allowed_paths": ["README.md"],
            "test_targets": ["tests/test_dev_worker.py"],
            "max_steps": 12,
            "acceptance_criteria": [],
            "noop_allowed": False,
        }],
    }
    monkeypatch.setattr(
        night_shift, "preflight",
        lambda repo: {"repository": str(repository), "base_head": "base"},
    )
    monkeypatch.setattr(night_shift.worker, "load_handlers", lambda: {})
    monkeypatch.setattr(
        night_shift, "_create_night_worktree",
        lambda repo, run_id: (repository, f"octopus/night-{run_id}"),
    )
    monkeypatch.setattr(night_shift.worker, "enqueue", lambda *args, **kwargs: 42)
    monkeypatch.setattr(
        night_shift.worker, "run_one",
        lambda **kwargs: {
            "id": 42,
            "status": "done",
            "output": {
                "commit": "abc123",
                "backend": "kilo",
                "worktree": str(repository),
                "branch": "codex/devtask-42",
            },
        },
    )
    monkeypatch.setattr(
        night_shift, "_fast_forward",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        night_shift, "_git",
        lambda repo, *args, **kwargs: "base" if args[:2] == ("rev-parse", "HEAD") else "",
    )
    written = {}

    def capture(report):
        written.update(report.copy())
        return tmp_path / "report.json"

    monkeypatch.setattr(night_shift, "_write_report", capture)

    with pytest.raises(RuntimeError, match="boom"):
        night_shift.run(repository, plan, max_tasks=1, max_hours=1, max_failures=1)

    assert written["status"] == "crash:RuntimeError"
    assert "boom" in written["error"]
    assert written["final_head"] == "base"
    assert written["finished_at"] >= written["started_at"]


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
        night_shift.dev_worker,
        "_run_tests",
        lambda *args, **kwargs: ("baseline green", True),
    )
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


def test_fast_forward_imports_from_independent_clone(tmp_path):
    from octopus import night_shift

    base = tmp_path / "base"
    task = tmp_path / "task"
    base.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=base, check=True)
    (base / "x.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "x.txt"], cwd=base, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=base, check=True)

    subprocess.run(["git", "clone", "-q", "--no-local", str(base / ".git"), str(task)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=task, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=task, check=True)
    subprocess.run(["git", "checkout", "-qb", "codex/devtask-test"], cwd=task, check=True)
    (task / "x.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "two"], cwd=task, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=task, text=True).strip()

    night_shift._fast_forward(base, commit, task, "codex/devtask-test")

    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base, text=True).strip() == commit


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
