@echo off
REM ---------------------------------------------------------------------------
REM winpodx OEM first-boot bootstrap (agent-first state-machine version).
REM
REM This .bat is intentionally minimal -- a 14-line shim around the
REM PowerShell state machine. It exists at all only because dockur's
REM autounattend.xml FirstLogonCommand entry has historically been a
REM .bat invocation; rather than churn the dockur side, we keep the
REM .bat surface tiny and move every install decision into PowerShell:
REM   1. install-state-helpers.ps1 (marker / log / retry / failure
REM      primitives) is dot-sourced first.
REM   2. install-step-functions.ps1 (Phase 0 -> Phase 3 step bodies +
REM      orchestrator + watchdog launcher) is dot-sourced second.
REM   3. Invoke-InstallStateMachine runs the 10-step state machine.
REM
REM The .bat itself does NOT do any setup work. Defender exclusion,
REM state-dir creation, token staging, agent install, rdprrap install,
REM vbs launchers, oem runtime fixes, max sessions, multi-session
REM activation, and final token rotation all live as
REM Invoke-Step-<name> functions in install-step-functions.ps1, each
REM gated by the marker / post-condition / retry contract documented
REM in docs/design/AGENT_FIRST_INSTALL_DESIGN.md.
REM
REM Idempotency: re-running this bat is safe. The state-machine reads
REM <step>.done markers and skips completed steps after re-verifying
REM the post-condition. install-resume.ps1 (registered as a logon-
REM trigger Scheduled Task at Phase 1) calls back into this same
REM Invoke-InstallStateMachine on next logon if install_failure.json
REM is present.
REM
REM Exit code: 0 on full state-machine success, 1 on any step failure
REM (a sanitized install_failure.json is written in that case).
REM ---------------------------------------------------------------------------

setlocal

set "WPX_OEM_DIR=%~dp0"
if "%WPX_OEM_DIR:~-1%"=="\" set "WPX_OEM_DIR=%WPX_OEM_DIR:~0,-1%"

set "WPX_HELPERS=%WPX_OEM_DIR%\install-state-helpers.ps1"
set "WPX_STEPS=%WPX_OEM_DIR%\install-step-functions.ps1"

if not exist "%WPX_HELPERS%" (
    echo [winpodx] FATAL: install-state-helpers.ps1 missing at %WPX_HELPERS%
    exit /b 2
)
if not exist "%WPX_STEPS%" (
    echo [winpodx] FATAL: install-step-functions.ps1 missing at %WPX_STEPS%
    exit /b 2
)

echo [winpodx] Starting agent-first install state machine...

REM Dot-source the helpers + step functions, then run the orchestrator.
REM Single PowerShell invocation: launching one PS host (vs three
REM separate ones for source/source/run) avoids the two extra cold
REM starts that pre-OEM-v25 install.bat measured at ~1.8s each.
REM
REM ExecutionPolicy Bypass is required because dockur's image ships
REM with the default Restricted policy. Bypass is scoped to this
REM Process only -- we do not modify the machine policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    ". '%WPX_HELPERS%'; . '%WPX_STEPS%'; exit (Invoke-InstallStateMachine)"
set "WPX_RC=%ERRORLEVEL%"

if "%WPX_RC%"=="0" (
    echo [winpodx] Install state machine completed successfully.
) else (
    echo [winpodx] Install state machine exited with code %WPX_RC%.
    echo [winpodx] See C:\winpodx\install-state\install.log and install_failure.json.
)

endlocal & exit /b %WPX_RC%
