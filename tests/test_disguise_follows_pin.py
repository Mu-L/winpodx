# SPDX-License-Identifier: MIT
"""The patched-QEMU image must follow the dockur pin, not lag behind it.

`disguise build-image` bakes `FROM $DOCKUR_IMAGE` plus that image's QEMU
version, so moving the pin strands the patched image on the old dockur --
which makes dockur reinstall Windows from scratch on the next start.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from winpodx.cli import setup_cmd
from winpodx.core.config import Config

_PATCH_SCRIPT = Path(__file__).resolve().parents[1] / "packaging/qemu-disguise/patch-strings.sh"


class TestPatchScriptFailsLoudlyOnAMovedTarget:
    """`sed -i` exits 0 when nothing matched, which would ship a half-disguised
    image on a QEMU bump. Every substitution must be asserted instead."""

    def test_no_substitution_uses_bare_sed_i(self):
        offenders = [
            line
            for line in _PATCH_SCRIPT.read_text(encoding="utf-8").splitlines()
            if line.startswith("sed -i")
        ]

        assert offenders == [], f"unverified substitutions: {offenders}"

    def test_every_substitution_goes_through_the_checked_wrapper(self):
        text = _PATCH_SCRIPT.read_text(encoding="utf-8")

        assert "psed() {" in text
        assert text.count("\npsed ") >= 14
        assert "PATCH TARGET MISSING" in text


def _pin_flow(cfg, new_pin):
    run = MagicMock(
        side_effect=[
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0, stdout=json.dumps([new_pin]), stderr=""),
        ]
    )
    return run, SimpleNamespace(run=run, CalledProcessError=subprocess.CalledProcessError)


class TestUpdateImagePinRebuildsHardenedImage:
    def test_a_stale_patched_image_is_rebuilt_before_compose(self):
        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.disguise_level = "max"
        cfg.pod.image = "ghcr.io/dockur/windows@sha256:old"
        new_pin = f"ghcr.io/dockur/windows@sha256:{'b' * 64}"
        run, fake_subprocess = _pin_flow(cfg, new_pin)
        order: list[str] = []

        with (
            patch("winpodx.core.config.Config.load", return_value=cfg),
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("platform.machine", return_value="x86_64"),
            patch.dict(sys.modules, {"subprocess": fake_subprocess}),
            patch.object(cfg, "save"),
            patch("winpodx.cli.setup_cmd.disguise_image_is_stale", lambda _c: True),
            patch(
                "winpodx.cli.setup_cmd.build_disguise_image",
                lambda *a, **k: order.append("build") or True,
            ),
            patch(
                "winpodx.core.compose.generate_compose",
                lambda _c: order.append("compose"),
            ),
        ):
            result = setup_cmd._update_image_pin()

        assert result == 0
        assert order == ["build", "compose"]

    def test_a_failed_rebuild_aborts_without_regenerating_compose(self):
        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.disguise_level = "max"
        cfg.pod.image = "ghcr.io/dockur/windows@sha256:old"
        new_pin = f"ghcr.io/dockur/windows@sha256:{'c' * 64}"
        run, fake_subprocess = _pin_flow(cfg, new_pin)

        with (
            patch("winpodx.core.config.Config.load", return_value=cfg),
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("platform.machine", return_value="x86_64"),
            patch.dict(sys.modules, {"subprocess": fake_subprocess}),
            patch.object(cfg, "save"),
            patch("winpodx.cli.setup_cmd.disguise_image_is_stale", lambda _c: True),
            patch("winpodx.cli.setup_cmd.build_disguise_image", lambda *a, **k: False),
            patch("winpodx.core.compose.generate_compose") as generate_compose,
        ):
            result = setup_cmd._update_image_pin()

        assert result == 3
        generate_compose.assert_not_called()

    def test_levels_below_max_never_trigger_a_rebuild(self):
        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.disguise_level = "balanced"
        cfg.pod.image = "ghcr.io/dockur/windows@sha256:old"
        new_pin = f"ghcr.io/dockur/windows@sha256:{'d' * 64}"
        run, fake_subprocess = _pin_flow(cfg, new_pin)

        with (
            patch("winpodx.core.config.Config.load", return_value=cfg),
            patch("shutil.which", return_value="/usr/bin/podman"),
            patch("platform.machine", return_value="x86_64"),
            patch.dict(sys.modules, {"subprocess": fake_subprocess}),
            patch.object(cfg, "save"),
            patch("winpodx.cli.setup_cmd.disguise_image_is_stale") as stale,
            patch("winpodx.cli.setup_cmd.build_disguise_image") as build,
            patch("winpodx.core.compose.generate_compose"),
        ):
            result = setup_cmd._update_image_pin()

        assert result == 0
        stale.assert_not_called()
        build.assert_not_called()
