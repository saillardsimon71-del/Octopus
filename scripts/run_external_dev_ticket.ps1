param(
    [Parameter(Mandatory = $true)]
    [string]$Plan,
    [double]$Hours = 1.0,
    [string]$RequestId = "",
    [string]$ResultPath = "",
    [switch]$AllowRerun
)

$ErrorActionPreference = "Stop"

function Write-JsonAtomic([string]$Path, [object]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $tmp = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Limit-Text([object]$Value, [int]$MaxChars = 300) {
    $text = [string]$Value
    if ($text.Length -le $MaxChars) { return $text }
    return $text.Substring(0, $MaxChars) + "..."
}

$repo = (git rev-parse --show-toplevel 2>$null | Out-String).Trim()
if (-not $repo) { throw "Run this helper from inside the OCTOPUS repository." }
Set-Location $repo
$repoRoot = [System.IO.Path]::GetFullPath($repo).TrimEnd("\") + "\"

function Repo-Relative([string]$FullPath) {
    $resolved = [System.IO.Path]::GetFullPath($FullPath)
    if (-not $resolved.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes OCTOPUS repository: $resolved"
    }
    return $resolved.Substring($repoRoot.Length).Replace("\", "/")
}

$planPath = [System.IO.Path]::GetFullPath((Join-Path $repo $Plan))
if (-not (Test-Path -LiteralPath $planPath -PathType Leaf)) { throw "Ticket plan not found: $planPath" }
if (-not $planPath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Ticket plan must live under the OCTOPUS repository." }

$ticketRoot = [System.IO.Path]::GetFullPath((Join-Path $repo "cache\astra-tickets")).TrimEnd("\") + "\"
if (-not $planPath.StartsWith($ticketRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Relay tickets must live under cache\astra-tickets\."
}
$planBody = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ([string]$planBody.policy -ne "product_ticket") {
    throw "Relay ticket policy must be product_ticket."
}

if ($Hours -le 0 -or $Hours -gt 8) { throw "Hours must be > 0 and <= 8." }

$status = (git status --porcelain | Out-String).Trim()
if ($status) { throw "OCTOPUS source repository is not clean. Commit/stash/revert before starting the external worker." }

$stopFile = Join-Path $repo "data\NIGHT_SHIFT_STOP"
if (Test-Path -LiteralPath $stopFile) {
    throw "Night-shift kill switch is active at $stopFile. Clear it explicitly with: python -m octopus night-resume"
}

$sha256 = [System.Security.Cryptography.SHA256]::Create()
try { $planHash = [BitConverter]::ToString($sha256.ComputeHash([System.IO.File]::ReadAllBytes($planPath))).Replace('-', '').ToLowerInvariant() }
finally { $sha256.Dispose() }
if (-not $RequestId) { $RequestId = "manual-" + $planHash.Substring(0, 12) }
if ($RequestId -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$") {
    throw "Invalid RequestId. Use 1-80 characters: letters, digits, dot, underscore, hyphen."
}

$relayRoot = Join-Path $repo "cache\astra-relay"
$receiptRoot = Join-Path $relayRoot "receipts"
$logRoot = Join-Path $relayRoot "logs"
New-Item -ItemType Directory -Force -Path $receiptRoot, $logRoot | Out-Null

$receiptPath = Join-Path $receiptRoot ($planHash + ".json")
if ((Test-Path -LiteralPath $receiptPath) -and -not $AllowRerun) {
    $previous = Get-Content -LiteralPath $receiptPath -Raw
    throw ("This exact ticket plan has already been attempted. Refusing accidental rerun. Receipt: " + $receiptPath + [Environment]::NewLine + $previous)
}

$started = (Get-Date).ToUniversalTime()
$logPath = Join-Path $logRoot ("{0}-{1}.log" -f $RequestId, $planHash.Substring(0, 12))

$receipt = [ordered]@{
    version = 1
    request_id = $RequestId
    plan_path = (Repo-Relative $planPath)
    plan_sha256 = $planHash
    status = "running"
    execution_status = "step_not_started"
    started_at_utc = $started.ToString("o")
    finished_at_utc = $null
    exit_code = $null
    log_path = (Repo-Relative $logPath)
    night_report_path = $null
    worker_summary = $null
}
Write-JsonAtomic -Path $receiptPath -Value $receipt

Write-Host "Starting bounded OCTOPUS worker outside the Codex model loop." -ForegroundColor Cyan
Write-Host "Request: $RequestId"
Write-Host "Plan SHA256: $planHash"
Write-Host "Plan: $planPath"
Write-Host "Log: $logPath"
Write-Host "Kill switch from another terminal: python -m octopus night-stop"
Write-Host ""

$beforeReports = @{}
$reportDir = Join-Path $repo "data\night-shift-reports"
if (Test-Path -LiteralPath $reportDir) {
    Get-ChildItem -LiteralPath $reportDir -Filter "*.json" -File | ForEach-Object {
        $beforeReports[$_.FullName] = $_.LastWriteTimeUtc
    }
}

& python -m octopus night-shift --repo . --plan $planPath --max-tasks 1 --hours $Hours 2>&1 | Tee-Object -FilePath $logPath
$code = $LASTEXITCODE

$nightReport = $null
if (Test-Path -LiteralPath $reportDir) {
    $candidates = @(Get-ChildItem -LiteralPath $reportDir -Filter "*.json" -File | Where-Object {
        (-not $beforeReports.ContainsKey($_.FullName)) -or ($_.LastWriteTimeUtc -gt $beforeReports[$_.FullName])
    } | Sort-Object LastWriteTimeUtc -Descending)
    if ($candidates.Count -gt 0) { $nightReport = Repo-Relative $candidates[0].FullName }
}

$receipt.status = if ($code -eq 0) { "completed" } else { "failed" }
$receipt.finished_at_utc = (Get-Date).ToUniversalTime().ToString("o")
$receipt.exit_code = $code
$receipt.night_report_path = $nightReport
$receipt.execution_status = if ($code -eq 0) { 'success' } else { 'step_not_started' }
if ($nightReport) {
    $report = Get-Content -LiteralPath (Join-Path $repo $nightReport) -Raw | ConvertFrom-Json
    $tickets = @($report.tickets | Select-Object -First 3)
    $changedPaths = @($tickets | ForEach-Object { @($_.result.output.changed_paths) } | Where-Object { $_ } | Sort-Object -Unique | Select-Object -First 30)
    $tests = @($tickets | ForEach-Object { @($_.result.output.tests) } | Where-Object { $_ } | Select-Object -First 20 | ForEach-Object { Limit-Text $_ 300 })
    if (-not $tests.Count) { $tests = @($planBody.tickets[0].test_targets | Select-Object -First 12) }
    $sourceCommit = [string]$report.final_head
    $baseHead = [string]$report.base_head
    $uncommittedPaths = @()
    if ($report.night_worktree) {
        $nightWorktree = [System.IO.Path]::GetFullPath([string]$report.night_worktree)
        $nightRoot = [System.IO.Path]::GetFullPath((Join-Path $repo 'data\night-worktrees')).TrimEnd('\') + '\'
        if (-not $nightWorktree.StartsWith($nightRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Night report worktree path escapes the approved local root.'
        }
        if (-not (Test-Path -LiteralPath $nightWorktree -PathType Container)) {
            throw 'Night report worktree is missing.'
        }
        $worktreeTop = (& git -C $nightWorktree rev-parse --show-toplevel 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [System.IO.Path]::GetFullPath($worktreeTop) -ne $nightWorktree) {
            throw 'Night report worktree identity mismatch.'
        }
        $unstaged = @(& git -C $nightWorktree diff --name-only --)
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect night worktree changes.' }
        $staged = @(& git -C $nightWorktree diff --cached --name-only --)
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect night worktree index.' }
        $untracked = @(& git -C $nightWorktree ls-files --others --exclude-standard)
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect night worktree untracked paths.' }
        $uncommittedPaths = @($unstaged + $staged + $untracked | Where-Object { $_ } | Sort-Object -Unique)
    }
    $diffStat = @()
    if ($baseHead -match '^[0-9a-fA-F]{40}$' -and $sourceCommit -match '^[0-9a-fA-F]{40}$') {
        $diffArguments = @('diff', '--stat', '--compact-summary', $baseHead, $sourceCommit, '--')
        if ($changedPaths.Count) { $diffArguments += $changedPaths }
        $diffStat = @(& git @diffArguments 2>$null | Select-Object -First 20 | ForEach-Object { Limit-Text $_ 300 })
    }
    $ticketSummaries = @($tickets | ForEach-Object {
        [ordered]@{
            task_id = $_.task_id
            status = Limit-Text $_.status 40
            commit = [string]$_.result.output.commit
            tests_passed = [bool]$_.result.output.tests_passed
            baseline_oracle_runs = [int]$_.result.output.baseline_oracle_runs
            post_change_tests_passed = [bool]$_.result.output.post_change_tests_passed
            gate_status = Limit-Text $_.result.output.gate_status 40
            changed_paths = @($_.result.output.changed_paths | Select-Object -First 30)
            error = Limit-Text $_.result.error 1200
            noop = [bool]$_.result.output.noop
        }
    })
    if ($ticketSummaries.Count -eq 1) {
        $ticketError = [string]$ticketSummaries[0].error
        if ($ticketSummaries[0].status -ne 'done') {
            $receipt.execution_status = if ($ticketError -match 'oracle baseline invalide|oracle baseline instable') { 'baseline_failed' }
                elseif ($ticketError -match 'timeout|Timeout') { 'timeout' }
                elseif ($ticketError -match 'acceptance gate|hors périmètre|frontière|policy') { 'policy_rejected' }
                elseif ($ticketError -match 'test|pytest|ORACLE_SIGNATURE') { 'tests_failed' }
                else { 'step_failed' }
        } elseif ([bool]$ticketSummaries[0].noop) {
            $receipt.execution_status = 'success'
        }
    }
    $receipt.worker_summary = [ordered]@{
        status = Limit-Text $report.status 60
        base_head = $baseHead
        source_commit = $sourceCommit
        changed_paths = $changedPaths
        uncommitted_paths = @($uncommittedPaths | Select-Object -First 30)
        diff_stat = $diffStat
        tests = $tests
        baseline_oracle = @($planBody.tickets[0].test_targets | Select-Object -First 20)
        post_change_tests = @($planBody.tickets[0].post_change_tests | Select-Object -First 20)
        baseline_oracle_runs = if ($ticketSummaries.Count -eq 1) { $ticketSummaries[0].baseline_oracle_runs } else { 0 }
        post_change_tests_passed = if ($ticketSummaries.Count -eq 1) { $ticketSummaries[0].post_change_tests_passed } else { $false }
        tickets = $ticketSummaries
        failure = if ($ticketSummaries.Count -eq 1 -and $ticketSummaries[0].status -ne 'done') { $ticketSummaries[0].error } else { $null }
        summary_truncated = $true
    }
}
if (-not $receipt.worker_summary) {
    $receipt.worker_summary = [ordered]@{
        status = 'worker_failed_before_report'
        base_head = [string]$planBody.base_head
        source_commit = [string]$planBody.base_head
        changed_paths = @()
        uncommitted_paths = @()
        diff_stat = @()
        tests = @($planBody.tickets[0].test_targets | Select-Object -First 12)
        baseline_oracle = @($planBody.tickets[0].test_targets | Select-Object -First 20)
        post_change_tests = @($planBody.tickets[0].post_change_tests | Select-Object -First 20)
        baseline_oracle_runs = 0
        post_change_tests_passed = $false
        tickets = @()
        failure = Limit-Text ((Get-Content -LiteralPath $logPath -Tail 12 -ErrorAction SilentlyContinue) -join ' ') 1200
        summary_truncated = $true
    }
}
if ($receipt.execution_status -ne 'success') { $receipt.status = 'failed' }
Write-JsonAtomic -Path $receiptPath -Value $receipt

if ($ResultPath) {
    $resolvedResult = [System.IO.Path]::GetFullPath((Join-Path $repo $ResultPath))
    Repo-Relative $resolvedResult | Out-Null
    Write-JsonAtomic -Path $resolvedResult -Value $receipt
}

Write-Host ""
if ($code -eq 0) {
    Write-Host "External worker finished. Receipt: $receiptPath" -ForegroundColor Green
} else {
    Write-Host "External worker failed with code $code. Receipt: $receiptPath" -ForegroundColor Yellow
}

exit $code
