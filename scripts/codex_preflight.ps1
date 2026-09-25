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

git show-ref --verify --quiet refs/remotes/origin/main *> $null
$mainRef = if ($LASTEXITCODE -eq 0) { "origin/main" } else { "main" }
git merge-base --is-ancestor $mainRef HEAD 2>$null
if ($LASTEXITCODE -eq 0) { Ok "$mainRef is an ancestor of HEAD" }
else { Fail "prepared branch is behind/diverged from $mainRef; reconcile before Codex" }

$configPath = Join-Path $repo ".codex\config.toml"
$rulesPath = Join-Path $repo ".codex\rules\no-model-worker.rules"
if (-not (Test-Path -LiteralPath $rulesPath -PathType Leaf)) {
    Fail ".codex/rules/no-model-worker.rules missing"
} else {
    $rulesRaw = [System.IO.File]::ReadAllText($rulesPath)
    $requiredWorkerBlocks = @(
        'pattern = ["kilo"]',
        'pattern = ["kilo.cmd"]',
        'pattern = ["python", "-m", "octopus", "night-shift"]',
        'decision = "forbidden"'
    )
    $missingBlocks = @($requiredWorkerBlocks | Where-Object { -not $rulesRaw.Contains($_) })
    if ($missingBlocks.Count -eq 0) {
        Ok "Codex worker-block execpolicy file present"
    } else {
        Fail ("worker execpolicy incomplete: " + ($missingBlocks -join ", "))
    }

    if (Command-Exists "codex") {
        $policyResult = (& codex execpolicy check --rules $rulesPath kilo run 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $policyResult -match '"decision"\s*:\s*"forbidden"') {
            Ok "Codex execpolicy parser confirms Kilo is forbidden in model shell"
        } else {
            Fail ("Codex execpolicy did not confirm forbidden Kilo launch: " + $policyResult)
        }
    }
}

$runnerPath = Join-Path $repo "scripts\run_external_dev_ticket.ps1"
if (Test-Path -LiteralPath $runnerPath -PathType Leaf) {
    Ok "external human-run worker helper present"
} else {
    Fail "scripts/run_external_dev_ticket.ps1 missing"
}

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
        'apps = false',
        'plugins = false',
        'remote_plugin = false',
        'hooks = false',
        'skill_search = false',
        'skill_mcp_dependency_install = false',
        'include_instructions = false',
        'use_memories = false',
        'generate_memories = false'
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

if (Command-Exists "codex") {
    $loginStatus = (& codex login status 2>&1 | Out-String).Trim()
    Info ("Codex auth: " + $loginStatus)
    if ($loginStatus -match '(?i)chatgpt') {
        Ok "Codex is using ChatGPT sign-in (plan allowance path)"
    } else {
        Fail "Codex is not confirmed as ChatGPT-authenticated; do not start Astra until 'codex login status' confirms ChatGPT sign-in"
    }
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

$kiloCmd = if (Command-Exists "kilo") { "kilo" } elseif (Command-Exists "kilo.cmd") { "kilo.cmd" } else { $null }
if ($kiloCmd) {
    $expectedRoute = "kilo/stepfun/step-3.7-flash:free"
    $catalog = (& $kiloCmd models kilo 2>&1 | Out-String)
    $routes = @($catalog -split "\r?\n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($routes -contains $expectedRoute) {
        Ok "exact free Kilo route available: $expectedRoute"
    } else {
        Fail "expected free Kilo route absent from live catalog: $expectedRoute"
    }
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

# Purity checks: global instructions are concatenated into project context, and
# user-level MCP servers can remain available independently of Apps/Plugins.
$globalInstructionFiles = @(
    (Join-Path $codexHome "AGENTS.md"),
    (Join-Path $codexHome "AGENTS.override.md")
)
foreach ($path in $globalInstructionFiles) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        $body = [System.IO.File]::ReadAllText($path).Trim()
        if ($body.Length -gt 0) {
            Fail "non-empty global Codex instructions detected at $path; review/remove them for the purified OCTOPUS session"
        }
    }
}

$globalConfig = Join-Path $codexHome "config.toml"
if (Test-Path -LiteralPath $globalConfig -PathType Leaf) {
    $mcpDefs = @(Select-String -LiteralPath $globalConfig -Pattern '^\s*\[mcp_servers\.' -AllMatches -ErrorAction SilentlyContinue)
    if ($mcpDefs.Count -gt 0) {
        Fail "user-level MCP server definitions detected in $globalConfig; explicitly disable/review them before the purified OCTOPUS session"
    } else {
        Ok "no user-level MCP server definitions detected"
    }
} else {
    Ok "no user-level Codex config.toml MCP definitions detected"
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host ("NOT READY — " + $failures.Count + " blocking issue(s).") -ForegroundColor Red
    $failures | ForEach-Object { Write-Host (" - " + $_) }
    exit 2
}

Write-Host "READY FOR CODEX / GPT-6 ASTRA" -ForegroundColor Green
Write-Host "Launch from this repository root with: codex"
Write-Host "If Codex asks whether to trust this project, approve the repository BEFORE sending the first prompt."
Write-Host "Then run /status before the first task and use a normal prompt, not /goal."
