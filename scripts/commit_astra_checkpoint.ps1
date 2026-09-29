param(
    [Parameter(Mandatory = $true)]
    [string]$Repo,
    [Parameter(Mandatory = $true)]
    [string]$CheckpointPath,
    [string]$ResultPath = "",
    [string]$ExpectedBranch = "prep/astra-local-orchestration"
)

$ErrorActionPreference = "Stop"

function Write-JsonAtomic([string]$Path, [object]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $tmp = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Invoke-Git([string[]]$Arguments) {
    $output = @(& git -C $Repo @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed:`n$($output -join [Environment]::NewLine)"
    }
    return $output
}

$Repo = [System.IO.Path]::GetFullPath($Repo).TrimEnd("\")
if (-not (Test-Path -LiteralPath $Repo -PathType Container)) { throw "Repository not found: $Repo" }
$repoRoot = $Repo + "\"

$CheckpointPath = [System.IO.Path]::GetFullPath($CheckpointPath)
if (-not (Test-Path -LiteralPath $CheckpointPath -PathType Leaf)) {
    throw "Checkpoint request not found: $CheckpointPath"
}

$actualRepo = ((Invoke-Git @("rev-parse", "--show-toplevel")) -join "").Trim()
if ([System.IO.Path]::GetFullPath($actualRepo).TrimEnd("\") -ne $Repo) {
    throw "Path is not the requested Git repository: $Repo"
}

$branch = ((Invoke-Git @("branch", "--show-current")) -join "").Trim()
if ($ExpectedBranch -and $branch -ne $ExpectedBranch) {
    throw "Expected branch $ExpectedBranch, got $branch"
}

$checkpoint = Get-Content -LiteralPath $CheckpointPath -Raw | ConvertFrom-Json
if ([int]$checkpoint.version -ne 2) { throw "Unsupported Astra checkpoint version." }

$requestId = [string]$checkpoint.request_id
if ($requestId -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$") {
    throw "Invalid Astra checkpoint request_id."
}
$archiveRoot = Join-Path (Split-Path -Parent $CheckpointPath) "requests"
$archivePath = Join-Path $archiveRoot ("checkpoint-" + $requestId + ".json")
if (Test-Path -LiteralPath $archivePath) { throw "Checkpoint request_id was already consumed: $requestId" }

$kind = if ($checkpoint.kind) { [string]$checkpoint.kind } else { "working_tree" }
$message = ([string]$checkpoint.message).Trim()
if ($kind -ne "worker_commit" -and (-not $message -or $message.Length -gt 120 -or $message -match "[`r`n]")) {
    throw "Astra checkpoint message must be one line and <=120 characters."
}

$head = ((Invoke-Git @("rev-parse", "HEAD")) -join "").Trim()
if ([string]$checkpoint.base_head -ne $head) {
    throw "Checkpoint base_head does not match HEAD. Expected $head, got $($checkpoint.base_head)."
}

& git -C $Repo diff --cached --quiet
if ($LASTEXITCODE -eq 1) { throw "Refusing checkpoint with pre-existing staged changes." }
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the Git index." }

$declared = New-Object System.Collections.Generic.List[string]
foreach ($path in @($checkpoint.paths)) {
    $value = ([string]$path).Trim().Replace("\", "/")
    if (-not $value -or [System.IO.Path]::IsPathRooted($value)) {
        throw "Checkpoint paths must be non-empty repository-relative paths."
    }
    $resolved = [System.IO.Path]::GetFullPath((Join-Path $Repo $value.Replace("/", "\")))
    if (-not $resolved.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Checkpoint path escapes the repository: $value"
    }
    $relative = $resolved.Substring($repoRoot.Length).Replace("\", "/")
    if ($relative -eq ".git" -or $relative.StartsWith(".git/", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Git metadata cannot be checkpointed."
    }
    if ($declared.Contains($relative)) { throw "Duplicate checkpoint path: $relative" }
    [void]$declared.Add($relative)
}
if ($declared.Count -eq 0) { throw "Checkpoint paths cannot be empty." }

$wanted = @($declared | Sort-Object -Unique)
if ($kind -eq "working_tree") {
    $tracked = @((Invoke-Git @("diff", "--name-only", "--relative", "HEAD", "--")) | Where-Object { $_ })
    $untracked = @((Invoke-Git @("ls-files", "--others", "--exclude-standard", "--")) | Where-Object { $_ })
    $changed = @($tracked + $untracked | Sort-Object -Unique)
    $difference = @(Compare-Object -ReferenceObject $wanted -DifferenceObject $changed)
    if ($difference.Count -gt 0) {
        throw "Checkpoint paths do not exactly match the working tree changes. Declared: $($wanted -join ', '); changed: $($changed -join ', ')"
    }

    $unmerged = @((Invoke-Git @("diff", "--name-only", "--diff-filter=U", "--")) | Where-Object { $_ })
    if ($unmerged.Count -gt 0) { throw "Checkpoint contains unmerged paths: $($unmerged -join ', ')" }

    & git -C $Repo add -A -- @wanted
    if ($LASTEXITCODE -ne 0) { throw "Host git add failed for checkpoint $requestId." }

    $staged = @((Invoke-Git @("diff", "--no-renames", "--cached", "--name-only", "--relative", "--")) | Where-Object { $_ } | Sort-Object -Unique)
    $stagedDifference = @(Compare-Object -ReferenceObject $wanted -DifferenceObject $staged)
    if ($stagedDifference.Count -gt 0) { throw "Staged paths differ from the checkpoint request." }

    & git -C $Repo commit -m $message
    if ($LASTEXITCODE -ne 0) { throw "Host git commit failed for checkpoint $requestId." }
    $commit = ((Invoke-Git @("rev-parse", "HEAD")) -join "").Trim()
} elseif ($kind -eq "worker_commit") {
    $dirty = @((Invoke-Git @("status", "--porcelain", "--untracked-files=all")) | Where-Object { $_ })
    if ($dirty.Count -gt 0) { throw "Worker integration requires a clean source repository." }

    $sourceCommit = ([string]$checkpoint.source_commit).Trim()
    if ($sourceCommit -notmatch "^[0-9a-fA-F]{40}$") { throw "Worker checkpoint requires a full source_commit SHA." }
    $resolvedCommit = ((Invoke-Git @("rev-parse", "$sourceCommit^{commit}")) -join "").Trim()
    if ($resolvedCommit -ne $sourceCommit.ToLowerInvariant()) { throw "Worker source_commit did not resolve exactly." }
    $ancestry = (((Invoke-Git @("rev-list", "--parents", "-n", "1", $sourceCommit)) -join "").Trim() -split "\s+")
    if ($ancestry.Count -ne 2 -or $ancestry[1] -ne $head) {
        throw "Worker commit must be a single-parent direct child of checkpoint base_head."
    }
    $message = ((Invoke-Git @("show", "-s", "--format=%s", $sourceCommit)) -join "").Trim()
    if (-not $message -or $message.Length -gt 120 -or $message -match "[`r`n]") {
        throw "Worker commit subject must be one line and <=120 characters."
    }

    $workerPaths = @((Invoke-Git @("diff", "--no-renames", "--name-only", "--relative", $head, $sourceCommit, "--")) | Where-Object { $_ } | Sort-Object -Unique)
    $workerDifference = @(Compare-Object -ReferenceObject $wanted -DifferenceObject $workerPaths)
    if ($workerDifference.Count -gt 0) {
        throw "Worker commit paths do not exactly match the reviewed checkpoint paths."
    }

    & git -C $Repo merge --ff-only $sourceCommit
    if ($LASTEXITCODE -ne 0) { throw "Host fast-forward failed for worker checkpoint $requestId." }
    $commit = $sourceCommit.ToLowerInvariant()
} else {
    throw "Unsupported Astra checkpoint kind: $kind"
}

$remaining = @((Invoke-Git @("status", "--porcelain", "--untracked-files=all")) | Where-Object { $_ })
if ($remaining.Count -gt 0) { throw "Working tree is still dirty after checkpoint commit." }

New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
Move-Item -LiteralPath $CheckpointPath -Destination $archivePath

$receipt = [ordered]@{
    version = 1
    request_id = $requestId
    base_head = $head
    commit = $commit
    kind = $kind
    message = $message
    paths = $wanted
    committed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
}
if ($ResultPath) { Write-JsonAtomic -Path ([System.IO.Path]::GetFullPath($ResultPath)) -Value $receipt }
$receipt | ConvertTo-Json -Depth 10
