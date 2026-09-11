# SPDX-License-Identifier: MIT
"""The GUI wizard and a scripted `winpodx setup` share one install path.

The interactive wizard collects its answers with `input()`, which needs a tty --
`handle_setup` silently downgrades `--customize` to auto without one, so the GUI
"Customize" button used to offer no choices at all. Supplying the answers up
front lets both routes converge on the same downstream install.
"""

from __future__ import annotations

import argparse

from winpodx.cli.setup_cmd import apply_setup_presets
from winpodx.core.config import Config


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


class TestApplySetupPresets:
    def test_supplied_values_override_the_tier_defaults(self):
        cfg = Config()
        cfg.pod.cpu_cores = 4
        cfg.pod.ram_gb = 6

        applied = apply_setup_presets(cfg, _args(cpu_cores=12, ram_gb=32))

        assert cfg.pod.cpu_cores == 12
        assert cfg.pod.ram_gb == 32
        assert set(applied) == {"cpu_cores", "ram_gb"}

    def test_absent_fields_leave_the_detected_defaults_alone(self):
        cfg = Config()
        cfg.pod.cpu_cores = 4
        cfg.pod.ram_gb = 6

        applied = apply_setup_presets(cfg, _args(ram_gb=16))

        assert cfg.pod.cpu_cores == 4
        assert applied == ["ram_gb"]

    def test_none_and_empty_string_are_treated_as_unspecified(self):
        cfg = Config()
        cfg.pod.language = "Korean"

        applied = apply_setup_presets(cfg, _args(language=None, region="", timezone="Asia/Seoul"))

        assert cfg.pod.language == "Korean"
        assert cfg.pod.timezone == "Asia/Seoul"
        assert applied == ["timezone"]

    def test_an_args_namespace_without_any_preset_fields_is_a_no_op(self):
        cfg = Config()
        before = (cfg.pod.cpu_cores, cfg.pod.ram_gb, cfg.pod.win_version)

        applied = apply_setup_presets(cfg, _args(backend=None, customize=False))

        assert applied == []
        assert (cfg.pod.cpu_cores, cfg.pod.ram_gb, cfg.pod.win_version) == before

    def test_values_are_revalidated_so_the_wizard_cannot_bypass_clamping(self):
        cfg = Config()

        apply_setup_presets(cfg, _args(cpu_cores=9999, ram_gb=9999))

        assert cfg.pod.cpu_cores == 128
        assert cfg.pod.ram_gb == 512

    def test_the_windows_username_routes_to_the_rdp_section(self):
        cfg = Config()

        applied = apply_setup_presets(cfg, _args(rdp_user="Kim"))

        assert cfg.rdp.user == "Kim"
        assert applied == ["rdp_user"]
