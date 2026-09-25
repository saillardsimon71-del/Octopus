param(
    [Parameter(Mandatory = $true)]
    [string]$Plan,
    [double]$Hours = 1.0
)

$ErrorActionPreference = "Stop"

$repo = (git rev-parse --show-toplevel 2>$null)
if (-not $repo) { throw "Run this helper from inside the OCTOPUS repository." }
Set-Location $repo

$planPath = [System.IO.Path]::GetFullPath((Join-Path $repo $Plan))
if (-not (Test-Path -LiteralPath $planPath -PathType Leaf)) {
    throw "Ticket plan not found: $planPath"
}

$repoRoot = [System.IO.Path]::GetFullPath($repo).TrimEnd('\') + '\'
if (-not $planPath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Ticket plan must live under the OCTOPUS repository (normally cache\astra-tickets\...)."
}

if ($Hours -le 0 -or $Hours -gt 8) {
    throw "Hours must be > 0 and <= 8."
}

$status = git status --porcelain
if (-not [string]::IsNullOrWhiteSpace(($status -join ""))) {
    throw "OCTOPUS source repository is not clean. Commit/stash/revert before starting the external worker."
}

Write-Host "Starting one bounded OCTOPUS development ticket outside the Codex model loop." -ForegroundColor Cyan
Write-Host "Plan: $planPath"
Write-Host "Kill switch from another terminal: python -m octopus night-stop"
Write-Host ""

python -m octopus night-shift --repo . --plan $planPath --max-tasks 1 --hours $Hours
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
    Write-Host "External worker finished. Return to the Codex/Astra conversation and say: WORKER_FINISHED." -ForegroundColor Green
    Write-Host "Do not merge/cherry-pick manually; let Astra inspect the report and diff first."
} else {
    Write-Host "External worker exited with code $code. Return to Astra with the complete terminal output." -ForegroundColor Yellow
}
exit $code
