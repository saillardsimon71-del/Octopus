param(
    [string]$CodexHome = "",
    [ValidateSet("B", "C", "D", "E", "F")]
    [string]$Phase = "B",
    [int]$MaxAstraTurns = 2,
    [int]$MaxRelayCycles = 6,
    [ValidateSet("medium", "high")]
    [string]$Reasoning = "medium",
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

function Read-TurnUsage([string]$JsonLog) {
    $usage = $null
    foreach ($line in Get-Content -LiteralPath $JsonLog -ErrorAction Stop) {
        if (-not $line.TrimStart().StartsWith("{")) { continue }
        try { $event = $line | ConvertFrom-Json } catch { continue }
        if ($event.type -eq "turn.completed" -and $event.usage) {
            $usage = [ordered]@{}
            foreach ($key in @('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')) {
                $usage[$key] = if ($null -ne $event.usage.$key) { [long]$event.usage.$key } else { 0 }
            }
        }
    }
    return $usage
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

$phaseSpec = switch ($Phase) {
    "B" {
        [ordered]@{
            documents = "docs/migrations/VIDEO_ENGINE_REMOVAL.md"
            mission = "Remove the legacy video engine while preserving shared consumers."
        }
    }
    "C" {
        [ordered]@{
            documents = "docs/migrations/AGNES_VIDEO_REPLACEMENT.md"
            mission = "Implement the bounded Agnes video adapter and its deterministic mocked HTTP tests. Never perform a live generation or use paid credentials."
        }
    }
    "D" {
        [ordered]@{
            documents = "docs/migrations/OCTOPUS_HERMES_REPLACEMENT_MATRIX.md and docs/migrations/HERMES_COMPONENT_EXTRACTION.md"
            mission = "Implement the Hermes P0 tool-registry replacement only. Preserve OCTOPUS policies and do not introduce a second registry or broad plugin discovery."
        }
    }
    "E" {
        [ordered]@{
            documents = "docs/migrations/CODEX_START_2026-09-27.md"
            mission = "Complete the necessary Hermes integration, remove verified blockers to correct OCTOPUS operation, and align the implementation with the supervised economic-workshop vision. Implement and validate corrections; do not stop after an audit report."
        }
    }
    "F" {
        [ordered]@{
            documents = "docs/migrations/FINAL_READINESS_ANTI_CONTAMINATION.md"
            mission = "Verify final readiness for a supervised economic dry run and prevent historical business context from becoming an active cold-start objective. Fix only reproduced blockers, add a deterministic contamination canary, and report READY or NOT READY."
        }
    }
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
$sessionBeforePreflight = Join-Path $repo "cache\astra-relay\session.json"
if (-not $NewSession -and (Test-Path -LiteralPath $sessionBeforePreflight -PathType Leaf)) {
    try { $earlySession = Get-Content -LiteralPath $sessionBeforePreflight -Raw | ConvertFrom-Json -ErrorAction Stop } catch { $earlySession = $null }
    $earlyStatus = if ($earlySession) { [string]$earlySession.status } else { "" }
    if ($earlyStatus -eq "active" -or ($earlyStatus -eq "failed" -and $ResumeFailed)) {
        $preflightCommand += "-AllowResumableSession"
    }
}
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
$handoffPath = Join-Path $relayRoot "handoff.json"
$validationPath = Join-Path $relayRoot "validation.json"
$usagePath = Join-Path $relayRoot "usage.json"
$baselineStatePath = Join-Path $relayRoot "baseline.json"
$baselineLogPath = Join-Path $relayRoot "baseline-pytest.log"
$astraTurns = 0
$relayCycles = 0
$threadId = $null
$modelInvoked = $false
$calls = @()
$lastResult = ""
$previousTotals = @{}
if (Test-Path -LiteralPath $usagePath -PathType Leaf) {
    $previousUsage = Get-Content -LiteralPath $usagePath -Raw | ConvertFrom-Json
    if ($previousUsage.totals) {
        foreach ($key in @('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')) {
            $previousTotals[$key] = [long]$previousUsage.totals.$key
        }
    }
}

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
    throw "The previous Codex call failed. Inspect its log, then use -ResumeFailed for a fresh call or -NewSession."
}

if ($existingSession -and -not $NewSession -and $existingStatus -in @('active', 'failed')) {
    $existingPhase = if ($existingSession.phase) { [string]$existingSession.phase } else { "B" }
    if ($existingPhase -ne $Phase) {
        throw "The resumable Astra session belongs to phase $existingPhase. Use -NewSession for phase $Phase."
    }
}

if ($existingSession) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    Move-Item -LiteralPath $sessionStatePath -Destination (Join-Path $sessionArchiveRoot ("session-" + $stamp + "-" + $PID + ".json"))
}

function Save-SessionState([string]$Status) {
    Write-JsonAtomic -Path $sessionStatePath -Value ([ordered]@{
        version = 3
        status = $Status
        phase = $Phase
        thread_id = $script:threadId
        codex_home = $CodexHome
        current_head = (git rev-parse HEAD | Out-String).Trim()
        baseline_fingerprint = $baselineFingerprint
        astra_turns_this_run = $script:astraTurns
        relay_cycles_this_run = $script:relayCycles
        updated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    })
}

function Invoke-CodexStreaming(
    [string]$Executable,
    [string[]]$Arguments,
    [string]$JsonLog,
    [string]$StderrLog
) {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $jsonWriter = New-Object System.IO.StreamWriter($JsonLog, $false, $utf8)
    $stderrWriter = New-Object System.IO.StreamWriter($StderrLog, $false, $utf8)
    $savedErrorActionPreference = $ErrorActionPreference
    $observedThread = $null
    $exitCode = $null

    try {
        # Windows PowerShell 5.1 converts native stderr into NativeCommandError.
        # It is diagnostic output, not a failed Codex process; the exit code is authoritative.
        $ErrorActionPreference = "Continue"
        & $Executable @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $line = [string]$_.Exception.Message
                $stderrWriter.WriteLine($line)
                $stderrWriter.Flush()
                Write-Host $line -ForegroundColor DarkYellow
                return
            }

            $line = [string]$_
            $jsonWriter.WriteLine($line)
            $jsonWriter.Flush()
            Write-Host $line

            if (-not $observedThread -and $line.TrimStart().StartsWith("{")) {
                try { $event = $line | ConvertFrom-Json -ErrorAction Stop } catch { $event = $null }
                if ($event -and $event.type -eq "thread.started" -and $event.thread_id) {
                    $observedThread = [string]$event.thread_id
                    $script:threadId = $observedThread
                    $ErrorActionPreference = "Stop"
                    try { Save-SessionState -Status "active" } finally { $ErrorActionPreference = "Continue" }
                }
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedErrorActionPreference
        $jsonWriter.Dispose()
        $stderrWriter.Dispose()
    }

    return [pscustomobject]@{
        exit_code = $exitCode
        thread_id = $observedThread
    }
}

function Save-Handoff([string]$Result, [string]$NextDecision, [string[]]$Files = @()) {
    $changed = if ($Files.Count) { @($Files | Select-Object -First 30) } else { @((git status --porcelain --untracked-files=all | ForEach-Object { if ($_.Length -gt 3) { $_.Substring(3) } }) | Select-Object -First 30) }
    Write-JsonAtomic -Path $handoffPath -Value ([ordered]@{
        version = 1
        objective = $phaseSpec.mission
        phase = $Phase
        head = (git rev-parse HEAD | Out-String).Trim()
        last_result = $Result
        files = $changed
        baseline = @{ exit_code = $baseline.exit_code; log_path = $baseline.log_path }
        blocked = $null
        next_decision = $NextDecision
    })
}

function Save-UsageSummary {
    $totals = [ordered]@{}
    foreach ($key in @('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')) {
        $sum = [long]$previousTotals[$key]
        foreach ($call in $script:calls) { if ($call.usage) { $sum += [long]$call.usage[$key] } }
        $totals[$key] = $sum
    }
    Write-JsonAtomic -Path $usagePath -Value ([ordered]@{ version = 1; calls = $script:calls; totals = $totals })
    return $totals
}

function Invoke-HostValidation {
    if (-not (Test-Path -LiteralPath $validationPath -PathType Leaf)) { return $null }
    $request = Get-Content -LiteralPath $validationPath -Raw | ConvertFrom-Json
    if ([int]$request.version -ne 1 -or [string]$request.kind -ne 'full_pytest') {
        throw 'Unsupported host validation request. Only full_pytest is allowed.'
    }
    $validationLog = Join-Path $relayRoot 'validation-pytest.log'
    $started = Get-Date
    & $pythonExe -m pytest -q --tb=short *> $validationLog
    $validationExit = $LASTEXITCODE
    $result = [ordered]@{
        version = 1
        kind = 'full_pytest'
        head = (git rev-parse HEAD | Out-String).Trim()
        exit_code = $validationExit
        duration_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
        log_path = 'cache/astra-relay/validation-pytest.log'
        summary = @((Get-Content -LiteralPath $validationLog -Tail 8) | ForEach-Object { [string]$_ })
    }
    Write-JsonAtomic -Path (Join-Path $resultRoot 'validation-latest.json') -Value $result
    Move-Item -LiteralPath $validationPath -Destination (Join-Path $archiveRoot ('validation-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '.json'))
    return $result
}

function Invoke-AstraTurn([string]$Prompt, [string]$Reason) {
    $script:astraTurns++
    if ($script:astraTurns -gt $MaxAstraTurns) {
        throw "Astra turn budget exhausted ($MaxAstraTurns). Stopping before another model call."
    }

    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $base = "turn-{0:D2}-{1}" -f $script:astraTurns, $stamp
    $jsonLog = Join-Path $turnLogRoot ($base + ".jsonl")
    $stderrLog = Join-Path $turnLogRoot ($base + ".stderr.log")
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
        $codexArguments = @(
            "exec", "--json", "--strict-config", "--disable", "shell_snapshot",
            "--model", "gpt-6-astra", "-c", "model_reasoning_effort=$Reasoning", "--cd", $repo,
            "--output-last-message", $lastMessage
        )
        $codexArguments += $Prompt
        $processResult = Invoke-CodexStreaming -Executable $CodexExe -Arguments $codexArguments -JsonLog $jsonLog -StderrLog $stderrLog
        $code = $processResult.exit_code
        $observedThread = $processResult.thread_id
    } finally {
        if ($null -eq $savedHome) { Remove-Item Env:HOME -ErrorAction SilentlyContinue } else { $env:HOME = $savedHome }
        if ($null -eq $savedUserProfile) { Remove-Item Env:USERPROFILE -ErrorAction SilentlyContinue } else { $env:USERPROFILE = $savedUserProfile }
        if ($null -eq $savedGitConfigGlobal) { Remove-Item Env:GIT_CONFIG_GLOBAL -ErrorAction SilentlyContinue } else { $env:GIT_CONFIG_GLOBAL = $savedGitConfigGlobal }
    }
    $usage = Read-TurnUsage $jsonLog
    $script:calls += [ordered]@{ call = $script:astraTurns; reason = $Reason; thread_id = $observedThread; usage = $usage }
    $null = Save-UsageSummary
    $usageBrief = if ($usage) { $usage | ConvertTo-Json -Compress } else { 'unavailable' }
    Write-Host ("ASTRA CALL {0}: {1}; usage={2}" -f $script:astraTurns, $Reason, $usageBrief)
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
        stderr_log = $stderrLog
        last_message = $lastMessage
    }
}

$baselineBrief = (@($baseline.summary) | Select-Object -Last 4) -join ' | '
if ($baselineBrief.Length -gt 800) { $baselineBrief = $baselineBrief.Substring($baselineBrief.Length - 800) }

$initialPrompt = @"
You are the GPT-6 Astra root constructor for OCTOPUS phase $Phase. Read $($phaseSpec.documents) only when needed.
Mission: $($phaseSpec.mission)
HEAD: $head. Host baseline: exit=$($baseline.exit_code), log=$($baseline.log_path), summary=$baselineBrief.
Compact handoff: cache/astra-relay/handoff.json. Read it if present; inspect only relevant files.
Use targeted tests while editing. For a long full suite, atomically publish cache/astra-relay/validation.json with {"version":1,"kind":"full_pytest"}, then end the turn. The host runs it after you exit and writes cache/astra-relay/results/validation-latest.json.
For tested changes, publish checkpoint.json version 2 with request_id, base_head, message and exact changed paths, then end the turn. Host owns Git metadata writes. For a bounded Step task, publish one product_ticket and request.json from a clean tree, then end the turn.
Do not launch Step, poll, wait for validation, or run a full suite inside Astra. Each follow-up is a fresh Astra call with a compact disk handoff; no thread resume. Stop after the requested boundary.
"@

if (Test-Path -LiteralPath $handoffPath -PathType Leaf) {
    $oldHandoff = Get-Content -LiteralPath $handoffPath -Raw | ConvertFrom-Json
    if ($NewSession -or [string]$oldHandoff.phase -ne $Phase) {
        $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
        Move-Item -LiteralPath $handoffPath -Destination (Join-Path $sessionArchiveRoot ("handoff-" + $stamp + "-" + $PID + ".json"))
    }
}
if (-not (Test-Path -LiteralPath $handoffPath -PathType Leaf)) {
    Save-Handoff -Result 'ready' -NextDecision 'Start bounded phase work.'
}

$nextReason = 'phase work'
$nextPrompt = $initialPrompt
while ($true) {
    $pending = @($checkpointPath, $requestPath, $validationPath | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($pending.Count -gt 1) { throw 'Only one checkpoint, Step relay, or validation request may be pending.' }
    if (Test-Path -LiteralPath $checkpointPath -PathType Leaf) {
        $checkpointResultPath = Join-Path $resultRoot 'checkpoint-latest.json'
        & powershell -NoProfile -ExecutionPolicy Bypass -File $checkpointRunner -Repo $repo -CheckpointPath $checkpointPath -ResultPath $checkpointResultPath
        if ($LASTEXITCODE -ne 0) { throw "Host checkpoint failed. Request left in place: $checkpointPath" }
        $receipt = Get-Content -LiteralPath $checkpointResultPath -Raw | ConvertFrom-Json
        $lastResult = "Checkpoint $($receipt.commit): $($receipt.message)"
        Save-Handoff -Result $lastResult -NextDecision 'Review committed result or stop.' -Files @($receipt.paths)
        $nextReason = 'checkpoint review'
        $nextPrompt = "Fresh Astra review. Read cache/astra-relay/handoff.json and cache/astra-relay/results/checkpoint-latest.json. Continue phase $Phase only if needed."
    } elseif (Test-Path -LiteralPath $requestPath -PathType Leaf) {
        if ($relayCycles -ge $MaxRelayCycles) { throw "Relay cycle budget exhausted. Request untouched: $requestPath" }
        $relayCycles++
        $request = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
        if ([int]$request.version -ne 1 -or [string]$request.request_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { throw 'Invalid Step relay request.' }
        $requestId = [string]$request.request_id
        $plan = [string]$request.plan_path
        if (-not $plan) { throw 'Relay request missing plan_path.' }
        $hours = if ($null -ne $request.hours) { [double]$request.hours } else { 1.0 }
        if ($hours -le 0 -or $hours -gt 8) { throw 'Relay request hours must be >0 and <=8.' }
        if ((git status --porcelain --untracked-files=all | Out-String).Trim()) { throw 'Step relay requires a clean source tree.' }
        $archivedRequest = Join-Path $archiveRoot ($requestId + '.json')
        if (Test-Path -LiteralPath $archivedRequest) { throw "RequestId already consumed: $requestId" }
        Move-Item -LiteralPath $requestPath -Destination $archivedRequest
        $resultPath = "cache/astra-relay/results/$requestId.json"
        & powershell -NoProfile -ExecutionPolicy Bypass -File $runner -Plan $plan -Hours $hours -RequestId $requestId -ResultPath $resultPath
        $workerExit = $LASTEXITCODE
        $lastResult = "Step $requestId exited $workerExit; result=$resultPath"
        Save-Handoff -Result $lastResult -NextDecision 'Review worker result and exact diff; publish worker_commit checkpoint only if acceptable.'
        $nextReason = 'Step review'
        $nextPrompt = "Fresh Astra Step review. Read cache/astra-relay/handoff.json and $resultPath; inspect the referenced report and actual diff. If acceptable, publish checkpoint.json version 2 with kind=worker_commit, base_head, source_commit, message and exact paths. End the turn."
    } elseif (Test-Path -LiteralPath $validationPath -PathType Leaf) {
        $validationResult = Invoke-HostValidation
        $lastResult = "Full pytest exited $($validationResult.exit_code); result=cache/astra-relay/results/validation-latest.json"
        Save-Handoff -Result $lastResult -NextDecision 'Review concise host validation result and resolve actual failures.'
        $nextReason = 'validation review'
        $nextPrompt = 'Fresh Astra validation review. Read cache/astra-relay/handoff.json and cache/astra-relay/results/validation-latest.json. Inspect only relevant failing tests and bounded log excerpts.'
    } elseif ($modelInvoked) {
        if ((git status --porcelain --untracked-files=all | Out-String).Trim()) { throw 'Astra ended with uncheckpointed changes.' }
        Save-SessionState -Status 'completed'
        break
    }

    if ($astraTurns -ge $MaxAstraTurns) {
        Save-SessionState -Status 'active'
        Write-Host "Astra call budget reached. Handoff: $handoffPath" -ForegroundColor Yellow
        break
    }
    $turn = Invoke-AstraTurn -Prompt $nextPrompt -Reason $nextReason
    $threadId = $turn.thread_id
    $modelInvoked = $true
    Save-SessionState -Status 'active'
}

$totals = Save-UsageSummary
Write-Host ("Astra calls: {0}/{1}; totals={2}" -f $astraTurns, $MaxAstraTurns, ($totals | ConvertTo-Json -Compress))
Write-Host "Handoff: $handoffPath"
