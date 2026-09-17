$ErrorActionPreference = "Stop"

Write-Host "OCTOPUS — setup local cloud-first" -ForegroundColor Cyan
Write-Host "Le poste local installe uniquement le control-plane, Chromium et OmniRoute." -ForegroundColor DarkGray

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

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Warning "Docker CLI introuvable : installer/démarrer Docker Desktop pour OmniRoute."
} else {
    try {
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "daemon Docker indisponible"
        }

        $running = (& docker ps --filter "name=^omniroute$" --format "{{.Names}}")
        if ($running -eq "omniroute") {
            Write-Host "OmniRoute est déjà démarré." -ForegroundColor Green
        } else {
            $existing = (& docker ps -a --filter "name=^omniroute$" --format "{{.Names}}")
            if ($existing -eq "omniroute") {
                Write-Host "Redémarrage du conteneur OmniRoute existant..." -ForegroundColor Yellow
                & docker start omniroute | Out-Null
            } else {
                Write-Host "Téléchargement/démarrage d'OmniRoute..." -ForegroundColor Yellow
                & docker pull diegosouzapw/omniroute:latest
                & docker run -d --name omniroute --restart unless-stopped --stop-timeout 40 `
                    -p 127.0.0.1:20128:20128 `
                    -v omniroute-data:/app/data `
                    diegosouzapw/omniroute:latest | Out-Null
            }
        }
    } catch {
        Write-Warning "Docker CLI est installé mais Docker Desktop n'est pas démarré. OmniRoute devra être lancé après démarrage de Docker Desktop."
    }
}

# Variables non secrètes : elles rendent le profil cloud-first explicite.
[Environment]::SetEnvironmentVariable("OMNIROUTE_ENABLED", "1", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_MODEL", "auto/free", "User")
[Environment]::SetEnvironmentVariable("PODALUX_VIDEO_RENDERER", "cloud", "User")

Write-Host ""
Write-Host "Setup local terminé." -ForegroundColor Green
Write-Host "Le terminal courant ne recharge pas automatiquement les nouvelles variables User." -ForegroundColor DarkGray
Write-Host "Fermer/réouvrir PowerShell, puis définir les secrets suivants sans les committer :" -ForegroundColor Yellow
Write-Host '  OMNIROUTE_API_KEY'
Write-Host '  PODALUX_RUNPOD_ENDPOINT_ID'
Write-Host '  PODALUX_RUNPOD_API_TOKEN'
Write-Host '  (H3 uniquement) OCTOPUS_MINIMAX_H3_ENDPOINT_ID / OCTOPUS_MINIMAX_H3_API_TOKEN'
Write-Host ""
Write-Host "Ensuite : .\.venv\Scripts\python.exe -m agents.run doctor" -ForegroundColor Cyan
Write-Host "Puis : .\.venv\Scripts\python.exe run_gui.py" -ForegroundColor Cyan
