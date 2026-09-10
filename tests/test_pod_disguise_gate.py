# SPDX-License-Identifier: MIT
"""Fail-closed gate for a patched-QEMU image built on a different dockur.

Booting `max` on a patched image whose base predates the pin makes dockur treat
the guest disk as foreign and reinstall Windows from scratch. These lock the
gate that stops that, and prove nothing destructive runs first.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from winpodx.core.config import Config
from winpodx.core.pod.backend import PodState
from winpodx.core.pod.disguise import DisguiseImageStaleError, validate_disguise_image
from winpodx.core.pod.lifecycle import start_pod


def _max_cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.disguise_level = "max"
    cfg.pod.disguise_image = "winpodx-windows-disguise"
    return cfg


def _stale(value):
    return patch("winpodx.cli.disguise.disguise_image_is_stale", lambda _c: value)


class TestValidateDisguiseImage:
    def test_a_confirmed_base_mismatch_raises(self):
        with (
            _stale(True),
            patch("winpodx.cli.disguise._image_label_version", lambda _b, _i: "5.16"),
            patch("winpodx.cli.disguise.pinned_dockur_version", lambda _c: "6.05"),
            pytest.raises(DisguiseImageStaleError) as excinfo,
        ):
            validate_disguise_image(_max_cfg())

        message = str(excinfo.value)
        assert "5.16" in message and "6.05" in message
        assert "winpodx disguise build-image" in message

    def test_a_matching_image_passes(self):
        with _stale(False):
            validate_disguise_image(_max_cfg())

    def test_unreadable_labels_keep_working(self):
        # Unverifiable is not proof of a mismatch, and it destroys nothing --
        # hard-failing here would strand configs that run fine today.
        with _stale(None):
            validate_disguise_image(_max_cfg())

    def test_levels_below_max_are_never_gated(self):
        cfg = _max_cfg()
        cfg.pod.disguise_level = "balanced"

        with patch("winpodx.cli.disguise.disguise_image_is_stale") as probe:
            validate_disguise_image(cfg)

        probe.assert_not_called()


class TestStartPodRefusesAStaleImage:
    def test_it_reports_an_error_and_never_starts_the_backend(self):
        backend = MagicMock()

        with (
            _stale(True),
            patch("winpodx.cli.disguise._image_label_version", lambda _b, _i: "5.16"),
            patch("winpodx.cli.disguise.pinned_dockur_version", lambda _c: "6.05"),
            patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend),
        ):
            status = start_pod(_max_cfg())

        assert status.state is PodState.ERROR
        assert "build-image" in (status.error or "")
        backend.start.assert_not_called()
        backend.is_running.assert_not_called()


class TestRecreateValidatesBeforeDestroying:
    def test_a_stale_image_aborts_before_stop_or_wipe(self, monkeypatch):
        from winpodx.cli import pod as pod_cli

        monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(_max_cfg))

        stop = MagicMock()
        wipe = MagicMock()
        monkeypatch.setattr("winpodx.core.pod.stop_pod", stop, raising=False)
        monkeypatch.setattr(pod_cli, "_wipe_pod_storage", wipe, raising=False)

        with (
            _stale(True),
            patch("winpodx.cli.disguise._image_label_version", lambda _b, _i: "5.16"),
            patch("winpodx.cli.disguise.pinned_dockur_version", lambda _c: "6.05"),
            pytest.raises(SystemExit) as excinfo,
        ):
            pod_cli._recreate(wipe_storage=True, assume_yes=True)

        assert excinfo.value.code == 1
        stop.assert_not_called()
        wipe.assert_not_called()
