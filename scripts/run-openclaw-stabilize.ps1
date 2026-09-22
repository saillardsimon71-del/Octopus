param(
    [string]$Repo = "C:\Users\saill\Projects\video-factory",
    [string]$Model = "mistral/mistral-medium-3-5",
    [ValidateSet("none","low","medium","high")]
    [string]$Thinking = "high",
    [int]$TimeoutSeconds = 3600
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git introuvable dans PATH" }
if (-not (Get-Command openclaw -ErrorAction SilentlyContinue)) { throw "openclaw introuvable. Installe-le puis relance ce script." }
if (-not $env:MISTRAL_API_KEY) { throw "MISTRAL_API_KEY n est pas defini dans cette session PowerShell." }
if (-not (Test-Path $Repo)) { throw "Repo introuvable: $Repo" }

$Brief = Join-Path $Repo "docs\OPENCLAW_STABILIZATION_BRIEF.md"
if (-not (Test-Path $Brief)) { throw "Brief introuvable: $Brief. Fais d abord git pull --ff-only." }

Write-Host "=== OCTOPUS / OpenClaw / Mistral ==="
Write-Host "Repo      : $Repo"
Write-Host "Model     : $Model"
Write-Host "Thinking  : $Thinking"
Write-Host "Timeout   : $TimeoutSeconds s"

Push-Location $Repo
try {
    git status --short --branch

    Write-Host ""
    Write-Host "Verification du provider Mistral..."
    openclaw models list --provider mistral

    Write-Host ""
    Write-Host "Lancement de l agent de stabilisation..."
    openclaw agent exec `
        --cwd $Repo `
        --model $Model `
        --thinking $Thinking `
        --timeout $TimeoutSeconds `
        --auth-env-only `
        --message-file $Brief `
        --json
}
finally {
    Pop-Location
}