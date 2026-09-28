param(
    [string]$CodexHome = "",
    [ValidateSet("B", "C", "D", "E", "F", "G")]
    [string]$Phase = "B",
    [int]$MaxAstraTurns = 2,
    [int]$MaxRelayCycles = 6,
    [int]$MaxHandoffChars = 6000,
    [int]$MaxSnapshotChars = 8000,
    [int]$MaxPreparedContextChars = 30000,
    [int]$MaxRunMinutes = 180,
    [int]$MaxRunTokens = 300000,
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
    $json = $Value | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Write-BoundedJsonAtomic([string]$Path, [object]$Value, [int]$MaxChars, [string]$Label) {
    $json = $Value | ConvertTo-Json -Depth 20
    if ($json.Length -gt $MaxChars) {
        throw "$Label exceeds its $MaxChars character limit: $($json.Length)."
    }
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $tmp = "$Path.tmp-$PID"
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
    return $json
}

function Limit-Text([object]$Value, [int]$MaxChars = 500) {
    $text = [string]$Value
    if ($text.Length -le $MaxChars) { return $text }
    return $text.Substring(0, $MaxChars) + "..."
}

function Get-LocalSha256([string]$Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose() }
    } finally { $sha.Dispose() }
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

function Get-AstraContinuation([string]$LastMessagePath) {
    if (-not (Test-Path -LiteralPath $LastMessagePath -PathType Leaf)) { return "" }
    foreach ($line in Get-Content -LiteralPath $LastMessagePath) {
        if ($line -match '^ASTRA_CONTINUE:\s*(\S.*)$') {
            if ($Matches[1].Length -gt 600) { throw 'ASTRA_CONTINUE exceeds its 600 character limit.' }
            return $Matches[1]
        }
    }
    return ""
}

function Get-AstraStatus([string]$LastMessagePath) {
    if (-not (Test-Path -LiteralPath $LastMessagePath -PathType Leaf)) { return '' }
    foreach ($line in Get-Content -LiteralPath $LastMessagePath) {
        if ($line -match '^ASTRA_STATUS:\s*(STABLE|BLOCKED)\s*$') { return $Matches[1] }
    }
    return ''
}

function Get-AstraState([string]$LastMessagePath) {
    if (-not (Test-Path -LiteralPath $LastMessagePath -PathType Leaf)) { return $null }
    foreach ($line in Get-Content -LiteralPath $LastMessagePath) {
        if ($line.StartsWith('ASTRA_STATE_JSON ')) {
            $json = $line.Substring(17)
            if ($json.Length -gt 4000) { throw 'ASTRA_STATE_JSON exceeds its 4000 character limit.' }
            return $json | ConvertFrom-Json -ErrorAction Stop
        }
    }
    return $null
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

function Resolve-StepRelayPlan([object]$Request, [string]$RequestId) {
    $productTicket = [string]$Request.product_ticket
    if (-not $productTicket) {
        throw 'Relay request requires a bounded product_ticket.'
    }

    $productTicketPath = if ([System.IO.Path]::IsPathRooted($productTicket)) {
        [System.IO.Path]::GetFullPath($productTicket)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $repo $productTicket))
    }
    $repoRoot = [System.IO.Path]::GetFullPath($repo).TrimEnd('\', '/') + '\'
    if (-not $productTicketPath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Product ticket must live under the OCTOPUS repository.'
    }
    if (-not (Test-Path -LiteralPath $productTicketPath -PathType Leaf)) {
        throw "Product ticket not found: $productTicketPath"
    }
    $ticket = Get-Content -LiteralPath $productTicketPath -Raw | ConvertFrom-Json
    foreach ($field in @('request_id', 'phase', 'base_head')) {
        if ([string]$ticket.$field -cne [string]$Request.$field) {
            throw "Product ticket $field does not match relay request."
        }
    }
    $currentHead = (& git -C $repo rev-parse HEAD 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]$ticket.base_head -cne $currentHead) {
        throw 'Product ticket base_head must match the current constructor HEAD.'
    }

    $allowedEditProperty = $ticket.PSObject.Properties['allowed_edit_paths']
    $allowedPathsProperty = $ticket.PSObject.Properties['allowed_paths']
    $allowedPaths = @(if ($allowedEditProperty) {
        $allowedEditProperty.Value | Where-Object { $null -ne $_ }
    } elseif ($allowedPathsProperty) {
        $allowedPathsProperty.Value | Where-Object { $null -ne $_ }
    })
    $testTargetsProperty = $ticket.PSObject.Properties['test_targets']
    $testTargets = @(if ($testTargetsProperty) {
        $testTargetsProperty.Value | Where-Object { $null -ne $_ }
    })
    $missingDispatchFields = @()
    if (-not $allowedPaths.Count) { $missingDispatchFields += 'allowed_edit_paths (or explicit canonical allowed_paths)' }
    if (-not $testTargets.Count) { $missingDispatchFields += 'test_targets' }
    if ($missingDispatchFields.Count) {
        throw ('Product ticket is not dispatchable to Step: explicit ' + ($missingDispatchFields -join ' and ') + ' required. starting_paths and likely_tests are discovery hints only.')
    }
    foreach ($path in $allowedPaths) {
        if (-not ($path -is [string]) -or -not $path.Trim()) {
            throw 'Product ticket allowed_edit_paths must contain non-empty repository-relative strings.'
        }
        $normalizedPath = $path.Replace('\', '/').Trim()
        $pathParts = @($normalizedPath.Split('/'))
        if ($normalizedPath.StartsWith('/') -or $pathParts[0].Contains(':') -or @($pathParts | Where-Object { $_ -in @('', '.', '..') }).Count) {
            throw "Product ticket contains an invalid allowed_edit_paths entry: $path"
        }
        if ($pathParts[0] -in @('.git', '.venv', 'cache', 'data', '.codex')) {
            throw "Product ticket edit path targets protected repository state: $path"
        }
        $resolved = [System.IO.Path]::GetFullPath((Join-Path $repo $normalizedPath))
        if (-not $resolved.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Product ticket edit path escapes the repository: $path"
        }
        $cursor = Split-Path -Parent $resolved
        while ($cursor.Length -ge $repo.Length) {
            if (Test-Path -LiteralPath $cursor) {
                $item = Get-Item -LiteralPath $cursor -Force
                if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                    throw "Product ticket edit path traverses a link: $path"
                }
            }
            if ($cursor -eq $repo) { break }
            $cursor = Split-Path -Parent $cursor
        }
        if (Test-Path -LiteralPath $resolved) {
            $item = Get-Item -LiteralPath $resolved -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Product ticket edit path is a link: $path"
            }
        }
    }
    foreach ($target in $testTargets) {
        if (-not ($target -is [string]) -or -not $target.Trim()) {
            throw 'Product ticket test_targets must contain non-empty strings executable in the Step sandbox.'
        }
        $targetPath = $target.Split('::', 2)[0].Replace('\', '/')
        if (-not $targetPath.StartsWith('tests/') -or -not $targetPath.EndsWith('.py')) {
            throw "Product ticket test target is not compatible with the Step pytest sandbox: $target"
        }
        if ([System.IO.Path]::IsPathRooted($targetPath)) {
            throw "Product ticket test target is not available in the Step sandbox: $target"
        }
        $resolvedTarget = [System.IO.Path]::GetFullPath((Join-Path $repo $targetPath))
        if (-not $resolvedTarget.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $resolvedTarget -PathType Leaf)) {
            throw "Product ticket test target is not available in the Step sandbox: $target"
        }
        $cursor = $resolvedTarget
        while ($cursor.Length -ge $repo.Length) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Product ticket test target traverses a link: $target"
            }
            if ($cursor -eq $repo) { break }
            $cursor = Split-Path -Parent $cursor
        }
    }

    $goalParts = @(
        "Request: $([string]$ticket.request_id)",
        "Base head: $([string]$ticket.base_head)",
        "Title: $([string]$ticket.title)",
        "Objective: $([string]$ticket.objective)",
        "Authorization: $([string]$ticket.authorization)"
    )
    if (@($ticket.architecture).Count) {
        $goalParts += 'Architecture:'
        $goalParts += @($ticket.architecture | ForEach-Object { '- ' + [string]$_ })
    }
    if ($ticket.scope) {
        $goalParts += 'Scope: ' + ($ticket.scope | ConvertTo-Json -Depth 10 -Compress)
    }
    if (@($ticket.work).Count) {
        $goalParts += 'Work:'
        $goalParts += @($ticket.work | ForEach-Object { '- ' + [string]$_ })
    }
    if (@($ticket.prohibitions).Count) {
        $goalParts += 'Prohibitions:'
        $goalParts += @($ticket.prohibitions | ForEach-Object { '- ' + [string]$_ })
    }

    $acceptanceContract = $ticket.acceptance_contract
    if (-not $acceptanceContract) {
        $acceptanceContract = [ordered]@{
            version = 1
            id = ([string]$ticket.request_id + '_tests')
            artifact_type = 'code'
            probe = [ordered]@{ kind = 'none' }
            must = @([ordered]@{
                id = 'tests_green'
                fact = 'tests.passed'
                op = 'equals'
                expected = $true
            })
        }
    }
    $workTicket = [ordered]@{
        goal = ($goalParts | Where-Object { $_ }) -join [Environment]::NewLine
        allowed_paths = $allowedPaths
        test_targets = $testTargets
        max_steps = if ($null -ne $ticket.max_steps) { [int]$ticket.max_steps } else { 20 }
        max_files_changed = if ($null -ne $ticket.max_files_changed) { [int]$ticket.max_files_changed } else { $allowedPaths.Count }
        max_lines_added = if ($null -ne $ticket.max_lines_added) { [int]$ticket.max_lines_added } else { 2500 }
        max_lines_deleted = if ($null -ne $ticket.max_lines_deleted) { [int]$ticket.max_lines_deleted } else { 2500 }
        noop_allowed = if ($null -ne $ticket.noop_allowed) { [bool]$ticket.noop_allowed } else { $false }
        acceptance_criteria = @($ticket.acceptance)
        acceptance_contract = $acceptanceContract
    }
    if ($workTicket.max_steps -lt 1 -or $workTicket.max_steps -gt 50 -or
        $workTicket.max_files_changed -lt 1 -or $workTicket.max_files_changed -gt [math]::Min(20, $allowedPaths.Count) -or
        $workTicket.max_lines_added -lt 1 -or $workTicket.max_lines_added -gt 5000 -or
        $workTicket.max_lines_deleted -lt 1 -or $workTicket.max_lines_deleted -gt 5000) {
        throw 'Product ticket exceeds bounded Step work limits.'
    }
    $normalized = [ordered]@{
        name = [string]$ticket.request_id
        policy = 'product_ticket'
        version = $ticket.version
        request_id = [string]$ticket.request_id
        phase = [string]$ticket.phase
        base_head = [string]$ticket.base_head
        objective = [string]$ticket.objective
        scope = $ticket.scope
        prohibitions = @($ticket.prohibitions)
        tickets = @($workTicket)
    }
    $normalizedPath = Join-Path $ticketRoot ($RequestId + '.json')
    Write-JsonAtomic -Path $normalizedPath -Value $normalized
    return ('cache/astra-tickets/' + $RequestId + '.json')
}

$repo = (git rev-parse --show-toplevel 2>$null | Out-String).Trim()
if (-not $repo) { throw "Run this launcher from inside the OCTOPUS repository." }
$repo = [System.IO.Path]::GetFullPath($repo).TrimEnd("\")
Set-Location $repo

if (-not $CodexHome) { $CodexHome = Join-Path $HOME ".codex-octopus" }
$CodexHome = [System.IO.Path]::GetFullPath($CodexHome).TrimEnd("\")
$env:CODEX_HOME = $CodexHome

if ($MaxAstraTurns -lt 1 -or $MaxAstraTurns -gt 2) {
    throw "MaxAstraTurns must be 1 or 2."
}
if ($MaxRelayCycles -lt 0 -or $MaxRelayCycles -gt 20) {
    throw "MaxRelayCycles must be between 0 and 20."
}
if ($MaxRunMinutes -lt 1 -or $MaxRunTokens -lt 1) { throw 'Run time and token budgets must be positive.' }

$phaseSpec = switch ($Phase) {
    "B" {
        [ordered]@{
            documents = @("docs/migrations/VIDEO_ENGINE_REMOVAL.md")
            mission = "Remove the legacy video engine while preserving shared consumers."
        }
    }
    "C" {
        [ordered]@{
            documents = @("docs/migrations/AGNES_VIDEO_REPLACEMENT.md")
            mission = "Implement the bounded Agnes video adapter and its deterministic mocked HTTP tests. Never perform a live generation or use paid credentials."
        }
    }
    "D" {
        [ordered]@{
            documents = @("docs/migrations/OCTOPUS_HERMES_REPLACEMENT_MATRIX.md", "docs/migrations/HERMES_COMPONENT_EXTRACTION.md")
            mission = "Implement the Hermes P0 tool-registry replacement only. Preserve OCTOPUS policies and do not introduce a second registry or broad plugin discovery."
        }
    }
    "E" {
        [ordered]@{
            documents = @("docs/migrations/CODEX_START_2026-09-27.md")
            mission = "Complete the necessary Hermes integration, remove verified blockers to correct OCTOPUS operation, and align the implementation with the supervised economic-workshop vision. Implement and validate corrections; do not stop after an audit report."
        }
    }
    "F" {
        [ordered]@{
            documents = @("docs/migrations/FINAL_READINESS_ANTI_CONTAMINATION.md")
            mission = "Verify final readiness for a supervised economic dry run and prevent historical business context from becoming an active cold-start objective. Fix only reproduced blockers, add a deterministic contamination canary, and report READY or NOT READY."
        }
    }
    "G" {
        [ordered]@{
            documents = @("docs/migrations/OPERATIONALIZATION.md")
            mission = "Make OCTOPUS operational through clean, stable runtime entry points without constructor phases or manual PowerShell choreography. Reuse existing runtime boundaries, fix only demonstrated blockers, preserve permissions, economy, and journal guarantees, and do not build the GUI or connect real accounts."
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
$snapshotPath = Join-Path $relayRoot "snapshot.json"
$contextMetricsPath = Join-Path $relayRoot "context-metrics.json"
$baselineStatePath = Join-Path $relayRoot "baseline.json"
$baselineLogPath = Join-Path $relayRoot "baseline-pytest.log"
$astraTurns = 0
$astraTurnsInCycle = 0
$relayCycles = 0
$threadId = $null
$modelInvoked = $false
$calls = @()
$lastResult = ""
$lastCheckpointSummary = $null
$lastValidationSummary = $null
$lastWorkerSummary = $null
$reviewContext = $null
$lastTurn = $null
$previousLifetimeTotals = @{}
if (Test-Path -LiteralPath $usagePath -PathType Leaf) {
    $previousUsage = Get-Content -LiteralPath $usagePath -Raw | ConvertFrom-Json
    $storedLifetime = if ($previousUsage.lifetime_totals) { $previousUsage.lifetime_totals } else { $previousUsage.totals }
    if ($storedLifetime) {
        foreach ($key in @('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')) {
            $previousLifetimeTotals[$key] = [long]$storedLifetime.$key
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
    $requirementsHashResult = Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256
    ([string]$requirementsHashResult.Hash).ToLowerInvariant()
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
    [string]$StderrLog,
    [string]$InputText = ""
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
        $handleLine = {
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
        if ($PSBoundParameters.ContainsKey('InputText')) {
            $InputText | & $Executable @Arguments 2>&1 | ForEach-Object $handleLine
        } else {
            & $Executable @Arguments 2>&1 | ForEach-Object $handleLine
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

function Get-ChangedPaths {
    $tracked = @(git diff --name-only --relative HEAD -- | Where-Object { $_ })
    $untracked = @(git ls-files --others --exclude-standard -- | Where-Object { $_ })
    return @($tracked + $untracked | Sort-Object -Unique | Select-Object -First 30)
}

function Get-CompactDiffStat([string]$Base = "", [string]$Target = "", [string[]]$Paths = @()) {
    $arguments = @("diff", "--stat", "--compact-summary")
    if ($Base -and $Target) { $arguments += @($Base, $Target) }
    $arguments += "--"
    if ($Paths.Count) { $arguments += $Paths }
    $lines = @(& git @arguments 2>$null | Select-Object -First 20 | ForEach-Object { Limit-Text $_ 300 })
    return $lines
}

function Save-Handoff(
    [string]$Result,
    [string]$NextDecision,
    [string[]]$Files = @(),
    [string[]]$Decisions = @(),
    [string[]]$Tests = @(),
    [string[]]$PendingHostRequests = @(),
    [string]$Blocked = "",
    [object]$State = $null
) {
    $changed = if ($Files.Count) { @($Files | Select-Object -First 30) } else { @(Get-ChangedPaths) }
    $previous = if (Test-Path -LiteralPath $handoffPath -PathType Leaf) {
        Get-Content -LiteralPath $handoffPath -Raw | ConvertFrom-Json
    } else { $null }
    $facts = if ($State) { @($State.facts) } else { @($previous.facts) }
    $hypotheses = if ($State) { @($State.hypotheses) } else { @($previous.hypotheses) }
    $inspectedByPath = [ordered]@{}
    foreach ($item in @($previous.inspected)) {
        if ($item -and $item.path) { $inspectedByPath[[string]$item.path] = $item }
    }
    foreach ($item in @($State.inspected)) {
        if (-not $item -or -not $item.path) { continue }
        $key = [string]$item.path
        if ($inspectedByPath.Contains($key) -and -not $item.refresh) {
            $inspectedByPath[$key] = [ordered]@{ path = $key; sha256 = $inspectedByPath[$key].sha256; summary = $item.summary }
        } else {
            $inspectedByPath[$key] = $item
        }
    }
    $inspected = @($inspectedByPath.Values)
    $remaining = if ($State) { @($State.remaining) } else { @($previous.remaining) }
    $tickets = if ($State) { @($State.tickets) } else { @($previous.tickets) }
    $stateTests = if ($State) { @($State.tests) } else { @($previous.tests) }
    $verifiedInspected = @()
    foreach ($item in @($inspected | Select-Object -First 12)) {
        $path = ([string]$item.path).Replace('\', '/')
        if ($path -notmatch '^[A-Za-z0-9_./-]+$' -or $path.StartsWith('/') -or @($path.Split('/') | Where-Object { $_ -in @('', '.', '..') }).Count) { continue }
        $fullPath = [System.IO.Path]::GetFullPath((Join-Path $repo $path))
        if (-not $fullPath.StartsWith($repo + '\', [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { continue }
        $hash = if ($item.refresh -or -not $item.sha256) { Get-LocalSha256 -Path $fullPath } else { [string]$item.sha256 }
        $verifiedInspected += [ordered]@{ path = $path; sha256 = $hash; summary = Limit-Text $item.summary 180 }
    }
    $handoff = [ordered]@{
        version = 3
        objective = Limit-Text $phaseSpec.mission 1200
        phase = $Phase
        head = (git rev-parse HEAD | Out-String).Trim()
        facts = @($facts | Select-Object -First 8 | ForEach-Object { Limit-Text $_ 200 })
        hypotheses = @($hypotheses | Select-Object -First 5 | ForEach-Object { Limit-Text $_ 200 })
        inspected = $verifiedInspected
        tickets = @($tickets | Select-Object -First 5 | ForEach-Object { Limit-Text $_ 120 })
        remaining = @($remaining | Select-Object -First 8 | ForEach-Object { Limit-Text $_ 200 })
        decisions = @($Decisions | Select-Object -First 12 | ForEach-Object { Limit-Text $_ 500 })
        files_modified = @($changed | ForEach-Object { Limit-Text $_ 300 })
        tests = @(@($Tests) + @($stateTests) | Select-Object -First 12 | ForEach-Object { Limit-Text $_ 200 })
        pending_host_requests = @($PendingHostRequests | Select-Object -First 5 | ForEach-Object { Limit-Text $_ 200 })
        last_result = if ($State -and $Result -match 'ASTRA_STATE_JSON') { '' } else { Limit-Text $Result 1000 }
        blocked = if ($Blocked) { Limit-Text $Blocked 800 } else { $null }
        next_decision = Limit-Text $NextDecision 600
    }
    $null = Write-BoundedJsonAtomic -Path $handoffPath -Value $handoff -MaxChars $MaxHandoffChars -Label "Astra handoff"
}

function New-CompactReceipt([object]$Receipt) {
    if (-not $Receipt) { return $null }
    return [ordered]@{
        request_id = Limit-Text $Receipt.request_id 100
        base_head = [string]$Receipt.base_head
        commit = [string]$Receipt.commit
        kind = Limit-Text $Receipt.kind 40
        message = Limit-Text $Receipt.message 200
        paths = @($Receipt.paths | Select-Object -First 30 | ForEach-Object { Limit-Text $_ 300 })
    }
}

function New-CompactWorkerSummary([object]$Summary) {
    if (-not $Summary) { return $null }
    return [ordered]@{
        status = Limit-Text $Summary.status 60
        base_head = [string]$Summary.base_head
        source_commit = [string]$Summary.source_commit
        changed_paths = @($Summary.changed_paths | Select-Object -First 20 | ForEach-Object { Limit-Text $_ 240 })
        diff_stat = @($Summary.diff_stat | Select-Object -First 12 | ForEach-Object { Limit-Text $_ 240 })
        tests = @($Summary.tests | Select-Object -First 12 | ForEach-Object { Limit-Text $_ 240 })
        summary_truncated = $true
    }
}

function Assert-ValidWorkerReviewReceipt(
    [object]$Receipt,
    [int]$WorkerExit,
    [string]$RequestId,
    [string]$Repository
) {
    if ($WorkerExit -ne 0) {
        throw "External worker failed with code $WorkerExit. Astra review was not started."
    }
    if (-not $Receipt -or [int]$Receipt.version -ne 1) {
        throw 'External worker receipt is missing or unsupported. Astra review was not started.'
    }
    if ([string]$Receipt.request_id -cne $RequestId) {
        throw 'External worker receipt request_id mismatch. Astra review was not started.'
    }
    if ([string]$Receipt.status -ne 'completed' -or [int]$Receipt.exit_code -ne 0) {
        throw 'External worker receipt does not record a successful completion. Astra review was not started.'
    }

    $summary = $Receipt.worker_summary
    if (-not $summary) {
        throw 'External worker receipt has no worker_summary. Astra review was not started.'
    }
    $baseHead = ([string]$summary.base_head).Trim().ToLowerInvariant()
    $sourceCommit = ([string]$summary.source_commit).Trim().ToLowerInvariant()
    if ($baseHead -notmatch '^[0-9a-f]{40}$' -or $sourceCommit -notmatch '^[0-9a-f]{40}$') {
        throw 'External worker summary requires full base_head and source_commit SHAs. Astra review was not started.'
    }

    $declaredPaths = @()
    foreach ($value in @($summary.changed_paths)) {
        $path = ([string]$value).Replace('\', '/').Trim()
        $parts = @($path.Split('/'))
        if (-not $path -or $path.StartsWith('/') -or $parts[0].Contains(':') -or @($parts | Where-Object { $_ -in @('', '.', '..') }).Count) {
            throw 'External worker summary contains an invalid changed_paths entry. Astra review was not started.'
        }
        $declaredPaths += $path
    }
    if (-not $declaredPaths.Count) {
        throw 'External worker summary requires non-empty changed_paths. Astra review was not started.'
    }
    $uniqueDeclaredPaths = @($declaredPaths | Sort-Object -Unique)
    if ($uniqueDeclaredPaths.Count -ne $declaredPaths.Count) {
        throw 'External worker summary contains duplicate changed_paths. Astra review was not started.'
    }
    if (-not @($summary.tests).Count) {
        throw 'External worker summary requires a non-empty tests summary. Astra review was not started.'
    }

    $tickets = @($summary.tickets)
    if ($tickets.Count -ne 1) {
        throw 'External worker summary requires exactly one bounded ticket result. Astra review was not started.'
    }
    $ticket = $tickets[0]
    if ([string]$ticket.status -ne 'done' -or -not [bool]$ticket.tests_passed -or [string]$ticket.gate_status -ne 'ACCEPTED') {
        throw 'External worker ticket is not done with passing tests and an ACCEPTED gate. Astra review was not started.'
    }
    if (([string]$ticket.commit).Trim().ToLowerInvariant() -ne $sourceCommit) {
        throw 'External worker ticket commit differs from source_commit. Astra review was not started.'
    }
    $ticketPaths = @($ticket.changed_paths | ForEach-Object { ([string]$_).Replace('\', '/').Trim() } | Sort-Object -Unique)
    if (@(Compare-Object -ReferenceObject $uniqueDeclaredPaths -DifferenceObject $ticketPaths).Count) {
        throw 'External worker ticket paths differ from worker_summary.changed_paths. Astra review was not started.'
    }

    $currentHead = (& git -C $Repository rev-parse HEAD 2>$null | Out-String).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $currentHead -ne $baseHead) {
        throw 'External worker base_head differs from the current constructor HEAD. Astra review was not started.'
    }
    $resolvedCommit = (& git -C $Repository rev-parse "$sourceCommit^{commit}" 2>$null | Out-String).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $resolvedCommit -ne $sourceCommit) {
        throw 'External worker source_commit does not resolve exactly. Astra review was not started.'
    }
    $ancestry = ((& git -C $Repository rev-list --parents -n 1 $sourceCommit 2>$null | Out-String).Trim() -split '\s+')
    if ($LASTEXITCODE -ne 0 -or $ancestry.Count -ne 2 -or $ancestry[1].ToLowerInvariant() -ne $baseHead) {
        throw 'External worker source_commit is not a direct child of base_head. Astra review was not started.'
    }
    $actualPaths = @(& git -C $Repository diff --no-renames --name-only --relative $baseHead $sourceCommit -- 2>$null | Where-Object { $_ } | Sort-Object -Unique)
    if ($LASTEXITCODE -ne 0 -or @(Compare-Object -ReferenceObject $uniqueDeclaredPaths -DifferenceObject $actualPaths).Count) {
        throw 'External worker changed_paths differ from the source commit diff. Astra review was not started.'
    }
    return $summary
}

function Write-AstraContext([string]$TaskPrompt) {
    $handoffJson = if (Test-Path -LiteralPath $handoffPath -PathType Leaf) { Get-Content -LiteralPath $handoffPath -Raw } else { '{}' }
    if ($handoffJson.Length -gt $MaxHandoffChars) { throw "Astra handoff exceeds its $MaxHandoffChars character limit." }
    $branch = (git branch --show-current | Out-String).Trim()
    $currentHead = (git rev-parse HEAD | Out-String).Trim()
    $packet = [ordered]@{
        version = 3
        phase = $Phase
        objective = Limit-Text $phaseSpec.mission 1200
        head = $currentHead
        branch = $branch
        handoff = $handoffJson | ConvertFrom-Json
        review = $script:reviewContext
        step_summary = $script:lastWorkerSummary
        validation = $script:lastValidationSummary
    }
    $inspectedStatus = @()
    foreach ($item in @($packet.handoff.inspected)) {
        $path = ([string]$item.path).Replace('\', '/')
        if ($path -notmatch '^[A-Za-z0-9_./-]+$' -or $path.StartsWith('/') -or @($path.Split('/') | Where-Object { $_ -in @('', '.', '..') }).Count) { continue }
        $fullPath = [System.IO.Path]::GetFullPath((Join-Path $repo $path))
        if (-not $fullPath.StartsWith($repo + '\', [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { continue }
        $current = Get-LocalSha256 -Path $fullPath
        $inspectedStatus += [ordered]@{ path = $path; changed_since_inspection = $current -ne [string]$item.sha256 }
    }
    $packet.inspected_status = $inspectedStatus
    if ($script:astraTurns -eq 0) {
        $packet.baseline = [ordered]@{
            status = if ([int]$baseline.exit_code -eq 0) { 'passed' } else { 'failed' }
            exit_code = [int]$baseline.exit_code
            duration_seconds = $baseline.duration_seconds
        }
    }
    $packetJson = Write-BoundedJsonAtomic -Path $snapshotPath -Value $packet -MaxChars $MaxSnapshotChars -Label 'Astra context packet'
    $preparedPrompt = "$TaskPrompt`n`nHOST_CONTEXT_JSON`n$packetJson"
    $agentsChars = (Get-Content -LiteralPath (Join-Path $repo 'AGENTS.md') -Raw).Length
    $estimatedInputChars = $preparedPrompt.Length + $agentsChars
    if ($estimatedInputChars -gt $MaxPreparedContextChars) {
        throw "Prepared Astra context exceeds its $MaxPreparedContextChars character limit: $estimatedInputChars."
    }
    Write-JsonAtomic -Path $contextMetricsPath -Value ([ordered]@{
        version = 2
        estimated_prompt_chars = $preparedPrompt.Length
        implicit_instruction_chars = $agentsChars
        estimated_input_chars = $estimatedInputChars
        packet_chars = $packetJson.Length
        handoff_chars = $handoffJson.Length
        call_kind = if ($script:reviewContext) { 'review' } elseif ($script:astraTurns) { 'continuation' } else { 'initial' }
    })
    return $preparedPrompt
}

function Save-UsageSummary {
    $runTotals = [ordered]@{}
    $lifetimeTotals = [ordered]@{}
    foreach ($key in @('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')) {
        $runSum = 0
        foreach ($call in $script:calls) { if ($call.usage) { $runSum += [long]$call.usage[$key] } }
        $runTotals[$key] = $runSum
        $lifetimeTotals[$key] = [long]$previousLifetimeTotals[$key] + $runSum
    }
    Write-JsonAtomic -Path $usagePath -Value ([ordered]@{
        version = 2
        calls = $script:calls
        run_totals = $runTotals
        lifetime_totals = $lifetimeTotals
    })
    return [ordered]@{ run_totals = $runTotals; lifetime_totals = $lifetimeTotals }
}

function Get-PytestCounts([string[]]$Lines) {
    $text = $Lines -join ' '
    $counts = [ordered]@{}
    foreach ($name in @('passed', 'failed', 'skipped')) {
        $match = [regex]::Match($text, "(?<!\d)(\d+)\s+$name\b")
        $counts[$name] = if ($match.Success) { [int]$match.Groups[1].Value } else { 0 }
    }
    return $counts
}

function Invoke-HostValidation {
    if (-not (Test-Path -LiteralPath $validationPath -PathType Leaf)) { return $null }
    $request = Get-Content -LiteralPath $validationPath -Raw | ConvertFrom-Json
    if ([int]$request.version -ne 1 -or [string]$request.kind -ne 'full_pytest') {
        throw 'Unsupported host validation request. Only full_pytest is allowed.'
    }
    $currentHead = (git rev-parse HEAD | Out-String).Trim()
    $previousPath = Join-Path $resultRoot 'validation-latest.json'
    if (Test-Path -LiteralPath $previousPath -PathType Leaf) {
        $previous = Get-Content -LiteralPath $previousPath -Raw | ConvertFrom-Json
        if ([string]$previous.head -eq $currentHead -and -not (git status --porcelain --untracked-files=all | Out-String).Trim()) {
            Move-Item -LiteralPath $validationPath -Destination (Join-Path $archiveRoot ('validation-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '.json'))
            return $previous
        }
    }
    $validationLog = Join-Path $relayRoot 'validation-pytest.log'
    $started = Get-Date
    & $pythonExe -m pytest -q --tb=short *> $validationLog
    $validationExit = $LASTEXITCODE
    $tail = @(Get-Content -LiteralPath $validationLog -Tail 20)
    $counts = Get-PytestCounts -Lines $tail
    $failures = @(Select-String -LiteralPath $validationLog -Pattern '^(FAILED|ERROR)\s+' | Select-Object -First 8 | ForEach-Object { Limit-Text $_.Line 240 })
    $result = [ordered]@{
        version = 1
        kind = 'full_pytest'
        head = (git rev-parse HEAD | Out-String).Trim()
        status = if ($validationExit -eq 0) { 'passed' } else { 'failed' }
        exit_code = $validationExit
        duration_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
        log_path = 'cache/astra-relay/validation-pytest.log'
        tests = $counts
        failing_tests = $failures
        output_truncated = $true
    }
    Write-JsonAtomic -Path (Join-Path $resultRoot 'validation-latest.json') -Value $result
    Move-Item -LiteralPath $validationPath -Destination (Join-Path $archiveRoot ('validation-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '.json'))
    return $result
}

if (-not $lastValidationSummary) {
    $previousValidationPath = Join-Path $resultRoot 'validation-latest.json'
    if (Test-Path -LiteralPath $previousValidationPath -PathType Leaf) {
        try {
            $previousValidation = Get-Content -LiteralPath $previousValidationPath -Raw | ConvertFrom-Json -ErrorAction Stop
            $lastValidationSummary = [ordered]@{
                kind = Limit-Text $previousValidation.kind 40
                head = [string]$previousValidation.head
                status = Limit-Text $previousValidation.status 20
                exit_code = [int]$previousValidation.exit_code
                duration_seconds = $previousValidation.duration_seconds
                tests = $previousValidation.tests
                failing_tests = @($previousValidation.failing_tests | Select-Object -First 8 | ForEach-Object { Limit-Text $_ 240 })
                output_truncated = $true
            }
        } catch {
            $lastValidationSummary = $null
        }
    }
}

function Invoke-AstraTurn([string]$Prompt, [string]$Reason) {
    $script:astraTurns++
    $script:astraTurnsInCycle++
    if ($script:astraTurnsInCycle -gt $MaxAstraTurns) {
        throw "Astra turn budget exhausted ($MaxAstraTurns). Stopping before another model call."
    }

    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $base = "turn-{0:D2}-{1}" -f $script:astraTurns, $stamp
    $jsonLog = Join-Path $turnLogRoot ($base + ".jsonl")
    $stderrLog = Join-Path $turnLogRoot ($base + ".stderr.log")
    $lastMessage = Join-Path $turnLogRoot ($base + "-last.txt")

    Write-Host ""
    Write-Host ("=== GPT-6 ASTRA TURN {0}/{1} ===" -f $script:astraTurnsInCycle, $MaxAstraTurns) -ForegroundColor Magenta

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
            "exec", "--json", "--ephemeral", "--strict-config", "--disable", "shell_snapshot",
            "--model", "gpt-6-astra", "-c", "model_reasoning_effort=$Reasoning", "--cd", $repo,
            "--output-last-message", $lastMessage
        )
        $codexArguments += "-"
        $processResult = Invoke-CodexStreaming -Executable $CodexExe -Arguments $codexArguments -JsonLog $jsonLog -StderrLog $stderrLog -InputText $Prompt
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

$contextRules = @"
You are the architecture and review owner. Explore this local repository directly with bounded PowerShell commands. You may inspect several relevant files per turn using rg, bounded Get-Content excerpts, git status/log/diff, and short local pytest targets. Treat zero or multiple search matches as facts to investigate, never as a fatal discovery error. Limit each command output to 200 lines or 20000 characters; do not read large files whole. Do not repeat unchanged excerpts already recorded in the handoff. Never use network, external accounts, economic runtime, push or merge.
When the objective, exact repository-relative edit paths, test targets and constraints are clear, publish one bounded product_ticket and cache/astra-relay/request.json, then end the turn. The host validates paths and tests before Step runs. Do not run Step yourself. After Step, inspect its compact receipt and the relevant diff, then publish a checkpoint request if accepted. The host handles Git writes and full pytest.
For a verified terminal state, end with ASTRA_STATUS: STABLE or ASTRA_STATUS: BLOCKED. Otherwise the host continues automatically; ASTRA_CONTINUE: followed by one compact next action is optional. Before a terminal marker or Step request, write one line ASTRA_STATE_JSON {"facts":[],"hypotheses":[],"inspected":[{"path":"repo/relative.py","summary":"short fact"}],"tests":[],"tickets":[],"remaining":[]}. Set refresh=true on an inspected entry only when you reread a changed file. Keep the state under 4000 characters. The host hashes inspected files and returns changed_since_inspection in the next packet. Do not reread unchanged files. The host starts fresh calls across mini-sessions up to MaxRelayCycles.
"@

function New-AstraTaskPrompt([string]$Task) {
    return $Task + [Environment]::NewLine + [Environment]::NewLine + $contextRules
}

$initialPrompt = New-AstraTaskPrompt "Work on the phase $Phase objective in HOST_CONTEXT_JSON. Inspect the repository directly, then delegate bounded implementation to Step and review it."

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
$runStarted = Get-Date
while ($true) {
    $pending = @($checkpointPath, $requestPath, $validationPath | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($pending.Count -gt 1) { throw 'Only one checkpoint, Step relay, or validation request may be pending.' }
    if (Test-Path -LiteralPath $checkpointPath -PathType Leaf) {
        $checkpointResultPath = Join-Path $resultRoot 'checkpoint-latest.json'
        & powershell -NoProfile -ExecutionPolicy Bypass -File $checkpointRunner -Repo $repo -CheckpointPath $checkpointPath -ResultPath $checkpointResultPath
        if ($LASTEXITCODE -ne 0) { throw "Host checkpoint failed. Request left in place: $checkpointPath" }
        $receipt = Get-Content -LiteralPath $checkpointResultPath -Raw | ConvertFrom-Json
        $lastCheckpointSummary = New-CompactReceipt $receipt
        $reviewContext = [ordered]@{
            kind = 'checkpoint'
            base_head = [string]$receipt.base_head
            head = [string]$receipt.commit
            changed_paths = @($receipt.paths)
            diff_stat = @(Get-CompactDiffStat -Base $receipt.base_head -Target $receipt.commit -Paths @($receipt.paths))
            review_policy = 'Inspect one changed-file diff at a time; do not reload phase documents.'
        }
        $lastResult = "Checkpoint $($receipt.commit): $($receipt.message)"
        $astraState = if ($script:lastTurn) { Get-AstraState -LastMessagePath $script:lastTurn.last_message } else { $null }
        Save-Handoff -Result $lastResult -NextDecision 'Review only the committed changed paths, then stop unless a reproduced defect requires action.' -Files @($receipt.paths) -Decisions @('Host committed the exact declared paths.') -State $astraState
        $nextReason = 'checkpoint review'
        $nextPrompt = New-AstraTaskPrompt "Fresh checkpoint review for phase $Phase. The compact receipt and review range are in the inline snapshot. Inspect the actual diff only for the listed paths, one file at a time. Accept and conclude if correct; act only on a concrete defect."
    } elseif (Test-Path -LiteralPath $requestPath -PathType Leaf) {
        if ($relayCycles -ge $MaxRelayCycles) { throw "Relay cycle budget exhausted. Request untouched: $requestPath" }
        $relayCycles++
        $request = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
        if ([int]$request.version -ne 1 -or [string]$request.request_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { throw 'Invalid Step relay request.' }
        $requestId = [string]$request.request_id
        $plan = Resolve-StepRelayPlan -Request $request -RequestId $requestId
        $hours = if ($null -ne $request.hours) { [double]$request.hours } else { 1.0 }
        if ($hours -le 0 -or $hours -gt 8) { throw 'Relay request hours must be >0 and <=8.' }
        if ((git status --porcelain --untracked-files=all | Out-String).Trim()) { throw 'Step relay requires a clean source tree.' }
        $archivedRequest = Join-Path $archiveRoot ($requestId + '.json')
        if (Test-Path -LiteralPath $archivedRequest) { throw "RequestId already consumed: $requestId" }
        Move-Item -LiteralPath $requestPath -Destination $archivedRequest
        $resultPath = "cache/astra-relay/results/$requestId.json"
        & powershell -NoProfile -ExecutionPolicy Bypass -File $runner -Plan $plan -Hours $hours -RequestId $requestId -ResultPath $resultPath
        $workerExit = $LASTEXITCODE
        $workerReceipt = Get-Content -LiteralPath (Join-Path $repo $resultPath) -Raw | ConvertFrom-Json
        $validatedWorkerSummary = Assert-ValidWorkerReviewReceipt -Receipt $workerReceipt -WorkerExit $workerExit -RequestId $requestId -Repository $repo
        $lastWorkerSummary = New-CompactWorkerSummary $validatedWorkerSummary
        $workerPaths = @($validatedWorkerSummary.changed_paths)
        $reviewContext = [ordered]@{
            kind = 'step'
            base_head = [string]$validatedWorkerSummary.base_head
            head = [string]$validatedWorkerSummary.source_commit
            changed_paths = $workerPaths
            diff_stat = @($validatedWorkerSummary.diff_stat)
            review_policy = 'Use the compact worker receipt; inspect one changed-file diff at a time. Do not read the raw report or worker log.'
        }
        $lastResult = "Step $requestId exited $workerExit; result=$resultPath"
        $astraState = if ($script:lastTurn) { Get-AstraState -LastMessagePath $script:lastTurn.last_message } else { $null }
        Save-Handoff -Result $lastResult -NextDecision 'Review the compact worker receipt and listed paths; publish worker_commit checkpoint only if acceptable.' -Files $workerPaths -Tests @($validatedWorkerSummary.tests) -State $astraState
        $nextReason = 'Step review'
        $nextPrompt = New-AstraTaskPrompt "Fresh Step review for phase $Phase. Use the compact worker receipt in HOST_CONTEXT_JSON; do not open its raw report, evidence or log. Inspect the actual diff for each listed path. If acceptable, publish checkpoint.json version 2 with kind=worker_commit, base_head, source_commit, message and exact paths, then end the call."
    } elseif (Test-Path -LiteralPath $validationPath -PathType Leaf) {
        $validationResult = Invoke-HostValidation
        $lastValidationSummary = [ordered]@{
            kind = 'full_pytest'
            head = [string]$validationResult.head
            status = [string]$validationResult.status
            exit_code = [int]$validationResult.exit_code
            duration_seconds = $validationResult.duration_seconds
            tests = $validationResult.tests
            failing_tests = @($validationResult.failing_tests)
            output_truncated = $true
        }
        $reviewContext = [ordered]@{
            kind = 'validation'
            head = [string]$validationResult.head
            status = [string]$validationResult.status
            failing_tests = @($validationResult.failing_tests)
            review_policy = 'A passing compact receipt is sufficient. On failure inspect only named tests and bounded source excerpts.'
        }
        $lastResult = "Full pytest exited $($validationResult.exit_code); result=cache/astra-relay/results/validation-latest.json"
        $astraState = if ($script:lastTurn) { Get-AstraState -LastMessagePath $script:lastTurn.last_message } else { $null }
        Save-Handoff -Result $lastResult -NextDecision 'Conclude on pass; on failure resolve only the named failing tests.' -Tests @("full pytest: $($validationResult.status), exit $($validationResult.exit_code), $($validationResult.duration_seconds)s") -State $astraState
        $nextReason = 'validation review'
        $nextPrompt = New-AstraTaskPrompt 'Fresh validation review. The bounded host receipt is in the inline snapshot. If it passed, conclude without reading any log or documentation. If it failed, inspect only the named failing tests and the smallest relevant source excerpt.'
    } elseif ($modelInvoked) {
        if ((git status --porcelain --untracked-files=all | Out-String).Trim()) { throw 'Astra ended with uncheckpointed changes.' }
        $status = if ($script:lastTurn) { Get-AstraStatus -LastMessagePath $script:lastTurn.last_message } else { '' }
        $forcedContinuation = ''
        if ($status -eq 'STABLE') {
            $currentHead = (git rev-parse HEAD | Out-String).Trim()
            if ($script:lastWorkerSummary -and [string]$script:lastWorkerSummary.source_commit -ne $currentHead) {
                $status = ''
                $forcedContinuation = 'Review the Step diff and publish its checkpoint before STABLE.'
            } elseif (-not $script:lastValidationSummary -or [string]$script:lastValidationSummary.head -ne $currentHead) {
                Write-JsonAtomic -Path $validationPath -Value ([ordered]@{ version = 1; kind = 'full_pytest' })
                $lastMessage = Get-Content -LiteralPath $script:lastTurn.last_message -Raw
                $astraState = Get-AstraState -LastMessagePath $script:lastTurn.last_message
                Save-Handoff -Result $lastMessage -NextDecision 'Host full pytest before STABLE.' -State $astraState
                continue
            } elseif ([string]$script:lastValidationSummary.status -ne 'passed') {
                $status = ''
                $forcedContinuation = 'Full pytest failed; resolve the reported failures before STABLE.'
            }
        }
        if ($status) {
            $lastMessage = Get-Content -LiteralPath $script:lastTurn.last_message -Raw
            $astraState = Get-AstraState -LastMessagePath $script:lastTurn.last_message
            Save-Handoff -Result $lastMessage -NextDecision $status -State $astraState
            $terminalStatus = if ($status -eq 'BLOCKED') { 'blocked' } else { 'completed' }
            Save-SessionState -Status $terminalStatus
            break
        }
        $continuation = if ($script:lastTurn) { Get-AstraContinuation -LastMessagePath $script:lastTurn.last_message } else { "" }
        if ($forcedContinuation) { $continuation = $forcedContinuation }
        if (-not $continuation) { $continuation = 'Continue the phase objective from the compact handoff.' }
        $lastMessage = Get-Content -LiteralPath $script:lastTurn.last_message -Raw
        $astraState = Get-AstraState -LastMessagePath $script:lastTurn.last_message
        Save-Handoff -Result $lastMessage -NextDecision $continuation -State $astraState
        $reviewContext = $null
        $nextReason = 'bounded continuation'
        $nextPrompt = New-AstraTaskPrompt 'Continue the next action in HOST_CONTEXT_JSON. Use the handoff to avoid repeated reads.'
    }

    if ($astraTurnsInCycle -ge $MaxAstraTurns) {
        if ($nextReason -eq 'Step review') {
            $astraTurnsInCycle = 0
        } else {
            if ($relayCycles -ge $MaxRelayCycles) {
                Save-SessionState -Status 'active'
                Write-Host "Relay cycle budget reached. Handoff: $handoffPath" -ForegroundColor Yellow
                break
            }
            $relayCycles++
            $astraTurnsInCycle = 0
        }
    }
    $usedTokens = 0
    foreach ($call in $script:calls) {
        if ($call.usage) { $usedTokens += [long]$call.usage.input_tokens + [long]$call.usage.output_tokens }
    }
    if ($usedTokens -ge $MaxRunTokens -or ((Get-Date) - $runStarted).TotalMinutes -ge $MaxRunMinutes) {
        Save-SessionState -Status 'active'
        Write-Host "Astra run budget reached. Handoff: $handoffPath" -ForegroundColor Yellow
        break
    }
    $preparedPrompt = Write-AstraContext -TaskPrompt $nextPrompt
    $turn = Invoke-AstraTurn -Prompt $preparedPrompt -Reason $nextReason
    $lastTurn = $turn
    $threadId = $turn.thread_id
    $modelInvoked = $true
    Save-SessionState -Status 'active'
}

$usageTotals = Save-UsageSummary
Write-Host ("Astra calls: {0}; relay cycles: {1}/{2}; run_totals={3}; lifetime_totals={4}" -f $astraTurns, $relayCycles, $MaxRelayCycles, ($usageTotals.run_totals | ConvertTo-Json -Compress), ($usageTotals.lifetime_totals | ConvertTo-Json -Compress))
Write-Host "Handoff: $handoffPath"
