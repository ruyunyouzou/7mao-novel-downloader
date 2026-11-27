# -*- coding: utf-8 -*-
"""文库8（轻小说文库）登录对话框。

对齐 mikufoxxx/Wenku8_Flutter：只保留账号密码登录（含官网浏览器内输入
账号密码的官方路径），不再提供手动粘贴 Cookie、Cookie 验证、线路检测等
用户侧 Cookie 功能。账号密码登录成功后站点返回的会话由软件自动保存，
账号密码本身不落盘。
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QApplication, QTabWidget, QWidget, QFormLayout,
)

from .i18n import tr
from .datastore import DataStore
from .style import ACCENT, ACCENT_DARK, TEXT, TEXT_DIM, BG_CARD


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
    """文库8登录弹窗：账号密码 / 官网浏览器（官方路径）两种方式。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("wenku8.login.title"))
        self.setMinimumWidth(520)
        self._login_worker = None
        self._browser_dlg = None  # 内嵌浏览器登录对话框，强引用保活
        self._closing = False
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
        self.tabs.addTab(self._build_browser_tab(), tr("wenku8.login.tab_browser"))
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

    def _build_browser_tab(self) -> QWidget:
        """官网登录页：软件内嵌浏览器打开官网，用账号密码登录后自动回写会话。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 12, 4, 4)
        lay.setSpacing(12)

        hint = QLabel(tr("wenku8.browser.open_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        lay.addWidget(hint)

        from .wenku8_core.wenku8_auth import get_active_base
        base = get_active_base()
        base_lbl = QLabel(f"{base}")
        base_lbl.setWordWrap(True)
        base_lbl.setStyleSheet("font-size: 12px;")
        lay.addWidget(base_lbl)

        self.browser_open_btn = QPushButton(tr("wenku8.browser.open"))
        self.browser_open_btn.clicked.connect(self._on_open_browser_login)
        self._style_button(self.browser_open_btn)
        lay.addWidget(self.browser_open_btn)

        self.browser_result = QLabel("")
        self.browser_result.setWordWrap(True)
        lay.addWidget(self.browser_result)
        lay.addStretch()
        return page

    # ------------------------------------------------------------------ 官网浏览器登录
    def _on_open_browser_login(self) -> None:
        """打开内嵌浏览器登录窗口，账号密码登录成功后自动回写会话。"""
        from .wenku8_browser_login import Wenku8BrowserLoginDialog
        from .wenku8_core.wenku8_auth import get_active_base

        base = get_active_base()
        self._browser_dlg = Wenku8BrowserLoginDialog(base, self)
        self._browser_dlg.login_captured.connect(
            lambda cookie, hit_base: self._on_browser_login_done(cookie, hit_base))
        self._browser_dlg.show()

    def _on_browser_login_done(self, cookie: str, hit_base: str) -> None:
        """浏览器登录成功：回写会话、标记登录并刷新界面。"""
        cookie = str(cookie or "").strip()
        if not cookie:
            self.browser_result.setText(tr("wenku8.browser.failed"))
            self.browser_result.setStyleSheet("color: #c62828;")
            return
        from .wenku8_core.wenku8_auth import get_active_base
        base = str(hit_base or "").strip().rstrip("/") or get_active_base()
        self._save_cookie(cookie, base=base)
        self.browser_result.setText(
            tr("wenku8.browser.done", base=base))
        self.browser_result.setStyleSheet("color: #2e7d32;")
        self.status_label.setText(tr("wenku8.login.status_custom"))

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
            from .wenku8_core.wenku8_auth import get_active_base
            self._save_cookie(cookie, base=get_active_base())
            self.pwd_result.setText(tr("wenku8.login.login_ok"))
            self.pwd_result.setStyleSheet("color: #2e7d32;")
        else:
            self.pwd_result.setText(tr("wenku8.login.login_fail", err=msg))
            self.pwd_result.setStyleSheet("color: #c62828;")

    # ------------------------------------------------------------------ 会话保存
    def _refresh_status(self) -> None:
        custom = str(DataStore().settings_get("wenku8_cookie", "") or "").strip()
        if custom:
            self.status_label.setText(tr("wenku8.login.status_custom"))
        else:
            self.status_label.setText(tr("wenku8.login.status_none"))

    def _save_cookie(self, cookie: str, base: str = "") -> None:
        """保存账号密码/官网登录成功后站点返回的会话（内部机制，非用户侧功能）。

        必须同时写入「当前线路的独立 Cookie」：切线路时
        set_active_base(sync_cookie=True) 会用线路 Cookie 覆盖全局值，
        若登录只写全局不写线路，切一次线路就把登录态冲掉了。
        """
        from .wenku8_core.wenku8_auth import save_line_cookie, get_active_base
        DataStore().settings_set("wenku8_cookie", cookie)
        target = str(base or get_active_base() or "").strip().rstrip("/")
        if target:
            save_line_cookie(target, cookie)
        self._refresh_status()

    def retranslate(self, *args) -> None:
        self.setWindowTitle(tr("wenku8.login.title"))
        # tab 顺序须与 _build() 一致：0密码 / 1官网登录
        self.tabs.setTabText(0, tr("wenku8.login.tab_password"))
        self.tabs.setTabText(1, tr("wenku8.login.tab_browser"))
        self.username_edit.setPlaceholderText(tr("wenku8.login.username"))
        self.password_edit.setPlaceholderText(tr("wenku8.login.password"))
        self.login_btn.setText(tr("wenku8.login.login_btn"))
        if hasattr(self, "browser_open_btn"):
            self.browser_open_btn.setText(tr("wenku8.browser.open"))
        self._refresh_status()

    def closeEvent(self, event) -> None:  # noqa: N802
        """关闭对话框时标记关闭中，等待后台线程结束后再销毁，避免竞态崩溃。"""
        self._closing = True
        workers = [w for w in (self._login_worker,) if w is not None]
        for w in workers:
            try:
                w.finished_ok.disconnect()
            except Exception:  # noqa: BLE001
                pass
            try:
                if w.isRunning():
                    w.requestInterruption()
                    w.wait(2000)  # 最多等 2 秒，避免阻塞关闭
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)
