# =====================================================================
# watchdog.ps1 -- agent /health watchdog (install-time + steady-state).
#
# Launched from HKCU\Run\WinpodxAgent. First action on launch is to
# spawn agent.ps1 if /health doesn't already answer 200 -- so the same
# entry covers (a) cold-boot autostart and (b) respawn-on-crash without
# needing a separate agent.ps1 entry in HKCU\Run.
#
# The watchdog's only job is keeping agent.ps1 alive. It uses a fixed
# exponential backoff schedule (30s, 60s, 120s, 240s, 300s; last value
# stays in effect indefinitely) and NEVER exits permanently. The
# previous design had a 3-cycle hard-exit branch in install mode, on
# the theory that install-resume's logon trigger would re-launch the
# watchdog on the next user logon. In practice the user is still
# logged in from the autologon session when install.bat exits, so the
# logon trigger never fires and the watchdog's exit leaves the guest
# permanently agentless until the user reboots the VM. (Smoke test
# 2026-05-10 reproduced this on three separate runs: any Phase 2 bug
# that prevents install_complete.done from being written cascades into
# permanent agent death.)
#
# Failure visibility is the responsibility of Invoke-WinpodxStep in
# install-step-functions.ps1, which calls Write-WinpodxFailure when
# the per-step retry counter hits MaxRetries. The watchdog does not
# duplicate that signal.
#
# The only branch that remains is the LOG TARGET: while install.bat
# is in flight (no install_complete.done) the watchdog appends to the
# canonical install.log so its events sit in the same stream as the
# step bodies. After install_complete.done lands, it switches to a
# separate watchdog.log to avoid unbounded growth on long-lived
# sessions. (security review #6)
#
# This script depends on install-state-helpers.ps1 having been staged
# alongside it (install.bat copies both to C:\winpodx\agent\). If the
# helper file isn't reachable we fall back to bare logging via
# Add-Content so a packaging miss doesn't sink the watchdog silently.
# =====================================================================

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$script:HelperPath  = 'C:\winpodx\agent\install-state-helpers.ps1'
$script:OemHelper   = 'C:\OEM\install-state-helpers.ps1'
$script:AgentScript = 'C:\winpodx\agent\agent.ps1'
$script:HealthUrl   = 'http://127.0.0.1:8765/health'
$script:StateDir    = 'C:\winpodx\install-state'
$script:CompleteMarker = 'C:\winpodx\install-state\install_complete.done'
$script:WatchdogLog = 'C:\winpodx\install-state\watchdog.log'

# Polling + debounce + respawn-grace: shared across the whole life of
# the watchdog. The respawn budget no longer hard-exits -- see the
# header comment.
$script:PollSec       = 30
$script:DebounceSecs  = @(2, 5)
$script:RespawnGrace  = 60

# Backoff schedule between respawn cycles. Each entry is the wait
# between the end of one respawn cycle and the start of the next.
# Last value stays in effect indefinitely.
$script:BackoffSecs = @(30, 60, 120, 240, 300)

# Try to load the shared helpers. The watchdog is launched from
# HKCU\Run *after* install.bat has run Phase 1, so the helpers should
# always be present -- but we don't trust "should" for a long-running
# background process.
$script:HelpersLoaded = $false
foreach ($p in @($script:HelperPath, $script:OemHelper)) {
    if (Test-Path -LiteralPath $p) {
        try {
            . $p
            $script:HelpersLoaded = $true
            break
        } catch { }
    }
}

# Steady-state log target -- always usable, no helpers required.
function Write-WatchdogLog([string]$Level, [string]$Event, $Extra = $null) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    $record = [ordered]@{
        ts    = $ts
        level = $Level
        step  = 'watchdog'
        event = $Event
    }
    if ($null -ne $Extra -and $Extra -is [hashtable]) {
        foreach ($k in $Extra.Keys) {
            if (-not $record.Contains($k)) { $record[$k] = $Extra[$k] }
        }
    }
    $line = ($record | ConvertTo-Json -Compress -Depth 4)
    try {
        $dir = Split-Path -Parent $script:WatchdogLog
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Add-Content -LiteralPath $script:WatchdogLog -Value $line -ErrorAction SilentlyContinue
    } catch { }
}

# Bare fallback for cases where neither install.log nor watchdog.log
# is reachable (extremely degraded state).
function Write-Bare([string]$Level, [string]$Event, [string]$Detail) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    $line = "$ts $Level watchdog $Event"
    if ($Detail) { $line = "$line detail=$Detail" }
    try {
        $dir = Split-Path -Parent $script:WatchdogLog
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Add-Content -LiteralPath $script:WatchdogLog -Value $line -ErrorAction SilentlyContinue
    } catch { }
}

# Logger that picks install.log or watchdog.log based on mode.
function Log-Event([string]$Level, [string]$Event, $Extra = $null) {
    if (Test-SteadyState) {
        Write-WatchdogLog $Level $Event $Extra
        return
    }
    if ($script:HelpersLoaded) {
        try {
            if ($Extra) {
                Write-WinpodxLog -Level $Level -Step 'watchdog' -Event $Event -Extra $Extra
            } else {
                Write-WinpodxLog -Level $Level -Step 'watchdog' -Event $Event
            }
            return
        } catch { }
    }
    $detail = if ($Extra) { ($Extra | ConvertTo-Json -Compress -Depth 4) } else { '' }
    Write-Bare $Level $Event $detail
}

function Test-SteadyState {
    return [bool](Test-Path -LiteralPath $script:CompleteMarker -PathType Leaf)
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri $script:HealthUrl -UseBasicParsing `
            -TimeoutSec 5 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Probe with debounce: initial check + N retries with explicit backoffs.
# Returns $true if any of the (1 + len(DebounceSecs)) probes succeeds.
function Test-HealthDebounced {
    if (Test-Health) { return $true }
    foreach ($wait in $script:DebounceSecs) {
        Start-Sleep -Seconds $wait
        if (Test-Health) { return $true }
    }
    return $false
}

function Start-Agent {
    if (-not (Test-Path -LiteralPath $script:AgentScript)) {
        Log-Event 'ERROR' 'agent_script_missing' @{ path = $script:AgentScript }
        return $false
    }
    try {
        Start-Process powershell.exe `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                            '-File', $script:AgentScript) `
            -WindowStyle Hidden | Out-Null
        return $true
    } catch {
        Log-Event 'ERROR' 'spawn_failed' @{ detail = $_.Exception.Message }
        return $false
    }
}

# Wait for /health to come back after a respawn. Returns $true within
# RespawnGrace seconds, $false otherwise.
function Wait-Healthy {
    $deadline = (Get-Date).AddSeconds($script:RespawnGrace)
    while ((Get-Date) -lt $deadline) {
        if (Test-Health) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

Log-Event 'INFO' 'started' @{ steady = (Test-SteadyState) }

# Cold-boot path: agent might not be running at all. Try once before
# the loop so the autostart case doesn't have to wait for the first
# 30s poll.
if (-not (Test-Health)) {
    Log-Event 'INFO' 'cold_start_spawn'
    Start-Agent | Out-Null
    Wait-Healthy | Out-Null
}

$consecutiveFailures = 0
$backoffIdx = 0
$lastState = 'unknown'

while ($true) {
    Start-Sleep -Seconds $script:PollSec

    if (Test-HealthDebounced) {
        if ($lastState -ne 'up') {
            Log-Event 'INFO' 'health_recovered'
            $lastState = 'up'
        }
        $consecutiveFailures = 0
        $backoffIdx = 0
        continue
    }

    if ($lastState -ne 'down') {
        Log-Event 'WARN' 'health_lost'
        $lastState = 'down'
    }

    $consecutiveFailures += 1
    $steady = Test-SteadyState

    Log-Event 'WARN' 'respawn_cycle_start' @{
        cycle  = $consecutiveFailures
        steady = $steady
    }

    if (-not (Start-Agent)) {
        # spawn_failed already logged.
    }

    if (Wait-Healthy) {
        Log-Event 'INFO' 'respawn_recovered' @{
            cycle  = $consecutiveFailures
            steady = $steady
        }
        $consecutiveFailures = 0
        $backoffIdx = 0
        $lastState = 'up'
        continue
    }

    Log-Event 'ERROR' 'respawn_failed' @{
        cycle  = $consecutiveFailures
        steady = $steady
    }

    # Indefinite respawn with exponential backoff. The watchdog never
    # exits -- failure visibility is Invoke-WinpodxStep's job (it
    # writes install_failure.json on retry exhaustion). Watchdog's
    # only job is keeping agent.ps1 alive.
    $idx = [Math]::Min($backoffIdx, $script:BackoffSecs.Length - 1)
    $waitSecs = $script:BackoffSecs[$idx]
    Log-Event 'WARN' 'respawn_backoff' @{
        cycle      = $consecutiveFailures
        wait_secs  = $waitSecs
        steady     = $steady
    }
    Start-Sleep -Seconds $waitSecs
    $backoffIdx += 1
}
