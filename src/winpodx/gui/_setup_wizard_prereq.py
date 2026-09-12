# SPDX-License-Identifier: MIT
"""Prerequisites page: live HostState checklist + one pkexec Fix button."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._main_window_secondary_style import apply_w11_button
from winpodx.gui._settings_card import make_settings_card, make_settings_group
from winpodx.gui._setup_wizard_model import prereq_specs
from winpodx.gui._setup_wizard_worker import PkexecWorker
from winpodx.gui._widget_helpers import make_warning_callout
from winpodx.setup_wizard.host_state import HostState, detect_host_state


class PrerequisitesPage(QWidget):
    """Seven HostState rows; Next is gated on required items."""

    can_proceed_changed = Signal(bool)
    refreshed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = detect_host_state()
        self._thread: QThread | None = None
        self._worker: PkexecWorker | None = None
        self._cards: dict[str, QFrame] = {}
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._callout_host = QVBoxLayout()
        self._fix_btn = QPushButton(tr("Fix these"))
        self._fix_btn.setObjectName("wizardFixPrereqs")
        self._fix_btn.clicked.connect(self._on_fix)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_L)
        group, stack = make_settings_group(tr("Host checks"))
        for spec in prereq_specs():
            card = make_settings_card(self._icon_for(spec.field), spec.title, spec.note)
            self._cards[spec.field] = card
            stack.addWidget(card)
        root.addWidget(group)
        root.addLayout(self._callout_host)
        root.addWidget(self._hint)
        root.addWidget(self._fix_btn)
        root.addStretch(1)
        self._paint(self._state)

    def can_proceed(self) -> bool:
        """True when nothing on the host actually blocks a Windows install.

        Delegates to ``HostState.blocking_failures`` rather than re-deciding
        per row: a passing check can make another one moot. On a host whose
        ``/dev/kvm`` is world-accessible the user is not in the ``kvm`` group
        and never needs to be, and gating on the row alone stranded them on a
        failure no action could clear.
        """
        return not self._state.blocking_failures

    def _restyle(self) -> None:
        apply_w11_button(self._fix_btn, theme.BTN_PRIMARY, role="primary")
        self._hint.setStyleSheet(
            f"background: transparent; color: {theme.C.SUBTEXT1}; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )
        self._paint(self._state)

    def _icon_for(self, field: str) -> str:
        if field.startswith("dev_kvm") or field.startswith("kvm_"):
            return "hardware"
        if field.startswith("in_kvm"):
            return "hardware"
        return "gear"

    def _paint(self, state: HostState) -> None:
        from winpodx.gui._main_window_secondary_style import make_status_badge

        self._state = state
        while self._callout_host.count():
            item = self._callout_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        unfixable: list[str] = []
        blocking = set(state.blocking_failures)
        for spec in prereq_specs():
            ok = bool(getattr(state, spec.field))
            card = self._cards[spec.field]
            # A row can fail without blocking: kvm group membership is moot
            # once /dev/kvm is already accessible. Red is reserved for the
            # failures that actually stop the install.
            blocks = spec.field in blocking
            color = theme.C.GREEN if ok else theme.C.RED if blocks else theme.C.YELLOW
            label = tr("Pass") if ok else tr("Fail") if blocks else tr("Optional")
            badge = make_status_badge(label, color)
            old = getattr(card, "action_widget", None)
            if old is not None:
                card.row_layout.removeWidget(old)
                old.deleteLater()
            card.row_layout.addWidget(badge)
            card.action_widget = badge
            if not ok and spec.unfixable_hint and spec.field not in _fixable_fields(state):
                unfixable.append(spec.unfixable_hint)
        if unfixable:
            self._callout_host.addWidget(make_warning_callout("; ".join(unfixable)))
        fixable = bool(state.missing_fixable)
        self._fix_btn.setVisible(fixable)
        self._fix_btn.setEnabled(fixable and self._thread is None)
        if not state.in_kvm_group and state.kvm_group_exists:
            self._hint.setText(
                tr(
                    "kvm group membership requires you to log out and back in "
                    "before it takes effect."
                )
            )
        else:
            self._hint.setText("")
        self.can_proceed_changed.emit(self.can_proceed())
        self.refreshed.emit()

    def _on_fix(self) -> None:
        if self._thread is not None:
            return
        self._fix_btn.setEnabled(False)
        thread = QThread(self)
        worker = PkexecWorker(self._state)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_fix_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_fix)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_fix_finished(self, state: HostState, error: str) -> None:
        if error:
            self._hint.setText(_pkexec_message(error))
        self._paint(state)

    def wait_for_worker(self) -> None:
        """Join the pkexec thread before the page is destroyed."""
        self._cleanup_fix()

    def _cleanup_fix(self) -> None:
        thread = self._thread
        if thread is not None:
            try:
                thread.wait()
            except RuntimeError:
                pass
        self._thread = None
        self._worker = None
        self._fix_btn.setEnabled(bool(self._state.missing_fixable))


def _fixable_fields(state: HostState) -> set[str]:
    mapping = {
        "kvm-group-membership": "in_kvm_group",
        "subuid-entry": "subuid_configured",
        "subgid-entry": "subgid_configured",
        "kvm-module-persistence": "kvm_module_persistent",
    }
    return {mapping[item] for item in state.missing_fixable if item in mapping}


def _pkexec_message(error: str) -> str:
    lowered = error.lower()
    if "pkexec" in lowered and ("not found" in lowered or "path" in lowered):
        return tr("pkexec is not installed. Install polkit and try again.")
    if "dismissed" in lowered or "authentication failed" in lowered:
        return tr("Authentication was cancelled.")
    if "returned" in lowered or "timed out" in lowered:
        return tr("The elevated fix script failed.")
    return error
