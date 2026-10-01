param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Python,
    [switch]$ReadOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$dataPath = (Resolve-Path -LiteralPath $DataRoot).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path

$env:OCTOPUS_HOME = $dataPath
$env:OCTOPUS_DB = Join-Path $dataPath 'data\octopus.db'
$env:PODALUX_ROOT = $repoRoot
$env:OCTOPUS_BROWSER_HEADLESS = '0'
if ($ReadOnly) { $env:OCTOPUS_WORKBENCH_READONLY = '1' } else { Remove-Item Env:OCTOPUS_WORKBENCH_READONLY -ErrorAction SilentlyContinue }

Push-Location $repoRoot
try {
    & $pythonPath (Join-Path $repoRoot 'run_gui.py')
    if ($LASTEXITCODE -ne 0) {
        throw "Workbench exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
