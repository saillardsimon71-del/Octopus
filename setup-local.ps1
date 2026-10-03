$ErrorActionPreference = "Stop"

Write-Host "OCTOPUS - setup local cloud-first" -ForegroundColor Cyan
Write-Host "Le poste local installe uniquement le control-plane et Chromium." -ForegroundColor DarkGray

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    throw "Python Launcher 'py' est introuvable. Installer Python 3.11+ puis relancer."
}

$pythonExe = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Host "Création de .venv avec Python 3.11..." -ForegroundColor Yellow
    & py -3.11 -m venv .venv
}

if (-not (Test-Path $pythonExe)) {
    throw "Impossible de créer .venv. Vérifier que Python 3.11 est installé."
}

Write-Host "Installation des dépendances locales..." -ForegroundColor Yellow
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r requirements-local.txt

Write-Host "Installation de Chromium Playwright..." -ForegroundColor Yellow
& $pythonExe -m playwright install chromium

[Environment]::SetEnvironmentVariable("PODALUX_PYTHON", $pythonExe, "User")

Write-Host ""
Write-Host "Setup local terminé." -ForegroundColor Green
Write-Host "Le terminal courant ne recharge pas automatiquement les nouvelles variables User." -ForegroundColor DarkGray
Write-Host "Fermer/réouvrir PowerShell, puis définir les secrets suivants sans les committer :" -ForegroundColor Yellow
Write-Host '  OPENROUTER_API_KEY'
Write-Host ""
Write-Host "Ensuite : .\.venv\Scripts\python.exe -m agents.run doctor" -ForegroundColor Cyan
Write-Host "Puis : .\.venv\Scripts\python.exe run_gui.py" -ForegroundColor Cyan
