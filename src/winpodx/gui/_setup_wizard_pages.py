# SPDX-License-Identifier: MIT
"""Welcome, Review, Install, and Finish pages for the setup wizard."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._main_window_bringup import BringUpProgressDialog
from winpodx.gui._main_window_secondary_style import apply_w11_button
from winpodx.gui._settings_card import make_settings_card, make_settings_group
from winpodx.gui._setup_wizard_model import SetupAnswers
from winpodx.gui.icons import load_icon


class WelcomePage(QWidget):
    """What winpodx will do and how long it usually takes."""

    def __init__(self, *, reinstall: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = QLabel(tr("Reinstall Windows") if reinstall else tr("Welcome to WinPodX"))
        self._title.setWordWrap(True)
        self._body = QLabel(
            tr(
                "WinPodX runs Windows apps as native Linux windows. This wizard "
                "checks your host, lets you pick Windows settings, then downloads "
                "and installs Windows. It usually takes 5-10 minutes (longer on a "
                "slow connection)."
            )
            if not reinstall
            else tr(
                "This wipes the Windows disk and reinstalls with the settings you "
                "choose. Your WinPodX configuration is kept. It usually takes "
                "5-10 minutes."
            )
        )
        self._body.setWordWrap(True)
        self._skip_hint = QLabel(
            tr("You can skip and set up later from Tools.") if not reinstall else ""
        )
        self._skip_hint.setWordWrap(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_M)
        root.addWidget(self._title)
        root.addWidget(self._body)
        root.addWidget(self._skip_hint)
        root.addStretch(1)

    def _restyle(self) -> None:
        self._title.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; "
            f"font-size: {theme.FONT_TITLE}px; font-weight: 600;"
        )
        self._body.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; font-size: {theme.FONT_BODY}px;"
        )
        self._skip_hint.setStyleSheet(
            f"background: transparent; color: {theme.C.SUBTEXT1}; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )


class ReviewPage(QWidget):
    """Read-only summary of every choice plus the download warning."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: list[QWidget] = []
        self._warn = QLabel(
            tr(
                "This downloads Windows and takes roughly 5-10 minutes "
                "(longer on a slow connection)."
            )
        )
        self._warn.setWordWrap(True)
        self._stack_host = QVBoxLayout()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_L)
        root.addWidget(self._warn)
        root.addLayout(self._stack_host)
        root.addStretch(1)

    def set_answers(self, answers: SetupAnswers) -> None:
        """Rebuild the summary cards from the current answers."""
        while self._stack_host.count():
            item = self._stack_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        group, stack = make_settings_group(tr("Your choices"))
        rows = (
            (tr("Windows edition"), answers.win_version),
            (tr("UI language"), answers.language),
            (tr("Regional format"), answers.region),
            (tr("Keyboard layout"), answers.keyboard),
            (tr("Timezone"), answers.timezone),
            (tr("CPU cores"), str(answers.cpu_cores)),
            (tr("RAM (GB)"), str(answers.ram_gb)),
            (tr("Disk size"), answers.disk_size),
            (tr("Windows username"), answers.rdp_user),
        )
        for title, value in rows:
            stack.addWidget(make_settings_card("check", title, value))
        self._stack_host.addWidget(group)

    def _restyle(self) -> None:
        self._warn.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; font-size: {theme.FONT_BODY}px;"
        )


class InstallPage(QWidget):
    """Embed BringUpProgressDialog — no second progress implementation."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_cancel: Callable[[], None],
        cfg: Config | None,
    ) -> None:
        super().__init__(parent)
        self._on_cancel = on_cancel
        self._cfg = cfg
        self.progress: BringUpProgressDialog | None = None
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)

    def begin(self) -> None:
        """Embed BringUpProgressDialog and mark the install phase in progress."""
        if self.progress is not None:
            self._root.removeWidget(self.progress)
            self.progress.close()
            self.progress.deleteLater()
            self.progress = None
        progress = BringUpProgressDialog(
            self,
            on_cancel=self._on_cancel,
            cfg=self._cfg,
            phases=(
                (
                    "phase_1_pod",
                    "Install Windows",
                    "usually 5-10 min; longer on a slow connection",
                    False,
                ),
            ),
        )
        progress.setWindowFlags(Qt.WindowType.Widget)
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._root.addWidget(progress)
        self.progress = progress
        self.progress.on_phase("phase_1_pod", tr("Downloading and installing Windows..."))

    def finish(self, success: bool, error: str) -> None:
        """Tick or freeze the checklist from the worker result."""
        if self.progress is not None:
            self.progress.on_done(success, error)

    def log_text(self) -> str:
        """Pod-log buffer plus the last error, for Copy log."""
        if self.progress is None:
            return ""
        return self.progress.pod_log_view.toPlainText()


class FinishPage(QWidget):
    """Success: Open Applications. Failure: copy log / terminal / retry."""

    open_apps = Signal()
    open_terminal = Signal()
    retry = Signal()
    copy_log = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.is_failure = False
        self._icon = QLabel()
        self._icon.setFixedSize(32, 32)
        self._title = QLabel("")
        self._title.setWordWrap(True)
        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._open_apps = QPushButton(tr("Open Applications"))
        self._open_apps.setObjectName("wizardOpenApps")
        self._copy = QPushButton(tr("Copy log"))
        self._copy.setObjectName("wizardCopyLog")
        self._terminal = QPushButton(tr("Open Terminal page"))
        self._terminal.setObjectName("wizardOpenTerminal")
        self._retry = QPushButton(tr("Retry"))
        self._retry.setObjectName("wizardRetry")
        self._open_apps.clicked.connect(self.open_apps.emit)
        self._copy.clicked.connect(self.copy_log.emit)
        self._terminal.clicked.connect(self.open_terminal.emit)
        self._retry.clicked.connect(self.retry.emit)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_M)
        root.addWidget(self._icon)
        root.addWidget(self._title)
        root.addWidget(self._body)
        root.addWidget(self._open_apps)
        root.addWidget(self._copy)
        root.addWidget(self._terminal)
        root.addWidget(self._retry)
        root.addStretch(1)

    def show_success(self) -> None:
        """Ready state after a completed install."""
        self.is_failure = False
        self._title.setText(tr("Windows is ready"))
        self._body.setText(tr("You can launch apps from the Applications page."))
        self._set_buttons(success=True)
        self._restyle()

    def show_failure(self, error: str) -> None:
        """Keep the wizard open and surface the error."""
        self.is_failure = True
        self._title.setText(tr("Setup failed"))
        self._body.setText(error or tr("(no error message)"))
        self._set_buttons(success=False)
        self._restyle()

    def _set_buttons(self, *, success: bool) -> None:
        self._open_apps.setVisible(success)
        self._copy.setVisible(not success)
        self._terminal.setVisible(not success)
        self._retry.setVisible(not success)

    def _restyle(self) -> None:
        color = theme.C.RED if self.is_failure else theme.C.GREEN
        icon = "error" if self.is_failure else "check"
        self._icon.setPixmap(load_icon(icon, color, 32).pixmap(32, 32))
        self._title.setStyleSheet(
            f"background: transparent; color: {color}; "
            f"font-size: {theme.FONT_TITLE}px; font-weight: 600;"
        )
        self._body.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; font-size: {theme.FONT_BODY}px;"
        )
        apply_w11_button(self._open_apps, theme.BTN_PRIMARY, role="primary")
        apply_w11_button(self._copy, theme.BTN_SECONDARY, role="secondary")
        apply_w11_button(self._terminal, theme.BTN_SECONDARY, role="secondary")
        apply_w11_button(self._retry, theme.BTN_PRIMARY, role="primary")


def copy_to_clipboard(text: str) -> None:
    """Put ``text`` on the system clipboard."""
    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)
