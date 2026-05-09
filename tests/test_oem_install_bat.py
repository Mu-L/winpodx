"""Static checks for config/oem/install.bat first-boot bootstrap shim.

The agent-first refactor moved every install decision into
config/oem/install-state-helpers.ps1 + install-step-functions.ps1. The
.bat is now a tiny shim: dot-source the two .ps1 files and run the
orchestrator. Tests below pin only that shim contract -- the body of
each install step lives in pwsh and is exercised by the
pwsh-on-Linux harness owned by test-engineer.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_BAT = REPO_ROOT / "config" / "oem" / "install.bat"


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
