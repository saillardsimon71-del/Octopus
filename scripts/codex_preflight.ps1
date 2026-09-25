param(
    [switch]$SkipFetch,
    [string]$ExpectedCodexHome = ""
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

function Get-TomlValue([string]$Path, [string]$Section, [string]$Key) {
    $current = ""
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = ($rawLine -replace '\s+#.*$', '').Trim()
        if (-not $line) { continue }
        if ($line -match '^\[(.+)\]$') {
            $current = $Matches[1].Trim()
            continue
        }
        if ($current -ne $Section) { continue }
        if ($line -match ('^' + [regex]::Escape($Key) + '\s*=\s*(.+)$')) {
            return $Matches[1].Trim()
        }
    }
    return $null
}

function Require-TomlBool([string]$Path, [string]$Section, [string]$Key, [bool]$Expected) {
    $raw = Get-TomlValue -Path $Path -Section $Section -Key $Key
    $wanted = if ($Expected) { "true" } else { "false" }
    if ($null -eq $raw) {
        Fail "missing TOML key [$Section] $Key"
        return
    }
    if ($raw.ToLowerInvariant() -eq $wanted) {
        Ok "[$Section] $Key=$wanted"
    } else {
        Fail "[$Section] $Key expected $wanted, got $raw"
    }
}

# --------------------------------------------------------------------------------------
# Repository and dedicated Codex home
# --------------------------------------------------------------------------------------

$repo = (git rev-parse --show-toplevel 2>$null | Out-String).Trim()
if (-not $repo) { throw "Run this script from inside the OCTOPUS repository." }
$repo = [System.IO.Path]::GetFullPath($repo).TrimEnd("\")
Set-Location $repo
Ok "repository: $repo"

$expectedBranch = "prep/astra-local-orchestration"
$branch = (git branch --show-current | Out-String).Trim()
if ($branch -eq $expectedBranch) { Ok "branch: $branch" } else { Fail "expected branch $expectedBranch, got $branch" }

$status = (git status --porcelain | Out-String).Trim()
if ($status) { Fail "working tree is not clean" } else { Ok "working tree clean" }

if (-not $SkipFetch) {
    Info "fetching origin/main"
    git fetch --quiet origin main
    if ($LASTEXITCODE -ne 0) { Fail "git fetch origin main failed" }
}

git show-ref --verify --quiet refs/remotes/origin/main *> $null
$mainRef = if ($LASTEXITCODE -eq 0) { "origin/main" } else { "main" }
git merge-base --is-ancestor $mainRef HEAD 2>$null
if ($LASTEXITCODE -eq 0) { Ok "$mainRef is an ancestor of HEAD" } else { Fail "prepared branch is behind/diverged from $mainRef" }

if (-not $ExpectedCodexHome) { $ExpectedCodexHome = Join-Path $HOME ".codex-octopus" }
$ExpectedCodexHome = [System.IO.Path]::GetFullPath($ExpectedCodexHome).TrimEnd("\")

if (-not $env:CODEX_HOME) {
    Fail "CODEX_HOME is not set"
    $codexHome = $ExpectedCodexHome
} else {
    $codexHome = [System.IO.Path]::GetFullPath($env:CODEX_HOME).TrimEnd("\")
    if ($codexHome -eq $ExpectedCodexHome) {
        Ok "dedicated CODEX_HOME: $codexHome"
    } else {
        Fail "CODEX_HOME must be dedicated OCTOPUS home $ExpectedCodexHome, got $codexHome"
    }
}

$defaultHome = [System.IO.Path]::GetFullPath((Join-Path $HOME ".codex")).TrimEnd("\")
if ($codexHome -eq $defaultHome) { Fail "default ~/.codex is forbidden for the OCTOPUS constructor session" }

$userConfig = Join-Path $codexHome "config.toml"
if (-not (Test-Path -LiteralPath $userConfig -PathType Leaf)) {
    Fail "dedicated CODEX_HOME config.toml missing; run setup_octopus_codex_home.ps1"
} else {
    $userRaw = [System.IO.File]::ReadAllText($userConfig)
    if ($userRaw.StartsWith("# Managed by OCTOPUS setup_octopus_codex_home.ps1")) {
        Ok "dedicated CODEX_HOME config is OCTOPUS-managed"
    } else {
        Fail "dedicated CODEX_HOME config.toml is not OCTOPUS-managed"
    }

    if ($userRaw -match '(?m)^\s*cli_auth_credentials_store\s*=\s*"file"\s*$') {
        Ok "dedicated auth store is file-scoped to CODEX_HOME"
    } else {
        Fail "dedicated CODEX_HOME must use cli_auth_credentials_store=\"file\""
    }

    if ($userRaw -match '(?m)^\s*trust_level\s*=\s*"trusted"\s*$') {
        Ok "dedicated home contains trusted-project decision"
    } else {
        Fail "OCTOPUS project is not marked trusted in dedicated CODEX_HOME"
    }

    $forbiddenUserPatterns = @(
        '(?m)^\s*model_provider\s*=',
        '(?m)^\s*openai_base_url\s*=',
        '(?m)^\s*chatgpt_base_url\s*=',
        '(?m)^\s*profile\s*=',
        '(?m)^\s*\[model_providers\.',
        '(?m)^\s*\[profiles\.',
        '(?m)^\s*\[mcp_servers\.'
    )
    foreach ($pattern in $forbiddenUserPatterns) {
        if ($userRaw -match $pattern) { Fail "unexpected provider/profile/MCP routing in dedicated CODEX_HOME config" }
    }
}

foreach ($name in @("AGENTS.md", "AGENTS.override.md")) {
    $path = Join-Path $codexHome $name
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        $body = [System.IO.File]::ReadAllText($path).Trim()
        if ($body) { Fail "unexpected global Codex instructions in dedicated home: $path" }
    }
}

foreach ($dirName in @("rules", "skills", "plugins")) {
    $path = Join-Path $codexHome $dirName
    if (Test-Path -LiteralPath $path -PathType Container) {
        $items = @(Get-ChildItem -LiteralPath $path -Force -ErrorAction SilentlyContinue)
        if ($items.Count -gt 0) { Fail "unexpected $dirName content in dedicated CODEX_HOME: $path" }
    }
}

# --------------------------------------------------------------------------------------
# Project Codex config: structural checks
# --------------------------------------------------------------------------------------

$projectConfig = Join-Path $repo ".codex\config.toml"
if (-not (Test-Path -LiteralPath $projectConfig -PathType Leaf)) {
    Fail ".codex/config.toml missing"
} else {
    $modelRaw = Get-TomlValue -Path $projectConfig -Section "" -Key "model"
    if ($modelRaw -eq '"gpt-6-astra"') { Ok "root model pinned to gpt-6-astra" } else { Fail "root model is not exactly gpt-6-astra" }

    $reasonRaw = Get-TomlValue -Path $projectConfig -Section "" -Key "model_reasoning_effort"
    if ($reasonRaw -eq '"high"') { Ok "Astra reasoning effort high" } else { Fail "model_reasoning_effort must be high" }

    $webRaw = Get-TomlValue -Path $projectConfig -Section "" -Key "web_search"
    if ($webRaw -eq '"disabled"') { Ok "web search disabled" } else { Fail "web_search must be disabled" }

    Require-TomlBool -Path $projectConfig -Section "sandbox_workspace_write" -Key "network_access" -Expected $false
    Require-TomlBool -Path $projectConfig -Section "agents" -Key "enabled" -Expected $false
    Require-TomlBool -Path $projectConfig -Section "features.multi_agent_v2" -Key "enabled" -Expected $false
    Require-TomlBool -Path $projectConfig -Section "skills" -Key "include_instructions" -Expected $false
    Require-TomlBool -Path $projectConfig -Section "memories" -Key "use_memories" -Expected $false
    Require-TomlBool -Path $projectConfig -Section "memories" -Key "generate_memories" -Expected $false
}

# --------------------------------------------------------------------------------------
# Codex binary, effective features and authentication
# --------------------------------------------------------------------------------------

if (-not (Command-Exists "codex")) {
    Fail "codex CLI not found in PATH"
} else {
    $versionText = (& codex --version 2>&1 | Out-String).Trim()
    if ($versionText -match '(\d+)\.(\d+)\.(\d+)') {
        $version = [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
        if ($version -ge [version]::new(0, 157, 0)) {
            Ok "Codex $version (audited baseline >= 0.157.0)"
        } else {
            Fail "Codex $version is below audited baseline 0.157.0"
        }
    } else {
        Fail "could not parse Codex version: $versionText"
    }

    $login = (& codex login status 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $login -match '(?i)Logged in using ChatGPT') {
        Ok "Codex authenticated through ChatGPT plan"
    } else {
        Fail "Codex is not confirmed as ChatGPT-authenticated: $login"
    }

    foreach ($envName in @("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")) {
        $value = [Environment]::GetEnvironmentVariable($envName)
        if ($value) { Fail "$envName is set in the launcher environment; remove it to avoid provider ambiguity" }
    }

    $featureList = (& codex features list 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Fail "codex features list failed"
    } else {
        $off = @(
            "multi_agent",
            "multi_agent_v2",
            "goals",
            "memories",
            "fast_mode",
            "apps",
            "plugins",
            "remote_plugin",
            "plugin_sharing",
            "recommended_plugins",
            "hooks",
            "skill_search",
            "skill_mcp_dependency_install",
            "tool_suggest",
            "browser_use",
            "browser_use_full_cdp_access",
            "browser_use_external",
            "computer_use",
            "in_app_browser",
            "sleep_tool"
        )

        $featureLines = @($featureList -split '\r?\n')
        foreach ($name in $off) {
            $line = @($featureLines | Where-Object { $_ -match ("^" + [regex]::Escape($name) + "\s+") }) | Select-Object -First 1
            if (-not $line) {
                Fail "effective feature missing from codex features list: $name"
            } elseif ($line -match '\sfalse\s*$') {
                Ok "effective feature off: $name"
            } else {
                Fail "effective feature is not false: $($line.Trim())"
            }
        }

        $shellLine = @($featureLines | Where-Object { $_ -match '^shell_tool\s+' }) | Select-Object -First 1
        if ($shellLine -and $shellLine -match '\strue\s*$') {
            Ok "effective shell_tool remains enabled"
        } else {
            Fail "shell_tool must remain enabled for Astra repo work"
        }
    }
}

# --------------------------------------------------------------------------------------
# Execpolicy and worker/runtime prerequisites
# --------------------------------------------------------------------------------------

$rulesPath = Join-Path $repo ".codex\rules\no-model-worker.rules"
if (-not (Test-Path -LiteralPath $rulesPath -PathType Leaf)) {
    Fail ".codex/rules/no-model-worker.rules missing"
} elseif (Command-Exists "codex") {
    $checks = @(
        @("kilo", "run"),
        @("python", "-m", "octopus", "night-shift", "--repo", ".")
    )
    foreach ($command in $checks) {
        $result = (& codex execpolicy check --rules $rulesPath @command 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $result -match '"decision"\s*:\s*"forbidden"') {
            Ok "execpolicy forbids model-shell worker command: $($command -join ' ')"
        } else {
            Fail "execpolicy did not forbid: $($command -join ' ') :: $result"
        }
    }
}

$kiloCmd = if (Command-Exists "kilo") { "kilo" } elseif (Command-Exists "kilo.cmd") { "kilo.cmd" } else { $null }
if (-not $kiloCmd) {
    Fail "Kilo CLI not found"
} else {
    $expectedRoute = "kilo/stepfun/step-3.7-flash:free"
    $catalog = (& $kiloCmd models kilo 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0 -and @($catalog -split '\r?\n' | ForEach-Object { $_.Trim() }) -contains $expectedRoute) {
        Ok "exact free Step route available: $expectedRoute"
    } else {
        Fail "exact free Step route unavailable: $expectedRoute"
    }
}

if (Command-Exists "python") {
    $pythonText = (& python --version 2>&1 | Out-String).Trim()
    Ok $pythonText
} else {
    Fail "python not found in PATH"
}

if (Command-Exists "docker") {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { Ok "Docker daemon reachable" } else { Fail "Docker daemon not reachable" }
} else {
    Fail "Docker not found"
}

$stopFile = Join-Path $repo "data\NIGHT_SHIFT_STOP"
if (Test-Path -LiteralPath $stopFile) { Fail "night-shift kill switch is active: $stopFile" } else { Ok "night-shift kill switch clear" }

$astraFlashSkill = Join-Path $HOME ".agents\skills\astra-flash-orchestrator"
$astraFlashRole = Join-Path $codexHome "agents\astra_flash_builder.toml"
if ((Test-Path -LiteralPath $astraFlashSkill) -or (Test-Path -LiteralPath $astraFlashRole)) {
    Fail "active Astra Flash Orchestrator residue detected"
} else {
    Ok "no active Astra Flash Orchestrator residue"
}

# --------------------------------------------------------------------------------------
# Exact pinned upstreams are fetched before the offline Astra process starts
# --------------------------------------------------------------------------------------

$fetcher = Join-Path $repo "scripts\fetch_pinned_upstreams.ps1"
if (-not (Test-Path -LiteralPath $fetcher -PathType Leaf)) {
    Fail "scripts/fetch_pinned_upstreams.ps1 missing"
} elseif (-not $SkipFetch) {
    Info "fetching/verifying exact pinned Agnes and Hermes sources"
    & $fetcher -Target all
    if ($LASTEXITCODE -eq 0) { Ok "pinned upstream sources ready" } else { Fail "pinned upstream fetch/verification failed" }
} else {
    Ok "upstream network fetch skipped by explicit operator flag"
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host ("NOT READY — " + $failures.Count + " blocking issue(s).") -ForegroundColor Red
    $failures | ForEach-Object { Write-Host (" - " + $_) }
    exit 2
}

Write-Host "READY FOR GPT-6 ASTRA RELAY" -ForegroundColor Green
Write-Host "Constructor mode: codex exec, exact thread resume, bounded Astra turns, out-of-band Step."
exit 0
