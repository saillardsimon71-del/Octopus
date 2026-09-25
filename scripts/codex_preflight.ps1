param(
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"
$failures = [System.Collections.Generic.List[string]]::new()

function Ok([string]$Message) { Write-Host ("[OK]  " + $Message) -ForegroundColor Green }
function Fail([string]$Message) {
    Write-Host ("[KO]  " + $Message) -ForegroundColor Red
    $failures.Add($Message)
}
function Info([string]$Message) { Write-Host ("[..]  " + $Message) }

function Command-Exists([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

$repo = (git rev-parse --show-toplevel 2>$null)
if (-not $repo) {
    throw "Run this script from inside the OCTOPUS Git repository."
}
Set-Location $repo
Ok "repository: $repo"

$expectedBranch = "prep/astra-local-orchestration"
$branch = (git branch --show-current).Trim()
if ($branch -eq $expectedBranch) { Ok "branch: $branch" } else { Fail "expected branch $expectedBranch, got $branch" }

$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace(($status -join ""))) { Ok "working tree clean" }
else { Fail "working tree is not clean; commit/stash/revert before Codex" }

if (-not $SkipFetch) {
    Info "fetching origin/main only"
    git fetch --quiet origin main
    if ($LASTEXITCODE -ne 0) { Fail "git fetch origin main failed" }
}

$mainRef = if (git show-ref --verify --quiet refs/remotes/origin/main) { "origin/main" } else { "main" }
git merge-base --is-ancestor $mainRef HEAD 2>$null
if ($LASTEXITCODE -eq 0) { Ok "$mainRef is an ancestor of HEAD" }
else { Fail "prepared branch is behind/diverged from $mainRef; reconcile before Codex" }

$configPath = Join-Path $repo ".codex\config.toml"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    Fail ".codex/config.toml missing"
} else {
    $raw = [System.IO.File]::ReadAllText($configPath)
    $required = @(
        'model = "gpt-6-astra"',
        'model_reasoning_effort = "high"',
        'approval_policy = "on-request"',
        'approvals_reviewer = "user"',
        'sandbox_mode = "workspace-write"',
        'web_search = "disabled"',
        'enabled = false',
        'multi_agent = false',
        'goals = false',
        'memories = false',
        'fast_mode = false',
        'skill_mcp_dependency_install = false',
        'use_memories = false'
    )
    $missing = @($required | Where-Object { -not $raw.Contains($_) })
    if ($missing.Count -eq 0) { Ok "Codex project policy contains required quota/safety locks" }
    else { Fail ("Codex project policy missing: " + ($missing -join ", ")) }
}

if (Command-Exists "codex") {
    $versionText = (& codex --version 2>&1 | Out-String).Trim()
    if ($versionText -match '(\d+)\.(\d+)\.(\d+)') {
        $version = [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
        $minimum = [version]::new(0,153,0)
        if ($version -ge $minimum) { Ok "Codex $version (Astra-compatible >= 0.153.0)" }
        else { Fail "Codex $version is too old for Astra; require >= 0.153.0" }
    } else {
        Fail "could not parse Codex version: $versionText"
    }
} else {
    Fail "codex CLI not found in PATH"
}

if (Command-Exists "kilo") {
    $kiloVersion = (& kilo --version 2>&1 | Out-String).Trim()
    Ok ("Kilo CLI present" + $(if ($kiloVersion) { ": $kiloVersion" } else { "" }))
} elseif (Command-Exists "kilo.cmd") {
    $kiloVersion = (& kilo.cmd --version 2>&1 | Out-String).Trim()
    Ok ("Kilo CLI present" + $(if ($kiloVersion) { ": $kiloVersion" } else { "" }))
} else {
    Fail "Kilo CLI not found; bounded free worker cannot run"
}

if (Command-Exists "python") {
    $pythonVersion = (& python --version 2>&1 | Out-String).Trim()
    Ok $pythonVersion
} else {
    Fail "python not found in PATH"
}

if (Command-Exists "docker") {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { Ok "Docker daemon reachable (required by product_ticket sandbox)" }
    else { Fail "Docker CLI exists but daemon is not reachable" }
} else {
    Fail "Docker not found; product_ticket Docker sandbox cannot run"
}

$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$residualPaths = @(
    (Join-Path $HOME ".agents\skills\astra-flash-orchestrator"),
    (Join-Path $codexHome "agents\astra_flash_builder.toml")
) | Where-Object { Test-Path -LiteralPath $_ }
if ($residualPaths.Count -gt 0) {
    Fail ("active Astra Flash Orchestrator residue: " + ($residualPaths -join ", "))
}

$markers = @(
    (Join-Path $codexHome "AGENTS.md"),
    (Join-Path $codexHome "AGENTS.override.md")
)
$policyHit = $false
foreach ($path in $markers) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        if (Select-String -LiteralPath $path -SimpleMatch "astra-flash-orchestrator managed policy" -Quiet) {
            $policyHit = $true
            Fail "Astra Flash managed policy still present in $path"
        }
    }
}
if (($residualPaths.Count -eq 0) -and -not $policyHit) {
    Ok "no active Astra Flash Orchestrator residue detected"
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host ("NOT READY — " + $failures.Count + " blocking issue(s).") -ForegroundColor Red
    $failures | ForEach-Object { Write-Host (" - " + $_) }
    exit 2
}

Write-Host "READY FOR CODEX / GPT-6 ASTRA" -ForegroundColor Green
Write-Host "Launch from this repository root with: codex"
Write-Host "Then run /status before the first task and use a normal prompt, not /goal."
