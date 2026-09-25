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

if ($Hours -le 0 -or $Hours -gt 8) { throw "Hours must be > 0 and <= 8." }

$status = (git status --porcelain | Out-String).Trim()
if ($status) { throw "OCTOPUS source repository is not clean. Commit/stash/revert before starting the external worker." }

$stopFile = Join-Path $repo "data\NIGHT_SHIFT_STOP"
if (Test-Path -LiteralPath $stopFile) {
    throw "Night-shift kill switch is active at $stopFile. Clear it explicitly with: python -m octopus night-resume"
}

$planHash = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant()
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
    started_at_utc = $started.ToString("o")
    finished_at_utc = $null
    exit_code = $null
    log_path = (Repo-Relative $logPath)
    night_report_path = $null
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
