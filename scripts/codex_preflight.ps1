param(
    [switch]$SkipFetch,
    [string]$ExpectedCodexHome = ''
)

$ErrorActionPreference = 'Stop'
$Failures = New-Object System.Collections.Generic.List[string]

function Add-Ok([string]$Message) {
    Write-Host ('[OK]  ' + $Message) -ForegroundColor Green
}

function Add-Fail([string]$Message) {
    Write-Host ('[KO]  ' + $Message) -ForegroundColor Red
    [void]$Failures.Add($Message)
}

function Has-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-NativeCapture([string]$FilePath, [string[]]$Arguments) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.Arguments = ($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    [pscustomobject]@{
        ExitCode = $proc.ExitCode
        Output = (($stdout + [Environment]::NewLine + $stderr).Trim())
    }
}

# Repo -------------------------------------------------------------------------------
$Repo = (git rev-parse --show-toplevel 2>$null | Out-String).Trim()
if (-not $Repo) { throw 'Run this script from inside OCTOPUS.' }
$Repo = [System.IO.Path]::GetFullPath($Repo).TrimEnd([char]92)
Set-Location $Repo
Add-Ok ('repository: ' + $Repo)

$ExpectedBranch = 'prep/astra-local-orchestration'
$Branch = (git branch --show-current | Out-String).Trim()
if ($Branch -eq $ExpectedBranch) {
    Add-Ok ('branch: ' + $Branch)
} else {
    Add-Fail ('expected branch ' + $ExpectedBranch + ', got ' + $Branch)
}

$Status = (git status --porcelain | Out-String).Trim()
if ($Status) { Add-Fail 'working tree is not clean' } else { Add-Ok 'working tree clean' }

if (-not $SkipFetch) {
    Write-Host '[..]  fetching origin/main'
    git fetch --quiet origin main
    if ($LASTEXITCODE -ne 0) { Add-Fail 'git fetch origin main failed' }
}

git show-ref --verify --quiet refs/remotes/origin/main *> $null
if ($LASTEXITCODE -eq 0) { $MainRef = 'origin/main' } else { $MainRef = 'main' }
git merge-base --is-ancestor $MainRef HEAD 2>$null
if ($LASTEXITCODE -eq 0) {
    Add-Ok ($MainRef + ' is an ancestor of HEAD')
} else {
    Add-Fail ('branch is behind/diverged from ' + $MainRef)
}

# Dedicated CODEX_HOME ----------------------------------------------------------------
if (-not $ExpectedCodexHome) { $ExpectedCodexHome = Join-Path $HOME '.codex-octopus' }
$ExpectedCodexHome = [System.IO.Path]::GetFullPath($ExpectedCodexHome).TrimEnd([char]92)

if (-not $env:CODEX_HOME) {
    Add-Fail 'CODEX_HOME is not set'
    $CodexHome = $ExpectedCodexHome
} else {
    $CodexHome = [System.IO.Path]::GetFullPath($env:CODEX_HOME).TrimEnd([char]92)
    if ($CodexHome -eq $ExpectedCodexHome) {
        Add-Ok ('dedicated CODEX_HOME: ' + $CodexHome)
    } else {
        Add-Fail ('wrong CODEX_HOME: ' + $CodexHome)
    }
}

$DefaultHome = [System.IO.Path]::GetFullPath((Join-Path $HOME '.codex')).TrimEnd([char]92)
if ($CodexHome -eq $DefaultHome) { Add-Fail 'default ~/.codex is forbidden for this constructor session' }

$UserConfig = Join-Path $CodexHome 'config.toml'
if (-not (Test-Path -LiteralPath $UserConfig -PathType Leaf)) {
    Add-Fail 'dedicated CODEX_HOME config.toml missing'
} else {
    $UserRaw = [System.IO.File]::ReadAllText($UserConfig)
    if ($UserRaw.StartsWith('# Managed by OCTOPUS setup_octopus_codex_home.ps1')) {
        Add-Ok 'dedicated CODEX_HOME config is OCTOPUS-managed'
    } else {
        Add-Fail 'dedicated CODEX_HOME config is unmanaged'
    }
    if ($UserRaw -match 'cli_auth_credentials_store\s*=\s*"file"') {
        Add-Ok 'auth store is file-scoped'
    } else {
        Add-Fail 'auth store is not file-scoped'
    }
    if ($UserRaw -match 'trust_level\s*=\s*"trusted"') {
        Add-Ok 'project is trusted in dedicated home'
    } else {
        Add-Fail 'project trust entry missing'
    }
    $Forbidden = @('model_provider', 'openai_base_url', 'chatgpt_base_url', '[model_providers.', '[mcp_servers.')
    foreach ($Needle in $Forbidden) {
        if ($UserRaw.Contains($Needle)) { Add-Fail ('unexpected routing/MCP config: ' + $Needle) }
    }
}

foreach ($Name in @('AGENTS.md', 'AGENTS.override.md')) {
    $Path = Join-Path $CodexHome $Name
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        if ([System.IO.File]::ReadAllText($Path).Trim()) { Add-Fail ('unexpected global instructions: ' + $Path) }
    }
}

foreach ($DirName in @('rules', 'skills', 'plugins')) {
    $Path = Join-Path $CodexHome $DirName
    if (Test-Path -LiteralPath $Path -PathType Container) {
        $Items = @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue)
        if ($Items.Count -gt 0) { Add-Fail ('unexpected CODEX_HOME content: ' + $Path) }
    }
}

$ProjectConfig = Join-Path $Repo '.codex\config.toml'
if (Test-Path -LiteralPath $ProjectConfig -PathType Leaf) {
    $ProjectRaw = [System.IO.File]::ReadAllText($ProjectConfig)
    if ($ProjectRaw -match '(?ms)\[skills\.bundled\]\s*enabled\s*=\s*false') {
        Add-Ok 'bundled skills disabled in project config'
    } else {
        Add-Fail 'bundled skills are not explicitly disabled'
    }
    if ($ProjectRaw -match '(?ms)\[cloud\.skills\]\s*enabled\s*=\s*false') {
        Add-Ok 'cloud skills disabled in project config'
    } else {
        Add-Fail 'cloud skills are not explicitly disabled'
    }
}

$ProjectConfig = Join-Path $Repo '.codex\config.toml'
if (Test-Path -LiteralPath $ProjectConfig -PathType Leaf) {
    $ProjectRaw = [System.IO.File]::ReadAllText($ProjectConfig)
    if ($ProjectRaw -match '(?m)^\s*windows\.sandbox\s*=\s*"unelevated"\s*
if (-not (Has-Command 'codex')) {
    Add-Fail 'codex CLI not found'
} else {
    $VersionText = (& codex --version 2>&1 | Out-String).Trim()
    if ($VersionText -match '(\d+)\.(\d+)\.(\d+)') {
        $Version = New-Object System.Version([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
        $Minimum = New-Object System.Version(0, 157, 0)
        if ($Version -ge $Minimum) { Add-Ok ('Codex ' + $Version.ToString()) } else { Add-Fail ('Codex too old: ' + $Version.ToString()) }
    } else {
        Add-Fail ('cannot parse Codex version: ' + $VersionText)
    }

    $CodexExe = (Get-Command codex -ErrorAction Stop).Source
    $LoginResult = Invoke-NativeCapture -FilePath $CodexExe -Arguments @('login', 'status')
    $Login = $LoginResult.Output
    if (($LoginResult.ExitCode -eq 0) -and ($Login -match 'Logged in using ChatGPT')) {
        Add-Ok 'Codex authenticated through ChatGPT'
    } else {
        Add-Fail ('Codex ChatGPT auth not confirmed: ' + $Login)
    }

    foreach ($EnvName in @('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN')) {
        if ([Environment]::GetEnvironmentVariable($EnvName)) { Add-Fail ($EnvName + ' is set') }
    }

    $FeatureText = (& codex features list 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Add-Fail 'codex features list failed'
    } else {
        $Off = @(
            'multi_agent', 'multi_agent_v2', 'goals', 'memories', 'fast_mode',
            'apps', 'plugins', 'remote_plugin', 'plugin_sharing', 'recommended_plugins',
            'hooks', 'skill_search', 'skill_mcp_dependency_install', 'tool_suggest',
            'browser_use', 'browser_use_full_cdp_access', 'browser_use_external',
            'computer_use', 'in_app_browser', 'sleep_tool'
        )
        $FeatureLines = @($FeatureText -split "`r?`n")
        foreach ($Feature in $Off) {
            $Line = $FeatureLines | Where-Object { $_ -match ('^' + [regex]::Escape($Feature) + '\s+') } | Select-Object -First 1
            if (-not $Line) {
                Add-Fail ('effective feature missing: ' + $Feature)
            } elseif ($Line -match '\sfalse\s*$') {
                Add-Ok ('feature off: ' + $Feature)
            } else {
                Add-Fail ('feature not false: ' + $Line.Trim())
            }
        }
        $ShellLine = $FeatureLines | Where-Object { $_ -match '^shell_tool\s+' } | Select-Object -First 1
        if ($ShellLine -and ($ShellLine -match '\strue\s*$')) { Add-Ok 'shell_tool enabled' } else { Add-Fail 'shell_tool not enabled' }
    }
}

# Worker/runtime ---------------------------------------------------------------------
$Kilo = $null
if (Has-Command 'kilo') { $Kilo = 'kilo' } elseif (Has-Command 'kilo.cmd') { $Kilo = 'kilo.cmd' }
if (-not $Kilo) {
    Add-Fail 'Kilo CLI not found'
} else {
    $KiloVersion = (& $Kilo --version 2>&1 | Out-String).Trim()
    Add-Ok ('Kilo CLI present: ' + $KiloVersion)

    # Do NOT query the remote Kilo model catalog during startup. That command
    # can block on network/provider availability and Astra does not need Kilo
    # until it actually delegates a bounded ticket.
    $ExpectedRoute = 'kilo/stepfun/step-3.7-flash:free'
    $DevWorkerPath = Join-Path $Repo 'octopus\dev_worker.py'
    if (-not (Test-Path -LiteralPath $DevWorkerPath -PathType Leaf)) {
        Add-Fail 'octopus/dev_worker.py missing'
    } else {
        $DevWorkerText = [System.IO.File]::ReadAllText($DevWorkerPath)
        if ($DevWorkerText.Contains($ExpectedRoute)) {
            Add-Ok ('configured Step route: ' + $ExpectedRoute)
        } else {
            Add-Fail ('expected Step route not configured in dev_worker.py: ' + $ExpectedRoute)
        }
    }
}

if (Has-Command 'python') { Add-Ok ((& python --version 2>&1 | Out-String).Trim()) } else { Add-Fail 'python not found' }

if (Has-Command 'docker') {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { Add-Ok 'Docker daemon reachable' } else { Add-Fail 'Docker daemon not reachable' }
} else {
    Add-Fail 'Docker not found'
}

$StopFile = Join-Path $Repo 'data\NIGHT_SHIFT_STOP'
if (Test-Path -LiteralPath $StopFile) { Add-Fail 'night-shift kill switch is active' } else { Add-Ok 'night-shift kill switch clear' }

$OldSkill = Join-Path $HOME '.agents\skills\astra-flash-orchestrator'
if (Test-Path -LiteralPath $OldSkill) { Add-Fail 'old Astra Flash skill still present' } else { Add-Ok 'old Astra Flash skill absent' }

# Upstreams --------------------------------------------------------------------------
$Fetcher = Join-Path $Repo 'scripts\fetch_pinned_upstreams.ps1'
if (-not (Test-Path -LiteralPath $Fetcher -PathType Leaf)) {
    Add-Fail 'pinned upstream fetcher missing'
} elseif (-not $SkipFetch) {
    Write-Host '[..]  fetching/verifying pinned Hermes + Agnes'
    & $Fetcher -Target all
    if ($LASTEXITCODE -eq 0) { Add-Ok 'pinned upstreams ready' } else { Add-Fail 'pinned upstream fetch failed' }
} else {
    Add-Ok 'upstream fetch skipped by explicit flag'
}

# Verdict ---------------------------------------------------------------------------
Write-Host ''
if ($Failures.Count -gt 0) {
    Write-Host ('NOT READY - ' + $Failures.Count + ' blocking issue(s).') -ForegroundColor Red
    foreach ($Failure in $Failures) { Write-Host (' - ' + $Failure) }
    exit 2
}

Write-Host 'READY FOR GPT-6 ASTRA RELAY' -ForegroundColor Green
Write-Host 'Constructor mode: codex exec + exact thread resume + out-of-band Step.'
exit 0
) {
        Add-Ok 'Windows sandbox configured: unelevated'
    } else {
        Add-Fail 'windows.sandbox must be unelevated for non-interactive Codex exec on Windows'
    }
}

# Codex CLI --------------------------------------------------------------------------
if (-not (Has-Command 'codex')) {
    Add-Fail 'codex CLI not found'
} else {
    $VersionText = (& codex --version 2>&1 | Out-String).Trim()
    if ($VersionText -match '(\d+)\.(\d+)\.(\d+)') {
        $Version = New-Object System.Version([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
        $Minimum = New-Object System.Version(0, 157, 0)
        if ($Version -ge $Minimum) { Add-Ok ('Codex ' + $Version.ToString()) } else { Add-Fail ('Codex too old: ' + $Version.ToString()) }
    } else {
        Add-Fail ('cannot parse Codex version: ' + $VersionText)
    }

    $CodexExe = (Get-Command codex -ErrorAction Stop).Source
    $LoginResult = Invoke-NativeCapture -FilePath $CodexExe -Arguments @('login', 'status')
    $Login = $LoginResult.Output
    if (($LoginResult.ExitCode -eq 0) -and ($Login -match 'Logged in using ChatGPT')) {
        Add-Ok 'Codex authenticated through ChatGPT'
    } else {
        Add-Fail ('Codex ChatGPT auth not confirmed: ' + $Login)
    }

    foreach ($EnvName in @('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN')) {
        if ([Environment]::GetEnvironmentVariable($EnvName)) { Add-Fail ($EnvName + ' is set') }
    }

    $FeatureText = (& codex features list 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Add-Fail 'codex features list failed'
    } else {
        $Off = @(
            'multi_agent', 'multi_agent_v2', 'goals', 'memories', 'fast_mode',
            'apps', 'plugins', 'remote_plugin', 'plugin_sharing', 'recommended_plugins',
            'hooks', 'skill_search', 'skill_mcp_dependency_install', 'tool_suggest',
            'browser_use', 'browser_use_full_cdp_access', 'browser_use_external',
            'computer_use', 'in_app_browser', 'sleep_tool'
        )
        $FeatureLines = @($FeatureText -split "`r?`n")
        foreach ($Feature in $Off) {
            $Line = $FeatureLines | Where-Object { $_ -match ('^' + [regex]::Escape($Feature) + '\s+') } | Select-Object -First 1
            if (-not $Line) {
                Add-Fail ('effective feature missing: ' + $Feature)
            } elseif ($Line -match '\sfalse\s*$') {
                Add-Ok ('feature off: ' + $Feature)
            } else {
                Add-Fail ('feature not false: ' + $Line.Trim())
            }
        }
        $ShellLine = $FeatureLines | Where-Object { $_ -match '^shell_tool\s+' } | Select-Object -First 1
        if ($ShellLine -and ($ShellLine -match '\strue\s*$')) { Add-Ok 'shell_tool enabled' } else { Add-Fail 'shell_tool not enabled' }
    }
}

# Worker/runtime ---------------------------------------------------------------------
$Kilo = $null
if (Has-Command 'kilo') { $Kilo = 'kilo' } elseif (Has-Command 'kilo.cmd') { $Kilo = 'kilo.cmd' }
if (-not $Kilo) {
    Add-Fail 'Kilo CLI not found'
} else {
    $KiloVersion = (& $Kilo --version 2>&1 | Out-String).Trim()
    Add-Ok ('Kilo CLI present: ' + $KiloVersion)

    # Do NOT query the remote Kilo model catalog during startup. That command
    # can block on network/provider availability and Astra does not need Kilo
    # until it actually delegates a bounded ticket.
    $ExpectedRoute = 'kilo/stepfun/step-3.7-flash:free'
    $DevWorkerPath = Join-Path $Repo 'octopus\dev_worker.py'
    if (-not (Test-Path -LiteralPath $DevWorkerPath -PathType Leaf)) {
        Add-Fail 'octopus/dev_worker.py missing'
    } else {
        $DevWorkerText = [System.IO.File]::ReadAllText($DevWorkerPath)
        if ($DevWorkerText.Contains($ExpectedRoute)) {
            Add-Ok ('configured Step route: ' + $ExpectedRoute)
        } else {
            Add-Fail ('expected Step route not configured in dev_worker.py: ' + $ExpectedRoute)
        }
    }
}

if (Has-Command 'python') { Add-Ok ((& python --version 2>&1 | Out-String).Trim()) } else { Add-Fail 'python not found' }

if (Has-Command 'docker') {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { Add-Ok 'Docker daemon reachable' } else { Add-Fail 'Docker daemon not reachable' }
} else {
    Add-Fail 'Docker not found'
}

$StopFile = Join-Path $Repo 'data\NIGHT_SHIFT_STOP'
if (Test-Path -LiteralPath $StopFile) { Add-Fail 'night-shift kill switch is active' } else { Add-Ok 'night-shift kill switch clear' }

$OldSkill = Join-Path $HOME '.agents\skills\astra-flash-orchestrator'
if (Test-Path -LiteralPath $OldSkill) { Add-Fail 'old Astra Flash skill still present' } else { Add-Ok 'old Astra Flash skill absent' }

# Upstreams --------------------------------------------------------------------------
$Fetcher = Join-Path $Repo 'scripts\fetch_pinned_upstreams.ps1'
if (-not (Test-Path -LiteralPath $Fetcher -PathType Leaf)) {
    Add-Fail 'pinned upstream fetcher missing'
} elseif (-not $SkipFetch) {
    Write-Host '[..]  fetching/verifying pinned Hermes + Agnes'
    & $Fetcher -Target all
    if ($LASTEXITCODE -eq 0) { Add-Ok 'pinned upstreams ready' } else { Add-Fail 'pinned upstream fetch failed' }
} else {
    Add-Ok 'upstream fetch skipped by explicit flag'
}

# Verdict ---------------------------------------------------------------------------
Write-Host ''
if ($Failures.Count -gt 0) {
    Write-Host ('NOT READY - ' + $Failures.Count + ' blocking issue(s).') -ForegroundColor Red
    foreach ($Failure in $Failures) { Write-Host (' - ' + $Failure) }
    exit 2
}

Write-Host 'READY FOR GPT-6 ASTRA RELAY' -ForegroundColor Green
Write-Host 'Constructor mode: codex exec + exact thread resume + out-of-band Step.'
exit 0
