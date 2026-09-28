import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = "powershell"


def run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
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


def test_step_relay_v2_normalizes_product_ticket_and_prefers_it_over_legacy_plan(tmp_path: Path):
    repo = init_repo(tmp_path)
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
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
        "starting_paths": ["octopus/__main__.py"],
        "likely_tests": ["tests/test_gui.py"],
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
    normalized = json.loads(
        (repo / "cache" / "astra-tickets" / "phase-g-runtime-001.json").read_text(encoding="utf-8-sig")
    )
    assert normalized == {"policy": "product_ticket", **ticket}


def test_step_relay_keeps_legacy_plan_path_unchanged(tmp_path: Path):
    repo = init_repo(tmp_path)
    request = {
        "version": 1,
        "kind": "step",
        "request_id": "legacy-001",
        "plan_path": "cache/astra-tickets/existing-ticket.json",
    }

    result = resolve_step_relay_plan(repo, request, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == "cache/astra-tickets/existing-ticket.json"
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
    assert "Relay request missing product_ticket or plan_path" in missing_plan.stderr
    assert not (repo / "cache" / "astra-tickets" / "missing-001.json").exists()


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


def test_host_checkpoint_fast_forwards_reviewed_worker_commit(tmp_path: Path):
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
        message="worker: bounded change",
    )
    request.write_text(json.dumps(body), encoding="utf-8")

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
    assert git(repo, "rev-parse", "HEAD") == worker_commit
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


def test_constructor_context_is_bounded_and_does_not_preload_documents():
    launcher = (ROOT / "scripts" / "start_octopus_astra.ps1").read_text(encoding="utf-8")

    assert "[int]$MaxHandoffChars = 6000" in launcher
    assert "[int]$MaxSnapshotChars = 8000" in launcher
    assert "[int]$MaxManifestChars = 6000" in launcher
    assert "[int]$MaxHostPrepareChars = 4200" in launcher
    assert "[int]$MaxPreparedContextChars = 30000" in launcher
    assert "HOST_SNAPSHOT_JSON" in launcher
    assert "CONTEXT_MANIFEST_JSON" in launcher
    assert "MINIMAL_HANDOFF_JSON" in launcher
    assert "estimated_prompt_chars" in launcher
    assert "context_file_count = $contextFileCount" in launcher
    assert "Read $($phaseSpec.documents)" not in launcher
    assert "Read cache/astra-relay/handoff.json" not in launcher
    assert "referenced report and actual diff" not in launcher
    assert "Never reread a document whose manifest hash is unchanged" in launcher
    assert "capped at 200 lines and 20000 characters" in launcher
    assert "HOST_REVIEW_PACKET_JSON" in launcher
    assert "packet = 'call_1_prepare'" in launcher
    assert "packet = 'call_2_review'" in launcher


def test_astra_prompt_forbids_general_exploration_and_routes_mechanical_work_early():
    launcher = (ROOT / "scripts" / "start_octopus_astra.ps1").read_text(encoding="utf-8")

    assert "The shell-command budget is zero by default" in launcher
    assert "General repository exploration is forbidden" in launcher
    assert "Run no long test in Astra and never run a full suite" in launcher
    assert "search for tests already supplied by HOST PREPARE" in launcher
    assert "As soon as files, expected behavior, oracle/tests and limits can be stated" in launcher
    assert "then end the call immediately" in launcher
    assert "After publishing any Step, checkpoint or host-validation request" in launcher


def test_context_manifest_marks_unchanged_documents(tmp_path: Path):
    repo = init_repo(tmp_path)
    phase_doc = repo / "docs" / "phase.md"
    phase_doc.parent.mkdir(parents=True)
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    phase_doc.write_text("PHASE DOCUMENT MUST NOT BE PRELOADED\n", encoding="utf-8")
    git(repo, "add", "AGENTS.md", "docs/phase.md")
    git(repo, "commit", "-m", "context documents")
    relay = repo / "cache" / "astra-relay"
    relay.mkdir(parents=True)
    (relay / "handoff.json").write_text("{}", encoding="utf-8")
    manifest = repo / "context-manifest.json"
    snapshot = repo / "snapshot.json"
    metrics = repo / "metrics.json"
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "manifest.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-JsonAtomic','Write-BoundedJsonAtomic','Limit-Text','Get-ChangedPaths','Get-CompactDiffStat','Invoke-BoundedRg','Find-AssociatedTests','Get-HostPreparation','Save-ContextManifest','Write-AstraContext')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$contextManifestPath='{manifest}';$snapshotPath='{snapshot}';$contextMetricsPath='{metrics}'
$handoffPath='{relay / "handoff.json"}';$checkpointPath='{relay / "checkpoint.json"}';$requestPath='{relay / "request.json"}';$validationPath='{relay / "validation.json"}'
$Phase='F';$MaxManifestChars=6000;$MaxSnapshotChars=8000;$MaxHandoffChars=6000;$MaxHostPrepareChars=4200;$MaxPreparedContextChars=30000
$phaseSearches=@('contamination','readiness')
$phaseSpec=@{{documents=@('docs/phase.md');mission='bounded mission'}};$baseline=@{{exit_code=0;duration_seconds=1}}
$first=Write-AstraContext -TaskPrompt 'first';$second=Write-AstraContext -TaskPrompt 'second'
@{{prompt=$second;manifest=(Get-Content -LiteralPath $contextManifestPath -Raw|ConvertFrom-Json);metrics=(Get-Content -LiteralPath $contextMetricsPath -Raw|ConvertFrom-Json)}}|ConvertTo-Json -Depth 20
""",
        encoding="utf-8",
    )

    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert [item["changed_since_previous_call"] for item in payload["manifest"]["documents"]] == [False, False]
    assert all("content" not in item for item in payload["manifest"]["documents"])
    assert "PHASE DOCUMENT MUST NOT BE PRELOADED" not in payload["prompt"]
    assert payload["metrics"]["context_file_count"] == 3
    assert payload["metrics"]["estimated_input_chars"] <= 30000


def test_host_prepare_discovers_runtime_symbols_and_tests_with_hard_bounds(tmp_path: Path):
    repo = init_repo(tmp_path)
    source = repo / "octopus" / "sample_engine.py"
    source.parent.mkdir()
    source.write_text("class SampleEngine:\n    pass\n", encoding="utf-8")
    test_file = repo / "tests" / "test_sample_engine.py"
    test_file.parent.mkdir()
    test_file.write_text("from octopus.sample_engine import SampleEngine\n", encoding="utf-8")
    git(repo, "add", "octopus/sample_engine.py", "tests/test_sample_engine.py")
    git(repo, "commit", "-m", "sample runtime")
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "host-prepare.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Limit-Text','Invoke-BoundedRg','Find-AssociatedTests','Get-HostPreparation')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$phaseSearches=@('SampleEngine');$MaxHostPrepareChars=4200
$packet=Get-HostPreparation
@{{packet=$packet;chars=($packet|ConvertTo-Json -Depth 10).Length}}|ConvertTo-Json -Depth 20
""",
        encoding="utf-8",
    )

    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["chars"] <= 4200
    assert "octopus/sample_engine.py" in payload["packet"]["runtime_paths"]
    assert any("class SampleEngine" in item for item in payload["packet"]["symbols_and_entrypoints"])
    assert "tests/test_sample_engine.py" in payload["packet"]["likely_tests"]
    assert len(payload["packet"]["targeted_searches"][0]["matches"]) <= 2


def test_call_2_uses_only_compact_review_packet(tmp_path: Path):
    repo = init_repo(tmp_path)
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
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
foreach($name in @('Write-JsonAtomic','Write-BoundedJsonAtomic','Limit-Text','Get-ChangedPaths','Get-CompactDiffStat','Write-AstraContext')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$repo='{repo}';$snapshotPath='{relay / "snapshot.json"}';$contextMetricsPath='{relay / "metrics.json"}'
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
    assert "HOST_REVIEW_PACKET_JSON" in payload["prompt"]
    assert "CONTEXT_MANIFEST_JSON" not in payload["prompt"]
    assert "MINIMAL_HANDOFF_JSON" not in payload["prompt"]
    assert payload["snapshot"]["packet"] == "call_2_review"
    assert payload["snapshot"]["previous_decision"]["decisions"] == ["delegate bounded change"]
    assert payload["snapshot"]["host_test_summary"]["source"] == "step_host"
    assert payload["metrics"]["context_file_count"] == 1
    assert payload["metrics"]["manifest_document_count"] == 0


def test_handoff_rejects_massive_context(tmp_path: Path):
    repo = init_repo(tmp_path)
    handoff = repo / "cache" / "astra-relay" / "handoff.json"
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    runner = tmp_path / "handoff.ps1"
    runner.write_text(
        f"""
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw ($errors|ForEach-Object Message)}}
foreach($name in @('Write-BoundedJsonAtomic','Limit-Text','Get-ChangedPaths','Save-Handoff')){{
  $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
  Invoke-Expression $fn.Extent.Text
}}
$handoffPath='{handoff}';$MaxHandoffChars=6000;$Phase='F'
$phaseSpec=@{{mission='bounded mission'}}
$large=@(1..12|ForEach-Object{{'x'*1000}})
try {{ Save-Handoff -Result ('y'*5000) -NextDecision ('z'*5000) -Decisions $large; exit 2 }}
catch {{ if($_.Exception.Message -notmatch 'character limit'){{throw}} }}
""",
        encoding="utf-8",
    )

    result = run(POWERSHELL, "-NoProfile", "-File", str(runner), cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not handoff.exists()


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
