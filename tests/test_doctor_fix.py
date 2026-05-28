# SPDX-License-Identifier: MIT
"""Pin `winpodx doctor --fix` auto-remediation (0.6.0 item K).

`--fix` runs the registered fix for every warn/fail finding that has one.
Fixes are best-effort + idempotent: a failing fix is reported, not fatal,
and the remaining fixes still run. Findings without a `.fix` are reported
but left alone.
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import pytest

from winpodx.cli import doctor
from winpodx.cli.doctor import Finding, _run_fixes, handle_doctor


def _args(*, json_=False, quick=False, fix=False) -> argparse.Namespace:
    return argparse.Namespace(json=json_, quick=quick, fix=fix)


# ---- Finding.fix field ----


def test_finding_fix_defaults_none() -> None:
    assert Finding("ok", "x").fix is None


def test_finding_fix_callable_runs() -> None:
    f = Finding("warn", "x", fix=lambda: "done")
    assert f.fix is not None
    assert f.fix() == "done"


def test_finding_fix_excluded_from_json(capsys: pytest.CaptureFixture[str]) -> None:
    # --json must not choke on a callable field; payload carries only the
    # four serialisable keys.
    findings = [Finding("warn", "t", detail="d", suggestion="s", fix=lambda: "x")]
    with (
        patch.object(doctor, "_check_install_source", return_value=findings[0]),
        patch.object(doctor, "_check_freerdp", return_value=Finding("ok", "f")),
        patch.object(doctor, "_check_kvm", return_value=Finding("ok", "k")),
        patch.object(doctor, "_check_container_backend", return_value=[]),
        patch.object(doctor, "_check_config_state", return_value=Finding("ok", "c")),
        patch.object(doctor, "_check_desktop_entries", return_value=Finding("ok", "de")),
        patch.object(doctor, "_check_pending_setup", return_value=Finding("ok", "p")),
        patch.object(doctor, "_check_autostart_entry", return_value=Finding("ok", "a")),
        patch.object(doctor, "_check_initialized_flag", return_value=Finding("ok", "i")),
        patch.object(doctor, "_check_container_health", return_value=[]),
    ):
        handle_doctor(_args(json_=True))
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert all(
        set(item.keys()) == {"severity", "title", "detail", "suggestion"} for item in payload
    )


# ---- _run_fixes mechanics ----


def test_run_fixes_runs_only_warn_fail_with_fix(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    findings = [
        Finding("ok", "ok-with-fix", fix=lambda: calls.append("ok") or "x"),  # skipped: ok
        Finding("warn", "warn-no-fix"),  # skipped: no fix
        Finding("warn", "warn-fix", fix=lambda: calls.append("warn") or "fixed-warn"),
        Finding("fail", "fail-fix", fix=lambda: calls.append("fail") or "fixed-fail"),
    ]
    _run_fixes(findings)
    assert calls == ["warn", "fail"]  # ok-with-fix NOT run
    out = capsys.readouterr().out
    assert "fixed-warn" in out
    assert "fixed-fail" in out


def test_run_fixes_no_fixable(capsys: pytest.CaptureFixture[str]) -> None:
    _run_fixes([Finding("ok", "a"), Finding("warn", "b")])  # no fixes
    assert "nothing to remediate" in capsys.readouterr().out


def test_run_fixes_failed_fix_is_caught(capsys: pytest.CaptureFixture[str]) -> None:
    def boom() -> str:
        raise RuntimeError("kaboom")

    ran_after = []
    findings = [
        Finding("fail", "explodes", fix=boom),
        Finding("warn", "after", fix=lambda: ran_after.append(1) or "ok"),
    ]
    _run_fixes(findings)
    out = capsys.readouterr().out
    assert "fix failed" in out
    assert "kaboom" in out
    assert ran_after == [1]  # a failing fix doesn't abort the rest


def test_fix_runs_even_with_fail_finding(capsys: pytest.CaptureFixture[str]) -> None:
    # A FAIL finding makes handle_doctor exit 1 -- the fix must run BEFORE
    # that exit.
    ran = []
    fail_fixable = Finding("fail", "boom", fix=lambda: ran.append(1) or "fixed")
    with (
        patch.object(doctor, "_check_install_source", return_value=fail_fixable),
        patch.object(doctor, "_check_freerdp", return_value=Finding("ok", "f")),
        patch.object(doctor, "_check_kvm", return_value=Finding("ok", "k")),
        patch.object(doctor, "_check_container_backend", return_value=[]),
        patch.object(doctor, "_check_config_state", return_value=Finding("ok", "c")),
        patch.object(doctor, "_check_desktop_entries", return_value=Finding("ok", "de")),
        patch.object(doctor, "_check_pending_setup", return_value=Finding("ok", "p")),
        patch.object(doctor, "_check_autostart_entry", return_value=Finding("ok", "a")),
        patch.object(doctor, "_check_initialized_flag", return_value=Finding("ok", "i")),
        patch.object(doctor, "_check_container_health", return_value=[]),
    ):
        with pytest.raises(SystemExit) as exc:
            handle_doctor(_args(fix=True))
    assert exc.value.code == 1  # still exits on FAIL
    assert ran == [1]  # but fix ran first
    assert "fixed" in capsys.readouterr().out


def test_no_fix_flag_skips_remediation(capsys: pytest.CaptureFixture[str]) -> None:
    ran = []
    warn_fixable = Finding("warn", "w", fix=lambda: ran.append(1) or "fixed")
    with (
        patch.object(doctor, "_check_install_source", return_value=warn_fixable),
        patch.object(doctor, "_check_freerdp", return_value=Finding("ok", "f")),
        patch.object(doctor, "_check_kvm", return_value=Finding("ok", "k")),
        patch.object(doctor, "_check_container_backend", return_value=[]),
        patch.object(doctor, "_check_config_state", return_value=Finding("ok", "c")),
        patch.object(doctor, "_check_desktop_entries", return_value=Finding("ok", "de")),
        patch.object(doctor, "_check_pending_setup", return_value=Finding("ok", "p")),
        patch.object(doctor, "_check_autostart_entry", return_value=Finding("ok", "a")),
        patch.object(doctor, "_check_initialized_flag", return_value=Finding("ok", "i")),
        patch.object(doctor, "_check_container_health", return_value=[]),
    ):
        handle_doctor(_args(fix=False))  # no --fix
    assert ran == []  # fix NOT run without --fix


# ---- concrete fix functions ----


def test_fix_autostart_entry_removes_dangling() -> None:
    with patch("winpodx.desktop.autostart.disable_tray_autostart", return_value=True):
        result = doctor._fix_autostart_entry()
    assert "removed" in result


def test_fix_desktop_entries_reregisters() -> None:
    fake_apps = [object(), object(), object()]
    with (
        patch("winpodx.core.app.list_available_apps", return_value=fake_apps),
        patch("winpodx.cli.app._register_desktop_entries") as reg,
    ):
        result = doctor._fix_desktop_entries()
    reg.assert_called_once_with(fake_apps)
    assert "3 app(s)" in result


def test_fix_pending_setup_delegates_to_resume() -> None:
    with patch("winpodx.utils.pending.resume") as resume:
        result = doctor._fix_pending_setup()
    resume.assert_called_once()
    assert "resumed" in result


# ---- check wiring: fixable findings expose .fix ----


def test_autostart_check_fail_carries_fix() -> None:
    # When the winpodx binary is missing but an autostart entry exists, the
    # FAIL finding must carry _fix_autostart_entry.
    import winpodx.cli.doctor as d

    with patch("shutil.which", return_value=None), patch("pathlib.Path.exists", return_value=True):
        finding = d._check_autostart_entry()
    assert finding.severity == "fail"
    assert finding.fix is doctor._fix_autostart_entry


def test_desktop_entries_check_warn_carries_fix() -> None:
    class _App:
        def __init__(self, name: str) -> None:
            self.name = name

    with (
        patch("winpodx.core.app.list_available_apps", return_value=[_App("word")]),
        patch("pathlib.Path.exists", return_value=False),
    ):
        finding = doctor._check_desktop_entries()
    assert finding.severity == "warn"
    assert finding.fix is doctor._fix_desktop_entries
