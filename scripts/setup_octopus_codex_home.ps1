param(
    [string]$CodexHome = "",
    [switch]$SkipLogin
)

$ErrorActionPreference = "Stop"

$repo = (git rev-parse --show-toplevel 2>$null | Out-String).Trim()
if (-not $repo) { throw "Run this script from inside the OCTOPUS repository." }
$repo = [System.IO.Path]::GetFullPath($repo).TrimEnd("\")
Set-Location $repo

if (-not $CodexHome) { $CodexHome = Join-Path $HOME ".codex-octopus" }
$CodexHome = [System.IO.Path]::GetFullPath($CodexHome).TrimEnd("\")
if ($CodexHome -eq [System.IO.Path]::GetFullPath((Join-Path $HOME ".codex")).TrimEnd("\")) {
    throw "Refusing to reuse the default Codex home. Use the dedicated OCTOPUS home."
}

New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
$env:CODEX_HOME = $CodexHome

$escapedRepo = $repo.Replace("\", "\\")
$configPath = Join-Path $CodexHome "config.toml"
$managed = @(
    "# Managed by OCTOPUS setup_octopus_codex_home.ps1",
    "# Dedicated Codex home: auth/trust only. Project behavior lives in .codex/config.toml.",
    'cli_auth_credentials_store = "file"',
    "",
    ('[projects."{0}"]' -f $escapedRepo),
    'trust_level = "trusted"',
    ""
) -join [Environment]::NewLine

if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $existing = [System.IO.File]::ReadAllText($configPath)
    if (-not $existing.StartsWith("# Managed by OCTOPUS setup_octopus_codex_home.ps1")) {
        throw "Dedicated CODEX_HOME already contains an unmanaged config.toml: $configPath. Refusing to overwrite."
    }
}
[System.IO.File]::WriteAllText($configPath, $managed, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "[OK] CODEX_HOME: $CodexHome" -ForegroundColor Green
Write-Host "[OK] Project marked trusted in dedicated home: $repo" -ForegroundColor Green

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    $OfficialBin = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin'
    $OfficialExe = Join-Path $OfficialBin 'codex.exe'
    if (Test-Path -LiteralPath $OfficialExe -PathType Leaf) {
        $env:PATH = $OfficialBin + [System.IO.Path]::PathSeparator + $env:PATH
        Write-Host ('[OK] Added official Codex standalone bin to this process PATH: ' + $OfficialBin) -ForegroundColor Green
    } else {
        throw 'codex CLI not found. Install the official Windows standalone CLI first: powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"'
    }
}

if (-not $SkipLogin) {
    $status = (& codex login status 2>&1 | Out-String).Trim()
    if ($status -notmatch "(?i)Logged in using ChatGPT") {
        Write-Host "Dedicated OCTOPUS Codex home is not logged in with ChatGPT yet." -ForegroundColor Yellow
        Write-Host "Starting ChatGPT login now..."
        & codex login
        if ($LASTEXITCODE -ne 0) { throw "codex login failed." }
        $status = (& codex login status 2>&1 | Out-String).Trim()
    }
    if ($status -notmatch "(?i)Logged in using ChatGPT") {
        throw "Dedicated OCTOPUS Codex home is not confirmed as ChatGPT-authenticated."
    }
    Write-Host "[OK] $status" -ForegroundColor Green
}

Write-Host ""
Write-Host "Dedicated OCTOPUS Codex home is ready." -ForegroundColor Cyan
Write-Host "This script sets CODEX_HOME for its current PowerShell process only; the builder launcher sets it again automatically."
