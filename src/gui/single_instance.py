# -*- coding: utf-8 -*-
"""单实例保护：保证同一用户同时只运行一个 GUI 进程。

背景：Windows 下用户反复双击 exe，若旧实例只是最小化到托盘（或异常残留），
会累积多个常驻进程并锁定安装目录 (_internal/*.dll)，导致无法删除/覆盖旧版本。

实现：基于 Qt 的 QLocalServer / QLocalSocket（跨平台、无第三方依赖）。
- 启动时尝试连接固定名称的本地 server：
  * 连得上 → 已有主实例在运行 → 发送 "activate" 请求让其显示窗口，本实例立即退出。
  * 连不上 → 创建 server 成为主实例，监听后续实例。
- 采用"僵尸 server"清理：若连接成功但写不进数据，说明上次进程异常退出留下残留
  server，此时移除它并接管。

注意：更新模式（--do-update）由独立 updater 进程处理，不经过本模块，
因此不会与新版本启动冲突。
"""

from __future__ import annotations

import hashlib
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# 固定的 server 名：带用户名 hash，避免多用户会话互相干扰；长度受限故取前 12 位
_NAME = "xingyu_novel_single_instance_" + hashlib.md5(
    (os.environ.get("USERNAME") or os.environ.get("USER") or "default").encode("utf-8")
).hexdigest()[:12]

_CONNECT_MS = 800


class SingleInstance(QObject):
    """单实例守卫。``activated`` 在第二个实例请求激活时发出。"""

    activated = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._server = QLocalServer(self)
        self._socket: QLocalSocket | None = None
        self.is_first = False

    def acquire(self) -> bool:
        """尝试获取单实例身份。返回 True=本进程为主实例，False=已有实例、本进程应退出。"""
        # 先探测是否已有主实例
        probe = QLocalSocket(self)
        probe.connectToServer(_NAME)
        if probe.waitForConnected(_CONNECT_MS):
            # 已有实例：请求其激活窗口，然后本实例退出
            probe.write(b"activate\n")
            probe.flush()
            probe.waitForBytesWritten(_CONNECT_MS)
            probe.disconnectFromServer()
            self.is_first = False
            return False
        # 连不上：清理可能的残留 server，再监听
        QLocalServer.removeServer(_NAME)
        if not self._server.listen(_NAME):
            # 极端情况下仍监听失败，放行以免卡死程序
            self.is_first = True
            return True
        self._server.newConnection.connect(self._on_new_connection)
        self.is_first = True
        return True

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            sock.readyRead.connect(lambda s=sock: self._read_request(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read_request(self, sock: QLocalSocket) -> None:
        try:
            data = bytes(sock.readAll())
        except Exception:  # noqa: BLE001
            data = b""
        if b"activate" in data:
            self.activated.emit()

    def release(self) -> None:
        """进程退出时关闭 server，避免留下僵尸连接。"""
        try:
            if self._server.isListening():
                self._server.close()
            QLocalServer.removeServer(_NAME)
        except Exception:  # noqa: BLE001
            pass
