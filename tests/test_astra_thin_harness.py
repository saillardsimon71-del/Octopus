import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def run_fake_harness(tmp_path, terminal="STABLE", relays=2, tokens=300000, total_tokens=1000000, unsafe=False, step_failure=False, large_context=False):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "prep/astra-local-orchestration")
    git(repo, "config", "user.name", "Constructor Test")
    git(repo, "config", "user.email", "constructor@example.invalid")
    (repo / ".gitignore").write_text("cache/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("Local test rules.\n", encoding="utf-8")
    (repo / "docs" / "migrations").mkdir(parents=True)
    (repo / "docs" / "migrations" / "OPERATIONALIZATION.md").write_text("Runtime symptom: HTTP 413.\n", encoding="utf-8")
    (repo / "octopus").mkdir()
    (repo / "octopus" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "octopus" / "transport.py").write_text("def value():\n    return 1  # proxy path\n", encoding="utf-8")
    (repo / "octopus" / "other.py").write_text("# proxy configuration\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_runtime.py").write_text("from octopus.transport import value\n\ndef test_value():\n    assert value() == 2\n", encoding="utf-8")
    scripts = repo / "scripts"
    scripts.mkdir()
    for name in ("setup_octopus_codex_home.ps1", "codex_preflight.ps1", "fetch_pinned_upstreams.ps1"):
        (scripts / name).write_text("param()\nexit 0\n", encoding="utf-8")
    (scripts / "commit_astra_checkpoint.ps1").write_text(
        """param([string]$Repo,[string]$CheckpointPath,[string]$ResultPath)
$request=Get-Content -LiteralPath $CheckpointPath -Raw|ConvertFrom-Json
git -C $Repo reset --hard $request.source_commit|Out-Null
if($LASTEXITCODE -ne 0){throw 'fake checkpoint failed'}
$receipt=@{version=1;request_id=$request.request_id;base_head=$request.base_head;commit=$request.source_commit;kind='worker_commit';message='fake Step repair';paths=@('octopus/transport.py')}
[IO.File]::WriteAllText($ResultPath,($receipt|ConvertTo-Json -Depth 10))
Remove-Item -LiteralPath $CheckpointPath
exit 0
""",
        encoding="utf-8",
    )
    (scripts / "run_external_dev_ticket.ps1").write_text(
        """param([string]$Plan,[double]$Hours,[string]$RequestId,[string]$ResultPath)
$ErrorActionPreference='Stop'
$repo=(git rev-parse --show-toplevel).Trim()
$branch=(git branch --show-current).Trim()
$base=(git rev-parse HEAD).Trim()
$sha=[System.Security.Cryptography.SHA256]::Create()
try{$hash=[BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes((Join-Path $repo $Plan)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
if($env:FAKE_STEP_FAILURE -eq '1'){
  $receipt=@{version=1;request_id=$RequestId;plan_path=$Plan;plan_sha256=$hash;status='failed';execution_status='baseline_failed';exit_code=0;worker_summary=@{
    status='backlog_complete';base_head=$base;source_commit=$base;changed_paths=@();uncommitted_paths=@();tests=@('tests/test_runtime.py');failure='oracle baseline failed';tickets=@(@{status='failed';changed_paths=@();error='oracle baseline failed'})
  }}
  [IO.File]::WriteAllText((Join-Path $repo $ResultPath),($receipt|ConvertTo-Json -Depth 12))
  exit 0
}
git switch -c "worker/fake-$RequestId" | Out-Null
Set-Content -LiteralPath 'octopus/transport.py' -Value "def value():`n    return 2  # proxy path"
git add octopus/transport.py
git commit -m 'fake Step repair' | Out-Null
$source=(git rev-parse HEAD).Trim()
python -m pytest -q tests/test_runtime.py --tb=short | Out-Null
if($LASTEXITCODE -ne 0){throw 'Step test failed'}
git switch $branch | Out-Null
$receipt=@{version=1;request_id=$RequestId;plan_path=$Plan;plan_sha256=$hash;status='completed';execution_status='success';exit_code=0;worker_summary=@{
  status='backlog_complete';base_head=$base;source_commit=$source
  changed_paths=@('octopus/transport.py');diff_stat=@('1 file changed')
  tests=@('1 passed');tickets=@(@{status='done';commit=$source;tests_passed=$true;gate_status='ACCEPTED';changed_paths=@('octopus/transport.py')})
}}
if($env:FAKE_LARGE_CONTEXT -eq '1'){$receipt.worker_summary.diff_stat=@(1..16|ForEach-Object{'diff '+$_+('d'*220)})}
$full=Join-Path $repo $ResultPath
[IO.File]::WriteAllText($full,($receipt|ConvertTo-Json -Depth 12))
exit 0
""",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (fake_bin / "codex.ps1").write_text(
        """$countPath=Join-Path $env:FAKE_STATE 'count.txt'
$count=if(Test-Path $countPath){[int](Get-Content $countPath -Raw)+1}else{1}
[IO.File]::WriteAllText($countPath,[string]$count)
$prompt=($input|Out-String)
[IO.File]::WriteAllText((Join-Path $env:FAKE_STATE "prompt-$count.txt"),$prompt)
$index=[Array]::IndexOf([object[]]$args,'--output-last-message')
if($index -lt 0){exit 7}
if($args -contains 'resume'){exit 8}
$repo=$env:FAKE_REPO
if($env:FAKE_TERMINAL -eq 'CONTINUE_FOREVER' -or ($env:FAKE_TERMINAL -in @('CONTINUE','BLOCKED_EARLY') -and $count -le 2)){
  $message=if($env:FAKE_TERMINAL -eq 'BLOCKED_EARLY' -and $count -eq 2){'ASTRA_STATUS: BLOCKED'}else{'ASTRA_CONTINUE: next bounded action'}
}elseif($env:FAKE_TERMINAL -eq 'CONTINUE' -and $count -eq 3){
  $message='ASTRA_STATUS: BLOCKED'
}elseif($count -eq 1){
  $missing=@(rg --fixed-strings 'agent.react_step' octopus 2>$null).Count
  $many=@(rg --line-number 'proxy' octopus 2>$null).Count
  $source=(Get-Content 'octopus/transport.py' -TotalCount 10|Out-String).Trim()
  @{missing=$missing;many=$many;source=$source}|ConvertTo-Json|Set-Content (Join-Path $env:FAKE_STATE 'discovery.json')
  $head=(git rev-parse HEAD).Trim()
  $paths=if($env:FAKE_UNSAFE -eq '1'){@('.git/config')}else{@('octopus/transport.py')}
  $ticket=@{version=1;request_id='step-runtime';phase='G';base_head=$head;title='Repair runtime proxy';objective='Repair observed runtime path';authorization='local code only';allowed_edit_paths=$paths;test_targets=@('tests/test_runtime.py');acceptance=@('runtime test passes')}
  $ticketPath=Join-Path $repo 'cache/astra-tickets/ticket.json'
  New-Item -ItemType Directory -Force -Path (Split-Path $ticketPath) | Out-Null
  [IO.File]::WriteAllText($ticketPath,($ticket|ConvertTo-Json -Depth 10))
  $request=@{version=1;request_id='step-runtime';phase='G';base_head=$head;product_ticket='cache/astra-tickets/ticket.json';hours=1}
  $requestPath=Join-Path $repo 'cache/astra-relay/request.json'
  [IO.File]::WriteAllText($requestPath,($request|ConvertTo-Json -Depth 10))
  $message='ASTRA_STATE_JSON {"facts":["HTTP 413; handler absent; proxy matches two files"],"hypotheses":[],"inspected":[{"path":"octopus/transport.py","summary":"runtime value path"}],"tests":[],"tickets":["step-runtime"],"remaining":["review Step"]}'
  if($env:FAKE_LARGE_CONTEXT -eq '1'){
    $facts=@('SAFETY: permissions stay local')+@(1..6|ForEach-Object{'runtime fact '+$_+('f'*220)})
    $hypotheses=@(1..4|ForEach-Object{'open hypothesis '+$_+('h'*220)})
    $tests=@('target test passed')+@(1..4|ForEach-Object{'test result '+$_+('t'*150)})
    $state=@{facts=$facts;hypotheses=$hypotheses;inspected=@(@{path='octopus/transport.py';summary='runtime value path'});tests=$tests;tickets=@('step-runtime');remaining=@('review Step')}
    $message='ASTRA_STATE_JSON '+($state|ConvertTo-Json -Depth 10 -Compress)
  }
}elseif($count -eq 2){
  $marker="HOST_CONTEXT_JSON`n"
  $packet=$prompt.Substring($prompt.LastIndexOf($marker)+$marker.Length)|ConvertFrom-Json
  if($packet.review.execution_status -eq 'baseline_failed'){
    [IO.File]::WriteAllText((Join-Path $env:FAKE_STATE 'failure-review.txt'),($packet.step_summary|ConvertTo-Json -Depth 10))
    $message='ASTRA_STATUS: BLOCKED'
  }else{
  $diff=git diff $packet.review.base_head $packet.review.head -- octopus/transport.py|Out-String
  [IO.File]::WriteAllText((Join-Path $env:FAKE_STATE 'review.txt'),$diff)
  $message="ASTRA_STATE_JSON {`"facts`": [`"Step returned passing test and a direct-child commit`"],`"hypotheses`":[],`"inspected`": [{`"path`":`"octopus/transport.py`",`"summary`":`"reviewed runtime path`"}],`"tests`": [`"1 passed`"],`"tickets`": [`"step-runtime`"],`"remaining`": [`"final review`"]}"
  if($env:FAKE_TERMINAL -ne 'AUTO'){
    $checkpoint=@{version=2;request_id='checkpoint-step-runtime';base_head=$packet.review.base_head;kind='worker_commit';source_commit=$packet.review.head;message='fake Step repair';paths=@('octopus/transport.py')}
    [IO.File]::WriteAllText((Join-Path $repo 'cache/astra-relay/checkpoint.json'),($checkpoint|ConvertTo-Json -Depth 10))
  }
  }
}elseif($env:FAKE_TERMINAL -eq 'AUTO' -and $count -eq 3){
  $checkpoint=@{version=2;request_id='checkpoint-step-runtime';base_head=(git rev-parse HEAD).Trim();kind='worker_commit';source_commit=(git rev-parse 'worker/fake-step-runtime').Trim();message='fake Step repair';paths=@('octopus/transport.py')}
  [IO.File]::WriteAllText((Join-Path $repo 'cache/astra-relay/checkpoint.json'),($checkpoint|ConvertTo-Json -Depth 10))
  $message='ASTRA_STATE_JSON {"facts":["checkpoint requested"],"hypotheses":[],"inspected":[],"tests":[],"tickets":["step-runtime"],"remaining":["full validation"]}'
}elseif($env:FAKE_TERMINAL -eq 'BLOCKED' -and $count -eq 3){
  $message='ASTRA_STATUS: BLOCKED'
}elseif(($env:FAKE_TERMINAL -eq 'STABLE' -and $count -eq 3) -or ($env:FAKE_TERMINAL -eq 'AUTO' -and $count -eq 4)){
  $marker="HOST_CONTEXT_JSON`n"
  $packet=$prompt.Substring($prompt.LastIndexOf($marker)+$marker.Length)|ConvertFrom-Json
  if(-not $packet.inspected_status[0].changed_since_inspection){throw 'changed file was not detected'}
  $source=(Get-Content 'octopus/transport.py' -TotalCount 10|Out-String).Trim()
  [IO.File]::WriteAllText((Join-Path $env:FAKE_STATE 'reread.txt'),$source)
  $message='ASTRA_STATE_JSON {"facts":["checkpoint applied"],"hypotheses":[],"inspected":[{"path":"octopus/transport.py","summary":"verified new runtime code","refresh":true}],"tests":[],"tickets":["step-runtime"],"remaining":["full validation"]}' + "`nASTRA_STATUS: STABLE"
}elseif($env:FAKE_TERMINAL -eq 'EXHAUST'){
  $message='ASTRA_CONTINUE: another local review'
}else{
  $message='ASTRA_STATUS: STABLE'
}
[IO.File]::WriteAllText([string]$args[$index+1],$message)
Write-Output ('{"type":"thread.started","thread_id":"thread-' + $count + '"}')
Write-Output '{"type":"turn.completed","usage":{"input_tokens":3,"cached_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":0}}'
exit 0
""",
        encoding="utf-8",
    )
    local_app_data = tmp_path / "local-app-data"
    local_app_data.mkdir()
    env = os.environ.copy()
    env.update({
        "LOCALAPPDATA": str(local_app_data),
        "FAKE_STATE": str(state),
        "FAKE_REPO": str(repo),
        "FAKE_TERMINAL": terminal,
        "FAKE_UNSAFE": "1" if unsafe else "0",
        "FAKE_STEP_FAILURE": "1" if step_failure else "0",
        "FAKE_LARGE_CONTEXT": "1" if large_context else "0",
        "PATH": str(fake_bin) + os.pathsep + env["PATH"],
    })
    launcher = ROOT / "scripts" / "start_octopus_astra.ps1"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher),
         "-CodexHome", str(tmp_path / "codex-home"), "-Phase", "G", "-MaxAstraTurns", "2",
         "-MaxRelayCycles", str(relays), "-MaxRunTokens", str(tokens),
         "-MaxTotalRunTokens", str(total_tokens), "-SkipFetch", "-NewSession"],
        cwd=repo, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    return repo, state, result


@pytest.mark.parametrize("terminal,expected,calls,relays", [("STABLE", "completed", 4, 2), ("BLOCKED", "blocked", 3, 2), ("AUTO", "completed", 5, 3)])
def test_generic_discovery_step_review_and_multi_cycle(tmp_path, terminal, expected, calls, relays):
    repo, state, result = run_fake_harness(tmp_path, terminal=terminal, relays=relays)
    assert result.returncode == 0, result.stdout + result.stderr
    discovery = json.loads((state / "discovery.json").read_text(encoding="utf-8"))
    assert discovery["missing"] == 0
    assert discovery["many"] == 2
    assert "def value" in discovery["source"]
    assert "+    return 2" in (state / "review.txt").read_text(encoding="utf-8")
    if terminal != "BLOCKED":
        assert "return 2" in (state / "reread.txt").read_text(encoding="utf-8")
    assert (state / "count.txt").read_text(encoding="utf-8") == str(calls)
    second = (state / "prompt-2.txt").read_text(encoding="utf-8")
    third = (state / "prompt-3.txt").read_text(encoding="utf-8")
    assert "step_summary" in second and "source_commit" in second
    assert "HOST_CONTEXT_JSON" in third
    assert "CONTEXT_MANIFEST_JSON" not in third and "HOST_SNAPSHOT_JSON" not in third
    assert "runtime value path" in second
    second_packet = json.loads(second.split("HOST_CONTEXT_JSON\n", 1)[1])
    assert second_packet["inspected_status"] == [{"path": "octopus/transport.py", "changed_since_inspection": False}]
    handoff = json.loads((repo / "cache/astra-relay/handoff.json").read_text(encoding="utf-8"))
    assert handoff["inspected"][0]["sha256"]
    assert handoff["tests"]
    session = json.loads((repo / "cache/astra-relay/session.json").read_text(encoding="utf-8"))
    assert session["status"] == expected
    assert session["relay_cycles_this_run"] == relays
    assert (repo / "octopus/transport.py").read_text(encoding="utf-8").find("return 2") >= 0
    if terminal != "BLOCKED":
        validation = json.loads((repo / "cache/astra-relay/results/validation-latest.json").read_text(encoding="utf-8"))
        assert validation["status"] == "passed"
    metrics = json.loads((repo / "cache/astra-relay/context-metrics.json").read_text(encoding="utf-8"))
    assert metrics["packet_chars"] < 8000
    assert metrics["estimated_input_chars"] < 30000
    assert git(repo, "status", "--porcelain") == ""


def test_baseline_failure_receipt_gets_astra_review(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, step_failure=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "2"
    review = json.loads((state / "failure-review.txt").read_text(encoding="utf-8"))
    assert review["execution_status"] == "baseline_failed"
    assert review["changed_paths"] == []
    assert "oracle baseline failed" in review["failure"]
    session = json.loads((repo / "cache/astra-relay/session.json").read_text(encoding="utf-8"))
    assert session["status"] == "blocked"
    assert git(repo, "status", "--porcelain") == ""


def test_large_handoff_compacts_and_continues_step_review(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, large_context=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "4"
    second = (state / "prompt-2.txt").read_text(encoding="utf-8")
    packet = json.loads(second.split("HOST_CONTEXT_JSON\n", 1)[1])
    assert len(second.split("HOST_CONTEXT_JSON\n", 1)[1].strip()) <= 8000
    assert packet["compacted"] is True
    assert "SAFETY: permissions stay local" in packet["handoff"]["facts"]
    assert "target test passed" in packet["handoff"]["tests"]
    assert packet["handoff"]["tickets"] == ["step-runtime"]
    assert packet["review"]["head"] == packet["step_summary"]["source_commit"]
    assert git(repo, "status", "--porcelain") == ""


def test_relay_budget_stops_without_extra_astra_call(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, terminal="EXHAUST", relays=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "2"
    assert "Relay cycle budget reached" in result.stdout
    session = json.loads((repo / "cache/astra-relay/session.json").read_text(encoding="utf-8"))
    assert session["status"] == "active"
    assert session["stop_reason"] == "relay_cycle_limit"


def test_unsafe_ticket_fails_before_step(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, unsafe=True)
    assert result.returncode != 0
    assert "protected repository state" in result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "1"
    assert "worker/fake-step-runtime" not in git(repo, "branch", "--list")


def test_mini_session_budget_continues_with_fresh_calls(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, terminal="CONTINUE", relays=2, tokens=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "3"
    session = json.loads((repo / "cache/astra-relay/session.json").read_text(encoding="utf-8"))
    assert session["status"] == "blocked"
    assert session["mini_session_calls"] == 1
    assert session["total_astra_calls"] == 3
    assert session["relay_cycles_this_run"] == 1
    assert session["stop_reason"] == "blocked"
    assert "Astra calls: 3" in result.stdout
    assert "mini_session_calls=1" in result.stdout
    assert "stop_reason=blocked" in result.stdout


def test_terminal_status_stops_before_mini_session_rollover(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, terminal="BLOCKED_EARLY", relays=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "2"
    session = json.loads((repo / "cache/astra-relay/session.json").read_text(encoding="utf-8"))
    assert session["stop_reason"] == "blocked"


def test_multiple_mini_sessions_stop_at_relay_limit(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, terminal="CONTINUE_FOREVER", relays=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "6"
    session = json.loads((repo / "cache/astra-relay/session.json").read_text(encoding="utf-8"))
    assert session["mini_session_calls"] == 2
    assert session["total_astra_calls"] == 6
    assert session["relay_cycles"] == 2
    assert session["stop_reason"] == "relay_cycle_limit"


def test_global_token_budget_stops_with_explicit_reason(tmp_path):
    repo, state, result = run_fake_harness(tmp_path, terminal="CONTINUE", relays=2, tokens=10, total_tokens=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "count.txt").read_text(encoding="utf-8") == "2"
    session = json.loads((repo / "cache/astra-relay/session.json").read_text(encoding="utf-8"))
    assert session["status"] == "active"
    assert session["stop_reason"] == "global_token_limit"
    assert "stop_reason=global_token_limit" in result.stdout
