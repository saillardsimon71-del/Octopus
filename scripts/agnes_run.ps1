param(
    [string]$UpstreamDir = "cache/upstreams/agnes-video-generator",
    [switch]$Docker,
    [string]$SecureEnvFile = "$env:APPDATA\octopus\agnes.env"
)

$ErrorActionPreference = "Stop"
$root = (git rev-parse --show-toplevel 2>$null)
if (-not $root) { $root = $PWD.Path }

function Get-AgnesKey {
    # 1. Env var (secure, not logged)
    if ($env:AGNES_API_KEY -and $env:AGNES_API_KEY.Trim().Length -gt 10) {
        return $env:AGNES_API_KEY
    }
    # 2. Secure env file outside repo
    if (Test-Path -LiteralPath $SecureEnvFile) {
        $lines = Get-Content -LiteralPath $SecureEnvFile | Where-Object { $_ -match "AGNES_API_KEY=" }
        foreach ($l in $lines) {
            if ($l -match "AGNES_API_KEY\s*=\s*(.+)$") {
                $v = $Matches[1].Trim().Trim('"').Trim("'")
                if ($v.Length -gt 10) { return $v }
            }
        }
    }
    # 3. .env inside upstream cache (allowed because cache/ is gitignored)
    $envFile = Join-Path $root "$UpstreamDir/.env"
    if (Test-Path -LiteralPath $envFile) {
        $lines = Get-Content -LiteralPath $envFile | Where-Object { $_ -match "AGNES_API_KEY=" }
        foreach ($l in $lines) {
            if ($l -notmatch "^\s*#") {
                if ($l -match "AGNES_API_KEY\s*=\s*(.+)$") {
                    $v = $Matches[1].Trim().Trim('"').Trim("'")
                    if ($v.Length -gt 10 -and $v -ne "your-key-here") { return $v }
                }
            }
        }
    }
    return $null
}

$key = Get-AgnesKey
if (-not $key) {
    Write-Host "[Agnes] ERROR: AGNES_API_KEY not found in secure locations." -ForegroundColor Red
    Write-Host "Set it securely via one of:" -ForegroundColor Yellow
    Write-Host "  `$env:AGNES_API_KEY='your-key'   (session only, recommended)"
    Write-Host "  setx AGNES_API_KEY 'your-key'    (persistent user env)"
    Write-Host "  File $SecureEnvFile with line AGNES_API_KEY=your-key"
    Write-Host "Never put the key in prompts, logs, or Git."
    exit 1
}

if ($Docker) {
    Write-Host "[Agnes] Starting via Docker (loopback only 127.0.0.1:8765)" -ForegroundColor Cyan
    $dest = Join-Path $root $UpstreamDir
    if (-not (Test-Path -LiteralPath $dest)) { throw "Upstream dir missing $dest" }
    Push-Location $dest
    try {
        # Ensure data dirs
        New-Item -ItemType Directory -Force -Path "agnes_data/working" | Out-Null
        New-Item -ItemType Directory -Force -Path "agnes_data/config" | Out-Null
        docker build -t agnes-video:pinned .
        if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
        # Stop old if exists
        docker rm -f agnes-video 2>$null | Out-Null
        docker run -d --name agnes-video `
          -p 127.0.0.1:8765:8765 `
          -e HOST=127.0.0.1 -e PORT=8765 `
          -e AGNES_API_KEY=$key `
          -v ${PWD}/agnes_data/working:/app/.working_dir `
          -v ${PWD}/agnes_data/config:/app/.agnes_config `
          --restart unless-stopped `
          agnes-video:pinned
        if ($LASTEXITCODE -ne 0) { throw "docker run failed" }
        Write-Host "[OK] Container agnes-video started on http://127.0.0.1:8765" -ForegroundColor Green
        Start-Sleep -Seconds 3
        curl.exe -s http://127.0.0.1:8765/api/health
        Write-Host ""
    } finally { Pop-Location }
    exit 0
}

# Native
$dest = Join-Path $root $UpstreamDir
$venvPy = Join-Path $dest ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host "[Agnes] venv not found, run scripts/agnes_setup.ps1 first" -ForegroundColor Red
    exit 1
}
Write-Host "[Agnes] Starting native server on http://127.0.0.1:8765 (loopback only)" -ForegroundColor Cyan
Write-Host "[Agnes] Working dirs preserved between restarts" -ForegroundColor Cyan
Write-Host "[Agnes] Key injected via env var, not logged, not in args" -ForegroundColor Cyan

$env:HOST="127.0.0.1"
$env:PORT="8765"
$env:AGNES_API_KEY=$key

Push-Location $dest
try {
    & $venvPy server.py
} finally { Pop-Location }
