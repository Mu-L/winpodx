# SPDX-License-Identifier: MIT
"""``winpodx doctor`` -- diagnose common winpodx state issues (#255 PR 6).

Read-only diagnostic. Walks a small set of checks for things that
commonly leave users stuck (half-installed state, orphan containers,
stale autostart entries, broken deps) and prints a per-check report
with a severity tag and the suggested next command.

Output format mirrors ``apt`` / ``brew doctor``:

    [OK]   freerdp 3.x present at /usr/bin/xfreerdp3
    [WARN] tray autostart entry references missing binary
           Suggested: winpodx uninstall && winpodx setup
    [FAIL] container winpodx-windows exists but config is missing
           Suggested: winpodx uninstall --purge --yes

Doctor never mutates state -- the suggested commands are printed for
the user to copy.

Exit codes:
    0 -- no FAIL findings (warnings may be present)
    1 -- one or more FAIL findings

Designed to be cheap (< 2 s on a healthy install): every subprocess
probe has a short timeout, and the network never gets touched.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from winpodx.core.i18n import tr


@dataclass(frozen=True)
class Finding:
    severity: str  # "ok" | "warn" | "fail"
    title: str
    detail: str = ""
    suggestion: str = ""
    # Optional auto-remediation for `winpodx doctor --fix`. A callable that
    # performs the fix and returns a one-line result string. Only invoked for
    # warn / fail findings. None => the finding has no safe automatic fix
    # (e.g. "binary present but config missing" needs an interactive setup).
    # Excluded from --json output (callables aren't serialisable; the JSON
    # payload only carries severity / title / detail / suggestion).
    fix: Optional[Callable[[], str]] = None

    def severity_tag(self) -> str:
        return {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}.get(self.severity, "[?]   ")


def handle_doctor(args: argparse.Namespace) -> None:
    """Run all checks + print the report. Exit 1 on any FAIL finding.

    Flags
    -----
    --json   Serialise the Finding list to JSON (severity, title, detail,
             suggestion) instead of the human-readable report.
    --quick  Skip slow probes (container health / guest exec) and run only
             the cheap local checks: freerdp, kvm, backend-on-PATH,
             config-state, desktop-entries, pending-setup, autostart,
             initialized-flag. Useful for quick pre-flight checks where a
             10-second timeout on ``podman ps`` would be disruptive.
    --fix    After reporting, auto-remediate every warn / fail finding that
             carries a safe, idempotent fix (re-register stale desktop
             entries, remove a dangling autostart entry, resume a pending
             install). Findings without a registered fix are reported but
             left alone. Re-run ``winpodx doctor`` afterwards to verify.
    """
    emit_json: bool = getattr(args, "json", False)
    quick: bool = getattr(args, "quick", False)
    do_fix: bool = getattr(args, "fix", False)

    findings: list[Finding] = []

    # --- cheap / always-on checks ---
    findings.append(_check_install_source())
    findings.append(_check_freerdp())
    findings.append(_check_kvm())
    findings.extend(_check_container_backend())
    findings.append(_check_config_state())
    findings.append(_check_desktop_entries())
    findings.append(_check_pending_setup())
    findings.append(_check_autostart_entry())
    findings.append(_check_initialized_flag())

    # --- slow probes (container health / guest exec): skipped by --quick ---
    if not quick:
        findings.extend(_check_container_health())

    if emit_json:
        import json

        payload = [
            {
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "suggestion": f.suggestion,
            }
            for f in findings
            if f is not None
        ]
        print(json.dumps(payload, indent=2))
        fail_count = sum(1 for f in findings if f is not None and f.severity == "fail")
        if fail_count:
            sys.exit(1)
        return

    print()
    print("=== winpodx doctor ===")
    if quick:
        print(tr("(--quick: container-health probe skipped)"))
    print()
    fail_count = 0
    warn_count = 0
    for f in findings:
        if f is None:
            continue
        if f.severity == "fail":
            fail_count += 1
        elif f.severity == "warn":
            warn_count += 1
        print(f"{f.severity_tag()} {f.title}")
        if f.detail:
            print(f"        {f.detail}")
        if f.suggestion:
            print(tr("        Suggested: {suggestion}").format(suggestion=f.suggestion))

    print()

    # --- auto-fix: run BEFORE the summary exit so fixes always execute even
    # when there's a FAIL finding (which otherwise exits 1). ---
    if do_fix:
        _run_fixes(findings)
        print()

    if fail_count:
        print(
            tr("Summary: {fail_count} FAIL, {warn_count} WARN").format(
                fail_count=fail_count, warn_count=warn_count
            )
        )
        sys.exit(1)
    elif warn_count:
        print(
            tr("Summary: {warn_count} WARN, no FAIL — winpodx is mostly OK.").format(
                warn_count=warn_count
            )
        )
    else:
        print(tr("Summary: all checks passed."))


def _run_fixes(findings: list[Finding]) -> None:
    """Run the registered fix for every warn / fail finding that has one.

    Each fix is best-effort + idempotent: an exception is caught and reported
    rather than aborting the remaining fixes. Findings without a `.fix` (or
    with severity "ok") are skipped silently.
    """
    fixable = [f for f in findings if f is not None and f.fix is not None and f.severity != "ok"]
    if not fixable:
        print(tr("--fix: nothing to remediate (no auto-fixable warnings/failures)."))
        return
    print(tr("--fix: remediating {n} finding(s)...").format(n=len(fixable)))
    for f in fixable:
        try:
            result = f.fix()  # type: ignore[misc]
            print(tr("  ✓ {title}: {result}").format(title=f.title, result=result))
        except Exception as e:  # noqa: BLE001 — a failed fix must not abort the rest
            print(tr("  ✗ {title}: fix failed ({error})").format(title=f.title, error=e))
    print(tr("Re-run `winpodx doctor` to verify."))


# -----------------------------------------------------------------------
# Individual checks. Each returns a single Finding or a list of them.
# -----------------------------------------------------------------------


def _check_install_source() -> Finding:
    try:
        from winpodx.utils.install_source import detect

        src = detect()
    except Exception as e:  # noqa: BLE001
        return Finding("warn", "install source detection failed", detail=str(e))
    if src.kind == "unknown":
        return Finding(
            "warn",
            "install source not detected",
            detail=src.label,
            suggestion="Reinstall via curl install.sh or your distro's package manager.",
        )
    return Finding("ok", f"install source: {src.label}")


def _check_freerdp() -> Finding:
    # Delegate to winpodx.utils.deps.check_freerdp so doctor sees the same
    # set of binaries the launcher does (xfreerdp3 / xfreerdp / sdl-freerdp3
    # / sdl-freerdp + Flatpak). Pre-0.6.0 doctor only looked for the first
    # two and reported MISSING on hosts that had the others.
    from winpodx.utils.deps import check_freerdp

    dep = check_freerdp()
    if dep.found:
        # Best-effort version string for the human reader; a failure to run
        # --version doesn't downgrade the finding (binary exists, that's the
        # signal we care about for doctor).
        version_line = ""
        try:
            result = subprocess.run(
                [dep.path, "--version"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            version_line = result.stdout.splitlines()[0] if result.stdout else ""
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return Finding("ok", f"freerdp present at {dep.path}", detail=version_line)
    return Finding(
        "fail",
        "freerdp not found on PATH",
        detail=dep.note
        or "Looked for xfreerdp3 / xfreerdp / sdl-freerdp3 / sdl-freerdp; none resolved.",
        suggestion="Install via your distro package manager (freerdp / freerdp3 / freerdp-x11).",
    )


def _check_kvm() -> Finding:
    # Delegate to winpodx.utils.deps.check_kvm so doctor keys off the same
    # /dev/kvm signal as the setup wizard + GUI Quick Start.
    from winpodx.utils.deps import check_kvm

    dep = check_kvm()
    if dep.found:
        return Finding("ok", f"{dep.path} present")
    return Finding(
        "fail",
        "/dev/kvm not present",
        detail=(
            "Hardware virtualization is disabled, missing kvm module, "
            "or your user lacks the kvm group."
        ),
        suggestion=(
            "Check BIOS VT-x/AMD-V, run `modprobe kvm_intel` or "
            "`modprobe kvm_amd`, ensure your user is in the kvm group."
        ),
    )


def _check_container_backend() -> list[Finding]:
    """Probe the configured backend + verify it resolves."""
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception as e:  # noqa: BLE001
        return [Finding("warn", "config could not be loaded", detail=str(e))]

    backend = cfg.pod.backend
    if backend == "manual":
        return [Finding("ok", "backend = manual (no container management)")]
    path = shutil.which(backend)
    if path is None:
        return [
            Finding(
                "fail",
                f"configured backend {backend!r} not on PATH",
                suggestion=(
                    f"Install {backend} or change backend via `winpodx config set pod.backend ...`."
                ),
            )
        ]
    return [Finding("ok", f"backend {backend!r} at {path}")]


def _check_config_state() -> Finding:
    """Detect half-installed state: binary present but config missing,
    or vice versa."""
    from winpodx.core.config import Config

    config_path = Config.path()
    binary_path = shutil.which("winpodx")
    if binary_path and not config_path.exists():
        return Finding(
            "warn",
            "winpodx binary present but config missing",
            detail=f"binary: {binary_path}; expected config: {config_path}",
            suggestion=(
                "Run `winpodx setup` (or just `winpodx` -- first-run prompt will offer setup)."
            ),
        )
    if config_path.exists() and not binary_path:
        return Finding(
            "fail",
            "config present but winpodx binary not on PATH",
            detail=f"config: {config_path}; PATH binary: missing",
            suggestion="Reinstall winpodx via curl install.sh or your distro's package manager.",
        )
    if not binary_path and not config_path.exists():
        return Finding(
            "warn",
            "winpodx not installed (binary + config both absent)",
            suggestion="Install via `curl ... install.sh | bash` or distro package manager.",
        )
    return Finding("ok", "binary + config both present")


def _check_container_health() -> list[Finding]:
    """Check whether a container exists and matches what config expects."""
    try:
        from winpodx.core.config import Config
    except Exception:  # noqa: BLE001
        return []
    try:
        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return []
    if cfg.pod.backend not in ("podman", "docker"):
        return []
    runtime = shutil.which(cfg.pod.backend)
    if runtime is None:
        return []
    try:
        result = subprocess.run(
            [runtime, "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [
            Finding(
                "warn",
                f"could not query {cfg.pod.backend} ps",
                suggestion=f"Check that {cfg.pod.backend} is functional.",
            )
        ]

    findings: list[Finding] = []
    container_name = cfg.pod.container_name
    found = False
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        if name == container_name:
            found = True
            findings.append(Finding("ok", f"container {container_name} state: {state.lower()}"))
            break
    if not found:
        findings.append(
            Finding(
                "warn",
                f"container {container_name} not found",
                detail=(
                    "Config references a container that doesn't exist "
                    "(may be intentional if you haven't run setup yet)."
                ),
                suggestion="Run `winpodx pod start` or `winpodx setup` to create it.",
            )
        )
    return findings


def _check_pending_setup() -> Finding:
    """Half-installed marker from install.sh -- means a prior install
    didn't finish wait-ready / migrate / discovery."""
    from winpodx.utils.paths import config_dir

    pending = config_dir() / ".pending_setup"
    if not pending.exists():
        return Finding("ok", "no pending install steps")
    try:
        steps = pending.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        steps = []
    return Finding(
        "warn",
        f"pending setup steps detected ({len(steps)} item(s))",
        detail=", ".join(steps) if steps else "(marker present but empty)",
        suggestion=(
            "Run `winpodx doctor --fix` to resume now, or any `winpodx <cmd>` to auto-resume."
        ),
        fix=_fix_pending_setup,
    )


def _check_desktop_entries() -> Finding:
    """Discovered apps with no `.desktop` entry on disk — a common state after
    an interrupted refresh or a desktop-cache wipe. The apps are known
    (persisted under the data dir) but never landed in the DE menu."""
    try:
        from winpodx.core.app import list_available_apps
        from winpodx.utils.paths import applications_dir
    except Exception as e:  # noqa: BLE001
        return Finding("warn", "could not enumerate apps", detail=str(e))

    apps = list_available_apps()
    if not apps:
        return Finding("ok", "no discovered apps yet (nothing to register)")
    appdir = applications_dir()
    missing = [a.name for a in apps if not (appdir / f"winpodx-{a.name}.desktop").exists()]
    if not missing:
        return Finding("ok", f"all {len(apps)} discovered app(s) have desktop entries")
    return Finding(
        "warn",
        f"{len(missing)} discovered app(s) missing desktop entries",
        detail=", ".join(missing[:8]) + (" ..." if len(missing) > 8 else ""),
        suggestion="Run `winpodx doctor --fix` (or `winpodx app refresh`) to re-register them.",
        fix=_fix_desktop_entries,
    )


def _check_autostart_entry() -> Finding:
    """Tray autostart entry referencing a missing binary is a common
    leftover after a botched uninstall."""
    from winpodx.utils.paths import config_dir

    autostart = config_dir().parent / "autostart" / "winpodx-tray.desktop"
    if not autostart.exists():
        return Finding("ok", "no autostart entry (or none expected)")
    binary = shutil.which("winpodx")
    if binary is None:
        return Finding(
            "fail",
            "autostart entry references a missing winpodx binary",
            detail=str(autostart),
            suggestion="Run `winpodx doctor --fix` to remove the dangling autostart entry.",
            fix=_fix_autostart_entry,
        )
    return Finding("ok", "autostart entry present and binary resolves")


def _check_initialized_flag() -> Finding:
    """First-run prompt fires when cfg.pod.initialized is False. Surface
    as info so users know whether the prompt is expected on next run."""
    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception:  # noqa: BLE001
        return Finding("warn", "could not read initialized flag (config load failed)")
    if cfg.pod.initialized:
        return Finding("ok", "cfg.pod.initialized = true (no first-run prompt expected)")
    return Finding(
        "warn",
        "cfg.pod.initialized = false (first-run prompt will fire on next CLI/GUI launch)",
        suggestion="Run `winpodx setup` to silence the prompt and provision the guest.",
    )


# -----------------------------------------------------------------------
# Fix actions for `winpodx doctor --fix`. Each is idempotent + best-effort
# and returns a one-line result string. Host-side fixes are self-contained;
# the pending-setup fix delegates to the existing resume path (which is
# real-Windows smoke-gated, but this only *triggers* it -- no new guest code).
# -----------------------------------------------------------------------


def _fix_autostart_entry() -> str:
    """Remove a dangling tray autostart entry (binary no longer on PATH)."""
    from winpodx.desktop.autostart import disable_tray_autostart

    removed = disable_tray_autostart()
    return "removed dangling autostart entry" if removed else "no autostart entry to remove"


def _fix_desktop_entries() -> str:
    """Re-register `.desktop` entries from the already-persisted app list.

    Uses the persisted apps (no guest round-trip) so the fix works even when
    the pod is down. `_register_desktop_entries` both installs missing entries
    and prunes stale ones, so it converges the menu to the persisted set.
    """
    from winpodx.cli.app import _register_desktop_entries
    from winpodx.core.app import list_available_apps

    apps = list_available_apps()
    _register_desktop_entries(apps)
    return f"re-registered desktop entries for {len(apps)} app(s)"


def _fix_pending_setup() -> str:
    """Resume a half-finished install (delegates to the unified resume path)."""
    from winpodx.utils import pending

    lines: list[str] = []
    pending.resume(printer=lines.append)
    return "resumed pending install steps" + (f" ({len(lines)} line(s))" if lines else "")
