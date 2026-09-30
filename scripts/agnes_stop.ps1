param(
    [switch]$Docker
)

$ErrorActionPreference = "Stop"

if ($Docker -or (docker ps --filter name=agnes-video --format "{{.Names}}" 2>$null | Where-Object { $_ -eq "agnes-video" })) {
    Write-Host "[Agnes] Stopping Docker container agnes-video (preserving volumes)" -ForegroundColor Cyan
    docker stop agnes-video
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] docker stop failed or container not running" -ForegroundColor Yellow
    } else {
        Write-Host "[OK] Stopped. Files preserved in volumes/bind mounts." -ForegroundColor Green
        Write-Host "To remove container but keep files: docker rm agnes-video"
    }
    exit 0
}

# Native: find python processes listening on 8765
Write-Host "[Agnes] Stopping native processes on 127.0.0.1:8765" -ForegroundColor Cyan
$conns = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "[OK] No process listening on 8765" -ForegroundColor Green
    exit 0
}
foreach ($c in $conns) {
    $pid = $c.OwningProcess
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Stopping PID $pid ($($proc.ProcessName))" -ForegroundColor Yellow
        Stop-Process -Id $pid -Force
    }
}
Write-Host "[OK] Stopped. Working dirs .working_dir and .agnes_config preserved." -ForegroundColor Green
