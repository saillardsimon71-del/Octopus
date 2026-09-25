param(
    [string]$CodexHome = "",
    [switch]$SkipLogin
)

$ErrorActionPreference = "Stop"

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

function Resolve-CodexExecutable {
    $official = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin\codex.exe'
    if (Test-Path -LiteralPath $official -PathType Leaf) {
        return [System.IO.Path]::GetFullPath($official)
    }
    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw 'codex CLI not found. Install the official Windows standalone CLI first: powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"'
}

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

# This dedicated home is intentionally minimal. Codex may have materialized
# bundled/system skills during an earlier failed launch. They are generated
# content, not user data, and are disabled for OCTOPUS.
$generatedSkills = Join-Path $CodexHome "skills"
if (Test-Path -LiteralPath $generatedSkills -PathType Container) {
    Remove-Item -LiteralPath $generatedSkills -Recurse -Force
    Write-Host ("[OK] Removed generated bundled skills from dedicated home: " + $generatedSkills) -ForegroundColor Green
}

Write-Host "[OK] CODEX_HOME: $CodexHome" -ForegroundColor Green
Write-Host "[OK] Project marked trusted in dedicated home: $repo" -ForegroundColor Green

$codexExe = Resolve-CodexExecutable
Write-Host ('[OK] Codex executable: ' + $codexExe) -ForegroundColor Green

if (-not $SkipLogin) {
    $statusResult = Invoke-NativeCapture -FilePath $codexExe -Arguments @("login", "status")
    $status = $statusResult.Output

    if (($statusResult.ExitCode -ne 0) -or ($status -notmatch "(?i)Logged in using ChatGPT")) {
        Write-Host "Dedicated OCTOPUS Codex home is not logged in with ChatGPT yet." -ForegroundColor Yellow
        Write-Host "Starting ChatGPT login now..."
        & $codexExe login
        if ($LASTEXITCODE -ne 0) { throw "codex login failed." }

        $statusResult = Invoke-NativeCapture -FilePath $codexExe -Arguments @("login", "status")
        $status = $statusResult.Output
    }

    if (($statusResult.ExitCode -ne 0) -or ($status -notmatch "(?i)Logged in using ChatGPT")) {
        throw ("Dedicated OCTOPUS Codex home is not confirmed as ChatGPT-authenticated. Status: " + $status)
    }
    Write-Host ("[OK] " + $status) -ForegroundColor Green
}

Write-Host ""
Write-Host "Dedicated OCTOPUS Codex home is ready." -ForegroundColor Cyan
Write-Host "This script sets CODEX_HOME for its current PowerShell process only; the builder launcher sets it again automatically."
