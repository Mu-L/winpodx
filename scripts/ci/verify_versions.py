#!/usr/bin/env python3
"""Verify that the project version string matches across all version-stamped files.

Runs during the lint stage of CI so a release-prep commit that forgets to bump
one of the version-stamped files (the original sin behind v0.5.2's mis-named
`winpodx_0.5.1_*.deb` assets, which shipped because `debian/changelog` stayed
at 0.5.1 while `pyproject.toml` and `__init__.py` moved to 0.5.2) fails CI
before the tag is pushed, not after.

Files checked:
  - pyproject.toml             ([project] version)
  - src/winpodx/__init__.py    (__version__)
  - debian/changelog           (first entry: winpodx (X.Y.Z) ...)

Exits 0 if all three agree, 1 with a stamped diff otherwise. Read-only —
makes no changes to the tree.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# stdlib on 3.11+; tomli backfill on 3.9 / 3.10 (matches winpodx's own pattern).
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

ROOT = Path(__file__).resolve().parents[2]


def pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def init_version() -> str:
    text = (ROOT / "src" / "winpodx" / "__init__.py").read_text()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise SystemExit("__version__ not found in src/winpodx/__init__.py")
    return m.group(1)


def debian_version() -> str:
    text = (ROOT / "debian" / "changelog").read_text()
    m = re.match(r"winpodx \(([^)]+)\)", text)
    if not m:
        raise SystemExit("first debian/changelog entry doesn't match 'winpodx (X.Y.Z) ...'")
    return m.group(1)


def main() -> int:
    versions = {
        "pyproject.toml [project] version": pyproject_version(),
        "src/winpodx/__init__.py __version__": init_version(),
        "debian/changelog (first entry)": debian_version(),
    }
    unique = set(versions.values())
    if len(unique) == 1:
        print(f"Version stamps consistent: {unique.pop()}")
        return 0
    print("Version stamp mismatch — release prep incomplete:")
    for path, v in versions.items():
        print(f"  {path:42s}  {v}")
    print(
        "\nBump the lagging file(s) and re-run before tagging.\n"
        "  See `chore(release): vX.Y.Z` commits on main for the convention."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
