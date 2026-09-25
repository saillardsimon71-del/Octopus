param(
    [string]$CodexHome = "",
    [int]$MaxAstraTurns = 4,
    [int]$MaxRelayCycles = 6,
    [int]$MaxResumeInputTokens = 250000,
    [switch]$SkipFetch,
    [switch]$NewSession,
    [switch]$ResumeFailed,
    [switch]$ValidateOnly
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

function Read-TurnInputTokens([string]$JsonLog) {
    $tokens = $null
    foreach ($line in Get-Content -LiteralPath $JsonLog -ErrorAction Stop) {
        if (-not $line.TrimStart().StartsWith("{")) { continue }
        try { $event = $line | ConvertFrom-Json } catch { continue }
        if ($event.type -eq "turn.completed" -and $null -ne $event.usage.input_tokens) {
            $tokens = [long]$event.usage.input_tokens
        }
    }
    return $tokens
}

function Resolve-CodexExecutable {
    $official = Join-Path $env:LOCALAPPDATA "Programs\OpenAI\Codex\bin\codex.exe"
    if (Test-Path -LiteralPath $official -PathType Leaf) {
        return [System.IO.Path]::GetFullPath($official)
    }
    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw "Codex CLI not found."
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
if ($MaxResumeInputTokens -lt 10000) {
    throw "MaxResumeInputTokens must be at least 10000."
}

$setup = Join-Path $repo "scripts\setup_octopus_codex_home.ps1"
$preflight = Join-Path $repo "scripts\codex_preflight.ps1"
$runner = Join-Path $repo "scripts\run_external_dev_ticket.ps1"
$checkpointRunner = Join-Path $repo "scripts\commit_astra_checkpoint.ps1"

$fetcher = Join-Path $repo "scripts\fetch_pinned_upstreams.ps1"

foreach ($required in @($setup, $preflight, $runner, $checkpointRunner, $fetcher)) {
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
$CodexExe = Resolve-CodexExecutable

$preflightCommand = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $preflight, "-ExpectedCodexHome", $CodexHome)
if ($SkipFetch) { $preflightCommand += "-SkipFetch" }
$pendingCheckpointBeforePreflight = Join-Path $repo "cache\astra-relay\checkpoint.json"
if (Test-Path -LiteralPath $pendingCheckpointBeforePreflight -PathType Leaf) { $preflightCommand += "-AllowPendingCheckpoint" }
& powershell @preflightCommand
if ($LASTEXITCODE -ne 0) { throw "Codex preflight failed. Astra was not started." }
if ($ValidateOnly) {
    Write-Host "Constructor validation passed. No baseline or model call was started." -ForegroundColor Green
    exit 0
}

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
$sessionArchiveRoot = Join-Path $relayRoot "sessions"
New-Item -ItemType Directory -Force -Path $relayRoot, $ticketRoot, $turnLogRoot, $resultRoot, $archiveRoot, $sessionArchiveRoot | Out-Null

$requestPath = Join-Path $relayRoot "request.json"
$checkpointPath = Join-Path $relayRoot "checkpoint.json"
$sessionStatePath = Join-Path $relayRoot "session.json"
$baselineStatePath = Join-Path $relayRoot "baseline.json"
$baselineLogPath = Join-Path $relayRoot "baseline-pytest.log"
$astraTurns = 0
$relayCycles = 0
$threadId = $null
$modelInvoked = $false

$head = (git rev-parse HEAD | Out-String).Trim()
$pythonExe = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    $pythonExe = (Get-Command python -ErrorAction Stop).Source
}
$pytestVersion = (& $pythonExe -m pytest --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not determine pytest version." }
$requirementsPath = Join-Path $repo "requirements-local.txt"
$requirementsHash = if (Test-Path -LiteralPath $requirementsPath -PathType Leaf) {
    (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
} else { "missing" }
$baselineFingerprint = "$head|$pythonExe|$pytestVersion|$requirementsHash"
$baseline = $null
if (Test-Path -LiteralPath $baselineStatePath -PathType Leaf) {
    try { $baseline = Get-Content -LiteralPath $baselineStatePath -Raw | ConvertFrom-Json } catch { $baseline = $null }
}

if ((Test-Path -LiteralPath $checkpointPath -PathType Leaf) -and (-not $baseline -or -not $baseline.completed)) {
    throw "Pending checkpoint recovery requires the baseline state from the original session."
}

if (-not (Test-Path -LiteralPath $checkpointPath -PathType Leaf) -and (-not $baseline -or [string]$baseline.fingerprint -ne $baselineFingerprint -or -not $baseline.completed)) {
    Write-Host ""
    Write-Host "=== HOST BASELINE (zero Astra turns) ===" -ForegroundColor Cyan
    Write-Host "$pythonExe -m pytest -q --tb=short"
    $started = Get-Date
    $baselineOutput = @(& $pythonExe -m pytest -q --tb=short 2>&1)
    $baselineExit = $LASTEXITCODE
    $baselineOutput | Set-Content -LiteralPath $baselineLogPath -Encoding UTF8
    $summary = @($baselineOutput | Select-Object -Last 30 | ForEach-Object { [string]$_ })
    $baseline = [ordered]@{
        version = 1
        completed = $true
        fingerprint = $baselineFingerprint
        head = $head
        command = "$pythonExe -m pytest -q --tb=short"
        exit_code = $baselineExit
        duration_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
        log_path = "cache/astra-relay/baseline-pytest.log"
        summary = $summary
        recorded_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
    Write-JsonAtomic -Path $baselineStatePath -Value $baseline
} else {
    Write-Host ("[OK] Reusing host baseline for " + $head.Substring(0, 12) + ": exit " + $baseline.exit_code) -ForegroundColor Green
}

$existingSession = $null
if (Test-Path -LiteralPath $sessionStatePath -PathType Leaf) {
    try { $existingSession = Get-Content -LiteralPath $sessionStatePath -Raw | ConvertFrom-Json } catch { throw "Invalid Astra session state: $sessionStatePath" }
}

$existingStatus = if ($existingSession) { [string]$existingSession.status } else { "" }
if ($existingSession -and $existingStatus -eq "failed" -and -not $NewSession -and -not $ResumeFailed) {
    throw "The previous Codex call failed. Inspect its log, then use -ResumeFailed to retry the exact thread or -NewSession to retire it."
}

if ($existingSession -and -not $NewSession -and (-not $existingStatus -or $existingStatus -eq "active" -or ($existingStatus -eq "failed" -and $ResumeFailed))) {
    $candidateThread = [string]$existingSession.thread_id
    if ($candidateThread) {
        $matchingLogs = @(Get-ChildItem -LiteralPath $turnLogRoot -Filter "*.jsonl" -File | Where-Object {
            (Read-ThreadId $_.FullName) -eq $candidateThread
        } | Sort-Object LastWriteTimeUtc -Descending)
        $previousInputTokens = if ($matchingLogs.Count -gt 0) { Read-TurnInputTokens $matchingLogs[0].FullName } else { $null }
        if (-not $ResumeFailed -and $null -ne $previousInputTokens -and $previousInputTokens -gt $MaxResumeInputTokens) {
            Write-Host ("[quota] Retiring thread $candidateThread after $previousInputTokens input tokens; starting a compact thread.") -ForegroundColor Yellow
        } else {
            $threadId = $candidateThread
            Write-Host ("[resume] Exact Astra thread: " + $threadId) -ForegroundColor Green
        }
    }
}

if ($existingSession -and -not $threadId) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    Move-Item -LiteralPath $sessionStatePath -Destination (Join-Path $sessionArchiveRoot ("session-" + $stamp + ".json"))
}

if ((Test-Path -LiteralPath $requestPath -PathType Leaf) -or (Test-Path -LiteralPath $checkpointPath -PathType Leaf)) {
    if (-not $threadId) { throw "Pending relay/checkpoint exists without a resumable exact Astra thread." }
}

function Save-SessionState([string]$Status) {
    Write-JsonAtomic -Path $sessionStatePath -Value ([ordered]@{
        version = 2
        status = $Status
        thread_id = $script:threadId
        codex_home = $CodexHome
        current_head = (git rev-parse HEAD | Out-String).Trim()
        baseline_fingerprint = $baselineFingerprint
        astra_turns_this_run = $script:astraTurns
        relay_cycles_this_run = $script:relayCycles
        updated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    })
}

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

    $env:HOME = $sterileUserHome
    $env:USERPROFILE = $sterileUserHome
    if (Test-Path -LiteralPath $originalGitConfig -PathType Leaf) {
        $env:GIT_CONFIG_GLOBAL = $originalGitConfig
    } else {
        Remove-Item Env:GIT_CONFIG_GLOBAL -ErrorAction SilentlyContinue
    }

    try {
        if ($ExistingThreadId) {
            & $CodexExe exec --json --strict-config --disable shell_snapshot --model gpt-6-astra --cd $repo --output-last-message $lastMessage resume $ExistingThreadId $Prompt 2>&1 |
                Tee-Object -FilePath $jsonLog
        } else {
            & $CodexExe exec --json --strict-config --disable shell_snapshot --model gpt-6-astra --cd $repo --output-last-message $lastMessage $Prompt 2>&1 |
                Tee-Object -FilePath $jsonLog
        }
        $code = $LASTEXITCODE
    } finally {
        if ($null -eq $savedHome) { Remove-Item Env:HOME -ErrorAction SilentlyContinue } else { $env:HOME = $savedHome }
        if ($null -eq $savedUserProfile) { Remove-Item Env:USERPROFILE -ErrorAction SilentlyContinue } else { $env:USERPROFILE = $savedUserProfile }
        if ($null -eq $savedGitConfigGlobal) { Remove-Item Env:GIT_CONFIG_GLOBAL -ErrorAction SilentlyContinue } else { $env:GIT_CONFIG_GLOBAL = $savedGitConfigGlobal }
    }
    $observedThread = Read-ThreadId $jsonLog
    if ($code -ne 0) {
        if ($observedThread) {
            $script:threadId = $observedThread
            Save-SessionState -Status "failed"
        }
        throw "Codex/Astra exited with code $code. Log: $jsonLog"
    }
    if (-not $observedThread) {
        throw "Could not extract thread.started/thread_id from Codex JSONL: $jsonLog"
    }
    if ($ExistingThreadId -and $observedThread -ne $ExistingThreadId) {
        throw "Codex resumed the wrong thread. Expected $ExistingThreadId, got $observedThread."
    }
    $script:modelInvoked = $true

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

$baselineBrief = (@($baseline.summary) | Select-Object -Last 8) -join " | "
if ($baselineBrief.Length -gt 1800) { $baselineBrief = $baselineBrief.Substring($baselineBrief.Length - 1800) }

$initialPrompt = @"
You are the GPT-6 Astra root constructor for the bounded OCTOPUS maintenance window.
Read root AGENTS.md (already injected) and docs/migrations/CODEX_START_2026-09-25.md once. Start at phase B. Do not rediscover the roadmap or rerun the baseline.

The deterministic host already validated Git, auth, sandbox, pinned upstreams, and ran the full Python baseline outside the model loop:
- head=$head
- exit_code=$($baseline.exit_code)
- log=$($baseline.log_path)
- tail=$baselineBrief

Read only VIDEO_ENGINE_REMOVAL.md and files directly needed for phase B. Use targeted searches and bounded reads. Run targeted tests while editing; run the full suite only once after the phase changes are complete.

Windows boundary: use PowerShell only. Read-only Git inspection is allowed. Never create/switch branches or run Git operations that write .git. For a tested phase, atomically publish checkpoint.json with version=2, request_id, base_head, message, and exact changed paths, then end the turn. The host commits those exact paths and resumes this exact thread.

Delegate only a bounded mechanical task with a clean source tree by publishing one product_ticket plus request.json, then end the turn. Never launch Kilo/night-shift, poll, sleep, or perform predictable Git mechanics yourself.
"@

if (-not $threadId) {
    $first = Invoke-AstraTurn -Prompt $initialPrompt
    $threadId = $first.thread_id
    Save-SessionState -Status "active"
}

while ($true) {
    if (Test-Path -LiteralPath $checkpointPath) {
        Write-Host ""
        Write-Host "=== HOST GIT CHECKPOINT ===" -ForegroundColor Yellow
        $checkpointResultPath = Join-Path $resultRoot "checkpoint-latest.json"
        & powershell -NoProfile -ExecutionPolicy Bypass -File $checkpointRunner -Repo $repo -CheckpointPath $checkpointPath -ResultPath $checkpointResultPath
        if ($LASTEXITCODE -ne 0) { throw "Host checkpoint failed. Request left in place: $checkpointPath" }
        $checkpointReceipt = Get-Content -LiteralPath $checkpointResultPath -Raw | ConvertFrom-Json
        $checkpointId = [string]$checkpointReceipt.request_id
        $checkpointSha = [string]$checkpointReceipt.commit
        $commitMessage = [string]$checkpointReceipt.message

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
        Save-SessionState -Status "active"
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
            "If acceptable, publish checkpoint.json version 2 with kind=worker_commit, base_head, the full reviewed source_commit SHA, message, and exact changed paths, then end the turn. The host will fast-forward only that direct child commit and resume this exact thread.",
            "If it is wrong or ambiguous, fix/take back the task according to CODEX_START; use kind=working_tree for tested direct changes.",
            "If another bounded Step task is genuinely justified, publish one new relay request only from a clean source tree and end the turn. Otherwise keep working directly until the mission is complete or an explicit human stop condition is reached. Never poll a worker and never launch Kilo/night-shift yourself."
        ) -join [Environment]::NewLine

        $next = Invoke-AstraTurn -Prompt $reviewPrompt -ExistingThreadId $threadId
        $threadId = $next.thread_id
        Save-SessionState -Status "active"
        continue
    }

    if (-not $modelInvoked) {
        $resumePrompt = @(
            "HOST_RESUME",
            "current_head=$head",
            "baseline_exit_code=$($baseline.exit_code)",
            "baseline_log=$($baseline.log_path)",
            "",
            "Resume the authorized maintenance from the exact prior state. Do not repeat repository discovery, broad reads, or the full baseline.",
            "The host now owns all Git metadata writes. Use checkpoint schema version 2 with base_head and exact changed paths.",
            "Continue phase B with targeted reads and tests. Never poll or launch Kilo/night-shift yourself."
        ) -join [Environment]::NewLine
        $next = Invoke-AstraTurn -Prompt $resumePrompt -ExistingThreadId $threadId
        $threadId = $next.thread_id
        Save-SessionState -Status "active"
        continue
    }

    $remainingDirty = (git status --porcelain --untracked-files=all | Out-String).Trim()
    if ($remainingDirty) {
        throw "Astra ended without checkpoint/relay while the repository is dirty. Refusing to stop silently."
    }
    Save-SessionState -Status "completed"
    break
}

Write-Host ""
Write-Host "OCTOPUS Astra builder stopped cleanly: no pending relay or checkpoint." -ForegroundColor Green
Write-Host ("Astra turns used: {0}/{1}" -f $astraTurns, $MaxAstraTurns)
Write-Host ("Relay cycles used: {0}/{1}" -f $relayCycles, $MaxRelayCycles)
Write-Host "Session state: $sessionStatePath"
