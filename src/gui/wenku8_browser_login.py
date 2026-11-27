# -*- coding: utf-8 -*-
"""文库8「官网登录」——软件内嵌浏览器登录，登录后自动回到软件。

交互参考 mikufoxxx/Wenku8_Flutter：点登录 → 在软件内打开官网登录页 →
用户在**真实 Chromium 环境**完成登录 → 软件监听到登录 Cookie 后自动回写
登录态、关闭窗口回到主界面，无需手动复制 document.cookie。

为什么能绕开风控：内嵌 WebEngine 是完整浏览器内核（正常 TLS 指纹 +
JS 执行 + 人机验证可完成），Cloudflare 视为真人放行；登录 Cookie 由
官网在正确域名下自然写入，不存在「.cc Cookie 拿到 .net 验证」的错配。

登录使用进程级共享持久化 profile（wenku8_web_context.get_shared_profile），
与搜索通道复用同一浏览器会话：官网质询拿到的 cf_clearance 不会随窗口关闭
而丢失，后续搜索同源 fetch 直接复用。判定登录成功的权威标志是
jieqiUserInfo（Jieqi CMS 登录态，未登录时不存在）。
Cookie 捕获走 QWebEngineProfile.cookieStore().cookieAdded 信号；
Cookie 序列化/域名匹配抽成纯函数，便于离线单测（不依赖 QApplication）。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal, QUrl
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from .i18n import tr

# Jieqi CMS 登录态 Cookie：登录后写入，未登录时不存在
LOGIN_COOKIE_NAME = "jieqiUserInfo"


# ---------------------------------------------------------------------------
# 纯逻辑（可离线单测，接受任意带 name()/value()/domain() 的替身）
# ---------------------------------------------------------------------------

def _qbytearray_to_str(raw) -> str:
    """QByteArray 或 str 统一转字符串。"""
    try:
        if isinstance(raw, str):
            return raw
        return bytes(raw).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""


def cookie_name(cookie) -> str:
    try:
        return _qbytearray_to_str(cookie.name())
    except Exception:  # noqa: BLE001
        return ""


def cookie_value(cookie) -> str:
    try:
        return _qbytearray_to_str(cookie.value())
    except Exception:  # noqa: BLE001
        return ""


def cookie_domain(cookie) -> str:
    try:
        return str(cookie.domain() or "").lstrip(".").lower()
    except Exception:  # noqa: BLE001
        return ""


def is_login_cookie(cookie) -> bool:
    """是否是有效的登录态 Cookie（jieqiUserInfo 且值非空）。"""
    return (cookie_name(cookie).lower() == LOGIN_COOKIE_NAME.lower()
            and bool(cookie_value(cookie).strip()))


def host_of(base: str) -> str:
    return str(base or "").split("//", 1)[-1].split("/", 1)[0].lower()


def domain_matches(cookie_domain_str: str, base: str) -> bool:
    """Cookie 域名是否属于目标站点（www 与裸域互通）。"""
    d = str(cookie_domain_str or "").lstrip(".").lower()
    host = host_of(base)
    if not d or not host:
        return False
    return d == host or host.endswith("." + d) or d.endswith("." + host)


class BrowserCookieJar:
    """累积 WebEngine 解析到的 Cookie，检测登录态并序列化为软件可用字符串。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self._cookies: dict[tuple[str, str], str] = {}  # (domain, name) -> value
        self.login_found = False

    def add(self, cookie) -> None:
        name = cookie_name(cookie)
        if not name:
            return
        dom = cookie_domain(cookie)
        self._cookies[(dom, name)] = cookie_value(cookie)
        if is_login_cookie(cookie) and domain_matches(dom, self.base):
            self.login_found = True

    def has_login(self) -> bool:
        return self.login_found

    def cookie_string(self) -> str:
        """序列化属于目标站点的 Cookie 为 'k=v; k=v'（供现有登录态机制复用）。"""
        parts = []
        for (dom, name), val in self._cookies.items():
            if not name or not domain_matches(dom, self.base):
                continue
            parts.append(f"{name}={val}")
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# 内嵌浏览器对话框
# ---------------------------------------------------------------------------

class Wenku8BrowserLoginDialog(QDialog):
    """内嵌 Chromium 打开官网登录页，登录成功自动回写 Cookie 并关闭。"""

    login_captured = Signal(str, str)  # cookie字符串, 命中域名(base)

    def __init__(self, base: str, parent=None) -> None:
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView

        super().__init__(parent)
        self.base = str(base or "").rstrip("/")
        self.setWindowTitle(tr("wenku8.browser.title"))
        self.setMinimumSize(900, 640)
        self._captured = False
        self.jar = BrowserCookieJar(self.base)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        hint = QLabel(tr("wenku8.browser.hint").format(base=self.base))
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 共享持久化 profile：与搜索通道复用同一浏览器会话，Cloudflare
        # clearance 在登录后继续有效（参考 App 全程单 WebView 的做法）。
        # 注意 profile 由模块单例持有，不能以 self 为 parent，否则登录窗口
        # 关闭时 profile 被销毁，clearance 丢失正是旧版的根因。
        from .wenku8_core.wenku8_web_context import get_shared_profile
        self.profile = get_shared_profile()
        self.cookie_store = self.profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self._on_cookie_added)

        self.view = QWebEngineView(self)
        self.page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.page)
        # 共享 profile 持久化后若已登录，cookie 已存在，cookieAdded 不再触发，
        # 会永久卡在「等待登录」。页面加载完成后主动读同源 document.cookie 兜底。
        self.page.loadFinished.connect(self._on_page_loaded)
        self.view.setUrl(QUrl(self.base + "/login.php"))
        root.addWidget(self.view, 1)

        self.status = QLabel(tr("wenku8.browser.waiting"))
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton(tr("wenku8.browser.cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        root.addLayout(btn_row)

    def _on_page_loaded(self, ok: bool = False) -> None:
        """页面加载后主动读同源 cookie：覆盖「profile 已有登录态无信号」场景。"""
        if self._captured:
            return
        try:
            self.page.runJavaScript(
                "document.cookie || ''", self._on_document_cookie)
        except Exception:  # noqa: BLE001
            pass

    def _on_document_cookie(self, raw: str) -> None:
        """从 document.cookie 识别已存在的登录态并直接完成回写。"""
        if self._captured or not isinstance(raw, str) or not raw:
            return
        parts = {}
        for seg in raw.split(";"):
            key, sep, val = seg.strip().partition("=")
            if sep and key:
                parts[key] = val
        if not str(parts.get(LOGIN_COOKIE_NAME, "")).strip():
            return
        # 已登录：把同源 cookie 灌进 jar 再统一回写（domain 用 host 便于匹配）
        host = host_of(self.base)
        for key, val in parts.items():
            self.jar._cookies[(host, key)] = val
        self.jar.login_found = True
        self._captured = True
        self.status.setText(tr("wenku8.browser.success_wait"))
        QTimer.singleShot(300, self._finish_capture)

    def _on_cookie_added(self, cookie) -> None:
        """监听到新 Cookie：累积并在检测到登录态时延迟回写（等 Cookie 齐全）。"""
        self.jar.add(cookie)
        if self.jar.has_login() and not self._captured:
            self._captured = True
            self.status.setText(tr("wenku8.browser.success_wait"))
            # 登录跳转后仍可能写入 jieqiVisitInfo 等，稍等再统一提取
            QTimer.singleShot(1200, self._finish_capture)

    def _finish_capture(self) -> None:
        cookie_str = self.jar.cookie_string()
        if not self.jar.has_login() or not cookie_str:
            self._captured = False
            return
        self.login_captured.emit(cookie_str, self.base)
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.cookie_store.cookieAdded.disconnect(self._on_cookie_added)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.page.loadFinished.disconnect(self._on_page_loaded)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
