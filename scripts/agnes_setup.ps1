param(
    [string]$UpstreamDir = "cache/upstreams/agnes-video-generator",
    [string]$Pin = "a87162d6df73ffe72186838ca0ae9d461e68589b"
)

$ErrorActionPreference = "Stop"
$root = (git rev-parse --show-toplevel 2>$null)
if (-not $root) { throw "Run from inside the OCTOPUS repository." }

Write-Host "[Agnes] Ensuring pinned upstream @ $Pin" -ForegroundColor Cyan
& "$root/scripts/fetch_pinned_upstreams.ps1" -Target agnes
if ($LASTEXITCODE -ne 0) { throw "fetch_pinned_upstreams failed" }

$dest = Join-Path $root $UpstreamDir
if (-not (Test-Path -LiteralPath $dest)) { throw "Upstream dir not found: $dest" }

$actual = (git -C $dest rev-parse HEAD).Trim()
if ($actual -ne $Pin) { throw "Pin mismatch: expected $Pin got $actual" }
Write-Host "[OK] Pin verified $actual -> $dest" -ForegroundColor Green

# Create venv isolated
$venv = Join-Path $dest ".venv"
if (-not (Test-Path -LiteralPath $venv)) {
    Write-Host "[Agnes] Creating venv $venv" -ForegroundColor Cyan
    python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

$python = Join-Path $venv "Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "venv python not found: $python" }

Write-Host "[Agnes] Installing requirements (pinned source, ranged deps)" -ForegroundColor Cyan
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $python -m pip install -r (Join-Path $dest "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Ensure working dirs exist and are preserved
$working = Join-Path $dest ".working_dir"
$config = Join-Path $dest ".agnes_config"
New-Item -ItemType Directory -Force -Path $working | Out-Null
New-Item -ItemType Directory -Force -Path $config | Out-Null
Write-Host "[Agnes] Working dirs preserved: $working , $config" -ForegroundColor Green

# Create .env template if not exists, enforcing HOST=127.0.0.1
$envFile = Join-Path $dest ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Host "[Agnes] Creating .env template with HOST=127.0.0.1 (no key)" -ForegroundColor Cyan
    @"
# Agnes local config — loopback only, key injected via env var, never committed
HOST=127.0.0.1
PORT=8765
# AGNES_API_KEY is injected at runtime from secure env var, not stored here
# To use .env for key (discouraged, but allowed if file is in cache/ ignored by Git):
# AGNES_API_KEY=your-key-here
"@ | Set-Content -Path $envFile -Encoding utf8
} else {
    # Enforce HOST=127.0.0.1 if file exists
    $content = Get-Content -LiteralPath $envFile -Raw
    if ($content -notmatch "HOST=127.0.0.1") {
        Write-Host "[WARN] .env does not enforce HOST=127.0.0.1 — fixing" -ForegroundColor Yellow
        $content = $content -replace "HOST=.*", "HOST=127.0.0.1"
        if ($content -notmatch "HOST=") { $content = "HOST=127.0.0.1`nPORT=8765`n" + $content }
        Set-Content -Path $envFile -Value $content -Encoding utf8
    }
}

Write-Host "[Agnes] Setup complete. Next:" -ForegroundColor Green
Write-Host "  1. Set secure key: `$env:AGNES_API_KEY='your-key'  (or setx for persistent)"
Write-Host "  2. Run: powershell -ExecutionPolicy Bypass -File scripts/agnes_run.ps1"
Write-Host "  3. Health: curl http://127.0.0.1:8765/api/health"
