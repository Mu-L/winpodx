# =====================================================================
# watchdog.ps1 -- in-process agent watchdog for the agent-first install.
#
# Launched from HKCU\Run\WinpodxAgent. First action on launch is to
# spawn agent.ps1 if /health doesn't already answer 200 -- so the same
# entry covers (a) cold-boot autostart and (b) respawn-on-crash without
# needing a separate agent.ps1 entry in HKCU\Run.
#
# Loop:
#   * Poll http://127.0.0.1:8765/health every 30s.
#   * On a probe failure: 2x retry with 2s and 5s backoff. Only after
#     two further consecutive failures do we count this as a death --
#     short Defender / TermService stalls don't trip the respawn cycle.
#   * On confirmed death: Start-Process agent.ps1, wait up to 60s for
#     /health to come back. Reset death counter on success.
#   * 3 consecutive failed respawn cycles -> Write-WinpodxFailure +
#     exit 1.
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
$script:LogFallback = 'C:\winpodx\install-state\watchdog.log'
$script:PollSec       = 30
$script:DebounceSecs  = @(2, 5)
$script:RespawnGrace  = 60
$script:MaxRespawns   = 3

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

function Write-Bare([string]$Level, [string]$Event, [string]$Detail) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    $line = "$ts $Level watchdog $Event"
    if ($Detail) { $line = "$line detail=$Detail" }
    try {
        $dir = Split-Path -Parent $script:LogFallback
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Add-Content -LiteralPath $script:LogFallback -Value $line -ErrorAction SilentlyContinue
    } catch { }
}

function Log-Event([string]$Level, [string]$Event, $Extra = $null) {
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

Log-Event 'INFO' 'started'

# Cold-boot path: agent might not be running at all. Try once before
# the loop so the autostart case doesn't have to wait for the first
# 30s poll.
if (-not (Test-Health)) {
    Log-Event 'INFO' 'cold_start_spawn'
    Start-Agent | Out-Null
    Wait-Healthy | Out-Null
}

$consecutiveFailures = 0
$lastState = 'unknown'

while ($true) {
    Start-Sleep -Seconds $script:PollSec

    if (Test-HealthDebounced) {
        if ($lastState -ne 'up') {
            Log-Event 'INFO' 'health_recovered'
            $lastState = 'up'
        }
        $consecutiveFailures = 0
        continue
    }

    if ($lastState -ne 'down') {
        Log-Event 'WARN' 'health_lost'
        $lastState = 'down'
    }

    $consecutiveFailures += 1
    Log-Event 'WARN' 'respawn_cycle_start' @{ cycle = $consecutiveFailures }

    if (-not (Start-Agent)) {
        # spawn_failed already logged.
    }

    if (Wait-Healthy) {
        Log-Event 'INFO' 'respawn_recovered' @{ cycle = $consecutiveFailures }
        $consecutiveFailures = 0
        $lastState = 'up'
        continue
    }

    Log-Event 'ERROR' 'respawn_failed' @{ cycle = $consecutiveFailures }

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
}
