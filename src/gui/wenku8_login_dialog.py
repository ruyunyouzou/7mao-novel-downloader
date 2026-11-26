# -*- coding: utf-8 -*-
"""文库8（轻小说文库）登录对话框。

提供两种登录方式：
1. 账号密码 —— 直接 POST 站点登录接口，成功后把会话 Cookie 交给现有机制保存；
2. Cookie   —— 粘贴浏览器 document.cookie，后台验证后保存为自定义登录态。

登录态只保存在本机 DataStore，账号密码本身不落盘。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QApplication, QTabWidget, QWidget, QFormLayout,
)

from .i18n import tr
from .datastore import DataStore
from .style import ACCENT, ACCENT_DARK, TEXT, TEXT_DIM, BORDER_LIGHT, BG_CARD


class _VerifyWorker(QThread):
    """后台校验 Cookie 登录态，避免网络请求阻塞界面。"""

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


class _LoginWorker(QThread):
    """后台用账号密码登录，避免网络阻塞界面。"""

    finished_ok = Signal(bool, str, str)  # ok, cookie字符串, 提示

    def __init__(self, username: str, password: str, parent=None) -> None:
        super().__init__(parent)
        self.username = username
        self.password = password

    def run(self) -> None:  # noqa: C901
        try:
            from .wenku8_core.wenku8_auth import login_with_password
            ok, cookie, msg = login_with_password(self.username, self.password)
        except Exception as exc:  # noqa: BLE001
            ok, cookie, msg = False, "", tr("wenku8.login.error", err=str(exc))
        self.finished_ok.emit(ok, cookie, msg)


class Wenku8LoginDialog(QDialog):
    """文库8 登录弹窗：账号密码 / Cookie 双方式。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("wenku8.login.title"))
        self.setMinimumWidth(520)
        self._verify_worker = None
        self._login_worker = None
        self._build()
        self._refresh_status()
        try:
            from .i18n import manager
            manager().changed.connect(self.retranslate)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ UI
    def _style_button(self, btn: QPushButton) -> None:
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT if self._dark else ACCENT_DARK}; "
            "color: #1a1a1a; border: none; border-radius: 8px; "
            "padding: 7px 16px; font-size: 12px; font-weight: bold; }"
        )

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

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_password_tab(), tr("wenku8.login.tab_password"))
        self.tabs.addTab(self._build_cookie_tab(), tr("wenku8.login.tab_cookie"))
        root.addWidget(self.tabs)

    def _build_password_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 12, 4, 4)
        lay.setSpacing(10)

        hint = QLabel(tr("wenku8.login.pwd_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        lay.addWidget(hint)

        form = QFormLayout()
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText(tr("wenku8.login.username"))
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText(tr("wenku8.login.password"))
        self.password_edit.returnPressed.connect(self._on_login)
        form.addRow(tr("wenku8.login.username"), self.username_edit)
        form.addRow(tr("wenku8.login.password"), self.password_edit)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        self.login_btn = QPushButton(tr("wenku8.login.login_btn"))
        self.login_btn.clicked.connect(self._on_login)
        self._style_button(self.login_btn)
        btn_row.addWidget(self.login_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.pwd_result = QLabel("")
        self.pwd_result.setWordWrap(True)
        lay.addWidget(self.pwd_result)
        lay.addStretch()
        return page

    def _build_cookie_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 12, 4, 4)
        lay.setSpacing(10)

        hint = QLabel(tr("wenku8.login.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        lay.addWidget(hint)

        self.cookie_edit = QLineEdit()
        self.cookie_edit.setEchoMode(QLineEdit.Password)
        self.cookie_edit.setPlaceholderText("PHPSESSID=...; jieqiUserInfo=...")
        stored = str(DataStore().settings_get("wenku8_cookie", "") or "")
        self.cookie_edit.setText(stored)
        lay.addWidget(self.cookie_edit)

        show_row = QHBoxLayout()
        self.show_check = QCheckBox(tr("wenku8.login.show"))
        self.show_check.stateChanged.connect(self._on_show_changed)
        show_row.addWidget(self.show_check)
        show_row.addStretch()
        lay.addLayout(show_row)

        btn_row = QHBoxLayout()
        self.verify_btn = QPushButton(tr("wenku8.login.verify"))
        self.verify_btn.clicked.connect(self._on_verify)
        self.save_btn = QPushButton(tr("wenku8.login.save"))
        self.save_btn.clicked.connect(self._on_save)
        self.reset_btn = QPushButton(tr("wenku8.login.reset"))
        self.reset_btn.clicked.connect(self._on_reset)
        for b in (self.verify_btn, self.save_btn, self.reset_btn):
            self._style_button(b)
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        lay.addWidget(self.result_label)
        lay.addStretch()
        return page

    # ------------------------------------------------------------------ 账号密码
    def _on_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            self.pwd_result.setText(tr("wenku8.login.pwd_empty"))
            self.pwd_result.setStyleSheet("color: #c62828;")
            return
        self.login_btn.setEnabled(False)
        self.pwd_result.setText(tr("wenku8.login.logging"))
        self.pwd_result.setStyleSheet(f"color: {TEXT_DIM};")
        QApplication.processEvents()
        self._login_worker = _LoginWorker(username, password, self)
        self._login_worker.finished_ok.connect(self._on_login_done)
        self._login_worker.start()

    def _on_login_done(self, ok: bool, cookie: str, msg: str) -> None:
        self.login_btn.setEnabled(True)
        if ok and cookie:
            self._save_cookie(cookie)
            self.pwd_result.setText(tr("wenku8.login.login_ok"))
            self.pwd_result.setStyleSheet("color: #2e7d32;")
        else:
            self.pwd_result.setText(tr("wenku8.login.login_fail", err=msg))
            self.pwd_result.setStyleSheet("color: #c62828;")

    # ------------------------------------------------------------------ Cookie
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
        self._verify_worker = _VerifyWorker(cookie, self)
        self._verify_worker.finished_ok.connect(self._on_verify_done)
        self._verify_worker.start()

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
        self.cookie_edit.setText(cookie)
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
        self.tabs.setTabText(0, tr("wenku8.login.tab_password"))
        self.tabs.setTabText(1, tr("wenku8.login.tab_cookie"))
        self.username_edit.setPlaceholderText(tr("wenku8.login.username"))
        self.password_edit.setPlaceholderText(tr("wenku8.login.password"))
        self.login_btn.setText(tr("wenku8.login.login_btn"))
        self.verify_btn.setText(tr("wenku8.login.verify"))
        self.save_btn.setText(tr("wenku8.login.save"))
        self.reset_btn.setText(tr("wenku8.login.reset"))
        self.show_check.setText(tr("wenku8.login.show"))
        self._refresh_status()
