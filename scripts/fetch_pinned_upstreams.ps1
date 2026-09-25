param(
    [ValidateSet("agnes","hermes","all")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"
$root = (git rev-parse --show-toplevel 2>$null)
if (-not $root) { throw "Run from inside the OCTOPUS repository." }
$cache = Join-Path $root "cache\upstreams"
New-Item -ItemType Directory -Force -Path $cache | Out-Null

$items = @(
    @{
        Name = "agnes"
        Repo = "https://github.com/lcy362/agnes-video-generator.git"
        Sha  = "a87162d6df73ffe72186838ca0ae9d461e68589b"
        Dir  = "agnes-video-generator"
    },
    @{
        Name = "hermes"
        Repo = "https://github.com/NousResearch/hermes-agent.git"
        Sha  = "59004a62356f3a4697ab0fe8ad5086d2b405e2a6"
        Dir  = "hermes-agent"
    }
)

foreach ($item in $items) {
    if ($Target -ne "all" -and $Target -ne $item.Name) { continue }
    $dest = Join-Path $cache $item.Dir
    Write-Host "[$($item.Name)] pin $($item.Sha)"
    if (-not (Test-Path -LiteralPath (Join-Path $dest ".git"))) {
        git clone --filter=blob:none --no-checkout $item.Repo $dest
        if ($LASTEXITCODE -ne 0) { throw "clone failed: $($item.Repo)" }
    }
    git -C $dest remote set-url origin $item.Repo
    git -C $dest fetch --quiet --no-tags origin
    if ($LASTEXITCODE -ne 0) { throw "fetch failed: $($item.Repo)" }
    git -C $dest cat-file -e "$($item.Sha)^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "pinned commit unavailable: $($item.Sha)" }
    git -C $dest checkout --quiet --detach $item.Sha
    if ($LASTEXITCODE -ne 0) { throw "checkout failed: $($item.Sha)" }
    $actual = (git -C $dest rev-parse HEAD).Trim()
    if ($actual -ne $item.Sha) { throw "pin mismatch for $($item.Name): $actual" }
    $dirty = git -C $dest status --porcelain
    if (-not [string]::IsNullOrWhiteSpace(($dirty -join ""))) {
        throw "upstream cache is dirty: $dest"
    }
    Write-Host "[OK] $($item.Name) -> $dest @ $actual" -ForegroundColor Green
}
