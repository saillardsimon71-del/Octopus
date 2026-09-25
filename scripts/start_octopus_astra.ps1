param(
    [string]$CodexHome = "",
    [int]$MaxAstraTurns = 8,
    [int]$MaxRelayCycles = 6,
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"

function Write-JsonAtomic([string]$Path, [object]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $tmp = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Read-ThreadId([string]$JsonLog) {
    foreach ($line in Get-Content -LiteralPath $JsonLog -ErrorAction Stop) {
        if (-not $line.TrimStart().StartsWith("{")) { continue }
        try { $event = $line | ConvertFrom-Json } catch { continue }
        if ($event.type -eq "thread.started" -and $event.thread_id) {
            return [string]$event.thread_id
        }
    }
    return $null
}

$repo = (git rev-parse --show-toplevel 2>$null | Out-String).Trim()
if (-not $repo) { throw "Run this launcher from inside the OCTOPUS repository." }
$repo = [System.IO.Path]::GetFullPath($repo).TrimEnd("\")
Set-Location $repo

if (-not $CodexHome) { $CodexHome = Join-Path $HOME ".codex-octopus" }
$CodexHome = [System.IO.Path]::GetFullPath($CodexHome).TrimEnd("\")
$env:CODEX_HOME = $CodexHome

if ($MaxAstraTurns -lt 1 -or $MaxAstraTurns -gt 20) {
    throw "MaxAstraTurns must be between 1 and 20."
}
if ($MaxRelayCycles -lt 0 -or $MaxRelayCycles -gt 20) {
    throw "MaxRelayCycles must be between 0 and 20."
}

$setup = Join-Path $repo "scripts\setup_octopus_codex_home.ps1"
$preflight = Join-Path $repo "scripts\codex_preflight.ps1"
$runner = Join-Path $repo "scripts\run_external_dev_ticket.ps1"

$fetcher = Join-Path $repo "scripts\fetch_pinned_upstreams.ps1"

foreach ($required in @($setup, $preflight, $runner, $fetcher)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing launcher dependency: $required"
    }

    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $required,
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null

    if ($parseErrors.Count -gt 0) {
        $details = ($parseErrors | ForEach-Object {
            "{0}:{1} {2}" -f $_.Extent.StartLineNumber, $_.Extent.StartColumnNumber, $_.Message
        }) -join [Environment]::NewLine
        throw "PowerShell syntax check failed before launch: $required" + [Environment]::NewLine + $details
    }
}

& $setup -CodexHome $CodexHome
if ($LASTEXITCODE -ne 0) { throw "Dedicated Codex home setup failed." }
$env:CODEX_HOME = $CodexHome

$preflightCommand = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $preflight, "-ExpectedCodexHome", $CodexHome)
if ($SkipFetch) { $preflightCommand += "-SkipFetch" }
& powershell @preflightCommand
if ($LASTEXITCODE -ne 0) { throw "Codex preflight failed. Astra was not started." }

$sterileUserHome = Join-Path $CodexHome "user-home"
New-Item -ItemType Directory -Force -Path $sterileUserHome | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $sterileUserHome ".agents") | Out-Null

$originalUserHome = $HOME
$originalGitConfig = Join-Path $originalUserHome ".gitconfig"

$relayRoot = Join-Path $repo "cache\astra-relay"
$ticketRoot = Join-Path $repo "cache\astra-tickets"
$turnLogRoot = Join-Path $relayRoot "astra-turns"
$resultRoot = Join-Path $relayRoot "results"
$archiveRoot = Join-Path $relayRoot "requests"
New-Item -ItemType Directory -Force -Path $relayRoot, $ticketRoot, $turnLogRoot, $resultRoot, $archiveRoot | Out-Null

$requestPath = Join-Path $relayRoot "request.json"
$checkpointPath = Join-Path $relayRoot "checkpoint.json"
if (Test-Path -LiteralPath $requestPath) {
    throw "Stale relay request exists at $requestPath. Inspect/remove it before starting a new builder session."
}
if (Test-Path -LiteralPath $checkpointPath) {
    throw "Stale Astra checkpoint exists at $checkpointPath. Inspect/remove it before starting a new builder session."
}

$sessionStatePath = Join-Path $relayRoot "session.json"
$astraTurns = 0
$relayCycles = 0
$threadId = $null

function Invoke-AstraTurn([string]$Prompt, [string]$ExistingThreadId = "") {
    $script:astraTurns++
    if ($script:astraTurns -gt $MaxAstraTurns) {
        throw "Astra turn budget exhausted ($MaxAstraTurns). Stopping before another model call."
    }

    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $base = "turn-{0:D2}-{1}" -f $script:astraTurns, $stamp
    $jsonLog = Join-Path $turnLogRoot ($base + ".jsonl")
    $lastMessage = Join-Path $turnLogRoot ($base + "-last.txt")

    Write-Host ""
    Write-Host ("=== GPT-6 ASTRA TURN {0}/{1} ===" -f $script:astraTurns, $MaxAstraTurns) -ForegroundColor Magenta

    # Codex 0.157 discovers user skills from the OS home (~/.agents/skills)
    # independently of CODEX_HOME. Run only the Codex child with a sterile home
    # so global personal skills/plugins cannot pollute or break OCTOPUS.
    $savedHome = $env:HOME
    $savedUserProfile = $env:USERPROFILE
    $savedGitConfigGlobal = $env:GIT_CONFIG_GLOBAL
    $savedShell = $env:SHELL
    $savedMSystem = $env:MSYSTEM
    $savedBashEnv = $env:BASH_ENV
    $savedEnvVar = $env:ENV
    $savedChereInvoking = $env:CHERE_INVOKING
    $savedGitOptionalLocks = $env:GIT_OPTIONAL_LOCKS

    $env:HOME = $sterileUserHome
    $env:USERPROFILE = $sterileUserHome
    Remove-Item Env:SHELL -ErrorAction SilentlyContinue
    Remove-Item Env:MSYSTEM -ErrorAction SilentlyContinue
    Remove-Item Env:BASH_ENV -ErrorAction SilentlyContinue
    Remove-Item Env:ENV -ErrorAction SilentlyContinue
    Remove-Item Env:CHERE_INVOKING -ErrorAction SilentlyContinue
    $env:GIT_OPTIONAL_LOCKS = "0"
    if (Test-Path -LiteralPath $originalGitConfig -PathType Leaf) {
        $env:GIT_CONFIG_GLOBAL = $originalGitConfig
    } else {
        Remove-Item Env:GIT_CONFIG_GLOBAL -ErrorAction SilentlyContinue
    }

    try {
        if ($ExistingThreadId) {
            & codex exec --json --strict-config --model gpt-6-astra --output-last-message $lastMessage resume $ExistingThreadId $Prompt 2>&1 |
                Tee-Object -FilePath $jsonLog
        } else {
            & codex exec --json --strict-config --model gpt-6-astra --output-last-message $lastMessage $Prompt 2>&1 |
                Tee-Object -FilePath $jsonLog
        }
        $code = $LASTEXITCODE
    } finally {
        if ($null -eq $savedHome) { Remove-Item Env:HOME -ErrorAction SilentlyContinue } else { $env:HOME = $savedHome }
        if ($null -eq $savedUserProfile) { Remove-Item Env:USERPROFILE -ErrorAction SilentlyContinue } else { $env:USERPROFILE = $savedUserProfile }
        if ($null -eq $savedGitConfigGlobal) { Remove-Item Env:GIT_CONFIG_GLOBAL -ErrorAction SilentlyContinue } else { $env:GIT_CONFIG_GLOBAL = $savedGitConfigGlobal }
        if ($null -eq $savedShell) { Remove-Item Env:SHELL -ErrorAction SilentlyContinue } else { $env:SHELL = $savedShell }
        if ($null -eq $savedMSystem) { Remove-Item Env:MSYSTEM -ErrorAction SilentlyContinue } else { $env:MSYSTEM = $savedMSystem }
        if ($null -eq $savedBashEnv) { Remove-Item Env:BASH_ENV -ErrorAction SilentlyContinue } else { $env:BASH_ENV = $savedBashEnv }
        if ($null -eq $savedEnvVar) { Remove-Item Env:ENV -ErrorAction SilentlyContinue } else { $env:ENV = $savedEnvVar }
        if ($null -eq $savedChereInvoking) { Remove-Item Env:CHERE_INVOKING -ErrorAction SilentlyContinue } else { $env:CHERE_INVOKING = $savedChereInvoking }
        if ($null -eq $savedGitOptionalLocks) { Remove-Item Env:GIT_OPTIONAL_LOCKS -ErrorAction SilentlyContinue } else { $env:GIT_OPTIONAL_LOCKS = $savedGitOptionalLocks }
    }
    if ($code -ne 0) {
        throw "Codex/Astra exited with code $code. Log: $jsonLog"
    }

    $observedThread = Read-ThreadId $jsonLog
    if (-not $observedThread) {
        throw "Could not extract thread.started/thread_id from Codex JSONL: $jsonLog"
    }
    if ($ExistingThreadId -and $observedThread -ne $ExistingThreadId) {
        throw "Codex resumed the wrong thread. Expected $ExistingThreadId, got $observedThread."
    }

    if (Test-Path -LiteralPath $lastMessage) {
        Write-Host ""
        Write-Host "--- Astra final message ---" -ForegroundColor DarkCyan
        Get-Content -LiteralPath $lastMessage
        Write-Host "---------------------------" -ForegroundColor DarkCyan
    }

    return [pscustomobject]@{
        thread_id = $observedThread
        json_log = $jsonLog
        last_message = $lastMessage
    }
}

$initialPrompt = @'
You are the GPT-6 Astra root constructor for OCTOPUS.
Read the injected root AGENTS.md and then docs/migrations/CODEX_START_2026-09-25.md.
Start the authorized maintenance mission immediately. Do not rediscover the roadmap.

IMPORTANT WINDOWS/GIT BOUNDARY:
- The current branch prep/astra-local-orchestration is already the canonical working branch.
- Never create/switch/delete branches and never run git add/commit/reset/merge/rebase/stash or any Git operation that writes .git.
- Read-only Git inspection is allowed. The deterministic parent supervisor owns Git metadata and phase commits outside the sandbox.
- When a coherent direct-Astra phase is tested and ready to commit, atomically publish cache/astra-relay/checkpoint.json using the checkpoint schema in CODEX_START, then end the turn. The supervisor will commit and resume this exact thread.

A deterministic local relay is supervising this Codex exec session.
If and only if a bounded Step task is justified, the source repository must first be clean. If your direct work is dirty, request a checkpoint first. Once clean, create exactly one product_ticket plan under cache/astra-tickets/, atomically publish cache/astra-relay/request.json using the relay schema from CODEX_START, and end this turn. Never launch Kilo/night-shift yourself.

Otherwise keep working directly in this same turn until the current mission is complete or you hit an explicit human stop condition. Do not end merely to provide a progress update.
'@

$first = Invoke-AstraTurn -Prompt $initialPrompt
$threadId = $first.thread_id

Write-JsonAtomic -Path $sessionStatePath -Value ([ordered]@{
    version = 1
    thread_id = $threadId
    codex_home = $CodexHome
    started_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    max_astra_turns = $MaxAstraTurns
    max_relay_cycles = $MaxRelayCycles
})

while ($true) {
    if (Test-Path -LiteralPath $checkpointPath) {
        $checkpoint = Get-Content -LiteralPath $checkpointPath -Raw | ConvertFrom-Json
        if ([int]$checkpoint.version -ne 1) { throw "Unsupported Astra checkpoint version." }

        $checkpointId = [string]$checkpoint.request_id
        if ($checkpointId -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$") {
            throw "Invalid Astra checkpoint request_id."
        }

        $commitMessage = ([string]$checkpoint.message).Trim()
        if (-not $commitMessage) { throw "Astra checkpoint missing commit message." }
        if ($commitMessage.Contains([Environment]::NewLine) -or $commitMessage.Length -gt 120) {
            throw "Astra checkpoint commit message must be one line and <=120 characters."
        }

        $archivedCheckpoint = Join-Path $archiveRoot ("checkpoint-" + $checkpointId + ".json")
        if (Test-Path -LiteralPath $archivedCheckpoint) {
            throw "Checkpoint request_id has already been consumed: $checkpointId"
        }
        Move-Item -LiteralPath $checkpointPath -Destination $archivedCheckpoint

        $dirty = (git status --porcelain --untracked-files=all | Out-String).Trim()
        if (-not $dirty) {
            throw "Astra requested checkpoint $checkpointId but the repository is clean."
        }

        Write-Host ""
        Write-Host ("=== HOST GIT CHECKPOINT: {0} ===" -f $checkpointId) -ForegroundColor Yellow
        git add -A
        if ($LASTEXITCODE -ne 0) { throw "Host git add failed for checkpoint $checkpointId." }

        git diff --cached --quiet
        if ($LASTEXITCODE -eq 0) { throw "Checkpoint produced no staged diff." }
        if ($LASTEXITCODE -ne 1) { throw "git diff --cached --quiet failed." }

        git commit -m $commitMessage
        if ($LASTEXITCODE -ne 0) { throw "Host git commit failed for checkpoint $checkpointId." }

        $checkpointSha = (git rev-parse HEAD | Out-String).Trim()
        Write-Host ("[checkpoint] committed " + $checkpointSha) -ForegroundColor Green

        $checkpointPrompt = @(
            "HOST_CHECKPOINT_COMMITTED",
            "request_id=$checkpointId",
            "commit=$checkpointSha",
            "message=$commitMessage",
            "",
            "The deterministic parent supervisor committed your tested workspace changes outside the Codex sandbox.",
            "Do not attempt Git metadata writes. Continue the authorized mission from exactly where you stopped.",
            "If direct changes reach another coherent tested checkpoint, publish checkpoint.json again. If a bounded Step task is justified, only publish request.json from a clean source tree."
        ) -join [Environment]::NewLine

        $next = Invoke-AstraTurn -Prompt $checkpointPrompt -ExistingThreadId $threadId
        $threadId = $next.thread_id
        continue
    }

    if (Test-Path -LiteralPath $requestPath) {
        if ($relayCycles -ge $MaxRelayCycles) {
            throw "Relay cycle budget exhausted ($MaxRelayCycles). Leaving request untouched: $requestPath"
        }
        $relayCycles++

        $request = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
        if ([int]$request.version -ne 1) { throw "Unsupported relay request version." }

        $requestId = [string]$request.request_id
        if ($requestId -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$") {
            throw "Invalid relay request_id."
        }

        $plan = [string]$request.plan_path
        if (-not $plan) { throw "Relay request missing plan_path." }

        $hours = if ($null -ne $request.hours) { [double]$request.hours } else { 1.0 }
        if ($hours -le 0 -or $hours -gt 8) {
            throw "Relay request hours must be >0 and <=8."
        }

        $sourceDirty = (git status --porcelain --untracked-files=all | Out-String).Trim()
        if ($sourceDirty) {
            throw "Astra published a Step relay request from a dirty source tree. Request a host checkpoint first."
        }

        $archivedRequest = Join-Path $archiveRoot ($requestId + ".json")
        if (Test-Path -LiteralPath $archivedRequest) {
            throw "RequestId has already been consumed: $requestId"
        }
        Move-Item -LiteralPath $requestPath -Destination $archivedRequest

        $resultPath = "cache/astra-relay/results/$requestId.json"

        Write-Host ""
        Write-Host ("=== RELAY CYCLE {0}/{1}: {2} ===" -f $relayCycles, $MaxRelayCycles, $requestId) -ForegroundColor Cyan

        & powershell -NoProfile -ExecutionPolicy Bypass -File $runner -Plan $plan -Hours $hours -RequestId $requestId -ResultPath $resultPath
        $workerExit = $LASTEXITCODE

        $reviewPrompt = @(
            "RELAY_COMPLETED",
            "request_id=$requestId",
            "worker_exit_code=$workerExit",
            "result_file=$resultPath",
            "",
            "Read the result file and its referenced night-shift report/log. Inspect the produced commit/worktree, actual diff, changed paths, acceptance evidence and tests. Review it yourself as root GPT-6 Astra.",
            "If acceptable, integrate it according to the current branch strategy without writing Git metadata from the sandbox; use a host checkpoint when direct workspace changes need committing. If it is wrong or ambiguous, fix/take back the task according to CODEX_START.",
            "If another bounded Step task is genuinely justified, publish one new relay request only from a clean source tree and end the turn. Otherwise keep working directly until the mission is complete or an explicit human stop condition is reached. Never poll a worker and never launch Kilo/night-shift yourself."
        ) -join [Environment]::NewLine

        $next = Invoke-AstraTurn -Prompt $reviewPrompt -ExistingThreadId $threadId
        $threadId = $next.thread_id
        continue
    }

    $remainingDirty = (git status --porcelain --untracked-files=all | Out-String).Trim()
    if ($remainingDirty) {
        throw "Astra ended without checkpoint/relay while the repository is dirty. Refusing to stop silently."
    }
    break
}

Write-Host ""
Write-Host "OCTOPUS Astra builder stopped cleanly: no pending relay request." -ForegroundColor Green
Write-Host ("Astra turns used: {0}/{1}" -f $astraTurns, $MaxAstraTurns)
Write-Host ("Relay cycles used: {0}/{1}" -f $relayCycles, $MaxRelayCycles)
Write-Host "Session state: $sessionStatePath"
