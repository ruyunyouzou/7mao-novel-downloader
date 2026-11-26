# -*- coding: utf-8 -*-
"""文库8（轻小说文库）登录对话框。

网站无开放扫码/OAuth，登录态由浏览器 Cookie 提供。本对话框把原本藏在设置页
深处的 Cookie 配置提升为标题栏一键入口：粘贴 Cookie → 后台验证登录态 →
保存为自定义登录态。软件内置了一份用户本人账号的 Cookie 作为默认值，
用户清空自定义值即回退到内置登录态，随时可覆盖。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QApplication,
)

from .i18n import tr
from .datastore import DataStore
from .style import ACCENT, ACCENT_DARK, TEXT, TEXT_DIM, BORDER_LIGHT, BG_CARD


class _VerifyWorker(QThread):
    """后台校验登录态，避免网络请求阻塞界面。"""

    finished_ok = Signal(bool, str)

    def __init__(self, cookie: str, parent=None) -> None:
        super().__init__(parent)
        self.cookie = cookie

    def run(self) -> None:  # noqa: C901
        try:
            from .wenku8_core.wenku8_auth import make_session, is_logged_in
            ok = bool(is_logged_in(make_session(self.cookie)))
            msg = tr("wenku8.login.valid") if ok else tr("wenku8.login.invalid")
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, tr("wenku8.login.error", err=str(exc))
        self.finished_ok.emit(ok, msg)


class Wenku8LoginDialog(QDialog):
    """文库8 Cookie 登录弹窗。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("wenku8.login.title"))
        self.setMinimumWidth(520)
        self._worker = None
        self._build()
        self._refresh_status()
        try:
            from .i18n import manager
            manager().changed.connect(self.retranslate)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        dark = bool(DataStore().settings_get("theme_mode", "dark") != "light")
        self._dark = dark
        self.setStyleSheet(
            f"QDialog {{ background-color: {BG_CARD if dark else '#ffffff'}; }}"
            f"QLabel {{ color: {TEXT if dark else '#2a2a2a'}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel(tr("wenku8.login.title"))
        title.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: "
            + (ACCENT if dark else ACCENT_DARK) + ";"
        )
        root.addWidget(title)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        hint = QLabel(tr("wenku8.login.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        root.addWidget(hint)

        # Cookie 输入框
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setEchoMode(QLineEdit.Password)
        self.cookie_edit.setPlaceholderText("PHPSESSID=...; jieqiUserInfo=...")
        stored = str(DataStore().settings_get("wenku8_cookie", "") or "")
        self.cookie_edit.setText(stored)
        root.addWidget(self.cookie_edit)

        show_row = QHBoxLayout()
        self.show_check = QCheckBox(tr("wenku8.login.show"))
        self.show_check.stateChanged.connect(self._on_show_changed)
        show_row.addWidget(self.show_check)
        show_row.addStretch()
        root.addLayout(show_row)

        # 操作按钮
        btn_row = QHBoxLayout()
        self.verify_btn = QPushButton(tr("wenku8.login.verify"))
        self.verify_btn.clicked.connect(self._on_verify)
        self.save_btn = QPushButton(tr("wenku8.login.save"))
        self.save_btn.clicked.connect(self._on_save)
        self.reset_btn = QPushButton(tr("wenku8.login.reset"))
        self.reset_btn.clicked.connect(self._on_reset)
        for b in (self.verify_btn, self.save_btn, self.reset_btn):
            b.setStyleSheet(
                f"QPushButton {{ background-color: {ACCENT if dark else ACCENT_DARK}; "
                "color: #1a1a1a; border: none; border-radius: 8px; "
                "padding: 7px 16px; font-size: 12px; font-weight: bold; }"
            )
            btn_row.addWidget(b)
        root.addLayout(btn_row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)

    # ------------------------------------------------------------------ 逻辑
    def _current_cookie(self) -> str:
        return self.cookie_edit.text().strip()

    def _refresh_status(self) -> None:
        custom = str(DataStore().settings_get("wenku8_cookie", "") or "").strip()
        if custom:
            self.status_label.setText(tr("wenku8.login.status_custom"))
        else:
            from .wenku8_core.wenku8_auth import get_cookie
            if get_cookie().strip():
                self.status_label.setText(tr("wenku8.login.status_builtin"))
            else:
                self.status_label.setText(tr("wenku8.login.status_none"))

    def _on_show_changed(self, state) -> None:
        self.cookie_edit.setEchoMode(
            QLineEdit.Normal if state else QLineEdit.Password)

    def _on_verify(self) -> None:
        cookie = self._current_cookie()
        if not cookie:
            self.result_label.setText(tr("wenku8.login.empty"))
            return
        self.verify_btn.setEnabled(False)
        self.result_label.setText(tr("wenku8.login.verifying"))
        QApplication.processEvents()
        self._worker = _VerifyWorker(cookie, self)
        self._worker.finished_ok.connect(self._on_verify_done)
        self._worker.start()

    def _on_verify_done(self, ok: bool, msg: str) -> None:
        self.verify_btn.setEnabled(True)
        self.result_label.setText(msg)
        self.result_label.setStyleSheet(
            "color: #2e7d32;" if ok else "color: #c62828;")
        if ok:
            self._save_cookie(self._current_cookie())

    def _on_save(self) -> None:
        cookie = self._current_cookie()
        if not cookie:
            self.result_label.setText(tr("wenku8.login.empty"))
            return
        self._save_cookie(cookie)
        self.result_label.setText(tr("wenku8.login.saved"))
        self.result_label.setStyleSheet("color: #2e7d32;")

    def _save_cookie(self, cookie: str) -> None:
        DataStore().settings_set("wenku8_cookie", cookie)
        self._refresh_status()
        # 同步设置页里的 Cookie 输入框（若主窗口已构建）
        app = QApplication.instance()
        if app:
            for w in app.topLevelWidgets():
                edit = getattr(w, "settings_wenku8_cookie", None)
                if edit is not None and hasattr(edit, "setText"):
                    edit.setText(cookie)

    def _on_reset(self) -> None:
        DataStore().settings_set("wenku8_cookie", "")
        self.cookie_edit.clear()
        self._refresh_status()
        self.result_label.setText(tr("wenku8.login.reset_done"))
        self.result_label.setStyleSheet(f"color: {TEXT_DIM};")

    def retranslate(self, *args) -> None:
        self.setWindowTitle(tr("wenku8.login.title"))
        self.verify_btn.setText(tr("wenku8.login.verify"))
        self.save_btn.setText(tr("wenku8.login.save"))
        self.reset_btn.setText(tr("wenku8.login.reset"))
        self.show_check.setText(tr("wenku8.login.show"))
        self._refresh_status()
