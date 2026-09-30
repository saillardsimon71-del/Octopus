param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Python
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$dataPath = (Resolve-Path -LiteralPath $DataRoot).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path

$env:OCTOPUS_HOME = $dataPath
$env:OCTOPUS_DB = Join-Path $dataPath 'data\octopus.db'
$env:PODALUX_ROOT = $repoRoot

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
