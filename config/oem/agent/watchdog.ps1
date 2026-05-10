# =====================================================================
# watchdog.ps1 -- agent /health watchdog (install-time + steady-state).
#
# Launched from HKCU\Run\WinpodxAgent. First action on launch is to
# spawn agent.ps1 if /health doesn't already answer 200 -- so the same
# entry covers (a) cold-boot autostart and (b) respawn-on-crash without
# needing a separate agent.ps1 entry in HKCU\Run.
#
# Two operating modes, branched on the install_complete marker:
#
#   INSTALL MODE (no install_complete.done present):
#     * 30s poll, 2s/5s debounce.
#     * Respawn after confirmed death; 60s grace for /health.
#     * 3 consecutive failed cycles -> Write-WinpodxFailure + exit 1
#       (signals install-resume the install can't proceed).
#     * Log target: install.log (the canonical install-time stream).
#
#   STEADY-STATE MODE (install_complete.done present):
#     * 30s poll, 2s/5s debounce -- same as install mode.
#     * Indefinite respawn with exponential backoff: 30s, 60s, 120s,
#       240s, 300s (cap at 5 min). Counter never resets to 3-fail
#       hard-exit -- after install completes, a transient flap should
#       NOT leave the user agentless until reboot. (security review #6)
#     * Log target: watchdog.log only (NOT install.log -- avoids
#       indefinite log growth during long-lived sessions).
#
# Mode is re-evaluated on every loop iteration: an install that
# completes mid-watchdog-life transitions cleanly to steady-state on
# the next poll without restarting the watchdog.
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

# Install-mode timings (unchanged).
$script:PollSec       = 30
$script:DebounceSecs  = @(2, 5)
$script:RespawnGrace  = 60
$script:MaxRespawns   = 3

# Steady-state backoff schedule. Each entry is the wait between the
# end of one respawn cycle and the start of the next. Last value
# stays in effect indefinitely (no exit, no reset).
$script:SteadyBackoffSecs = @(30, 60, 120, 240, 300)

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
$steadyCycleIdx = 0
$lastState = 'unknown'

while ($true) {
    Start-Sleep -Seconds $script:PollSec

    if (Test-HealthDebounced) {
        if ($lastState -ne 'up') {
            Log-Event 'INFO' 'health_recovered'
            $lastState = 'up'
        }
        $consecutiveFailures = 0
        $steadyCycleIdx = 0
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
        $steadyCycleIdx = 0
        $lastState = 'up'
        continue
    }

    Log-Event 'ERROR' 'respawn_failed' @{
        cycle  = $consecutiveFailures
        steady = $steady
    }

    if (-not $steady) {
        # INSTALL MODE: hard-exit after MaxRespawns to surface the
        # failure into install_failure.json so install-resume can act.
        if ($consecutiveFailures -ge $script:MaxRespawns) {
            Log-Event 'ERROR' 'respawn_budget_exhausted' @{ max = $script:MaxRespawns }
            if ($script:HelpersLoaded) {
                try {
                    Write-WinpodxFailure `
                        -Step 'agent_ready' -Phase 1 `
                        -Attempt $consecutiveFailures -MaxAttempts $script:MaxRespawns `
                        -ExitCode 1 `
                        -ErrorClass 'agent_watchdog_exhausted' `
                        -ErrorSummary "watchdog exhausted $($script:MaxRespawns) respawn cycles"
                } catch {
                    Write-Bare 'ERROR' 'failure_record_write_failed' $_.Exception.Message
                }
            }
            exit 1
        }
        # Below the install-mode budget -- loop continues with PollSec.
        continue
    }

    # STEADY-STATE MODE: indefinite respawn with exponential backoff.
    # Never exits. Counter never resets to a hard-fail. A transient
    # agent flap post-install must NOT leave the user agentless.
    $idx = [Math]::Min($steadyCycleIdx, $script:SteadyBackoffSecs.Length - 1)
    $waitSecs = $script:SteadyBackoffSecs[$idx]
    Log-Event 'WARN' 'steady_backoff' @{
        cycle      = $consecutiveFailures
        wait_secs  = $waitSecs
    }
    Start-Sleep -Seconds $waitSecs
    $steadyCycleIdx += 1
}
