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
    QScrollArea, QFrame, QSizePolicy,
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
            from .wenku8_core.wenku8_auth import verify_cookie_on_lines
            # 跨线路验证：Cookie 来自 .cc 就拿 .cc 验证，不再默认用 .net，
            # 且以 jieqiUserInfo 标志兜底，避免完整 Cookie 被误判无效。
            ok, _base, detail = verify_cookie_on_lines(self.cookie)
            msg = detail if ok else f"{tr('wenku8.login.invalid')}：{detail}"
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


class _LineCheckWorker(QThread):
    """后台检测单条线路连接状态，避免阻塞界面。"""

    finished_ok = Signal(str, str, str)  # base, state, detail

    def __init__(self, base: str, cookie: str, parent=None) -> None:
        super().__init__(parent)
        self.base = base
        self.cookie = cookie

    def run(self) -> None:  # noqa: C901
        try:
            from .wenku8_core.wenku8_auth import check_line
            state, detail = check_line(self.base, self.cookie)
        except Exception as exc:  # noqa: BLE001
            state, detail = "error", str(exc)
        self.finished_ok.emit(self.base, state, detail)


class Wenku8LoginDialog(QDialog):
    """文库8 登录弹窗：账号密码 / Cookie / 官网线路 三方式。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("wenku8.login.title"))
        self.setMinimumWidth(520)
        self._verify_worker = None
        self._login_worker = None
        self._line_workers = []
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
        self.tabs.addTab(self._build_cookie_tab(), tr("wenku8.login.tab_cookie"))
        self.tabs.addTab(self._build_lines_tab(), tr("wenku8.login.tab_lines"))
        root.addWidget(self.tabs)
        self._browser_dlg = None  # 内嵌浏览器登录对话框，强引用保活

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
        """官网登录页：软件内嵌浏览器打开官网，登录后自动回写 Cookie。"""
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
        base_row = QHBoxLayout()
        base_lbl = QLabel(f"{base}")
        base_lbl.setWordWrap(True)
        base_lbl.setStyleSheet("font-size: 12px;")
        base_row.addWidget(base_lbl)
        lay.addLayout(base_row)

        self.browser_open_btn = QPushButton(tr("wenku8.browser.open"))
        self.browser_open_btn.clicked.connect(self._on_open_browser_login)
        self._style_button(self.browser_open_btn)
        lay.addWidget(self.browser_open_btn)

        self.browser_result = QLabel("")
        self.browser_result.setWordWrap(True)
        lay.addWidget(self.browser_result)
        lay.addStretch()
        return page

    def _on_open_browser_login(self) -> None:
        """打开内嵌浏览器登录窗口，登录成功后自动回写 Cookie 与使用中线路。"""
        from .wenku8_browser_login import Wenku8BrowserLoginDialog
        from .wenku8_core.wenku8_auth import get_active_base

        base = get_active_base()
        self._browser_dlg = Wenku8BrowserLoginDialog(base, self)
        self._browser_dlg.login_captured.connect(
            lambda cookie, hit_base: self._on_browser_login_done(cookie, hit_base))
        self._browser_dlg.show()

    def _on_browser_login_done(self, cookie: str, hit_base: str) -> None:
        """浏览器登录成功：切到命中线路、回写 Cookie、标记登录并刷新界面。"""
        cookie = str(cookie or "").strip()
        if not cookie:
            self.browser_result.setText(tr("wenku8.browser.failed"))
            self.browser_result.setStyleSheet("color: #c62828;")
            return
        try:
            from .wenku8_core.wenku8_auth import set_active_base
            if hit_base:
                set_active_base(hit_base)
        except Exception:  # noqa: BLE001
            pass
        self._save_cookie(cookie)
        from .wenku8_core.wenku8_auth import get_active_base
        self.browser_result.setText(
            tr("wenku8.browser.done", base=hit_base or get_active_base()))
        self.browser_result.setStyleSheet("color: #2e7d32;")
        self.status_label.setText(tr("wenku8.login.status_custom"))
        self.cookie_edit.setText(cookie)
        # 刷新官网线路卡片（登录态已同步到命中线路）
        if hasattr(self, "lines_vbox"):
            self._render_lines()

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

    # ------------------------------------------------------------------ 官网线路
    def _build_lines_tab(self) -> QWidget:
        """官网线路管理页：每条线路独立检测/切换，可连接其他线路。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 12, 4, 4)
        lay.setSpacing(10)

        hint = QLabel(tr("wenku8.login.lines_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        lay.addWidget(hint)

        # 顶部：检测全部 + 连接其他线路
        top = QHBoxLayout()
        self.check_all_btn = QPushButton(tr("wenku8.login.check_all"))
        self.check_all_btn.clicked.connect(self._on_check_all)
        self._style_button(self.check_all_btn)
        top.addWidget(self.check_all_btn)
        top.addStretch()
        lay.addLayout(top)

        # 线路列表（可滚动）
        self.lines_scroll = QScrollArea()
        self.lines_scroll.setWidgetResizable(True)
        self.lines_scroll.setFrameShape(QFrame.NoFrame)
        self.lines_host = QWidget()
        self.lines_vbox = QVBoxLayout(self.lines_host)
        self.lines_vbox.setContentsMargins(0, 0, 0, 0)
        self.lines_vbox.setSpacing(8)
        self.lines_scroll.setWidget(self.lines_host)
        self.lines_scroll.setMinimumHeight(230)
        lay.addWidget(self.lines_scroll, 1)

        # 连接其他官网线路
        add_row = QHBoxLayout()
        self.new_line_edit = QLineEdit()
        self.new_line_edit.setPlaceholderText(tr("wenku8.login.new_line_ph"))
        self.add_line_btn = QPushButton(tr("wenku8.login.add_line"))
        self.add_line_btn.clicked.connect(self._on_add_line)
        self._style_button(self.add_line_btn)
        add_row.addWidget(self.new_line_edit, 1)
        add_row.addWidget(self.add_line_btn)
        lay.addLayout(add_row)

        self.lines_result = QLabel("")
        self.lines_result.setWordWrap(True)
        lay.addWidget(self.lines_result)

        # 保持登录开关
        state = self._line_state()
        self.keep_login_check = QCheckBox(tr("wenku8.login.keep_login"))
        self.keep_login_check.setChecked(bool(state.get("keep_login", True)))
        self.keep_login_check.stateChanged.connect(self._on_keep_login_changed)
        lay.addWidget(self.keep_login_check)

        self._render_lines()
        return page

    def _line_state(self) -> dict:
        from .wenku8_core.wenku8_auth import get_lines_state
        return get_lines_state()

    def _state_text(self, state: str) -> str:
        mapping = {
            "unchecked": tr("wenku8.login.line_unchecked"),
            "ok": tr("wenku8.login.line_ok"),
            "guest": tr("wenku8.login.line_guest"),
            "closed": tr("wenku8.login.line_closed"),
            "blocked": tr("wenku8.login.line_blocked"),
            "error": tr("wenku8.login.line_error"),
        }
        return mapping.get(state, state)

    def _render_lines(self) -> None:
        """重建线路卡片列表。"""
        # 同步清空旧卡片
        while self.lines_vbox.count():
            item = self.lines_vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        state = self._line_state()
        active = str(state.get("active") or "")
        for ln in state["lines"]:
            base = ln["base"]
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background-color: {self._line_card_bg()}; "
                f"border: 1px solid {BORDER_LIGHT}; border-radius: 10px; }}"
            )
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cv = QVBoxLayout(card)
            cv.setContentsMargins(12, 10, 12, 10)
            cv.setSpacing(6)

            is_active = base == active
            title = QLabel(ln.get("label") or base)
            title.setStyleSheet(
                f"font-weight: bold; font-size: 13px; color: "
                + (ACCENT if self._dark else ACCENT_DARK) + ";"
            )
            cv.addWidget(title)

            status_parts = [base]
            # 用「有效 Cookie」判断：线路自身没存但全局已登录也算有登录态，
            # 否则历史数据会显示成未登录、误导用户
            if str(ln.get("cookie") or "").strip() or self._effective_line_cookie(ln):
                status_parts.append(tr("wenku8.login.line_saved"))
            status_parts.append(self._state_text(ln.get("state", "unchecked")))
            if ln.get("detail"):
                status_parts.append(ln["detail"])
            status = QLabel(" · ".join(status_parts))
            status.setWordWrap(True)
            status.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
            cv.addWidget(status)

            row = QHBoxLayout()
            check_btn = QPushButton(tr("wenku8.login.line_check"))
            check_btn.clicked.connect(lambda _c=False, b=base: self._on_check_line(b))
            self._style_button(check_btn)
            row.addWidget(check_btn)
            if is_active:
                active_lbl = QLabel(tr("wenku8.login.line_active"))
                active_lbl.setStyleSheet(
                    f"color: {TEXT_DIM}; font-size: 12px; padding: 0 8px;")
                row.addWidget(active_lbl)
            else:
                switch_btn = QPushButton(tr("wenku8.login.line_switch"))
                switch_btn.clicked.connect(
                    lambda _c=False, b=base: self._on_switch_line(b))
                self._style_button(switch_btn)
                row.addWidget(switch_btn)
            # 自定义线路可删除
            from .wenku8_core.wenku8_auth import _BASE_CANDIDATES
            if base not in _BASE_CANDIDATES:
                del_btn = QPushButton(tr("wenku8.login.line_remove"))
                del_btn.clicked.connect(lambda _c=False, b=base: self._on_remove_line(b))
                del_btn.setStyleSheet(
                    "QPushButton { color: #c62828; background: transparent; "
                    "border: none; font-size: 11px; }")
                row.addWidget(del_btn)
            row.addStretch()
            cv.addLayout(row)
            self.lines_vbox.addWidget(card)
        self.lines_vbox.addStretch()

    def _line_card_bg(self) -> str:
        return "#2b2438" if self._dark else "#f3eff6"

    def _effective_line_cookie(self, ln: dict) -> str:
        """线路独立 Cookie 优先，缺失时回退全局登录态（兼容历史数据与只在
        Cookie/账号密码页登录过的用户），避免明明已登录却检测出「未登录」。"""
        own = str(ln.get("cookie") or "").strip()
        if own:
            return own
        try:
            from .wenku8_core.wenku8_auth import get_cookie
            return str(get_cookie() or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _on_check_all(self) -> None:
        state = self._line_state()
        self.check_all_btn.setEnabled(False)
        self.lines_result.setText(tr("wenku8.login.checking_all"))
        QApplication.processEvents()
        self._check_remaining = len(state["lines"])
        for ln in state["lines"]:
            self._check_one_line(ln["base"], self._effective_line_cookie(ln),
                                 on_done=self._on_one_check_batch_done)

    def _on_one_check_batch_done(self) -> None:
        """批量检测：最后一个完成后统一刷新卡片并恢复按钮。"""
        self._check_remaining = getattr(self, "_check_remaining", 1) - 1
        if self._check_remaining <= 0:
            self.check_all_btn.setEnabled(True)
            self.lines_result.setText(tr("wenku8.login.check_all_done"))
            self._render_lines()

    def _on_check_line(self, base: str) -> None:
        state = self._line_state()
        ln = next((ln for ln in state["lines"] if ln["base"] == base), None)
        cookie = self._effective_line_cookie(ln) if ln else ""
        self.lines_result.setText(tr("wenku8.login.line_checking"))
        self._check_one_line(base, cookie, on_done=self._render_lines)

    def _check_one_line(self, base: str, cookie: str, on_done) -> None:
        """启动单条线路检测；on_done 是完成后调用的回调（无参）。"""
        worker = _LineCheckWorker(base, cookie, self)
        self._line_workers.append(worker)

        def _done(b, state, detail):
            self.lines_result.setText(
                tr("wenku8.login.line_checked", base=b, state=self._state_text(state)))
            if callable(on_done):
                on_done()
        worker.finished_ok.connect(_done)
        worker.start()

    def _on_switch_line(self, base: str) -> None:
        from .wenku8_core.wenku8_auth import set_active_base
        set_active_base(base)
        self.lines_result.setText(tr("wenku8.login.line_switched", base=base))
        self.lines_result.setStyleSheet("color: #2e7d32;")
        self._render_lines()
        self._refresh_status()

    def _on_add_line(self) -> None:
        from .wenku8_core.wenku8_auth import add_line
        raw = self.new_line_edit.text().strip()
        ok, base, msg = add_line(raw)
        if ok:
            self.new_line_edit.clear()
            self.lines_result.setText(msg)
            self.lines_result.setStyleSheet("color: #2e7d32;")
            self._render_lines()
        else:
            self.lines_result.setText(msg)
            self.lines_result.setStyleSheet("color: #c62828;")

    def _on_remove_line(self, base: str) -> None:
        from .wenku8_core.wenku8_auth import remove_line
        if remove_line(base):
            self.lines_result.setText(tr("wenku8.login.line_removed", base=base))
            self.lines_result.setStyleSheet(f"color: {TEXT_DIM};")
            self._render_lines()

    def _on_keep_login_changed(self, state) -> None:
        from .wenku8_core.wenku8_auth import get_lines_state, save_lines_state
        data = get_lines_state()
        data["keep_login"] = bool(state)
        save_lines_state(data)

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
        # 关键：同步到「使用中」线路的独立 Cookie。否则官网线路页检测时
        # 拿到的是空 Cookie，带空登录态访问必然跳 login.php，明明登录成功
        # 却显示「可访问（未登录）」。
        try:
            from .wenku8_core.wenku8_auth import get_active_base, save_line_cookie
            save_line_cookie(get_active_base(), cookie)
        except Exception:  # noqa: BLE001
            pass
        self._refresh_status()
        # 登录成功后把使用中线路直接标记为已登录，避免卡片仍显示「未登录」
        try:
            from .wenku8_core.wenku8_auth import (
                get_active_base, get_lines_state, save_lines_state,
            )
            active = get_active_base()
            state = get_lines_state()
            for ln in state["lines"]:
                if ln["base"] == active and cookie:
                    ln["state"] = "ok"
                    ln["detail"] = tr("wenku8.login.line_ok")
            save_lines_state(state)
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self, "lines_vbox"):
            self._render_lines()
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
        self.tabs.setTabText(2, tr("wenku8.login.tab_lines"))
        self.username_edit.setPlaceholderText(tr("wenku8.login.username"))
        self.password_edit.setPlaceholderText(tr("wenku8.login.password"))
        self.login_btn.setText(tr("wenku8.login.login_btn"))
        self.verify_btn.setText(tr("wenku8.login.verify"))
        self.save_btn.setText(tr("wenku8.login.save"))
        self.reset_btn.setText(tr("wenku8.login.reset"))
        self.show_check.setText(tr("wenku8.login.show"))
        if hasattr(self, "check_all_btn"):
            self.check_all_btn.setText(tr("wenku8.login.check_all"))
            self.new_line_edit.setPlaceholderText(tr("wenku8.login.new_line_ph"))
            self.add_line_btn.setText(tr("wenku8.login.add_line"))
            self.keep_login_check.setText(tr("wenku8.login.keep_login"))
            self._render_lines()
        self._refresh_status()
