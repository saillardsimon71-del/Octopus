param(
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"
$failures = [System.Collections.Generic.List[string]]::new()

function Ok([string]$Message) {
    Write-Host ("[OK]  " + $Message) -ForegroundColor Green
}

function Fail([string]$Message) {
    Write-Host ("[KO]  " + $Message) -ForegroundColor Red
    $failures.Add($Message)
}

function Info([string]$Message) {
    Write-Host ("[..]  " + $Message)
}

function Command-Exists([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# --------------------------------------------------------------------------------------
# Repository identity and cleanliness
# --------------------------------------------------------------------------------------

$repo = (git rev-parse --show-toplevel 2>$null | Out-String).Trim()
if (-not $repo) {
    throw "Run this script from inside the OCTOPUS Git repository."
}
Set-Location $repo
Ok "repository: $repo"

$expectedBranch = "prep/astra-local-orchestration"
$branch = (git branch --show-current | Out-String).Trim()
if ($branch -eq $expectedBranch) {
    Ok "branch: $branch"
} else {
    Fail "expected branch $expectedBranch, got $branch"
}

$status = (git status --porcelain | Out-String).Trim()
if (-not $status) {
    Ok "working tree clean"
} else {
    Fail "working tree is not clean; commit/stash/revert before Codex"
}

if (-not $SkipFetch) {
    Info "fetching origin/main only"
    git fetch --quiet origin main
    if ($LASTEXITCODE -ne 0) {
        Fail "git fetch origin main failed"
    }
}

git show-ref --verify --quiet refs/remotes/origin/main *> $null
$mainRef = if ($LASTEXITCODE -eq 0) { "origin/main" } else { "main" }

git merge-base --is-ancestor $mainRef HEAD 2>$null
if ($LASTEXITCODE -eq 0) {
    Ok "$mainRef is an ancestor of HEAD"
} else {
    Fail "prepared branch is behind/diverged from $mainRef; reconcile before Codex"
}

# --------------------------------------------------------------------------------------
# Project-local Codex policy files
# --------------------------------------------------------------------------------------

$configPath = Join-Path $repo ".codex\config.toml"
$rulesPath = Join-Path $repo ".codex\rules\no-model-worker.rules"
$runnerPath = Join-Path $repo "scripts\run_external_dev_ticket.ps1"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    Fail ".codex/config.toml missing"
} else {
    $raw = [System.IO.File]::ReadAllText($configPath)
    $requiredConfigFragments = @(
        'model = "gpt-6-astra"',
        'model_reasoning_effort = "high"',
        'approval_policy = "on-request"',
        'approvals_reviewer = "user"',
        'sandbox_mode = "workspace-write"',
        'web_search = "disabled"',
        'network_access = false',
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
    $missing = @($requiredConfigFragments | Where-Object { -not $raw.Contains($_) })
    if ($missing.Count -eq 0) {
        Ok "project Codex config contains required model/quota/safety locks"
    } else {
        Fail ("project Codex config missing: " + ($missing -join ", "))
    }
}

if (-not (Test-Path -LiteralPath $rulesPath -PathType Leaf)) {
    Fail ".codex/rules/no-model-worker.rules missing"
} else {
    $rulesRaw = [System.IO.File]::ReadAllText($rulesPath)
    $requiredRuleFragments = @(
        'pattern = ["kilo"]',
        'pattern = ["kilo.cmd"]',
        'pattern = ["python", "-m", "octopus", "night-shift"]',
        'decision = "forbidden"'
    )
    $missingRules = @($requiredRuleFragments | Where-Object { -not $rulesRaw.Contains($_) })
    if ($missingRules.Count -eq 0) {
        Ok "worker-block execpolicy file contains required rules"
    } else {
        Fail ("worker execpolicy incomplete: " + ($missingRules -join ", "))
    }
}

if (Test-Path -LiteralPath $runnerPath -PathType Leaf) {
    Ok "external human-run worker helper present"
} else {
    Fail "scripts/run_external_dev_ticket.ps1 missing"
}

$upstreamFetcher = Join-Path $repo "scripts\fetch_pinned_upstreams.ps1"
if (-not (Test-Path -LiteralPath $upstreamFetcher -PathType Leaf)) {
    Fail "scripts/fetch_pinned_upstreams.ps1 missing"
} else {
    Info "fetching/verifying pinned Hermes and Agnes source outside the Codex model loop"
    & $upstreamFetcher -Target all
    if ($LASTEXITCODE -eq 0) {
        Ok "pinned upstream sources are present and clean"
    } else {
        Fail "pinned upstream fetch/verification failed"
    }
}

# --------------------------------------------------------------------------------------
# Codex CLI: version, effective project config, auth, execpolicy
# --------------------------------------------------------------------------------------

$hasCodex = Command-Exists "codex"
if (-not $hasCodex) {
    Fail "codex CLI not found in PATH"
} else {
    $versionText = (& codex --version 2>&1 | Out-String).Trim()
    if ($versionText -match '(\d+)\.(\d+)\.(\d+)') {
        $version = [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
        $minimum = [version]::new(0, 157, 0)
        if ($version -ge $minimum) {
            Ok "Codex $version (OCTOPUS-audited baseline >= 0.157.0)"
        } else {
            Fail "Codex $version is below the OCTOPUS-audited baseline; require >= 0.157.0 (Astra itself requires >= 0.153.0)"
        }
    } else {
        Fail "could not parse Codex version: $versionText"
    }

    # This uses the effective config stack. If the project is untrusted,
    # .codex/config.toml is skipped and several defaults below will be true.
    $featureList = (& codex features list 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Fail ("could not read effective Codex features: " + $featureList.Trim())
    } else {
        $mustBeOff = @(
            "apps",
            "fast_mode",
            "goals",
            "hooks",
            "memories",
            "multi_agent",
            "plugins",
            "remote_plugin",
            "skill_mcp_dependency_install",
            "skill_search"
        )
        $featureErrors = [System.Collections.Generic.List[string]]::new()
        $featureLines = @($featureList -split '\r?\n')

        foreach ($name in $mustBeOff) {
            $line = @($featureLines | Where-Object { $_ -match ("^" + [regex]::Escape($name) + "\s+") }) | Select-Object -First 1
            if (-not $line) {
                $featureErrors.Add("$name=missing")
            } elseif ($line -notmatch '\sfalse\s*$') {
                $featureErrors.Add("$name=not-false ($($line.Trim()))")
            }
        }

        if ($featureErrors.Count -eq 0) {
            Ok "effective Codex feature state confirms project isolation"
        } else {
            Fail ("effective Codex features do not match project policy: " + ($featureErrors -join "; ") + ". If the repo is not trusted yet: launch 'codex', approve the trust prompt WITHOUT sending a task, exit, then rerun this preflight.")
        }
    }

    $loginStatus = (& codex login status 2>&1 | Out-String).Trim()
    Info ("Codex auth: " + $loginStatus)
    if ($LASTEXITCODE -eq 0 -and $loginStatus -match '(?i)Logged in using ChatGPT') {
        Ok "Codex is using ChatGPT sign-in (plan allowance path)"
    } else {
        Fail "Codex is not confirmed as ChatGPT-authenticated; fix auth before Astra"
    }

    if (Test-Path -LiteralPath $rulesPath -PathType Leaf) {
        $policyKilo = (& codex execpolicy check --rules $rulesPath kilo run 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $policyKilo -match '"decision"\s*:\s*"forbidden"') {
            Ok "Codex execpolicy parser confirms direct Kilo launch is forbidden"
        } else {
            Fail ("Codex execpolicy did not forbid Kilo: " + $policyKilo)
        }

        $policyNight = (& codex execpolicy check --rules $rulesPath python -m octopus night-shift --repo . 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $policyNight -match '"decision"\s*:\s*"forbidden"') {
            Ok "Codex execpolicy parser confirms model-shell night-shift is forbidden"
        } else {
            Fail ("Codex execpolicy did not forbid night-shift: " + $policyNight)
        }
    }
}

# --------------------------------------------------------------------------------------
# Cheap worker route and deterministic sandbox prerequisites
# --------------------------------------------------------------------------------------

$kiloCmd = $null
if (Command-Exists "kilo") {
    $kiloCmd = "kilo"
} elseif (Command-Exists "kilo.cmd") {
    $kiloCmd = "kilo.cmd"
}

if ($null -eq $kiloCmd) {
    Fail "Kilo CLI not found; bounded free worker cannot run"
} else {
    $kiloVersion = (& $kiloCmd --version 2>&1 | Out-String).Trim()
    Ok ("Kilo CLI present" + $(if ($kiloVersion) { ": $kiloVersion" } else { "" }))

    $expectedRoute = "kilo/stepfun/step-3.7-flash:free"
    $catalog = (& $kiloCmd models kilo 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Fail "could not query live Kilo model catalog"
    } else {
        $routes = @($catalog -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($routes -contains $expectedRoute) {
            Ok "exact free Kilo route available: $expectedRoute"
        } else {
            Fail "expected free Kilo route absent from live catalog: $expectedRoute"
        }
    }
}

if (Command-Exists "python") {
    $pythonVersionText = (& python --version 2>&1 | Out-String).Trim()
    if ($pythonVersionText -match '(\d+)\.(\d+)\.(\d+)') {
        $pythonVersion = [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
        if ($pythonVersion -ge [version]::new(3, 10, 0)) {
            Ok $pythonVersionText
        } else {
            Fail "Python >= 3.10 required, got $pythonVersionText"
        }
    } else {
        Fail "could not parse Python version: $pythonVersionText"
    }
} else {
    Fail "python not found in PATH"
}

if (Command-Exists "docker") {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        Ok "Docker daemon reachable (required by product_ticket sandbox)"
    } else {
        Fail "Docker CLI exists but daemon is not reachable"
    }
} else {
    Fail "Docker not found; product_ticket Docker sandbox cannot run"
}

# --------------------------------------------------------------------------------------
# Purity: no old Astra Flash install, global instructions, MCP, or user exec rules
# --------------------------------------------------------------------------------------

$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }

$residualPaths = @(
    (Join-Path $HOME ".agents\skills\astra-flash-orchestrator"),
    (Join-Path $codexHome "agents\astra_flash_builder.toml")
) | Where-Object { Test-Path -LiteralPath $_ }

if ($residualPaths.Count -gt 0) {
    Fail ("active Astra Flash Orchestrator residue: " + ($residualPaths -join ", "))
}

$globalInstructionFiles = @(
    (Join-Path $codexHome "AGENTS.md"),
    (Join-Path $codexHome "AGENTS.override.md")
)

$policyHit = $false
foreach ($path in $globalInstructionFiles) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        continue
    }

    $body = [System.IO.File]::ReadAllText($path)
    if ($body.Contains("astra-flash-orchestrator managed policy")) {
        $policyHit = $true
        Fail "Astra Flash managed policy still present in $path"
    }
    if ($body.Trim().Length -gt 0) {
        Fail "non-empty global Codex instructions detected at $path; review/remove them for the purified OCTOPUS session"
    }
}

if (($residualPaths.Count -eq 0) -and -not $policyHit) {
    Ok "no active Astra Flash Orchestrator residue detected"
}

$globalConfig = Join-Path $codexHome "config.toml"
if (Test-Path -LiteralPath $globalConfig -PathType Leaf) {
    $globalRaw = [System.IO.File]::ReadAllText($globalConfig)

    $mcpDefs = @(Select-String -LiteralPath $globalConfig -Pattern '^\s*\[mcp_servers\.' -AllMatches -ErrorAction SilentlyContinue)
    if ($mcpDefs.Count -gt 0) {
        Fail "user-level MCP server definitions detected in $globalConfig; disable/review them before the purified OCTOPUS session"
    } else {
        Ok "no user-level MCP server definitions detected"
    }

    # Provider/profile routing is intentionally not trusted to project config:
    # Codex ignores project-scoped provider/profile keys. A user-level custom
    # provider could route gpt-6-astra away from the ChatGPT-plan backend.
    $routingPatterns = @(
        '(?m)^\s*model_provider\s*=',
        '(?m)^\s*openai_base_url\s*=',
        '(?m)^\s*chatgpt_base_url\s*=',
        '(?m)^\s*profile\s*=',
        '(?m)^\s*\[model_providers\.',
        '(?m)^\s*\[profiles\.'
    )
    $routingHits = @($routingPatterns | Where-Object { $globalRaw -match $_ })
    if ($routingHits.Count -gt 0) {
        Fail "user-level Codex provider/profile routing detected in $globalConfig; review/remove it so Astra is guaranteed to use the ChatGPT-plan route"
    } else {
        Ok "no user-level provider/profile routing overrides detected"
    }
} else {
    Ok "no user-level Codex config.toml MCP/provider overrides detected"
}

$userRulesDir = Join-Path $codexHome "rules"
if (Test-Path -LiteralPath $userRulesDir -PathType Container) {
    $userRules = @(Get-ChildItem -LiteralPath $userRulesDir -Filter "*.rules" -File -ErrorAction SilentlyContinue)
    if ($userRules.Count -gt 0) {
        Fail ("user-level Codex execpolicy rules detected: " + (($userRules | ForEach-Object { $_.FullName }) -join ", ") + ". Review/remove them for the purified OCTOPUS session.")
    } else {
        Ok "no user-level Codex execpolicy rules detected"
    }
} else {
    Ok "no user-level Codex execpolicy rules directory detected"
}

# --------------------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------------------

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host ("NOT READY — " + $failures.Count + " blocking issue(s).") -ForegroundColor Red
    $failures | ForEach-Object { Write-Host (" - " + $_) }
    exit 2
}

Write-Host "READY FOR CODEX / GPT-6 ASTRA" -ForegroundColor Green
Write-Host "Launch from this repository root with: codex"
Write-Host "Run /status before the first task."
Write-Host "Use a normal prompt: do NOT use /goal and do NOT use /review."
