import json
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from octopus import night_shift


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = "powershell"


def run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    if args and args[0] in {POWERSHELL, "docker"} and not shutil.which(args[0]):
        pytest.skip(f"prérequis de plateforme absent : {args[0]}")
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def git(repo: Path, *args: str) -> str:
    result = run("git", *args, cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "prep/astra-local-orchestration")
    git(repo, "config", "user.name", "Constructor Test")
    git(repo, "config", "user.email", "constructor@example.invalid")
    (repo / ".gitignore").write_text("cache/\n", encoding="utf-8")
    (repo / "product.txt").write_text("before\n", encoding="utf-8")
    git(repo, "add", ".gitignore", "product.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def resolve_step_relay_plan(repo: Path, request: dict, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "resolve-step-relay-plan.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-JsonAtomic','Resolve-StepRelayPlan')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  if(-not $fn){{throw "$name is missing"}}
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$ticketRoot=Join-Path $repo 'cache/astra-tickets'
$request=Get-Content -LiteralPath '{request_path}' -Raw|ConvertFrom-Json
Resolve-StepRelayPlan -Request $request -RequestId ([string]$request.request_id)|ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    return run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner),
        cwd=repo,
    )


def gate_step_relay_dispatch(repo: Path, request: dict, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    request_path = tmp_path / "dispatch-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "gate-step-relay-dispatch.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-JsonAtomic','Resolve-StepRelayPlan')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  if(-not $fn){{throw "$name is missing"}}
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$ticketRoot=Join-Path $repo 'cache/astra-tickets'
$request=Get-Content -LiteralPath '{request_path}' -Raw|ConvertFrom-Json
$workerCalls=0;$astraCalls=0
try {{
  $plan=Resolve-StepRelayPlan -Request $request -RequestId ([string]$request.request_id)
  $workerCalls++
  $astraCalls++
  @{{accepted=$true;plan=$plan;worker_calls=$workerCalls;astra_calls=$astraCalls}}|ConvertTo-Json -Compress
}} catch {{
  @{{accepted=$false;error=$_.Exception.Message;worker_calls=$workerCalls;astra_calls=$astraCalls}}|ConvertTo-Json -Compress
}}
""",
        encoding="utf-8",
    )
    return run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner),
        cwd=repo,
    )


def gate_worker_review(
    repo: Path,
    request_id: str,
    receipt: dict,
    worker_exit: int,
    tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    plan_dir = repo / "cache" / "astra-tickets"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{request_id}.json"
    if not plan_path.exists():
        plan_path.write_text(json.dumps({
            "request_id": request_id,
            "base_head": receipt.get("worker_summary", {}).get("base_head", git(repo, "rev-parse", "HEAD"))
            if receipt.get("worker_summary") else git(repo, "rev-parse", "HEAD"),
            "tickets": [{"allowed_paths": receipt.get("allowed_paths", ["product.txt"])}],
        }), encoding="utf-8")
    receipt.setdefault("plan_path", f"cache/astra-tickets/{request_id}.json")
    receipt.setdefault("plan_sha256", hashlib.sha256(plan_path.read_bytes()).hexdigest())
    receipt.setdefault("execution_status", "success" if receipt.get("status") == "completed" else "step_not_started")
    receipt_path = tmp_path / "worker-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "gate-worker-review.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Get-LocalSha256','Assert-ValidWorkerReviewReceipt')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  if(-not $fn){{throw "$name is missing"}}
  Invoke-Expression $fn.Extent.Text
}}
$script:astraCalls=0
try {{
  $receipt=Get-Content -LiteralPath '{receipt_path}' -Raw|ConvertFrom-Json
  $summary=Assert-ValidWorkerReviewReceipt -Receipt $receipt -WorkerExit {worker_exit} -RequestId '{request_id}' -Repository '{repo}'
  $script:astraCalls++
  @{{astra_calls=$script:astraCalls;accepted=$true;summary=$summary}}|ConvertTo-Json -Depth 20 -Compress
}} catch {{
  @{{astra_calls=$script:astraCalls;accepted=$false;error=$_.Exception.Message}}|ConvertTo-Json -Depth 20 -Compress
}}
""",
        encoding="utf-8",
    )
    return run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner),
        cwd=repo,
    )


def test_step_relay_v2_normalizes_product_ticket_and_prefers_it_over_legacy_plan(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    test_target = repo / "tests" / "test_runtime.py"
    test_target.parent.mkdir()
    test_target.write_text("def test_runtime():\n    assert True\n", encoding="utf-8")
    git(repo, "add", "tests/test_runtime.py")
    git(repo, "commit", "-m", "runtime oracle")
    base_head = git(repo, "rev-parse", "HEAD")
    ticket = {
        "version": 1,
        "request_id": "phase-g-runtime-001",
        "phase": "G",
        "base_head": base_head,
        "title": "Verify runtime startup",
        "objective": "Verify the existing runtime entry point.",
        "authorization": "Bounded phase G maintenance.",
        "architecture": ["Reuse existing runtime boundaries."],
        "starting_paths": ["scripts/start_octopus_astra.ps1"],
        "likely_tests": ["tests/test_gui.py"],
        "allowed_edit_paths": ["octopus/runtime.py"],
        "test_targets": ["tests/test_runtime.py"],
        "scope": {"implementation": "Fix one reproduced blocker."},
        "work": ["Reproduce before editing."],
        "acceptance": ["Focused regression passes."],
        "prohibitions": ["No external effects."],
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {
        "version": 1,
        "kind": "step",
        "request_id": "phase-g-runtime-001",
        "phase": "G",
        "base_head": base_head,
        "product_ticket": "cache/astra-relay/product_ticket.json",
        "plan_path": "cache/astra-tickets/legacy-must-not-win.json",
    }

    result = resolve_step_relay_plan(repo, request, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == "cache/astra-tickets/phase-g-runtime-001.json"
    normalized_path = repo / "cache" / "astra-tickets" / "phase-g-runtime-001.json"
    assert not normalized_path.read_bytes().startswith(b"\xef\xbb\xbf")
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert normalized["name"] == ticket["request_id"]
    assert normalized["policy"] == "product_ticket"
    assert normalized["request_id"] == ticket["request_id"]
    assert normalized["base_head"] == ticket["base_head"]
    assert normalized["objective"] == ticket["objective"]
    assert normalized["scope"] == ticket["scope"]
    assert normalized["prohibitions"] == ticket["prohibitions"]
    validated = night_shift.validate_plan(normalized)
    assert len(validated["tickets"]) == 1
    work_ticket = validated["tickets"][0]
    assert work_ticket["allowed_paths"] == ticket["allowed_edit_paths"]
    assert work_ticket["test_targets"] == ticket["test_targets"]
    assert work_ticket["post_change_tests"] == []
    assert work_ticket["acceptance_criteria"] == ticket["acceptance"]
    assert ticket["objective"] in work_ticket["goal"]
    assert ticket["scope"]["implementation"] in work_ticket["goal"]
    assert ticket["prohibitions"][0] in work_ticket["goal"]
    assert work_ticket["acceptance_contract"]["must"] == [
        {
            "id": "tests_green",
            "fact": "tests.passed",
            "op": "equals",
            "expected": True,
        }
    ]


@pytest.mark.parametrize("post_path, accepted", [
    ("tests/test_new.py", True),
    ("tests/test_missing.py", False),
    ("tests/../test_escape.py", False),
    ("tests/test_*.py", False),
    ("C:/tests/test_absolute.py", False),
])
def test_step_relay_post_change_test_contract(tmp_path: Path, post_path: str, accepted: bool):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_oracle.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    git(repo, "add", "tests/test_oracle.py")
    git(repo, "commit", "-m", "oracle")
    head = git(repo, "rev-parse", "HEAD")
    ticket = {
        "request_id": "post-change-001", "phase": "G", "base_head": head,
        "objective": "Add a regression test", "acceptance": ["Tests pass"],
        "allowed_edit_paths": ["octopus/runtime.py", "tests/test_new.py"],
        "test_targets": ["tests/test_oracle.py"], "post_change_tests": [post_path],
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {"request_id": ticket["request_id"], "phase": "G", "base_head": head,
               "product_ticket": "cache/astra-relay/product_ticket.json"}
    result = gate_step_relay_dispatch(repo, request, tmp_path)
    payload = json.loads(result.stdout)
    assert payload["accepted"] is accepted
    if accepted:
        plan = json.loads((repo / "cache" / "astra-tickets" / "post-change-001.json").read_text())
        assert night_shift.validate_plan(plan)["tickets"][0]["post_change_tests"] == [post_path]
    else:
        assert payload["worker_calls"] == 0


def test_step_relay_rejects_edited_oracle_even_with_post_change_tests(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_oracle.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    git(repo, "add", "tests/test_oracle.py")
    git(repo, "commit", "-m", "oracle")
    head = git(repo, "rev-parse", "HEAD")
    (relay / "product_ticket.json").write_text(json.dumps({
        "request_id": "oracle-edit-001", "phase": "G", "base_head": head,
        "objective": "Edit tests", "acceptance": ["Tests pass"],
        "allowed_edit_paths": ["tests/test_oracle.py", "tests/test_new.py"],
        "test_targets": ["tests/test_oracle.py"], "post_change_tests": ["tests/test_new.py"],
    }), encoding="utf-8")
    result = gate_step_relay_dispatch(repo, {
        "request_id": "oracle-edit-001", "phase": "G", "base_head": head,
        "product_ticket": "cache/astra-relay/product_ticket.json",
    }, tmp_path)
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert "cannot edit its Step test oracle" in payload["error"]


def test_step_relay_accepts_bounded_core_product_ticket(tmp_path: Path):
    repo = init_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_runtime.py").write_text("def test_runtime(): assert True\n", encoding="utf-8")
    git(repo, "add", "tests/test_runtime.py")
    git(repo, "commit", "-m", "oracle")
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    head = git(repo, "rev-parse", "HEAD")
    ticket = {
        "version": 1, "request_id": "core-ticket", "phase": "G", "base_head": head,
        "objective": "Repair the task worker and related fixture.",
        "acceptance": ["Runtime oracle passes."],
        "allowed_edit_paths": ["octopus/tasks.py", "octopus/worker.py", "tests/task_fixture.py"],
        "test_targets": ["tests/test_runtime.py"],
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {"request_id": ticket["request_id"], "phase": "G", "base_head": head,
               "product_ticket": "cache/astra-relay/product_ticket.json"}

    result = resolve_step_relay_plan(repo, request, tmp_path)

    assert result.returncode == 0, result.stderr
    plan = json.loads((repo / "cache" / "astra-tickets" / "core-ticket.json").read_text(encoding="utf-8"))
    assert night_shift.validate_plan(plan)["tickets"][0]["allowed_paths"] == ticket["allowed_edit_paths"]


@pytest.mark.parametrize("path", [
    "scripts/start_octopus_astra.ps1", ".codex/config.toml", "../outside.py",
    "C:/outside.py", "octopus/*.py", "octopus/tasks?.py",
])
def test_step_relay_rejects_unsafe_edit_scope_before_worker(tmp_path: Path, path: str):
    repo = init_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_runtime.py").write_text("def test_runtime(): assert True\n", encoding="utf-8")
    git(repo, "add", "tests/test_runtime.py")
    git(repo, "commit", "-m", "oracle")
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    head = git(repo, "rev-parse", "HEAD")
    ticket = {
        "version": 1, "request_id": "unsafe-ticket", "phase": "G", "base_head": head,
        "objective": "Repair the task path.", "allowed_edit_paths": [path],
        "acceptance": ["Runtime oracle passes."],
        "test_targets": ["tests/test_runtime.py"],
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {"request_id": ticket["request_id"], "phase": "G", "base_head": head,
               "product_ticket": "cache/astra-relay/product_ticket.json"}

    result = gate_step_relay_dispatch(repo, request, tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["worker_calls"] == payload["astra_calls"] == 0


def test_step_relay_rejects_excessive_edit_scope_before_worker(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    head = git(repo, "rev-parse", "HEAD")
    ticket = {
        "version": 1, "request_id": "wide-ticket", "phase": "G", "base_head": head,
        "objective": "Repair a task path.", "acceptance": ["Oracle passes."],
        "allowed_edit_paths": [f"octopus/file_{i}.py" for i in range(21)],
        "test_targets": ["tests/test_runtime.py"],
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {"request_id": ticket["request_id"], "phase": "G", "base_head": head,
               "product_ticket": "cache/astra-relay/product_ticket.json"}

    result = gate_step_relay_dispatch(repo, request, tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert "bounded Step path limits" in payload["error"]
    assert payload["worker_calls"] == payload["astra_calls"] == 0


def test_phase_g_discovery_ticket_fails_closed_before_worker_or_astra(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    base_head = git(repo, "rev-parse", "HEAD")
    ticket = {
        "version": 1,
        "request_id": "phase-g-runtime-entrypoint-001-reissue",
        "phase": "G",
        "base_head": base_head,
        "title": "Verify the existing runtime startup",
        "objective": "Fix only a demonstrated startup blocker.",
        "starting_paths": [
            "scripts/start_octopus_astra.ps1",
            "scripts/fetch_pinned_upstreams.ps1",
        ],
        "likely_tests": ["tests/test_astra_constructor.py", "tests/test_gui.py"],
        "scope": {
            "discovery": "Starting paths are search hints, not evidence of a runtime defect."
        },
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {
        "version": 1,
        "kind": "step",
        "request_id": ticket["request_id"],
        "phase": "G",
        "base_head": base_head,
        "product_ticket": "cache/astra-relay/product_ticket.json",
    }

    result = gate_step_relay_dispatch(repo, request, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["worker_calls"] == 0
    assert payload["astra_calls"] == 0
    assert "allowed_edit_paths" in payload["error"]
    assert "test_targets" in payload["error"]
    assert not (repo / "cache" / "astra-tickets" / f"{ticket['request_id']}.json").exists()


def test_step_relay_rejects_test_target_missing_from_step_sandbox(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    base_head = git(repo, "rev-parse", "HEAD")
    ticket = {
        "version": 1,
        "request_id": "phase-g-missing-oracle-001",
        "phase": "G",
        "base_head": base_head,
        "objective": "Fix one reproduced runtime blocker.",
        "allowed_edit_paths": ["octopus/runtime.py"],
        "test_targets": ["tests/test_runtime_missing.py::test_startup"],
        "acceptance": ["Runtime oracle passes."],
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {
        "version": 1,
        "kind": "step",
        "request_id": ticket["request_id"],
        "phase": "G",
        "base_head": base_head,
        "product_ticket": "cache/astra-relay/product_ticket.json",
    }

    result = gate_step_relay_dispatch(repo, request, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["worker_calls"] == 0
    assert payload["astra_calls"] == 0
    assert "not available in the Step sandbox" in payload["error"]


def test_bounded_json_writer_uses_utf8_without_bom(tmp_path: Path):
    output = tmp_path / "bounded.json"
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "write-bounded-json.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
$fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Write-BoundedJsonAtomic'}},$true)
Invoke-Expression $fn.Extent.Text
$null=Write-BoundedJsonAtomic -Path '{output}' -Value @{{kind='step';label='bounded'}} -MaxChars 200 -Label 'test JSON'
""",
        encoding="utf-8",
    )

    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    raw = output.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8")) == {"kind": "step", "label": "bounded"}


def test_step_relay_rejects_unbounded_legacy_plan(tmp_path: Path):
    repo = init_repo(tmp_path)
    request = {
        "version": 1,
        "kind": "step",
        "request_id": "legacy-001",
        "plan_path": "cache/astra-tickets/existing-ticket.json",
    }

    result = resolve_step_relay_plan(repo, request, tmp_path)

    assert result.returncode != 0
    assert "bounded product_ticket" in result.stderr
    assert not (repo / "cache" / "astra-tickets" / "legacy-001.json").exists()


def test_step_relay_rejects_product_ticket_outside_repo(tmp_path: Path):
    repo = init_repo(tmp_path)
    outside_ticket = tmp_path / "outside-ticket.json"
    outside_ticket.write_text(
        json.dumps(
            {
                "version": 1,
                "request_id": "outside-001",
                "phase": "G",
                "base_head": git(repo, "rev-parse", "HEAD"),
            }
        ),
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "kind": "step",
        "request_id": "outside-001",
        "phase": "G",
        "base_head": git(repo, "rev-parse", "HEAD"),
        "product_ticket": str(outside_ticket),
    }

    result = resolve_step_relay_plan(repo, request, tmp_path)

    assert result.returncode != 0
    assert "Product ticket must live under the OCTOPUS repository" in result.stderr
    assert not (repo / "cache" / "astra-tickets" / "outside-001.json").exists()


def test_step_relay_rejects_product_ticket_inconsistent_with_request(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    base_head = git(repo, "rev-parse", "HEAD")
    request = {
        "version": 1,
        "kind": "step",
        "request_id": "match-001",
        "phase": "G",
        "base_head": base_head,
        "product_ticket": "cache/astra-relay/product_ticket.json",
    }
    normalized = repo / "cache" / "astra-tickets" / "match-001.json"

    for field, wrong_value in (
        ("request_id", "other-request"),
        ("phase", "F"),
        ("base_head", "0" * 40),
    ):
        ticket = {
            "version": 1,
            "request_id": "match-001",
            "phase": "G",
            "base_head": base_head,
            "title": "Bounded ticket",
        }
        ticket[field] = wrong_value
        (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
        normalized.unlink(missing_ok=True)

        result = resolve_step_relay_plan(repo, request, tmp_path)

        assert result.returncode != 0
        assert f"Product ticket {field} does not match relay request" in result.stderr
        assert not normalized.exists()


def test_step_relay_rejects_product_ticket_not_based_on_current_head(tmp_path: Path):
    repo = init_repo(tmp_path)
    stale_head = git(repo, "rev-parse", "HEAD")
    test_target = repo / "tests" / "test_runtime.py"
    test_target.parent.mkdir()
    test_target.write_text("def test_runtime():\n    assert True\n", encoding="utf-8")
    git(repo, "add", "tests/test_runtime.py")
    git(repo, "commit", "-m", "advance constructor head")
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    ticket = {
        "version": 1,
        "request_id": "stale-head-001",
        "phase": "G",
        "base_head": stale_head,
        "objective": "Fix one reproduced runtime blocker.",
        "allowed_edit_paths": ["octopus/runtime.py"],
        "test_targets": ["tests/test_runtime.py"],
    }
    (relay / "product_ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    request = {
        "version": 1,
        "kind": "step",
        "request_id": ticket["request_id"],
        "phase": "G",
        "base_head": stale_head,
        "product_ticket": "cache/astra-relay/product_ticket.json",
    }

    result = gate_step_relay_dispatch(repo, request, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["worker_calls"] == 0
    assert payload["astra_calls"] == 0
    assert "base_head must match the current constructor HEAD" in payload["error"]


def test_step_relay_rejects_missing_ticket_or_plan(tmp_path: Path):
    repo = init_repo(tmp_path)
    base_request = {
        "version": 1,
        "kind": "step",
        "request_id": "missing-001",
        "phase": "G",
        "base_head": git(repo, "rev-parse", "HEAD"),
    }

    missing_ticket = resolve_step_relay_plan(
        repo,
        {**base_request, "product_ticket": "cache/astra-relay/missing.json"},
        tmp_path,
    )
    missing_plan = resolve_step_relay_plan(repo, base_request, tmp_path)

    assert missing_ticket.returncode != 0
    assert "Product ticket not found" in missing_ticket.stderr
    assert missing_plan.returncode != 0
    assert "bounded product_ticket" in missing_plan.stderr
    assert not (repo / "cache" / "astra-tickets" / "missing-001.json").exists()


def test_step_failure_without_summary_is_protocol_error(tmp_path: Path):
    repo = init_repo(tmp_path)
    request_id = "step-failed-001"
    receipt = {
        "version": 1,
        "request_id": request_id,
        "status": "failed",
        "exit_code": 2,
        "worker_summary": None,
    }

    result = gate_worker_review(repo, request_id, receipt, 2, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["astra_calls"] == 0
    assert "no worker_summary" in payload["error"]


def test_invalid_worker_summary_fails_closed_before_astra_review(tmp_path: Path):
    repo = init_repo(tmp_path)
    request_id = "invalid-summary-001"
    receipt = {
        "version": 1,
        "request_id": request_id,
        "status": "completed",
        "exit_code": 0,
        "worker_summary": {
            "status": "backlog_complete",
            "base_head": git(repo, "rev-parse", "HEAD"),
            "source_commit": "0" * 40,
            "changed_paths": [],
            "diff_stat": [],
            "tests": [],
            "tickets": [],
        },
    }

    result = gate_worker_review(repo, request_id, receipt, 0, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["astra_calls"] == 0
    assert "tests summary" in payload["error"]


def test_valid_compact_worker_receipt_allows_one_astra_review(tmp_path: Path):
    repo = init_repo(tmp_path)
    request_id = "valid-summary-001"
    base_head = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "worker/valid-summary")
    (repo / "product.txt").write_text("worker result\n", encoding="utf-8")
    git(repo, "commit", "-am", "worker: valid summary")
    source_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "prep/astra-local-orchestration")
    receipt = {
        "version": 1,
        "request_id": request_id,
        "status": "completed",
        "exit_code": 0,
        "worker_summary": {
            "status": "backlog_complete",
            "base_head": base_head,
            "source_commit": source_commit,
            "changed_paths": ["product.txt"],
            "diff_stat": ["product.txt | 2 +-"],
            "tests": ["1 passed"],
            "tickets": [
                {
                    "task_id": 1,
                    "status": "done",
                    "commit": source_commit,
                    "tests_passed": True,
                    "gate_status": "ACCEPTED",
                    "changed_paths": ["product.txt"],
                }
            ],
        },
    }

    result = gate_worker_review(repo, request_id, receipt, 0, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is True
    assert payload["astra_calls"] == 1
    assert payload["summary"]["source_commit"] == source_commit


def test_step_review_requires_post_change_proof_when_declared(tmp_path: Path):
    repo = init_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "worker/post-tests")
    (repo / "product.txt").write_text("updated\n", encoding="utf-8")
    git(repo, "commit", "-am", "worker result")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "prep/astra-local-orchestration")
    request_id = "post-proof-001"
    plan_dir = repo / "cache" / "astra-tickets"
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{request_id}.json").write_text(json.dumps({
        "request_id": request_id, "base_head": head,
        "tickets": [{"allowed_paths": ["product.txt"],
                     "test_targets": ["tests/test_oracle.py"],
                     "post_change_tests": ["tests/test_new.py"]}],
    }), encoding="utf-8")
    receipt = {
        "version": 1, "request_id": request_id, "status": "completed", "exit_code": 0,
        "worker_summary": {
            "status": "backlog_complete", "base_head": head, "source_commit": commit,
            "changed_paths": ["product.txt"], "tests": ["oracle and regression passed"],
            "baseline_oracle": ["tests/test_oracle.py"], "baseline_oracle_runs": 2,
            "post_change_tests": ["tests/test_new.py"], "post_change_tests_passed": True,
            "tickets": [{"status": "done", "commit": commit, "tests_passed": True,
                         "gate_status": "ACCEPTED", "changed_paths": ["product.txt"]}],
        },
    }
    accepted = gate_worker_review(repo, request_id, receipt, 0, tmp_path)
    assert json.loads(accepted.stdout)["accepted"] is True
    receipt["worker_summary"]["post_change_tests_passed"] = False
    rejected = gate_worker_review(repo, request_id, receipt, 0, tmp_path)
    payload = json.loads(rejected.stdout)
    assert payload["accepted"] is False
    assert "post_change_tests proof" in payload["error"]
    assert payload["astra_calls"] == 0


@pytest.mark.parametrize("execution_status", [
    "baseline_failed", "step_not_started", "step_failed", "tests_failed", "timeout",
])
def test_structured_worker_failure_reaches_astra_with_no_changes(tmp_path: Path, execution_status: str):
    repo = init_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    receipt = {
        "version": 1,
        "request_id": f"failure-{execution_status}",
        "status": "failed",
        "execution_status": execution_status,
        "exit_code": 1,
        "worker_summary": {
            "status": "backlog_complete",
            "base_head": head,
            "source_commit": head,
            "changed_paths": [],
            "tests": ["tests/test_tasks_worker.py"],
            "tickets": [{"status": "failed", "changed_paths": [], "error": "baseline failed"}],
            "failure": "baseline failed",
        },
    }
    result = gate_worker_review(repo, receipt["request_id"], receipt, 1, tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["accepted"] is True
    assert payload["astra_calls"] == 1
    assert payload["summary"]["changed_paths"] == []


def test_worker_failure_before_report_reaches_astra(tmp_path: Path):
    repo = init_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    receipt = {
        "version": 1, "request_id": "before-report-001", "status": "failed",
        "execution_status": "step_not_started", "exit_code": 2,
        "worker_summary": {
            "status": "worker_failed_before_report", "base_head": head,
            "source_commit": head, "changed_paths": [], "uncommitted_paths": [],
            "tests": ["tests/test_tasks_worker.py"], "tickets": [],
            "failure": "preflight failed",
        },
    }
    result = gate_worker_review(repo, "before-report-001", receipt, 2, tmp_path)
    payload = json.loads(result.stdout)
    assert payload["accepted"] is True
    assert payload["astra_calls"] == 1


def test_explicit_noop_success_reaches_astra_without_commit(tmp_path: Path):
    repo = init_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    receipt = {
        "version": 1, "request_id": "noop-001", "status": "completed",
        "execution_status": "success", "exit_code": 0,
        "worker_summary": {
            "base_head": head, "source_commit": head, "changed_paths": [],
            "tests": ["1 passed"],
            "tickets": [{"status": "done", "noop": True, "tests_passed": True,
                         "gate_status": "ACCEPTED", "changed_paths": [], "commit": ""}],
        },
    }
    result = gate_worker_review(repo, "noop-001", receipt, 0, tmp_path)
    assert json.loads(result.stdout)["astra_calls"] == 1


def test_worker_commit_outside_ticket_is_fatal(tmp_path: Path):
    repo = init_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
    git(repo, "add", "outside.txt")
    git(repo, "commit", "-m", "outside ticket")
    source = git(repo, "rev-parse", "HEAD")
    git(repo, "reset", "--hard", base)
    receipt = {
        "version": 1, "request_id": "outside-001", "status": "completed",
        "execution_status": "success", "exit_code": 0,
        "worker_summary": {
            "base_head": base, "source_commit": source,
            "changed_paths": ["outside.txt"], "tests": ["1 passed"],
            "tickets": [{"status": "done", "tests_passed": True, "gate_status": "ACCEPTED",
                         "commit": source, "changed_paths": ["outside.txt"]}],
        },
    }
    result = gate_worker_review(repo, "outside-001", receipt, 0, tmp_path)
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["astra_calls"] == 0
    assert "authorized ticket" in payload["error"]


def test_worker_uncommitted_protected_path_is_fatal(tmp_path: Path):
    repo = init_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    receipt = {
        "version": 1, "request_id": "protected-001", "status": "failed",
        "execution_status": "policy_rejected", "exit_code": 0,
        "worker_summary": {
            "base_head": head, "source_commit": head, "changed_paths": [],
            "uncommitted_paths": ["scripts/start_octopus_astra.ps1"],
            "tests": ["tests/test_tasks_worker.py"],
            "tickets": [{"status": "failed", "changed_paths": []}],
        },
    }
    result = gate_worker_review(repo, "protected-001", receipt, 0, tmp_path)
    payload = json.loads(result.stdout)
    assert payload["accepted"] is False
    assert payload["astra_calls"] == 0
    assert "uncommitted path" in payload["error"]


@pytest.mark.skipif(not shutil.which(POWERSHELL), reason="constructeur Windows : powershell absent")
def test_external_runner_emits_baseline_failure_receipt_and_review_continues(tmp_path: Path):
    repo = init_repo(tmp_path)
    request_id = "baseline-failure-001"
    head = git(repo, "rev-parse", "HEAD")
    plan_dir = repo / "cache" / "astra-tickets"
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{request_id}.json").write_text(json.dumps({
        "policy": "product_ticket", "request_id": request_id, "base_head": head,
        "tickets": [{"allowed_paths": ["product.txt"],
                     "test_targets": ["tests/test_tasks_worker.py"]}],
    }), encoding="utf-8")
    report = {
        "base_head": head, "final_head": head, "status": "backlog_complete",
        "tickets": [{"task_id": 1, "status": "failed", "result": {
            "output": None, "error": "DevWorkerError: oracle baseline invalide avant Kilo (run 1): ModuleNotFoundError: requests",
        }}],
    }
    source_report = tmp_path / "report.json"
    source_report.write_text(json.dumps(report), encoding="utf-8")
    report_dir = repo / "data" / "night-shift-reports"
    report_dir.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python.cmd").write_text(
        f'@echo off\ncopy /Y "{source_report}" "{report_dir / "fake-run.json"}" >NUL\nexit /b 0\n',
        encoding="ascii",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / "run_external_dev_ticket.ps1"),
         "-Plan", f"cache/astra-tickets/{request_id}.json",
         "-RequestId", request_id,
         "-ResultPath", f"cache/astra-relay/results/{request_id}.json"],
        cwd=repo, env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
        text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((repo / "cache" / "astra-relay" / "results" / f"{request_id}.json").read_text(encoding="utf-8-sig"))
    assert receipt["status"] == "failed"
    assert receipt["execution_status"] == "baseline_failed"
    assert receipt["worker_summary"]["changed_paths"] == []
    assert receipt["worker_summary"]["tests"] == ["tests/test_tasks_worker.py"]
    assert receipt["worker_summary"]["baseline_oracle"] == ["tests/test_tasks_worker.py"]
    assert receipt["worker_summary"]["post_change_tests"] == []
    assert receipt["worker_summary"]["post_change_tests_passed"] is False
    review = gate_worker_review(repo, request_id, receipt, 0, tmp_path)
    assert json.loads(review.stdout)["astra_calls"] == 1


def checkpoint(repo: Path, paths: list[str], request_id: str = "phase-1") -> Path:
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    request = relay / "checkpoint.json"
    request.write_text(
        json.dumps(
            {
                "version": 2,
                "request_id": request_id,
                "base_head": git(repo, "rev-parse", "HEAD"),
                "message": "phase: tested change",
                "paths": paths,
            }
        ),
        encoding="utf-8",
    )
    return request


def test_host_checkpoint_commits_only_declared_paths(tmp_path: Path):
    repo = init_repo(tmp_path)
    (repo / "product.txt").write_text("after\n", encoding="utf-8")
    request = checkpoint(repo, ["product.txt"])
    result_path = repo / "cache" / "astra-relay" / "checkpoint-result.json"

    result = run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "commit_astra_checkpoint.ps1"),
        "-Repo",
        str(repo),
        "-CheckpointPath",
        str(request),
        "-ResultPath",
        str(result_path),
        cwd=repo,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "log", "-1", "--format=%s") == "phase: tested change"
    assert git(repo, "status", "--porcelain") == ""
    receipt = json.loads(result_path.read_text(encoding="utf-8-sig"))
    assert receipt["request_id"] == "phase-1"
    assert receipt["commit"] == git(repo, "rev-parse", "HEAD")
    assert not request.exists()


def test_offline_sandbox_fake_step_review_checkpoint(tmp_path: Path):
    from octopus import dev_worker

    if run("docker", "image", "inspect", dev_worker.DEFAULT_TEST_SANDBOX_IMAGE).returncode:
        pytest.skip("local Docker sandbox image unavailable")
    repo = init_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_product.py").write_text(
        "from pathlib import Path\n"
        "def test_product():\n"
        "    assert Path('product.txt').read_text().strip() in {'before', 'after'}\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("cache/\n__pycache__/\n", encoding="utf-8")
    git(repo, "add", ".gitignore", "tests/test_product.py")
    git(repo, "commit", "-m", "add immutable oracle")
    base = git(repo, "rev-parse", "HEAD")
    command = [sys.executable, "-m", "pytest", "-q", "tests/test_product.py"]
    host = run(*command, cwd=repo)
    baseline, baseline_green = dev_worker._run_tests(repo, [command], sandbox="docker")
    assert host.returncode == 0, host.stdout + host.stderr
    assert baseline_green, baseline[-2000:]

    git(repo, "switch", "-c", "fake-step")
    (repo / "product.txt").write_text("after\n", encoding="utf-8")
    git(repo, "commit", "-am", "step: bounded change")
    source = git(repo, "rev-parse", "HEAD")
    targeted, targeted_green = dev_worker._run_tests(repo, [command], sandbox="docker")
    assert targeted_green, targeted[-2000:]
    git(repo, "switch", "prep/astra-local-orchestration")

    receipt = {
        "version": 1, "request_id": "fake-step-001", "status": "completed",
        "execution_status": "success", "exit_code": 0,
        "worker_summary": {
            "status": "backlog_complete", "base_head": base,
            "source_commit": source, "changed_paths": ["product.txt"],
            "tests": ["1 passed"],
            "tickets": [{"status": "done", "commit": source, "tests_passed": True,
                         "gate_status": "ACCEPTED", "changed_paths": ["product.txt"]}],
        },
    }
    review = gate_worker_review(repo, "fake-step-001", receipt, 0, tmp_path)
    assert json.loads(review.stdout)["astra_calls"] == 1
    checkpoint_path = repo / "cache" / "astra-relay" / "checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps({
        "version": 2, "request_id": "fake-step-001", "kind": "worker_commit",
        "base_head": base, "source_commit": source,
        "message": "step: bounded change", "paths": ["product.txt"],
    }), encoding="utf-8")
    result = run(
        POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "commit_astra_checkpoint.ps1"),
        "-Repo", str(repo), "-CheckpointPath", str(checkpoint_path), cwd=repo,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "rev-parse", "HEAD") == source
    assert git(repo, "status", "--porcelain") == ""


def test_host_checkpoint_rejects_undeclared_changes(tmp_path: Path):
    repo = init_repo(tmp_path)
    (repo / "product.txt").write_text("after\n", encoding="utf-8")
    (repo / "other.txt").write_text("unexpected\n", encoding="utf-8")
    request = checkpoint(repo, ["product.txt"])
    before = git(repo, "rev-parse", "HEAD")

    result = run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "commit_astra_checkpoint.ps1"),
        "-Repo",
        str(repo),
        "-CheckpointPath",
        str(request),
        cwd=repo,
    )

    assert result.returncode != 0
    assert git(repo, "rev-parse", "HEAD") == before
    assert request.exists()
    assert git(repo, "diff", "--cached", "--name-only") == ""


def test_host_checkpoint_accepts_rename_with_both_declared_paths(tmp_path: Path):
    repo = init_repo(tmp_path)
    (repo / "renamed.txt").write_text("before\n", encoding="utf-8")
    (repo / "product.txt").unlink()
    request = checkpoint(repo, ["product.txt", "renamed.txt"], request_id="rename-1")

    result = run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "commit_astra_checkpoint.ps1"),
        "-Repo",
        str(repo),
        "-CheckpointPath",
        str(request),
        cwd=repo,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "diff", "--no-renames", "--name-only", "HEAD^").splitlines() == [
        "product.txt",
        "renamed.txt",
    ]


@pytest.mark.parametrize("supplied_message", ["worker: bounded change", "different subject", None])
def test_host_checkpoint_fast_forwards_reviewed_worker_commit(tmp_path: Path, supplied_message: str | None):
    repo = init_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "worker/task")
    (repo / "product.txt").write_text("worker result\n", encoding="utf-8")
    git(repo, "commit", "-am", "worker: bounded change")
    worker_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "prep/astra-local-orchestration")

    request = checkpoint(repo, ["product.txt"], request_id="worker-1")
    body = json.loads(request.read_text(encoding="utf-8"))
    body.update(
        kind="worker_commit",
        source_commit=worker_commit,
        base_head=base,
    )
    if supplied_message is None:
        body.pop("message")
    else:
        body["message"] = supplied_message
    request.write_text(json.dumps(body), encoding="utf-8")
    result_path = repo / "cache" / "astra-relay" / "checkpoint-result.json"

    result = run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "commit_astra_checkpoint.ps1"),
        "-Repo",
        str(repo),
        "-CheckpointPath",
        str(request),
        "-ResultPath",
        str(result_path),
        cwd=repo,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "rev-parse", "HEAD") == worker_commit
    assert git(repo, "status", "--porcelain") == ""
    receipt = json.loads(result_path.read_text(encoding="utf-8-sig"))
    assert receipt["message"] == git(repo, "show", "-s", "--format=%s", worker_commit)


@pytest.mark.parametrize("invalid", ["source_commit", "base_head", "ancestry", "paths"])
def test_host_checkpoint_rejects_invalid_worker_commit(tmp_path: Path, invalid: str):
    repo = init_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "worker/task")
    (repo / "product.txt").write_text("worker result\n", encoding="utf-8")
    git(repo, "commit", "-am", "worker: bounded change")
    source = git(repo, "rev-parse", "HEAD")
    if invalid == "ancestry":
        (repo / "product.txt").write_text("worker result 2\n", encoding="utf-8")
        git(repo, "commit", "-am", "worker: second change")
        source = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "prep/astra-local-orchestration")

    request = checkpoint(repo, ["product.txt"], request_id="worker-invalid")
    body = json.loads(request.read_text(encoding="utf-8"))
    body.update(kind="worker_commit", source_commit=source, base_head=base)
    if invalid == "source_commit":
        body["source_commit"] = "0" * 40
    elif invalid == "base_head":
        body["base_head"] = "0" * 40
    elif invalid == "paths":
        body["paths"] = ["other.txt"]
    request.write_text(json.dumps(body), encoding="utf-8")

    result = run(
        POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "commit_astra_checkpoint.ps1"),
        "-Repo", str(repo), "-CheckpointPath", str(request), cwd=repo,
    )

    assert result.returncode != 0
    assert git(repo, "rev-parse", "HEAD") == base
    assert request.exists()
    assert git(repo, "status", "--porcelain") == ""


def test_constructor_powershell_parses_and_disables_shell_snapshot():
    scripts = [
        ROOT / "scripts" / "start_octopus_astra.ps1",
        ROOT / "scripts" / "setup_octopus_codex_home.ps1",
        ROOT / "scripts" / "codex_preflight.ps1",
        ROOT / "scripts" / "run_external_dev_ticket.ps1",
        ROOT / "scripts" / "commit_astra_checkpoint.ps1",
    ]
    quoted = ",".join("'" + str(path).replace("'", "''") + "'" for path in scripts)
    command = (
        f"$bad=@(); foreach($p in @({quoted})){{"
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$tokens,[ref]$errors)|Out-Null;"
        "if($errors.Count){$bad += $p + ':' + (($errors|ForEach-Object Message)-join ',')}};"
        "if($bad.Count){$bad|Write-Error;exit 1}"
    )
    result = run(POWERSHELL, "-NoProfile", "-Command", command)
    assert result.returncode == 0, result.stdout + result.stderr

    config = (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "shell_snapshot = false" in config
    assert '\nmodel = ' not in config
    assert '\nmodel_reasoning_effort = ' not in config
    assert 'model_verbosity = "low"' in config
    assert "model_auto_compact_token_limit = 120000" in config
    launcher = (ROOT / "scripts" / "start_octopus_astra.ps1").read_text(encoding="utf-8")
    assert "[switch]$ValidateOnly" in launcher
    assert '[ValidateSet("B", "C", "D", "E", "F", "G")]' in launcher
    assert '[string]$Phase = "B"' in launcher
    assert '[int]$MaxAstraTurns = 2' in launcher
    assert 'MaxAstraTurns must be 1 or 2' in launcher
    assert '[string]$Reasoning = "medium"' in launcher
    assert 'model_reasoning_effort=$Reasoning' in launcher
    assert '"--ephemeral"' in launcher
    assert 'codexArguments += @("resume"' not in launcher
    assert "Invoke-HostValidation" in launcher
    assert "Read-TurnUsage" in launcher
    assert "Save-Handoff" in launcher
    assert "-ExistingThreadId" not in launcher
    assert "AGNES_VIDEO_REPLACEMENT.md" in launcher
    assert "OCTOPUS_HERMES_REPLACEMENT_MATRIX.md" in launcher
    assert "CODEX_START_2026-09-27.md" in launcher
    assert '"F" {' in launcher
    assert "FINAL_READINESS_ANTI_CONTAMINATION.md" in launcher
    assert "deterministic contamination canary" in launcher
    assert '"G" {' in launcher
    assert "OPERATIONALIZATION.md" in launcher
    assert "clean, stable runtime entry points" in launcher


def test_call_2_uses_only_compact_review_packet(tmp_path: Path):
    repo = init_repo(tmp_path)
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (repo / "DO-NOT-READ.md").write_text("stable document content " * 1500, encoding="utf-8")
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    (relay / "handoff.json").write_text(
        json.dumps(
            {
                "decisions": ["delegate bounded change"],
                "last_result": "Step completed",
                "next_decision": "review changed path",
                "blocked": None,
            }
        ),
        encoding="utf-8",
    )
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "call-2.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-JsonAtomic','Write-BoundedJsonAtomic','Read-Handoff','Limit-Text','Get-LocalSha256','ConvertTo-CompactAstraValue','New-CompactAstraPacket','Get-ChangedPaths','Get-CompactDiffStat','Write-AstraContext')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$snapshotPath='{relay / "snapshot.json"}';$fullContextPath='{relay / "context-full.json"}';$contextMetricsPath='{relay / "metrics.json"}'
$handoffPath='{relay / "handoff.json"}';$checkpointPath='{relay / "checkpoint.json"}';$requestPath='{relay / "request.json"}';$validationPath='{relay / "validation.json"}'
$Phase='G';$MaxSnapshotChars=8000;$MaxHandoffChars=6000;$MaxPreparedContextChars=30000
$phaseSpec=@{{documents=@('DO-NOT-READ.md');mission='not needed in review'}};$script:astraTurns=1
$script:lastWorkerSummary=@{{status='completed';changed_paths=@('product.txt');tests=@('1 passed')}}
$script:lastValidationSummary=$null
$script:reviewContext=@{{kind='step';changed_paths=@('product.txt');diff_stat=@('product.txt | 1 +')}}
$prompt=Write-AstraContext -TaskPrompt 'review only'
@{{prompt=$prompt;snapshot=(Get-Content -LiteralPath $snapshotPath -Raw|ConvertFrom-Json);metrics=(Get-Content -LiteralPath $contextMetricsPath -Raw|ConvertFrom-Json)}}|ConvertTo-Json -Depth 20
""",
        encoding="utf-8",
    )

    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert "HOST_CONTEXT_JSON" in payload["prompt"]
    assert "CONTEXT_MANIFEST_JSON" not in payload["prompt"]
    assert "MINIMAL_HANDOFF_JSON" not in payload["prompt"]
    assert payload["snapshot"]["handoff"]["decisions"] == ["delegate bounded change"]
    assert payload["snapshot"]["step_summary"]["tests"] == ["1 passed"]
    assert payload["metrics"]["call_kind"] == "review"
    assert payload["metrics"]["compacted"] is False
    assert payload["metrics"]["packet_chars"] < 8000
    assert payload["metrics"]["estimated_input_chars"] < 15000
    assert payload["snapshot"]["document_refs"][0]["sha256"] == hashlib.sha256(
        (repo / "DO-NOT-READ.md").read_bytes()
    ).hexdigest()
    assert "stable document content" not in payload["prompt"]


def test_large_context_compacts_and_keeps_critical_state(tmp_path: Path):
    repo = init_repo(tmp_path)
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    inspected = [{"path": "product.txt", "sha256": "a" * 64, "summary": "inspected path " + "x" * 180}] * 16
    handoff = {
        "version": 3, "objective": "phase objective", "phase": "G",
        "facts": ["ordinary fact " + str(i) + " " + "x" * 180 for i in range(25)]
        + ["SAFETY: do not broaden permissions"],
        "hypotheses": ["open hypothesis " + str(i) + " " + "h" * 180 for i in range(10)],
        "inspected": inspected,
        "tests": ["target test passed"] + ["test " + str(i) + " " + "t" * 180 for i in range(20)],
        "tickets": ["step-runtime active"],
        "remaining": ["next action: review economy.cycle"],
        "blocked": "BLOCKER: worker receipt pending",
        "next_decision": "Review the active Step ticket and choose the next runtime action.",
    }
    (relay / "handoff.json").write_text(json.dumps(handoff), encoding="utf-8")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "large-context.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$null,[ref]$null)
foreach($name in @('Write-JsonAtomic','Write-BoundedJsonAtomic','Read-Handoff','Limit-Text','Get-LocalSha256','ConvertTo-CompactAstraValue','New-CompactAstraPacket','Write-AstraContext')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$handoffPath='{relay / "handoff.json"}'
$snapshotPath='{relay / "snapshot.json"}';$fullContextPath='{relay / "context-full.json"}';$contextMetricsPath='{relay / "metrics.json"}'
$Phase='G';$MaxSnapshotChars=8000;$MaxHandoffChars=20000;$MaxPreparedContextChars=30000
$phaseSpec=@{{documents=@();mission='phase objective'}};$script:astraTurns=1
$script:reviewContext=@{{kind='step';base_head=('a'*40);head=('b'*40);diff_stat=@(1..25|ForEach-Object{{'diff '+$_+('d'*300)}})}}
$script:lastWorkerSummary=@{{execution_status='success';tests=@('target test passed');source_commit=('b'*40)}}
$script:lastValidationSummary=@{{status='passed';tests='target test passed'}}
$prompt=Write-AstraContext -TaskPrompt 'continue'
@{{prompt=$prompt;metrics=(Get-Content $contextMetricsPath -Raw|ConvertFrom-Json)}}|ConvertTo-Json -Depth 20 -Compress
""",
        encoding="utf-8",
    )
    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    packet = json.loads(payload["prompt"].split("HOST_CONTEXT_JSON\n", 1)[1])
    full = json.loads((relay / "context-full.json").read_text(encoding="utf-8"))
    assert payload["metrics"]["compacted"] is True
    assert payload["metrics"]["context_chars"] <= 8000
    assert full["handoff"]["facts"] == handoff["facts"]
    assert len(full["review"]["diff_stat"]) == 25
    assert "SAFETY: do not broaden permissions" in packet["handoff"]["facts"]
    assert "target test passed" in packet["handoff"]["tests"]
    assert packet["handoff"]["tickets"] == ["step-runtime active"]
    assert packet["handoff"]["blocked"] == "BLOCKER: worker receipt pending"
    assert packet["handoff"]["next_decision"] == handoff["next_decision"]
    assert len(packet["handoff"]["inspected"]) == 1
    assert packet["handoff"]["inspected"][0]["sha256"] == "a" * 64
    assert len(packet["review"]["diff_stat"]) < 12
    assert packet["dropped_counts"]["handoff"] > 0


@pytest.mark.parametrize("content,error", [
    ("{broken", "malformed JSON"),
    ('{"next_decision":42}', "invalid next decision"),
    ('{"next_decision":"continue","tests":"passed"}', "invalid tests section"),
])
def test_corrupt_handoff_fails_closed(tmp_path: Path, content: str, error: str):
    repo = init_repo(tmp_path)
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    (relay / "handoff.json").write_text(content, encoding="utf-8")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "corrupt-context.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$null,[ref]$null)
foreach($name in @('Read-Handoff','Write-AstraContext')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$handoffPath='{relay / "handoff.json"}';$MaxHandoffChars=6000;Write-AstraContext -TaskPrompt 'continue'
""",
        encoding="utf-8",
    )
    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)
    assert result.returncode != 0
    assert error in result.stderr
    assert not (relay / "snapshot.json").exists()


def test_handoff_compacts_massive_context(tmp_path: Path):
    repo = init_repo(tmp_path)
    handoff = repo / "cache" / "astra-relay" / "handoff.json"
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "handoff.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-BoundedJsonAtomic','Read-Handoff','Limit-Text','ConvertTo-CompactAstraValue','Get-ChangedPaths','Save-Handoff')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$handoffPath='{handoff}';$MaxHandoffChars=6000;$Phase='F'
$phaseSpec=@{{mission='bounded mission'}}
$large=@(1..12|ForEach-Object{{'x'*1000}})
Save-Handoff -Result ('y'*5000) -NextDecision ('z'*5000) -Decisions $large
""",
        encoding="utf-8",
    )

    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads(handoff.read_text(encoding="utf-8"))
    assert len(handoff.read_text(encoding="utf-8")) <= 6000
    assert saved["next_decision"].startswith("z")
    assert saved["last_result"].startswith("y")
    assert saved["dropped_counts"]["decisions"] > 0


def test_oversized_handoff_fails_before_reading(tmp_path: Path):
    handoff = tmp_path / "handoff.json"
    with handoff.open("wb") as stream:
        stream.truncate(1_000_000_000)
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "oversized.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$null,[ref]$null)
$fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Read-Handoff'}},$true)
Invoke-Expression $fn.Extent.Text
Read-Handoff -Path '{handoff}' -MaxChars 6000
""",
        encoding="utf-8",
    )
    result = run(POWERSHELL, "-NoProfile", "-File", str(runner))
    assert result.returncode != 0
    assert "exceeds its 6000 character limit" in result.stderr
    assert handoff.stat().st_size == 1_000_000_000


def test_handoff_utf8_roundtrip_preserves_phase_g_state(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    handoff = relay / "handoff.json"
    nested_summary = "Déjà inspecté " + '{"handoff":"nested"}' * 100
    handoff.write_text(json.dumps({
        "version": 3, "phase": "G", "next_decision": "Continuer après validation",
        "facts": ["État vérifié"], "inspected": [{
            "path": "product.txt", "sha256": "a" * 64, "summary": nested_summary,
        }],
        "criteria": [{"id": "B", "status": "demonstrated", "evidence": {
            "kind": "test", "ref": "tests/test_gateway.py::test_http_413", "head": "b" * 40,
        }}],
        "remaining": ["Vérifier F"], "pending_checkpoint_head": "c" * 40,
    }, ensure_ascii=False), encoding="utf-8")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "roundtrip.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$null,[ref]$null)
foreach($name in @('Write-BoundedJsonAtomic','Read-Handoff','ConvertTo-CompactAstraValue','Get-ChangedPaths','Save-Handoff')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$handoffPath='{handoff}';$MaxHandoffChars=6000;$Phase='G'
$phaseSpec=@{{mission='phase objective'}};$phaseGCriteria=@(@{{id='B'}})
$script:pendingCheckpointHead='{'c' * 40}'
1..8|ForEach-Object{{ Save-Handoff -Result 'résumé' -NextDecision 'Continuer après validation' }}
""",
        encoding="utf-8-sig",
    )
    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads(handoff.read_text(encoding="utf-8"))
    assert handoff.stat().st_size <= 6000
    assert saved["facts"] == ["État vérifié"]
    assert saved["inspected"][0]["sha256"] == "a" * 64
    assert saved["inspected"][0]["summary"].startswith("Déjà inspecté")
    assert len(saved["inspected"][0]["summary"]) <= 503
    assert saved["criteria"][0]["evidence"]["ref"] == "tests/test_gateway.py::test_http_413"
    assert saved["pending_checkpoint_head"] == "c" * 40
    assert saved["remaining"] == ["Vérifier F"]


def test_codex_stream_keeps_stderr_out_of_json_and_saves_thread_early(tmp_path: Path):
    fake_codex = tmp_path / "fake-codex.cmd"
    fake_codex.write_text(
        "@echo off\n"
        'if not "%~1"=="-" exit /b 4\n'
        "set /p prompt=\n"
        'if not "%prompt%"=="line one final readiness" exit /b 5\n'
        'echo {"type":"thread.started","thread_id":"thread-test"}\n'
        "echo diagnostic-stderr 1>&2\n"
        'echo {"type":"turn.completed","usage":{"input_tokens":1}}\n'
        "exit /b 0\n",
        encoding="ascii",
    )
    json_log = tmp_path / "turn.jsonl"
    stderr_log = tmp_path / "turn.stderr.log"
    runner = tmp_path / "exercise-stream.ps1"
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner.write_text(
        f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{launcher}', [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw ($errors | ForEach-Object Message) }}
$function = $ast.Find({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-CodexStreaming' }}, $true)
if (-not $function) {{ throw 'Invoke-CodexStreaming is missing' }}
Invoke-Expression $function.Extent.Text
$script:threadId = ''
$script:savedSession = ''
function Save-SessionState([string]$Status) {{ $script:savedSession = "$Status|$script:threadId" }}
$result = Invoke-CodexStreaming -Executable '{fake_codex}' -Arguments @('-') -JsonLog '{json_log}' -StderrLog '{stderr_log}' -InputText 'line one final readiness'
[ordered]@{{
    exit_code = $result.exit_code
    thread_id = $result.thread_id
    saved_session = $script:savedSession
}} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )

    result = run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner),
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["exit_code"] == 0
    assert payload["thread_id"] == "thread-test"
    assert payload["saved_session"] == "active|thread-test"
    assert [json.loads(line)["type"] for line in json_log.read_text(encoding="utf-8").splitlines()] == [
        "thread.started",
        "turn.completed",
    ]
    assert stderr_log.read_text(encoding="utf-8").strip() == "diagnostic-stderr"


def test_turn_usage_reads_completed_jsonl_without_copying_log(tmp_path: Path):
    log = tmp_path / "turn.jsonl"
    log.write_text(
        '{"type":"thread.started","thread_id":"fresh"}\n'
        '{"type":"turn.completed","usage":{"input_tokens":21,"cached_input_tokens":13,'
        '"output_tokens":8,"reasoning_output_tokens":3}}\n',
        encoding="utf-8",
    )
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    command = f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
$fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Read-TurnUsage'}},$true)
Invoke-Expression $fn.Extent.Text
Read-TurnUsage '{log}' | ConvertTo-Json -Compress
"""
    result = run(POWERSHELL, "-NoProfile", "-Command", command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "input_tokens": 21,
        "cached_input_tokens": 13,
        "output_tokens": 8,
        "reasoning_output_tokens": 3,
    }


def test_usage_separates_new_run_totals_from_lifetime_totals(tmp_path: Path):
    usage = tmp_path / "usage.json"
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    command = f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-JsonAtomic','Save-UsageSummary')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$usagePath='{usage}'
$previousLifetimeTotals=@{{input_tokens=100;cached_input_tokens=40;output_tokens=20;reasoning_output_tokens=5}}
$script:calls=@()
$newSession=Save-UsageSummary
$script:calls=@(@{{usage=@{{input_tokens=7;cached_input_tokens=3;output_tokens=2;reasoning_output_tokens=1}}}})
$afterCall=Save-UsageSummary
@{{new_session=$newSession;after_call=$afterCall;file=(Get-Content -LiteralPath $usagePath -Raw|ConvertFrom-Json)}}|ConvertTo-Json -Depth 20
"""
    result = run(POWERSHELL, "-NoProfile", "-Command", command)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["new_session"]["run_totals"]["input_tokens"] == 0
    assert payload["new_session"]["lifetime_totals"]["input_tokens"] == 100
    assert payload["after_call"]["run_totals"]["input_tokens"] == 7
    assert payload["after_call"]["lifetime_totals"]["input_tokens"] == 107
    assert payload["file"]["version"] == 2
    assert "totals" not in payload["file"]


def test_host_validation_accepts_only_fixed_full_pytest(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    (relay / "results").mkdir(parents=True)
    (relay / "requests").mkdir()
    request = relay / "validation.json"
    request.write_text('{"version":1,"kind":"full_pytest"}', encoding="utf-8")
    fake_python = tmp_path / "fake-python.cmd"
    fake_python.write_text("@echo off\necho 8 passed in 0.01s\nexit /b 0\n", encoding="ascii")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    command = f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-JsonAtomic','Limit-Text','Get-PytestCounts','Invoke-HostValidation')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$relayRoot='{relay}';$resultRoot=Join-Path $relayRoot 'results';$archiveRoot=Join-Path $relayRoot 'requests'
$validationPath=Join-Path $relayRoot 'validation.json';$pythonExe='{fake_python}'
$result=Invoke-HostValidation
$result | ConvertTo-Json -Compress
"""
    result = run(POWERSHELL, "-NoProfile", "-Command", command, cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((relay / "results" / "validation-latest.json").read_text(encoding="utf-8-sig"))
    assert receipt["exit_code"] == 0
    assert receipt["status"] == "passed"
    assert receipt["tests"] == {"passed": 8, "failed": 0, "skipped": 0}
    assert receipt["failing_tests"] == []
    assert receipt["output_truncated"] is True
    assert "summary" not in receipt
    assert not request.exists()
    request.write_text('{"version":1,"kind":"arbitrary_command","command":"git reset --hard"}', encoding="utf-8")
    rejected = run(POWERSHELL, "-NoProfile", "-Command", command, cwd=repo)
    assert "Unsupported host validation request" in rejected.stderr


def test_host_validation_injects_only_counts_and_bounded_failures(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    (relay / "results").mkdir(parents=True)
    (relay / "requests").mkdir()
    (relay / "validation.json").write_text('{"version":1,"kind":"full_pytest"}', encoding="utf-8")
    fake_python = tmp_path / "fake-failing-python.cmd"
    fake_python.write_text(
        "@echo off\n"
        "for /L %%i in (1,1,12) do echo FAILED tests/test_%%i.py::test_case - bounded failure\n"
        "echo 12 failed, 3 passed, 2 skipped in 1.00s\n"
        "exit /b 1\n",
        encoding="ascii",
    )
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    command = f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-JsonAtomic','Limit-Text','Get-PytestCounts','Invoke-HostValidation')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$relayRoot='{relay}';$resultRoot=Join-Path $relayRoot 'results';$archiveRoot=Join-Path $relayRoot 'requests'
$validationPath=Join-Path $relayRoot 'validation.json';$pythonExe='{fake_python}'
Invoke-HostValidation | ConvertTo-Json -Depth 10 -Compress
exit 0
"""
    result = run(POWERSHELL, "-NoProfile", "-Command", command, cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["exit_code"] == 1
    assert receipt["tests"] == {"passed": 3, "failed": 12, "skipped": 2}
    assert len(receipt["failing_tests"]) == 8
    assert max(map(len, receipt["failing_tests"])) <= 240
    assert "summary" not in receipt


def test_resumable_session_requires_matching_head_and_live_status(tmp_path: Path):
    session = tmp_path / "session.json"
    runner = tmp_path / "exercise-session.ps1"
    preflight = ROOT / "scripts" / "codex_preflight.ps1"
    runner.write_text(
        f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{preflight}', [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw ($errors | ForEach-Object Message) }}
$function = $ast.Find({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-ResumableSessionState' }}, $true)
if (-not $function) {{ throw 'Test-ResumableSessionState is missing' }}
Invoke-Expression $function.Extent.Text
$path = '{session}'
$cases = @(
    @{{ version = 2; status = 'active'; thread_id = '01a0da20-aa1d-7911-873a-bb0fad387446'; current_head = 'abc123' }},
    @{{ version = 3; status = 'active'; thread_id = '01a0da20-aa1d-7911-873a-bb0fad387446'; current_head = 'abc123' }},
    @{{ version = 2; status = 'failed'; thread_id = '01a0da20-aa1d-7911-873a-bb0fad387446'; current_head = 'abc123' }},
    @{{ version = 2; status = 'completed'; thread_id = '01a0da20-aa1d-7911-873a-bb0fad387446'; current_head = 'abc123' }},
    @{{ version = 2; status = 'active'; thread_id = '01a0da20-aa1d-7911-873a-bb0fad387446'; current_head = 'wrong' }}
)
$results = foreach ($case in $cases) {{
    $case | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
    Test-ResumableSessionState -Path $path -Head 'abc123'
}}
@($results) | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )

    result = run(
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner),
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == [True, True, True, False, False]


def test_preflight_does_not_query_remote_kilo_catalog():
    preflight = (ROOT / "scripts" / "codex_preflight.ps1").read_text(encoding="utf-8")
    assert "models kilo" not in preflight
    assert "AllowPendingCheckpoint" in preflight
    runner = (ROOT / "scripts" / "run_external_dev_ticket.ps1").read_text(encoding="utf-8")
    assert 'policy -ne "product_ticket"' in runner
    assert "worker_summary" in runner
    assert "changed_paths" in runner
    assert "diff_stat" in runner
    assert "result.output.goal" not in runner
