"""Static checks for config/oem/install.bat first-boot bootstrap shim.

The agent-first refactor moved every install decision into
config/oem/install-state-helpers.ps1 + install-step-functions.ps1. The
.bat is now a tiny shim: dot-source the two .ps1 files and run the
orchestrator. Tests below pin only that shim contract -- the body of
each install step lives in pwsh and is exercised by the
pwsh-on-Linux harness owned by test-engineer.

Also includes regression guards for security-review findings on
install-step-functions.ps1 / watchdog.ps1 (token path mismatch,
rotation post-condition, watchdog steady-state behaviour, OEM
token ACL tightening). These are static-grep checks -- the
behavioural tests live under tests/pwsh/ in test-engineer's harness.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OEM_DIR = REPO_ROOT / "config" / "oem"
INSTALL_BAT = OEM_DIR / "install.bat"
STEP_FUNCTIONS = OEM_DIR / "install-step-functions.ps1"
WATCHDOG = OEM_DIR / "agent" / "watchdog.ps1"


def test_install_bat_exists() -> None:
    assert INSTALL_BAT.is_file()


def test_install_bat_has_no_non_ascii() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert all(ord(ch) < 128 for ch in text)


def test_install_bat_dot_sources_helpers_then_steps() -> None:
    """Helpers must dot-source first; step-functions reference helpers
    at parse time, so reversing the order would NRE at first call."""
    text = INSTALL_BAT.read_text(encoding="utf-8")
    helpers_idx = text.index("install-state-helpers.ps1")
    steps_idx = text.index("install-step-functions.ps1")
    invoke_idx = text.index("Invoke-InstallStateMachine")
    assert helpers_idx < steps_idx < invoke_idx


def test_install_bat_runs_orchestrator() -> None:
    """The .bat exit code must reflect the orchestrator return value
    so dockur's FirstLogonCommand surface (and host wait-ready) sees
    a non-zero exit when the state machine fails."""
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert "exit (Invoke-InstallStateMachine)" in text
    assert "exit /b %WPX_RC%" in text


def test_install_bat_preflight_checks_helper_files() -> None:
    """A missing .ps1 sibling is a packaging bug. Surface it loudly
    rather than letting powershell cold-start, fail to dot-source,
    and emit a confusing 'Invoke-InstallStateMachine not found'."""
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert 'if not exist "%WPX_HELPERS%"' in text
    assert 'if not exist "%WPX_STEPS%"' in text


# ----- Security review regression guards -----------------------------


def test_step_functions_token_src_at_oem_root() -> None:
    """Security review #1: WpxAgentTokenSrc must point at C:\\OEM\\agent_token.txt
    (the root of OEM, NOT under C:\\OEM\\agent\\). The host stager
    (utils/agent_token.py) and agent.ps1 ($TokenPath) are the source of
    truth -- they both name the root-level path. The earlier
    C:\\OEM\\agent\\agent_token.txt was a typo that made Phase 0.6 fail
    forever and turned Phase 3 rotation into a no-op."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    assert "$script:WpxAgentTokenSrc  = 'C:\\OEM\\agent_token.txt'" in text
    assert "C:\\OEM\\agent\\agent_token.txt" not in text


def test_step_functions_phase3_hardens_oem_token_cleanup() -> None:
    """Security review #2: Phase 3 cleanup must FAIL the step on
    zero/delete failure (not warn-and-continue). Look for the explicit
    return 1 paths on stat/zero failure, AND the post-condition that
    re-checks the OEM-source state."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    # Hard-fail return paths in the body when zeroing fails.
    assert "'oem_token_zero_failed'" in text
    assert "'oem_token_stat_failed'" in text
    # Post-condition explicitly inspects OEM source, not just the dst.
    assert "ReadAllBytes($script:WpxAgentTokenSrc)" in text


def test_step_functions_phase06_tightens_oem_source_acl() -> None:
    """Security review #12: Phase 0.6 must tighten the ACL on the
    OEM-source token BEFORE reading. Look for an icacls call on
    WpxAgentTokenSrc inside the token_staged body."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    # The early icacls call references the SOURCE constant, granting :R
    # (read-only) -- distinct from the DST call which uses :(R,W).
    assert "icacls.exe $script:WpxAgentTokenSrc /inheritance:r" in text
    assert "${user}:R" in text


def test_watchdog_branches_on_install_complete_marker() -> None:
    """Security review #6: watchdog must branch behaviour on the
    install_complete marker -- 3-cycle hard-exit during install, but
    indefinite respawn with exponential backoff in steady state.
    Pin both the marker constant and the steady backoff schedule."""
    text = WATCHDOG.read_text(encoding="utf-8")
    assert "install_complete.done" in text
    assert "Test-SteadyState" in text
    # Backoff schedule explicit values -- 30s, 60s, 120s, 240s, 300s cap.
    assert "$script:SteadyBackoffSecs = @(30, 60, 120, 240, 300)" in text


def test_watchdog_writes_steady_state_to_separate_log() -> None:
    """Security review #6: steady-state events go to watchdog.log,
    NOT install.log -- avoids unbounded growth of the install-time
    structured stream during long-lived sessions."""
    text = WATCHDOG.read_text(encoding="utf-8")
    assert "$script:WatchdogLog = 'C:\\winpodx\\install-state\\watchdog.log'" in text
    # The mode-aware logger picks WatchdogLog when steady, install.log
    # otherwise. Pin the conditional shape.
    assert "if (Test-SteadyState)" in text
