param(
    [string]$CodexHome = "",
    [ValidateSet("B", "C", "D", "E", "F", "G")]
    [string]$Phase = "B",
    [int]$MaxAstraTurns = 2,
    [int]$MaxRelayCycles = 6,
    [int]$MaxHandoffChars = 6000,
    [int]$MaxSnapshotChars = 8000,
    [int]$MaxManifestChars = 6000,
    [int]$MaxHostPrepareChars = 4200,
    [int]$MaxPreparedContextChars = 30000,
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

function Resolve-StepRelayPlan([object]$Request, [string]$RequestId) {
    $productTicket = [string]$Request.product_ticket
    if (-not $productTicket) {
        $legacyPlan = [string]$Request.plan_path
        if (-not $legacyPlan) { throw 'Relay request missing product_ticket or plan_path.' }
        return $legacyPlan
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

    $allowedPaths = @($ticket.allowed_paths | Where-Object { $null -ne $_ })
    if (-not $allowedPaths.Count) {
        $allowedPaths = @($ticket.starting_paths | Where-Object { $null -ne $_ })
    }
    if (-not $allowedPaths.Count) {
        throw 'Product ticket requires allowed_paths or starting_paths.'
    }
    $testTargets = @($ticket.test_targets | Where-Object { $null -ne $_ })
    if (-not $testTargets.Count) {
        $testTargets = @($ticket.likely_tests | Where-Object { $null -ne $_ })
    }
    if (-not $testTargets.Count) {
        throw 'Product ticket requires test_targets or likely_tests.'
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

$phaseSearches = switch ($Phase) {
    "B" { @('video_engine|VideoEngine', 'legacy.{0,20}video', 'video.{0,20}engine') }
    "C" { @('Agnes|agnes', 'video.{0,20}adapter', 'video.{0,20}provider') }
    "D" { @('Hermes|hermes', 'tool.?registry|ToolRegistry', 'tool.{0,20}adapter') }
    "E" { @('Hermes|hermes', 'development\.task', 'GuardedComputeManager') }
    "F" { @('cold.?start', 'contamination', 'readiness') }
    "G" { @('entry.?point', 'operational', 'startup|supervisor') }
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
$contextManifestPath = Join-Path $relayRoot "context-manifest.json"
$contextMetricsPath = Join-Path $relayRoot "context-metrics.json"
$baselineStatePath = Join-Path $relayRoot "baseline.json"
$baselineLogPath = Join-Path $relayRoot "baseline-pytest.log"
$astraTurns = 0
$relayCycles = 0
$threadId = $null
$modelInvoked = $false
$calls = @()
$lastResult = ""
$lastCheckpointSummary = $null
$lastValidationSummary = $null
$lastWorkerSummary = $null
$reviewContext = $null
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

function Invoke-BoundedRg([string]$Pattern, [string[]]$Roots, [int]$MaxResults = 4) {
    $searchRoots = @($Roots | Where-Object { Test-Path -LiteralPath (Join-Path $repo $_) })
    if (-not $searchRoots.Count -or -not (Get-Command rg -ErrorAction SilentlyContinue)) { return @() }
    $arguments = @(
        '--line-number', '--no-heading', '--color', 'never', '--smart-case',
        '--glob', '*.py', '--glob', '*.ps1', '--glob', '*.toml', '--glob', '*.json',
        '--glob', '!cache/**', '--glob', '!data/**', '--', $Pattern
    ) + $searchRoots
    return @(& rg @arguments 2>$null | Select-Object -First $MaxResults | ForEach-Object { Limit-Text $_ 220 })
}

function Find-AssociatedTests([string[]]$SourcePaths, [string[]]$SearchPatterns) {
    $testFiles = if ((Test-Path -LiteralPath (Join-Path $repo 'tests')) -and (Get-Command rg -ErrorAction SilentlyContinue)) {
        @(& rg --files tests --glob '*.py' 2>$null | ForEach-Object { ([string]$_).Replace('\', '/') } | Sort-Object -Unique)
    } else {
        @(git ls-files -- tests | Where-Object { $_ -like '*.py' } | Sort-Object -Unique)
    }
    if (-not $testFiles.Count) { return @() }
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($path in @($SourcePaths)) {
        if ($path -like 'tests/*' -and $testFiles -contains $path -and -not $found.Contains($path)) { [void]$found.Add($path) }
        $stem = [System.IO.Path]::GetFileNameWithoutExtension($path)
        if (-not $stem) { continue }
        foreach ($test in @($testFiles | Where-Object { $_ -match [regex]::Escape($stem) } | Select-Object -First 4)) {
            if (-not $found.Contains($test)) { [void]$found.Add($test) }
        }
    }
    if (Get-Command rg -ErrorAction SilentlyContinue) {
        foreach ($pattern in @($SearchPatterns)) {
            $matches = @(& rg --files-with-matches --color never --glob '*.py' -- $pattern tests 2>$null | Select-Object -First 4)
            foreach ($test in $matches) {
                $normalized = ([string]$test).Replace('\', '/')
                if (-not $found.Contains($normalized)) { [void]$found.Add($normalized) }
            }
        }
    }
    return @($found | Select-Object -First 16 | ForEach-Object { Limit-Text $_ 240 })
}

function Get-HostPreparation([string[]]$ChangedFiles = @()) {
    $searches = @()
    $runtimePaths = New-Object System.Collections.Generic.List[string]
    foreach ($pattern in @($phaseSearches)) {
        $matches = @(Invoke-BoundedRg -Pattern $pattern -Roots @('octopus', 'scripts') -MaxResults 2)
        $searches += [ordered]@{
            pattern = Limit-Text $pattern 100
            matches = @($matches)
        }
        foreach ($line in $matches) {
            if ($line -match '^([^:]+):\d+:') {
                $path = $Matches[1].Replace('\', '/')
                if ($path -notlike 'tests/*' -and -not $runtimePaths.Contains($path)) { [void]$runtimePaths.Add($path) }
            }
        }
    }
    foreach ($path in @($ChangedFiles)) {
        if ($path -notlike 'tests/*' -and -not $runtimePaths.Contains($path)) { [void]$runtimePaths.Add($path) }
    }
    $boundedRuntimePaths = @($runtimePaths | Select-Object -First 8)
    $symbols = if ($boundedRuntimePaths.Count) {
        @(Invoke-BoundedRg -Pattern '^(class|def|function)\s+[A-Za-z_][A-Za-z0-9_-]*|__main__|add_parser\(' -Roots $boundedRuntimePaths -MaxResults 5)
    } else { @() }
    $testSources = @($boundedRuntimePaths + @($ChangedFiles) | Sort-Object -Unique)
    $tests = @(Find-AssociatedTests -SourcePaths $testSources -SearchPatterns $phaseSearches | Select-Object -First 8)
    $packet = [ordered]@{
        search_scope = @('octopus', 'scripts', 'tests')
        targeted_searches = $searches
        runtime_paths = $boundedRuntimePaths
        symbols_and_entrypoints = @($symbols)
        likely_tests = $tests
        limits = [ordered]@{
            matches_per_search = 2
            runtime_paths = 8
            symbols = 5
            tests = 8
            text_chars_per_result = 220
        }
    }
    $json = $packet | ConvertTo-Json -Depth 10
    if ($json.Length -gt $MaxHostPrepareChars) {
        throw "Astra host preparation exceeds its $MaxHostPrepareChars character limit: $($json.Length)."
    }
    return $packet
}

function Save-Handoff(
    [string]$Result,
    [string]$NextDecision,
    [string[]]$Files = @(),
    [string[]]$Decisions = @(),
    [string[]]$Tests = @(),
    [string[]]$PendingHostRequests = @(),
    [string]$Blocked = ""
) {
    $changed = if ($Files.Count) { @($Files | Select-Object -First 30) } else { @(Get-ChangedPaths) }
    $handoff = [ordered]@{
        version = 2
        objective = Limit-Text $phaseSpec.mission 1200
        phase = $Phase
        head = (git rev-parse HEAD | Out-String).Trim()
        decisions = @($Decisions | Select-Object -First 12 | ForEach-Object { Limit-Text $_ 500 })
        files_modified = @($changed | ForEach-Object { Limit-Text $_ 300 })
        tests = @($Tests | Select-Object -First 20 | ForEach-Object { Limit-Text $_ 300 })
        pending_host_requests = @($PendingHostRequests | Select-Object -First 5 | ForEach-Object { Limit-Text $_ 200 })
        last_result = Limit-Text $Result 1000
        blocked = if ($Blocked) { Limit-Text $Blocked 800 } else { $null }
        next_decision = Limit-Text $NextDecision 600
    }
    $null = Write-BoundedJsonAtomic -Path $handoffPath -Value $handoff -MaxChars $MaxHandoffChars -Label "Astra handoff"
}

function Save-ContextManifest {
    $previousHashes = @{}
    if (Test-Path -LiteralPath $contextManifestPath -PathType Leaf) {
        try {
            $previous = Get-Content -LiteralPath $contextManifestPath -Raw | ConvertFrom-Json -ErrorAction Stop
            foreach ($document in @($previous.documents)) {
                $previousHashes[[string]$document.path] = [string]$document.sha256
            }
        } catch {
            $previousHashes = @{}
        }
    }

    $documentSpecs = @(
        [ordered]@{
            path = "AGENTS.md"
            role = "Project constitution, already injected by Codex"
            read_policy = "do_not_reread"
        }
    )
    foreach ($path in @($phaseSpec.documents)) {
        $documentSpecs += [ordered]@{
            path = $path
            role = "Optional phase reference; objective is already in the host snapshot"
            read_policy = "targeted_section_only_if_snapshot_is_insufficient"
        }
    }

    $documents = foreach ($spec in $documentSpecs) {
        $fullPath = Join-Path $repo $spec.path
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { throw "Context document missing: $($spec.path)" }
        $hashResult = Get-FileHash -LiteralPath $fullPath -Algorithm SHA256
        $hash = ([string]$hashResult.Hash).ToLowerInvariant()
        $size = (Get-Item -LiteralPath $fullPath).Length
        [ordered]@{
            path = $spec.path
            sha256 = $hash
            bytes = $size
            role = $spec.role
            read_policy = $spec.read_policy
            changed_since_previous_call = if ($previousHashes.ContainsKey($spec.path)) { $previousHashes[$spec.path] -ne $hash } else { $null }
        }
    }

    $manifest = [ordered]@{
        version = 1
        phase = $Phase
        rule = "No document is preloaded. Read one bounded section only when a missing fact is identified and state why. Never reread an unchanged document automatically."
        documents = @($documents)
    }
    return Write-BoundedJsonAtomic -Path $contextManifestPath -Value $manifest -MaxChars $MaxManifestChars -Label "Astra context manifest"
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
    $handoffJson = if (Test-Path -LiteralPath $handoffPath -PathType Leaf) { Get-Content -LiteralPath $handoffPath -Raw } else { "{}" }
    if ($handoffJson.Length -gt $MaxHandoffChars) {
        throw "Astra handoff exceeds its $MaxHandoffChars character limit: $($handoffJson.Length)."
    }

    $branch = (git branch --show-current | Out-String).Trim()
    $currentHead = (git rev-parse HEAD | Out-String).Trim()
    $changedFiles = @(Get-ChangedPaths)
    $handoff = $handoffJson | ConvertFrom-Json
    $isReviewCall = $script:astraTurns -ge 1
    $pendingRequests = @()
    foreach ($candidate in @(
        @{ name = "checkpoint"; path = $checkpointPath },
        @{ name = "step"; path = $requestPath },
        @{ name = "full_pytest"; path = $validationPath }
    )) {
        if (Test-Path -LiteralPath $candidate.path -PathType Leaf) { $pendingRequests += $candidate.name }
    }

    if ($isReviewCall) {
        $reviewPaths = if ($script:reviewContext -and $script:reviewContext.changed_paths) { @($script:reviewContext.changed_paths) } else { $changedFiles }
        $snapshot = [ordered]@{
            version = 2
            packet = 'call_2_review'
            phase = $Phase
            head = $currentHead
            branch = $branch
            previous_decision = [ordered]@{
                decisions = @($handoff.decisions | Select-Object -First 12 | ForEach-Object { Limit-Text $_ 300 })
                last_result = Limit-Text $handoff.last_result 600
                next_decision = Limit-Text $handoff.next_decision 500
            }
            changed_files = @($reviewPaths | Select-Object -First 30)
            step_summary = $script:lastWorkerSummary
            host_test_summary = if ($script:lastValidationSummary) {
                $script:lastValidationSummary
            } elseif ($script:lastWorkerSummary) {
                [ordered]@{ source = 'step_host'; tests = @($script:lastWorkerSummary.tests); output_truncated = $true }
            } else { $null }
            diff_summary = if ($script:reviewContext -and $script:reviewContext.diff_stat) { @($script:reviewContext.diff_stat) } else { @(Get-CompactDiffStat -Paths $reviewPaths) }
            review_kind = if ($script:reviewContext) { $script:reviewContext.kind } else { $null }
            blocking_issue = $handoff.blocked
        }
    } else {
        $hostPreparation = Get-HostPreparation -ChangedFiles $changedFiles
        $snapshot = [ordered]@{
            version = 2
            packet = 'call_1_prepare'
            phase = $Phase
            objective = Limit-Text $phaseSpec.mission 1200
            head = $currentHead
            branch = $branch
            dirty = $changedFiles.Count -gt 0
            baseline = [ordered]@{
                status = if ([int]$baseline.exit_code -eq 0) { "passed" } else { "failed" }
                exit_code = [int]$baseline.exit_code
                duration_seconds = $baseline.duration_seconds
            }
            changed_files = $changedFiles
            diff_stat = @(Get-CompactDiffStat -Paths $changedFiles)
            pending_requests = $pendingRequests
            previous_validation = $script:lastValidationSummary
            host_prepare = $hostPreparation
            blocking_issue = $handoff.blocked
        }
    }
    $snapshotJson = Write-BoundedJsonAtomic -Path $snapshotPath -Value $snapshot -MaxChars $MaxSnapshotChars -Label "Astra host snapshot"

    if ($isReviewCall) {
        $manifestJson = ''
        $preparedPrompt = @"
$TaskPrompt

HOST_REVIEW_PACKET_JSON
$snapshotJson
"@
        $handoffMetricChars = 0
        $contextFileCount = 1
    } else {
        $manifestJson = Save-ContextManifest
        $preparedPrompt = @"
$TaskPrompt

HOST_SNAPSHOT_JSON
$snapshotJson

CONTEXT_MANIFEST_JSON
$manifestJson

MINIMAL_HANDOFF_JSON
$handoffJson
"@
        $handoffMetricChars = $handoffJson.Length
        $contextFileCount = 3
    }
    $agentsChars = (Get-Content -LiteralPath (Join-Path $repo "AGENTS.md") -Raw).Length
    $estimatedInputChars = $preparedPrompt.Length + $agentsChars
    if ($estimatedInputChars -gt $MaxPreparedContextChars) {
        throw "Prepared Astra context exceeds its $MaxPreparedContextChars character limit: $estimatedInputChars."
    }
    Write-JsonAtomic -Path $contextMetricsPath -Value ([ordered]@{
        version = 1
        estimated_prompt_chars = $preparedPrompt.Length
        implicit_instruction_chars = $agentsChars
        estimated_input_chars = $estimatedInputChars
        snapshot_chars = $snapshotJson.Length
        handoff_chars = $handoffMetricChars
        context_manifest_chars = $manifestJson.Length
        context_file_count = $contextFileCount
        manifest_document_count = if ($isReviewCall) { 0 } else { @($phaseSpec.documents).Count + 1 }
        call_kind = if ($isReviewCall) { 'review' } else { 'prepare' }
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
CALL 1 includes the host snapshot, context manifest and minimal handoff. CALL 2 includes only HOST_REVIEW_PACKET_JSON. The supplied packet is authoritative. Do not reread its disk copies or reconstruct Git state, history, baseline, checkpoint or validation state.
AGENTS.md is already injected. Do not read it again. Do not read a phase document automatically. If one exact fact is missing, name the missing fact and read only one relevant manifest-listed section. Never reread a document whose manifest hash is unchanged.
The shell-command budget is zero by default. General repository exploration is forbidden: no broad rg, Get-ChildItem, git ls-files, recursive discovery, or search for tests already supplied by HOST PREPARE. Never read complete large files. If one indispensable fact is still missing, state it first, then use at most one targeted search or one excerpt capped at 200 lines and 20000 characters.
Never read Astra JSONL, pytest logs, night-shift reports, generated files or lockfiles. Inspect source only by symbol, a bounded excerpt, or one changed-file diff at a time. Use host-provided runtime paths, symbols, likely tests, changed paths and diff summaries.
Run no long test in Astra and never run a full suite. A single micro-test is allowed only when required to define an oracle before delegation. For all other pytest validation, atomically publish cache/astra-relay/validation.json with {"version":1,"kind":"full_pytest"}, then end the call. The host runs it after exit and returns only exit code, counts, duration and a short failure list.
As soon as files, expected behavior, oracle/tests and limits can be stated, publish the bounded product_ticket and cache/astra-relay/request.json, then end the call immediately. Do not inspect implementation details that Step can resolve mechanically.
For tested direct changes, publish cache/astra-relay/checkpoint.json version 2 with request_id, base_head, message and exact changed paths, then end the call. For a qualified bounded Step task, publish one product_ticket and cache/astra-relay/request.json from a clean tree, then end the call.
Do not launch Step, poll, wait, run full pytest or write Git metadata. After publishing any Step, checkpoint or host-validation request, perform no further inspection or command. A follow-up is a fresh ephemeral call. Stop after the requested boundary.
"@

function New-AstraTaskPrompt([string]$Task) {
    return $Task + [Environment]::NewLine + [Environment]::NewLine + $contextRules
}

$initialPrompt = New-AstraTaskPrompt "HOST PREPARE has already performed deterministic Git inspection, targeted searches, symbol discovery and likely-test discovery. Execute only the objective in the host snapshot for phase $Phase. Decide architecture, permissions and the bounded contract. Delegate to Step immediately when the mechanical ticket can be stated."

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
        Save-Handoff -Result $lastResult -NextDecision 'Review only the committed changed paths, then stop unless a reproduced defect requires action.' -Files @($receipt.paths) -Decisions @('Host committed the exact declared paths.')
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
        Save-Handoff -Result $lastResult -NextDecision 'Review the compact worker receipt and listed paths; publish worker_commit checkpoint only if acceptable.' -Files $workerPaths -Tests @($validatedWorkerSummary.tests)
        $nextReason = 'Step review'
        $nextPrompt = New-AstraTaskPrompt "Fresh Step review for phase $Phase. Use only the compact worker receipt in the inline snapshot; do not open its raw report, evidence or log. Inspect the actual diff for each listed path. If acceptable, publish checkpoint.json version 2 with kind=worker_commit, base_head, source_commit, message and exact paths, then end the call."
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
        Save-Handoff -Result $lastResult -NextDecision 'Conclude on pass; on failure resolve only the named failing tests.' -Tests @("full pytest: $($validationResult.status), exit $($validationResult.exit_code), $($validationResult.duration_seconds)s")
        $nextReason = 'validation review'
        $nextPrompt = New-AstraTaskPrompt 'Fresh validation review. The bounded host receipt is in the inline snapshot. If it passed, conclude without reading any log or documentation. If it failed, inspect only the named failing tests and the smallest relevant source excerpt.'
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
    $preparedPrompt = Write-AstraContext -TaskPrompt $nextPrompt
    $turn = Invoke-AstraTurn -Prompt $preparedPrompt -Reason $nextReason
    $threadId = $turn.thread_id
    $modelInvoked = $true
    Save-SessionState -Status 'active'
}

$usageTotals = Save-UsageSummary
Write-Host ("Astra calls: {0}/{1}; run_totals={2}; lifetime_totals={3}" -f $astraTurns, $MaxAstraTurns, ($usageTotals.run_totals | ConvertTo-Json -Compress), ($usageTotals.lifetime_totals | ConvertTo-Json -Compress))
Write-Host "Handoff: $handoffPath"
