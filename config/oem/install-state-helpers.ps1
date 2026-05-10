# =====================================================================
# install-state-helpers.ps1 -- guest-side primitives for the
# agent-first install state machine.
#
# Mirror of src/winpodx/core/agent_install_state.py + the PHASE_ORDER
# constant from src/winpodx/core/install_state.py. Sourced by
# install.bat (and install-resume.ps1) before install-step-functions.ps1.
#
# Public surface:
#   * Initialize-WinpodxStateDir          - create C:\winpodx\install-state\
#                                           with User+Administrators ACL
#   * New-WinpodxMarker $Name             - atomic write of <Name>.done
#   * Test-WinpodxMarker $Name            - True iff marker exists
#   * Get-WinpodxCompletedSteps           - list of step names with markers
#   * Increment-WinpodxRetry $Name        - atomic +1 in retry_counts.json
#   * Get-WinpodxRetry $Name              - current retry count, 0 if missing
#   * Reset-WinpodxRetry $Name            - zero one step's counter
#   * Invoke-WinpodxRedact $Line          - strip secrets per security review #3
#   * Write-WinpodxLog -Level -Step -Event [-Extra]
#                                         - append one redacted JSON line to install.log
#   * Write-WinpodxFailure -Step -Phase -Attempt -MaxAttempts
#                          -ExitCode -ErrorClass -ErrorSummary
#                                         - write schema-conformant install_failure.json
#
# Constants:
#   * $PHASE_ORDER  - ordered array mirroring core/install_state.py
#
# Redactor patterns (must produce byte-identical output to
# agent_install_state.redact_log_line for any input):
#   1. net user <user> <pw>                  -> net user <user> <REDACTED>
#   2. Authorization: Bearer <tok>           -> Authorization: Bearer <REDACTED>
#   3. xfreerdp /p:|/password:|-p: <pw>      -> /p:<REDACTED> etc. (case-sensitive)
#   4. xfreerdp /p|-p <pw> (space form)      -> /p <REDACTED> etc. (case-sensitive)
#   5. password=/token=/apikey=/api_key=     -> <key>=<REDACTED>   (case-insensitive)
#   6. base64-ish blob >= 40 chars           -> <BASE64-REDACTED>
#
# Helpers do NOT swallow exceptions; the caller (install.bat
# orchestrator) decides how to react to a failure.
# =====================================================================

Set-StrictMode -Version Latest

# ----- Module-scoped constants ---------------------------------------

$script:WpxStateDir          = 'C:\winpodx\install-state'
$script:WpxLogPath           = Join-Path $script:WpxStateDir 'install.log'
$script:WpxRetryCountsPath   = Join-Path $script:WpxStateDir 'retry_counts.json'
$script:WpxFailurePath       = Join-Path $script:WpxStateDir 'install_failure.json'
$script:WpxSessionIdPath     = Join-Path $script:WpxStateDir 'install_session_id.txt'

$script:WpxRedacted          = '<REDACTED>'
$script:WpxBase64Redacted    = '<BASE64-REDACTED>'

# Canonical step order. Matches core/install_state.py PHASE_ORDER and
# the markers documented in AGENT_FIRST_INSTALL_DESIGN.md "State
# directory layout". Iterated by install-step-functions.ps1 via $entry.name.
$PHASE_ORDER = @(
    [pscustomobject]@{ phase = 0;   name = 'defender_exclusion';   display = 'defender exclusion' }
    [pscustomobject]@{ phase = 0.5; name = 'state_dir_ready';      display = 'state dir ready' }
    [pscustomobject]@{ phase = 0.6; name = 'token_staged';         display = 'token staged' }
    [pscustomobject]@{ phase = 1;   name = 'agent_ready';          display = 'agent ready' }
    [pscustomobject]@{ phase = 2;   name = 'rdprrap_installed';    display = 'rdprrap install' }
    [pscustomobject]@{ phase = 2;   name = 'vbs_launchers';        display = 'vbs launchers' }
    [pscustomobject]@{ phase = 2;   name = 'oem_runtime_fixes';    display = 'oem runtime fixes' }
    [pscustomobject]@{ phase = 2;   name = 'max_sessions';         display = 'max sessions' }
    [pscustomobject]@{ phase = 2;   name = 'multi_session_active'; display = 'multi-session activate' }
    [pscustomobject]@{ phase = 3;   name = 'install_complete';     display = 'install complete' }
)

# Compiled regexes -- keep parity with agent_install_state.py.
$script:WpxNetUserRe   = [regex]::new('(net user\s+\S+\s+)\S+', 'IgnoreCase')
$script:WpxAuthBearerRe = [regex]::new('(Authorization:\s*Bearer\s+)\S+', 'IgnoreCase')
# xfreerdp/wfreerdp connection password (security review #8). Colon and
# space forms tracked separately: xfreerdp tolerates both. Case-sensitive,
# matching the host-side PasswordFilter in src/winpodx/utils/logging.py
# and xfreerdp's lowercase argv convention. False positives on bare
# `-p <path>` (mkdir, tar, ...) are accepted -- masking a path is the
# safer error mode than leaking a real password.
$script:WpxFreerdpPwColonRe = [regex]::new('(/p:|/password:|-p:)([^\s]+)')
$script:WpxFreerdpPwSpaceRe = [regex]::new('(/p\s+|-p\s+)(\S+)')
$script:WpxKvSecretRe  = [regex]::new(
    '\b(password|token|apikey|api_key)\s*=\s*([^\s''"&]+)',
    'IgnoreCase'
)
# Bare base64-ish blob: 40+ chars from the base64 alphabet, optional '='
# padding, with negative look-around so we don't slice partial matches
# out of larger non-base64 strings.
$script:WpxBase64Re    = [regex]::new(
    '(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])'
)

# Required top-level fields for install_failure.json. Mirrors
# docs/design/install_failure.schema.json.
$script:WpxFailureRequired = @(
    'session_id','failed_step','phase','attempt','max_attempts',
    'exit_code','error_class','error_summary','timestamp_utc',
    'environment','last_log_lines'
)

# ----- Internal: atomic write helper ---------------------------------

function _Wpx_WriteAtomic {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Content
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $tmp = Join-Path $parent (".$(Split-Path -Leaf $Path).$([guid]::NewGuid().ToString('N')).tmp")
    try {
        # -NoNewline + UTF8 (no BOM on pwsh 7+) for byte-stable output.
        $Content | Out-File -LiteralPath $tmp -Encoding utf8 -NoNewline
        Move-Item -LiteralPath $tmp -Destination $Path -Force
    } catch {
        if (Test-Path -LiteralPath $tmp) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

# ----- Public: Initialize-WinpodxStateDir ----------------------------

function Initialize-WinpodxStateDir {
<#
.SYNOPSIS
Create C:\winpodx\install-state\ with User+Administrators ACL. Idempotent.
.EXAMPLE
Initialize-WinpodxStateDir
#>
    [CmdletBinding()]
    param()

    if (-not (Test-Path -LiteralPath $script:WpxStateDir)) {
        New-Item -ItemType Directory -Path $script:WpxStateDir -Force | Out-Null
    }

    # icacls is Windows-only. On pwsh-on-Linux (test harness) we skip
    # ACL hardening; the directory still exists and is writable.
    $icacls = Get-Command -Name icacls -ErrorAction SilentlyContinue
    if ($null -eq $icacls) { return }

    # Resolve the interactive user (the auto-logon User in the guest).
    # Falls back to the current process identity if no console user.
    $userName = $env:USERNAME
    if ([string]::IsNullOrWhiteSpace($userName)) {
        $userName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    }

    # /inheritance:r removes inherited ACEs; /grant:r replaces existing.
    # Order: reset inheritance first, then add the two principals.
    & icacls $script:WpxStateDir /inheritance:r | Out-Null
    & icacls $script:WpxStateDir "/grant:r" "${userName}:(OI)(CI)F" | Out-Null
    & icacls $script:WpxStateDir "/grant:r" "Administrators:(OI)(CI)F" | Out-Null
}

# ----- Public: marker primitives -------------------------------------

function New-WinpodxMarker {
<#
.SYNOPSIS
Atomically write an empty <Name>.done sentinel under the state dir.
.EXAMPLE
New-WinpodxMarker -Name 'agent_ready'
#>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [ValidatePattern('^[a-z_][a-z0-9_]*$')] [string] $Name
    )
    $path = Join-Path $script:WpxStateDir "$Name.done"
    _Wpx_WriteAtomic -Path $path -Content ''
}

function Test-WinpodxMarker {
<#
.SYNOPSIS
Return $true iff the <Name>.done marker exists.
.EXAMPLE
if (Test-WinpodxMarker -Name 'agent_ready') { ... }
#>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [string] $Name
    )
    $path = Join-Path $script:WpxStateDir "$Name.done"
    return [bool](Test-Path -LiteralPath $path -PathType Leaf)
}

function Get-WinpodxCompletedSteps {
<#
.SYNOPSIS
Return the sorted list of step names whose <step>.done marker exists.
.EXAMPLE
Get-WinpodxCompletedSteps
#>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    if (-not (Test-Path -LiteralPath $script:WpxStateDir -PathType Container)) {
        return @()
    }
    $entries = Get-ChildItem -LiteralPath $script:WpxStateDir -Filter '*.done' `
                  -File -ErrorAction SilentlyContinue
    if ($null -eq $entries) { return @() }
    $names = foreach ($e in $entries) {
        $e.Name.Substring(0, $e.Name.Length - '.done'.Length)
    }
    return @($names | Sort-Object)
}

# ----- Public: retry counter -----------------------------------------

function _Wpx_LoadRetryCounts {
    if (-not (Test-Path -LiteralPath $script:WpxRetryCountsPath -PathType Leaf)) {
        return @{}
    }
    $raw = $null
    try {
        $raw = Get-Content -LiteralPath $script:WpxRetryCountsPath -Raw -ErrorAction Stop
    } catch {
        return @{}
    }
    if ([string]::IsNullOrWhiteSpace($raw)) { return @{} }
    $parsed = $null
    try {
        $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        # Corrupt file -- treat as empty (do not destroy; caller may
        # want to inspect post-hoc).
        return @{}
    }
    $out = @{}
    if ($null -eq $parsed) { return $out }
    foreach ($prop in $parsed.PSObject.Properties) {
        $val = $prop.Value
        if ($val -is [int] -or $val -is [long]) {
            $out[$prop.Name] = [int]$val
        }
    }
    return $out
}

function _Wpx_SaveRetryCounts {
    param([Parameter(Mandatory)] [hashtable] $Counts)

    # Sort keys for byte-stable output across pwsh versions.
    $ordered = [ordered]@{}
    foreach ($k in ($Counts.Keys | Sort-Object)) {
        $ordered[$k] = [int]$Counts[$k]
    }
    $json = ($ordered | ConvertTo-Json -Compress:$false -Depth 4)
    if ($null -eq $json) { $json = '{}' }
    _Wpx_WriteAtomic -Path $script:WpxRetryCountsPath -Content $json
}

function Increment-WinpodxRetry {
<#
.SYNOPSIS
Atomically increment the retry counter for $Name and return the new value.
.EXAMPLE
$n = Increment-WinpodxRetry -Name 'rdprrap_installed'
#>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory)] [ValidatePattern('^[a-z_][a-z0-9_]*$')] [string] $Name
    )
    $counts = _Wpx_LoadRetryCounts
    if ($counts.ContainsKey($Name)) {
        $counts[$Name] = [int]$counts[$Name] + 1
    } else {
        $counts[$Name] = 1
    }
    _Wpx_SaveRetryCounts -Counts $counts
    return [int]$counts[$Name]
}

function Get-WinpodxRetry {
<#
.SYNOPSIS
Return the current retry count for $Name (0 if missing).
.EXAMPLE
$n = Get-WinpodxRetry -Name 'rdprrap_installed'
#>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory)] [ValidatePattern('^[a-z_][a-z0-9_]*$')] [string] $Name
    )
    $counts = _Wpx_LoadRetryCounts
    if ($counts.ContainsKey($Name)) { return [int]$counts[$Name] }
    return 0
}

function Reset-WinpodxRetry {
<#
.SYNOPSIS
Zero the retry counter for $Name (no-op if missing).
.EXAMPLE
Reset-WinpodxRetry -Name 'rdprrap_installed'
#>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [ValidatePattern('^[a-z_][a-z0-9_]*$')] [string] $Name
    )
    $counts = _Wpx_LoadRetryCounts
    if ($counts.ContainsKey($Name)) {
        $counts[$Name] = 0
        _Wpx_SaveRetryCounts -Counts $counts
    }
}

# ----- Public: redactor ----------------------------------------------

function Invoke-WinpodxRedact {
<#
.SYNOPSIS
Strip secrets (net user pw, Authorization Bearer, xfreerdp /p:&lt;pw&gt;, password=/token=/apikey=, base64 >= 40) from $Line.
.EXAMPLE
$safe = Invoke-WinpodxRedact -Line $rawLogLine
#>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [AllowNull()] $Line
    )

    if ($null -eq $Line) { return '' }
    if ($Line -isnot [string]) { $Line = [string]$Line }
    if ($Line.Length -eq 0) { return $Line }

    # Order matches agent_install_state.redact_log_line so output is
    # byte-identical for any input. .NET regex back-reference uses $1.
    $out = $script:WpxNetUserRe.Replace($Line, ('$1' + $script:WpxRedacted))
    $out = $script:WpxAuthBearerRe.Replace($out, ('$1' + $script:WpxRedacted))
    $out = $script:WpxFreerdpPwColonRe.Replace($out, ('$1' + $script:WpxRedacted))
    $out = $script:WpxFreerdpPwSpaceRe.Replace($out, ('$1' + $script:WpxRedacted))
    $out = $script:WpxKvSecretRe.Replace($out, ('$1=' + $script:WpxRedacted))
    $out = $script:WpxBase64Re.Replace($out, $script:WpxBase64Redacted)
    return $out
}

# ----- Public: structured logger -------------------------------------

function Write-WinpodxLog {
<#
.SYNOPSIS
Append one redacted JSON line to install.log.
.EXAMPLE
Write-WinpodxLog -Level INFO -Step rdprrap_installed -Event start -Extra @{ attempt = 1 }
#>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [ValidateSet('DEBUG','INFO','WARN','ERROR')] [string] $Level,
        [Parameter(Mandatory)] [string] $Step,
        [Parameter(Mandatory)] [string] $Event,
        [hashtable] $Extra
    )

    $record = [ordered]@{
        ts    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        level = $Level
        step  = $Step
        event = $Event
    }
    if ($PSBoundParameters.ContainsKey('Extra') -and $null -ne $Extra) {
        foreach ($k in $Extra.Keys) {
            # Reserved keys win -- caller can't shadow ts/level/step/event.
            if ($record.Contains($k)) { continue }
            $record[$k] = $Extra[$k]
        }
    }

    $json = ($record | ConvertTo-Json -Compress -Depth 6)
    $safe = Invoke-WinpodxRedact -Line $json

    if (-not (Test-Path -LiteralPath $script:WpxStateDir -PathType Container)) {
        New-Item -ItemType Directory -Path $script:WpxStateDir -Force | Out-Null
    }
    Add-Content -LiteralPath $script:WpxLogPath -Value $safe -Encoding utf8
}

# ----- Public: install_failure.json writer ---------------------------

function _Wpx_GetSessionId {
    if (Test-Path -LiteralPath $script:WpxSessionIdPath -PathType Leaf) {
        $raw = (Get-Content -LiteralPath $script:WpxSessionIdPath -Raw -ErrorAction SilentlyContinue)
        if ($null -ne $raw) {
            $trimmed = $raw.Trim()
            if ($trimmed -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
                return $trimmed
            }
        }
    }
    # Schema requires a UUID. If the session file is missing/malformed,
    # mint a fresh one so the failure record still validates -- the
    # orchestrator's session bootstrap is upstream of any failure write.
    return [guid]::NewGuid().ToString()
}

function _Wpx_GetWindowsBuild {
    try {
        $os = [System.Environment]::OSVersion
        if ($null -ne $os -and $null -ne $os.Version) {
            return $os.Version.ToString()
        }
    } catch { }
    return 'unknown'
}

function _Wpx_GetDiskFs {
    # Get-Volume is Windows-only; on pwsh-on-Linux we return 'unknown'.
    $cmd = Get-Command -Name Get-Volume -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return 'unknown' }
    try {
        $sysDrive = $env:SystemDrive
        if ([string]::IsNullOrWhiteSpace($sysDrive)) { $sysDrive = 'C:' }
        $letter = $sysDrive.TrimEnd(':').TrimEnd('\')
        $vol = Get-Volume -DriveLetter $letter -ErrorAction Stop
        if ($null -ne $vol -and -not [string]::IsNullOrWhiteSpace($vol.FileSystem)) {
            return ($vol.FileSystem.ToString().ToLowerInvariant())
        }
    } catch { }
    return 'unknown'
}

function _Wpx_GetFreeBytes {
    $cmd = Get-Command -Name Get-Volume -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return 0 }
    try {
        $sysDrive = $env:SystemDrive
        if ([string]::IsNullOrWhiteSpace($sysDrive)) { $sysDrive = 'C:' }
        $letter = $sysDrive.TrimEnd(':').TrimEnd('\')
        $vol = Get-Volume -DriveLetter $letter -ErrorAction Stop
        if ($null -ne $vol -and $null -ne $vol.SizeRemaining) {
            return [int64]$vol.SizeRemaining
        }
    } catch { }
    return 0
}

function _Wpx_GetRamTotalMb {
    # CIM is Windows-only. Fall back to 0 (schema requires the field).
    $cmd = Get-Command -Name Get-CimInstance -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return 0 }
    try {
        $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
        if ($null -ne $cs -and $null -ne $cs.TotalPhysicalMemory) {
            return [int][math]::Floor([double]$cs.TotalPhysicalMemory / 1MB)
        }
    } catch { }
    return 0
}

function _Wpx_TailLog {
    # Last 50 lines of install.log, redacted, each capped to 1000 chars
    # (schema items.maxLength). Returns a plain pwsh list; the JSON-
    # serialisation boundary in Write-WinpodxFailure wraps with @(...)
    # to keep last_log_lines a JSON array even when this list is empty.
    if (-not (Test-Path -LiteralPath $script:WpxLogPath -PathType Leaf)) {
        return @()
    }
    $lines = $null
    try {
        $lines = Get-Content -LiteralPath $script:WpxLogPath -Tail 50 -ErrorAction Stop
    } catch {
        return @()
    }
    if ($null -eq $lines) { return @() }
    $out = foreach ($ln in $lines) {
        $safe = Invoke-WinpodxRedact -Line $ln
        if ($safe.Length -gt 1000) { $safe = $safe.Substring(0, 1000) }
        $safe
    }
    return @($out)
}

function Write-WinpodxFailure {
<#
.SYNOPSIS
Write a schema-conformant install_failure.json with environment auto-fill and a redacted log tail.
.EXAMPLE
Write-WinpodxFailure -Step rdprrap_installed -Phase 2 -Attempt 3 -MaxAttempts 3 -ExitCode 1 -ErrorClass rdprrap_install_failed -ErrorSummary 'installer.exe exited 1'
#>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [ValidatePattern('^[a-z_]+$')]
        [ValidateLength(1,80)] [string] $Step,

        [Parameter(Mandatory)] [ValidateRange(0,9)] [int] $Phase,

        [Parameter(Mandatory)] [ValidateRange(1,[int]::MaxValue)] [int] $Attempt,

        [Parameter(Mandatory)] [ValidateRange(1,[int]::MaxValue)] [int] $MaxAttempts,

        [Parameter(Mandatory)] [int] $ExitCode,

        [Parameter(Mandatory)] [ValidatePattern('^[a-z_]+$')]
        [ValidateLength(1,80)] [string] $ErrorClass,

        [Parameter(Mandatory)] [AllowEmptyString()] [string] $ErrorSummary
    )

    $summary = Invoke-WinpodxRedact -Line $ErrorSummary
    if ($summary.Length -gt 500) { $summary = $summary.Substring(0, 500) }

    $env_block = [ordered]@{
        windows_build = _Wpx_GetWindowsBuild
        disk_fs       = _Wpx_GetDiskFs
        free_bytes    = _Wpx_GetFreeBytes
        ram_total_mb  = _Wpx_GetRamTotalMb
    }

    # Wrap the helper's output with @(...) so `last_log_lines` always
    # serialises as a JSON array. pwsh's function-return pipeline unwraps
    # an empty @() to $null, which would otherwise emit
    # `"last_log_lines": null` and break the install_failure schema. The
    # wrap is at the JSON boundary (here) rather than inside the helper
    # so callers that don't serialise can use the helper idiomatically.
    $tail = @(_Wpx_TailLog)

    $payload = [ordered]@{
        session_id     = _Wpx_GetSessionId
        failed_step    = $Step
        phase          = $Phase
        attempt        = $Attempt
        max_attempts   = $MaxAttempts
        exit_code      = $ExitCode
        error_class    = $ErrorClass
        error_summary  = $summary
        timestamp_utc  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        environment    = $env_block
        last_log_lines = $tail
    }

    # Bare minimum schema check -- the helper does not pull in
    # jsonschema; the orchestrator + tests guard fuller validation.
    foreach ($field in $script:WpxFailureRequired) {
        if (-not $payload.Contains($field)) {
            throw "install_failure payload missing required field: $field"
        }
    }

    $json = ($payload | ConvertTo-Json -Depth 6)
    _Wpx_WriteAtomic -Path $script:WpxFailurePath -Content $json
}
