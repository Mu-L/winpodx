# SPDX-License-Identifier: MIT
"""Tests for the multi-monitor X-screen extent parser (display.layout)."""

from __future__ import annotations

from winpodx.display import layout

# Real `xrandr --listmonitors` from a mixed-DPI dual-monitor KDE Wayland host:
# eDP-1 2560x1600 @ +0+0, HDMI-A-1 2773x1560 @ +2561+0 (a 1 px gap at x=2560
# and mismatched heights -- the layout FreeRDP's /span refuses).
_DUAL = """Monitors: 2
 0: +*eDP-1 2560/344x1600/215+0+0  eDP-1
 1: +HDMI-A-1 2773/698x1560/393+2561+0  HDMI-A-1
"""

_SINGLE = """Monitors: 1
 0: +*eDP-1 2560/344x1600/215+0+0  eDP-1
"""

_STACKED = """Monitors: 2
 0: +*DP-1 1920/600x1080/340+0+0  DP-1
 1: +DP-2 1920/600x1080/340+0+1080  DP-2
"""


def test_parse_extent_dual_bounding_box() -> None:
    # max(x+w) = 2561+2773 = 5334; max(y+h) = max(1600, 1560) = 1600.
    assert layout.parse_monitor_extent(_DUAL) == (2, 5334, 1600)


def test_parse_extent_stacked_vertical() -> None:
    # Vertically stacked: width 1920, height 1080 + 1080 = 2160.
    assert layout.parse_monitor_extent(_STACKED) == (2, 1920, 2160)


def test_parse_extent_single_monitor() -> None:
    assert layout.parse_monitor_extent(_SINGLE) == (1, 2560, 1600)


def test_parse_extent_garbage_returns_none() -> None:
    assert layout.parse_monitor_extent("Monitors: 0\n") is None
    assert layout.parse_monitor_extent("total nonsense") is None


def test_detect_extent_none_for_single_monitor(monkeypatch) -> None:
    # Single monitor -> None (no span / explicit size needed).
    monkeypatch.setattr(layout, "_run_xrandr_listmonitors", lambda: _SINGLE)
    assert layout.detect_x_screen_extent() is None


def test_detect_extent_returns_bbox_for_dual(monkeypatch) -> None:
    monkeypatch.setattr(layout, "_run_xrandr_listmonitors", lambda: _DUAL)
    assert layout.detect_x_screen_extent() == (5334, 1600)


def test_detect_extent_none_when_xrandr_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(layout, "_run_xrandr_listmonitors", lambda: None)
    assert layout.detect_x_screen_extent() is None


def test_has_mixed_scale_true_for_differing(monkeypatch) -> None:
    layout._MIXED_SCALE_CACHE.clear()
    monkeypatch.setattr(layout, "detect_monitor_scales", lambda: [1.3, 1.0])
    assert layout.has_mixed_scale() is True


def test_has_mixed_scale_false_for_uniform(monkeypatch) -> None:
    layout._MIXED_SCALE_CACHE.clear()
    monkeypatch.setattr(layout, "detect_monitor_scales", lambda: [1.5, 1.5])
    assert layout.has_mixed_scale() is False


def test_has_mixed_scale_none_for_single_or_unknown(monkeypatch) -> None:
    layout._MIXED_SCALE_CACHE.clear()
    monkeypatch.setattr(layout, "detect_monitor_scales", lambda: [1.0])
    assert layout.has_mixed_scale() is None
    layout._MIXED_SCALE_CACHE.clear()
    monkeypatch.setattr(layout, "detect_monitor_scales", lambda: None)
    assert layout.has_mixed_scale() is None


def test_has_mixed_scale_is_cached(monkeypatch) -> None:
    # Second call must not re-probe (cached for the process lifetime).
    layout._MIXED_SCALE_CACHE.clear()
    calls = {"n": 0}

    def _probe():
        calls["n"] += 1
        return [1.3, 1.0]

    monkeypatch.setattr(layout, "detect_monitor_scales", _probe)
    assert layout.has_mixed_scale() is True
    assert layout.has_mixed_scale() is True
    assert calls["n"] == 1


def test_kde_monitor_scales_parses_outputs(monkeypatch) -> None:
    import json

    class _Proc:
        returncode = 0
        stdout = json.dumps(
            {
                "outputs": [
                    {"name": "eDP-1", "enabled": True, "scale": 1.3},
                    {"name": "HDMI-A-1", "enabled": True, "scale": 1.0},
                    {"name": "DP-9", "enabled": False, "scale": 2.0},  # ignored
                ]
            }
        )

    monkeypatch.setattr(layout.shutil, "which", lambda _n: "/usr/bin/kscreen-doctor")
    monkeypatch.setattr(layout.subprocess, "run", lambda *a, **k: _Proc())
    assert layout._kde_monitor_scales() == [1.3, 1.0]
