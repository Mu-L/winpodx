# SPDX-License-Identifier: MIT
"""Windows 11 Settings-style setup wizard (left step-rail + content)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._main_window_secondary_style import apply_w11_button
from winpodx.gui._setup_wizard_config import ConfigurationPage
from winpodx.gui._setup_wizard_model import collect_answers, to_namespace
from winpodx.gui._setup_wizard_pages import (
    FinishPage,
    InstallPage,
    ReviewPage,
    WelcomePage,
    copy_to_clipboard,
)
from winpodx.gui._setup_wizard_prereq import PrerequisitesPage
from winpodx.gui._setup_wizard_worker import SetupWorker
from winpodx.gui.theme_manager import instance as theme_manager_instance

_STEPS = (
    "Welcome",
    "Prerequisites",
    "Configuration",
    "Review",
    "Install",
    "Finish",
)


class SetupWizardDialog(QDialog):
    """First-run / reinstall wizard. Collects answers, then calls handle_setup."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mode: str = "first-run",
        cfg: Config | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._cfg = cfg
        self._reinstall = mode == "reinstall"
        self.open_apps = False
        self.open_terminal = False
        self._thread: QThread | None = None
        self._worker: SetupWorker | None = None
        self._answers = collect_answers(cfg if self._reinstall else None)
        self.setWindowTitle(tr("Reinstall Windows") if self._reinstall else tr("Set up WinPodX"))
        self.setMinimumSize(840, 560)
        self.setModal(True)
        self._rail_labels: list[QLabel] = []
        self._build()
        theme_manager_instance().scheme_changed.connect(self._restyle)
        self._restyle()
        self._sync_nav()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._rail = QFrame()
        self._rail.setObjectName("wizardRail")
        self._rail.setFixedWidth(220)
        rail_lay = QVBoxLayout(self._rail)
        rail_lay.setContentsMargins(theme.SPACE_L, theme.SPACE_XL, theme.SPACE_M, theme.SPACE_L)
        rail_lay.setSpacing(theme.SPACE_XS)
        for title in _STEPS:
            lbl = QLabel(tr(title))
            lbl.setProperty("wizardStep", True)
            self._rail_labels.append(lbl)
            rail_lay.addWidget(lbl)
        rail_lay.addStretch(1)
        pane = QWidget()
        pane_lay = QVBoxLayout(pane)
        pane_lay.setContentsMargins(theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_L)
        pane_lay.setSpacing(theme.SPACE_L)
        self.pages = QStackedWidget()
        self.welcome = WelcomePage(reinstall=self._reinstall)
        self.prereq = PrerequisitesPage()
        self.config = ConfigurationPage(self._answers)
        self.review = ReviewPage()
        self.install = InstallPage(on_cancel=lambda: None, cfg=self._cfg)
        self.finish = FinishPage()
        for page in (
            self.welcome,
            self.prereq,
            self.config,
            self.review,
            self.install,
            self.finish,
        ):
            self.pages.addWidget(page)
        self.prereq.can_proceed_changed.connect(lambda _ok: self._sync_nav())
        self.finish.open_apps.connect(self._on_open_apps)
        self.finish.open_terminal.connect(self._on_open_terminal)
        self.finish.retry.connect(lambda: self._goto(2))
        self.finish.copy_log.connect(lambda: copy_to_clipboard(self.install.log_text()))
        scroll = QScrollArea()
        scroll.setObjectName("wizardScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.pages.setMinimumWidth(520)
        scroll.setWidget(self.pages)
        self._scroll = scroll
        pane_lay.addWidget(scroll, 1)
        pane_lay.addLayout(self._footer())
        root.addWidget(self._rail)
        root.addWidget(pane, 1)

    def _footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.skip_btn = QPushButton(tr("Skip"))
        self.skip_btn.setObjectName("wizardSkip")
        self.skip_btn.clicked.connect(self.reject)
        self.back_btn = QPushButton(tr("Back"))
        self.back_btn.setObjectName("wizardBack")
        self.back_btn.clicked.connect(self._on_back)
        self.next_btn = QPushButton(tr("Next"))
        self.next_btn.setObjectName("wizardNext")
        self.next_btn.clicked.connect(self._on_next)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.back_btn)
        row.addWidget(self.next_btn)
        return row

    def _goto(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 3:
            self._answers = self.config.answers()
            self.review.set_answers(self._answers)
        self._sync_nav()
        self._restyle_rail()

    def _on_next(self) -> None:
        idx = self.pages.currentIndex()
        if idx == 3:
            self._start_install()
            return
        if idx < 5:
            self._goto(idx + 1)

    def _on_back(self) -> None:
        idx = self.pages.currentIndex()
        if idx > 0:
            self._goto(idx - 1)

    def _sync_nav(self) -> None:
        idx = self.pages.currentIndex()
        installing = idx == 4
        done = idx == 5
        self.back_btn.setVisible(idx > 0 and not installing and not done)
        self.next_btn.setVisible(not installing and not done)
        self.skip_btn.setVisible(self._mode == "first-run" and not installing and not done)
        self.next_btn.setText(tr("Install") if idx == 3 else tr("Next"))
        if idx == 1:
            self.next_btn.setEnabled(self.prereq.can_proceed())
        else:
            self.next_btn.setEnabled(True)

    def _start_install(self) -> None:
        if self._thread is not None:
            return
        self._answers = self.config.answers()
        self._goto(4)
        self.install.begin()
        thread = QThread(self)
        worker = SetupWorker(to_namespace(self._answers), reinstall=self._reinstall)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_setup_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_worker)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_setup_finished(self, success: bool, error: str) -> None:
        self.install.finish(success, error)
        if success:
            self.finish.show_success()
        else:
            self.finish.show_failure(error)
        self._goto(5)

    def _cleanup_worker(self) -> None:
        thread = self._thread
        if thread is not None:
            try:
                thread.wait()
            except RuntimeError:
                pass
        self._thread = None
        self._worker = None

    def _on_open_apps(self) -> None:
        self.open_apps = True
        self.accept()

    def _on_open_terminal(self) -> None:
        self.open_terminal = True
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        installing = self.pages.currentIndex() == 4
        thread = self._thread
        running = False
        if thread is not None:
            try:
                running = thread.isRunning()
            except RuntimeError:
                running = False
        if installing and running:
            event.ignore()
            return
        self.prereq.wait_for_worker()
        self._cleanup_worker()
        super().closeEvent(event)

    def _restyle(self) -> None:
        self.setStyleSheet(theme.DIALOG)
        self._scroll.setStyleSheet(theme.SCROLL_AREA)
        self._rail.setStyleSheet(
            f"QFrame#wizardRail {{ background: {theme.C.MANTLE}; border: none; "
            f"border-right: 1px solid {theme.C.SURFACE0}; }}"
        )
        apply_w11_button(self.next_btn, theme.BTN_PRIMARY, role="primary")
        apply_w11_button(self.back_btn, theme.BTN_SECONDARY, role="secondary")
        apply_w11_button(self.skip_btn, theme.BTN_GHOST, role="ghost")
        self.welcome._restyle()
        self.prereq._restyle()
        self.config._restyle()
        self.review._restyle()
        self.finish._restyle()
        self._restyle_rail()

    def _restyle_rail(self) -> None:
        current = self.pages.currentIndex()
        for i, lbl in enumerate(self._rail_labels):
            if i == current:
                color, weight = theme.C.TEXT, 600
            elif i < current:
                color, weight = theme.C.SUBTEXT1, 400
            else:
                color, weight = theme.C.OVERLAY0, 400
            lbl.setStyleSheet(
                f"background: transparent; color: {color}; "
                f"font-size: {theme.FONT_BODY}px; font-weight: {weight}; "
                f"padding: {theme.SPACE_S}px {theme.SPACE_S}px;"
            )
