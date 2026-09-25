param(
    [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }),
    [switch]$DeleteInstallerBackups
)

$ErrorActionPreference = "Stop"
$CodexHome = [System.IO.Path]::GetFullPath($CodexHome)
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$backupRoot = Join-Path $CodexHome ("astra-flash-removal-backup\" + $stamp)
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

$skill = Join-Path $HOME ".agents\skills\astra-flash-orchestrator"
$role = Join-Path $CodexHome "agents\astra_flash_builder.toml"
$policyFiles = @(
    (Join-Path $CodexHome "AGENTS.md"),
    (Join-Path $CodexHome "AGENTS.override.md")
)

$begin = "<!-- BEGIN astra-flash-orchestrator managed policy -->"
$end = "<!-- END astra-flash-orchestrator managed policy -->"

function Backup-Path([string]$Path, [string]$Name) {
    if (Test-Path -LiteralPath $Path) {
        $dest = Join-Path $backupRoot $Name
        Copy-Item -LiteralPath $Path -Destination $dest -Recurse -Force
    }
}

Backup-Path $skill "skill"
Backup-Path $role "astra_flash_builder.toml"

foreach ($policy in $policyFiles) {
    if (-not (Test-Path -LiteralPath $policy -PathType Leaf)) { continue }
    Backup-Path $policy ([System.IO.Path]::GetFileName($policy) + ".before")
    $text = [System.IO.File]::ReadAllText($policy)
    $start = $text.IndexOf($begin, [System.StringComparison]::Ordinal)
    if ($start -ge 0) {
        $finish = $text.IndexOf($end, $start, [System.StringComparison]::Ordinal)
        if ($finish -lt 0) {
            throw "Managed Astra Flash block starts but has no end marker in $policy. Backup created at $backupRoot; refusing partial edit."
        }
        $finish += $end.Length
        while ($finish -lt $text.Length -and ($text[$finish] -eq [char]13 -or $text[$finish] -eq [char]10)) {
            $finish++
        }
        $updated = ($text.Substring(0, $start) + $text.Substring($finish)).TrimEnd() + [Environment]::NewLine
        [System.IO.File]::WriteAllText($policy, $updated, [System.Text.UTF8Encoding]::new($false))
        Write-Host "CLEANED policy block: $policy"
    }
}

if (Test-Path -LiteralPath $role) {
    Remove-Item -LiteralPath $role -Force
    Write-Host "REMOVED role: $role"
}
if (Test-Path -LiteralPath $skill) {
    Remove-Item -LiteralPath $skill -Recurse -Force
    Write-Host "REMOVED skill: $skill"
}

$activeRoots = @(
    (Join-Path $CodexHome "AGENTS.md"),
    (Join-Path $CodexHome "AGENTS.override.md"),
    (Join-Path $CodexHome "agents"),
    (Join-Path $HOME ".agents\skills")
)
$patterns = "astra-flash-orchestrator|astra_flash_builder|Astra-led planning and Flash implementation"
$hits = @()
foreach ($root in $activeRoots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    if (Test-Path -LiteralPath $root -PathType Leaf) {
        $hits += Select-String -LiteralPath $root -Pattern $patterns -ErrorAction SilentlyContinue
    } else {
        $hits += Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue |
            Select-String -Pattern $patterns -ErrorAction SilentlyContinue
    }
}
if ($hits.Count -gt 0) {
    Write-Host ""
    Write-Host "Residual active references found:" -ForegroundColor Yellow
    $hits | ForEach-Object { Write-Host ("  " + $_.Path + ":" + $_.LineNumber + " " + $_.Line.Trim()) }
    throw "Astra Flash removal incomplete. Backup: $backupRoot"
}

if ($DeleteInstallerBackups) {
    $oldBackups = Join-Path $CodexHome "astra-flash-install-backups"
    if (Test-Path -LiteralPath $oldBackups) {
        Remove-Item -LiteralPath $oldBackups -Recurse -Force
        Write-Host "REMOVED inert installer receipts/backups: $oldBackups"
    }
}

Write-Host ""
Write-Host "Astra Flash Orchestrator active installation removed." -ForegroundColor Green
Write-Host "Backup of removed/edited active files: $backupRoot"
Write-Host "Codex config.toml, router configuration and authentication were not touched."
