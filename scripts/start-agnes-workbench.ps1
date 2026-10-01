param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Python,
    [switch]$ReadOnly
)

& (Join-Path $PSScriptRoot 'start-workbench.ps1') @PSBoundParameters
