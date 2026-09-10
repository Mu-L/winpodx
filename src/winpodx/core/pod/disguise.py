# SPDX-License-Identifier: MIT
"""Compatibility gate for the locally built patched-QEMU image (#246).

``disguise_level = "max"`` swaps the pinned dockur image for a patched one the
user built locally. That image bakes ``FROM $DOCKUR_IMAGE`` at build time, so
once the pin moves the two disagree -- and dockur does not recognise the newer
release's on-disk markers, so it silently reinstalls Windows from scratch and
the existing guest is gone.

The gate is fail-closed on a *verified* mismatch only. "No patched image" and
"cannot read the labels" keep the historical behaviour, because neither
destroys data and turning them into hard failures would strand configs that
work today.
"""

from __future__ import annotations


class DisguiseImageError(RuntimeError):
    """The ``max`` disguise image cannot be used as-is."""


class DisguiseImageStaleError(DisguiseImageError):
    """The patched image was built on a different dockur than the pin."""


def validate_disguise_image(cfg) -> None:  # type: ignore[no-untyped-def]
    """Raise ``DisguiseImageStaleError`` if running ``max`` would wipe the guest.

    Call before anything destructive or before a pod start. No-op unless the
    level is ``max`` and the mismatch is positively confirmed.
    """
    if not getattr(cfg.pod, "disguise_max", False):
        return

    from winpodx.cli.disguise import (
        _DISGUISE_TAG,
        _image_label_version,
        disguise_image_is_stale,
        pinned_dockur_version,
    )

    if disguise_image_is_stale(cfg) is not True:
        return

    backend = cfg.pod.backend if cfg.pod.backend in ("podman", "docker") else "podman"
    tag = (cfg.pod.disguise_image or "").strip() or _DISGUISE_TAG
    raise DisguiseImageStaleError(
        f"the patched image '{tag}' was built on dockur "
        f"{_image_label_version(backend, tag) or '?'}, but the pin is now "
        f"{pinned_dockur_version(cfg) or '?'}. Booting the guest on the older "
        f"dockur makes it reinstall Windows from scratch, destroying the "
        f"existing install. Rebuild it first:\n"
        f"  winpodx disguise build-image"
    )
