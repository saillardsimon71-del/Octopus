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
    assert 'model_reasoning_effort = "medium"' in config
    assert 'model_verbosity = "low"' in config
    assert "model_auto_compact_token_limit = 120000" in config
    launcher = (ROOT / "scripts" / "start_octopus_astra.ps1").read_text(encoding="utf-8")
    assert "[switch]$ValidateOnly" in launcher
    assert '[ValidateSet("B", "C", "D")]' in launcher
    assert '[string]$Phase = "B"' in launcher
    assert "MaxResumeInputTokens = 120000" in launcher
    assert "AGNES_VIDEO_REPLACEMENT.md" in launcher
    assert "OCTOPUS_HERMES_REPLACEMENT_MATRIX.md" in launcher


def test_codex_stream_keeps_stderr_out_of_json_and_saves_thread_early(tmp_path: Path):
    fake_codex = tmp_path / "fake-codex.cmd"
    fake_codex.write_text(
        "@echo off\n"
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
$result = Invoke-CodexStreaming -Executable '{fake_codex}' -Arguments @() -JsonLog '{json_log}' -StderrLog '{stderr_log}'
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
    assert json.loads(result.stdout.splitlines()[-1]) == [True, True, False, False]


def test_preflight_does_not_query_remote_kilo_catalog():
    preflight = (ROOT / "scripts" / "codex_preflight.ps1").read_text(encoding="utf-8")
    assert "models kilo" not in preflight
    assert "AllowPendingCheckpoint" in preflight
    runner = (ROOT / "scripts" / "run_external_dev_ticket.ps1").read_text(encoding="utf-8")
    assert 'policy -ne "product_ticket"' in runner
