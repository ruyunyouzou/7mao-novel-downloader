# -*- coding: utf-8 -*-
"""更新对话框：展示新版本说明、下载渠道，支持强制更新拦截。

由设置页「检查更新」与启动时静默检查共用。强制更新（manifest 的
min_version 高于本地）时隐藏「稍后再说」按钮，并拦截关闭。
"""

from __future__ import annotations

import webbrowser
from typing import Any, Dict

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
)


class UpdateDialog(QDialog):
    """更新提示对话框。

    参数 result 为 update_core.check_update() 返回的字典。
    """

    def __init__(self, result: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        from .i18n import tr
        self._tr = tr
        self.result = result
        force = bool(result.get("force"))

        self.setWindowTitle(tr("update.title"))
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setMinimumHeight(320)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        # 标题行
        title = QLabel(tr("update.new_version"))
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)

        ver_row = QHBoxLayout()
        cur = QLabel(f"{tr('update.current')}: {result.get('current', '')}")
        latest = QLabel(f"{tr('update.latest')}: {result.get('latest', '')}")
        latest.setStyleSheet("font-weight: 600; color: #4caf50;")
        ver_row.addWidget(cur)
        ver_row.addStretch()
        ver_row.addWidget(latest)
        root.addLayout(ver_row)

        if force:
            warn = QLabel(tr("update.force_hint"))
            warn.setStyleSheet("color: #f44336; font-weight: 600;")
            warn.setWordWrap(True)
            root.addWidget(warn)

        # 更新说明（去掉简单 Markdown 标记）
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setPlainText(self._clean_notes(result.get("notes") or ""))
        notes.setMinimumHeight(130)
        root.addWidget(notes)

        # 下载渠道按钮
        channels = result.get("channels") or []
        if channels:
            ch_label = QLabel(tr("update.channels"))
            ch_label.setStyleSheet("font-size: 12px; color: #888;")
            root.addWidget(ch_label)
        for ch in channels:
            name = ch.get("name") or tr("update.download")
            url = ch.get("url") or ""
            btn = QPushButton(f"⬇ {name}")
            btn.setObjectName("channelBtn")
            btn.clicked.connect(lambda _checked=False, u=url: self._open(u))
            root.addWidget(btn)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        if not force:
            later = QPushButton(tr("update.later"))
            later.clicked.connect(self.reject)
            btn_row.addWidget(later)
            close_btn = QPushButton(tr("update.close"))
            close_btn.clicked.connect(self.accept)
            btn_row.addWidget(close_btn)
        else:
            # 强制更新：只给一个「前往下载」按钮，点渠道按钮不算关闭
            go_btn = QPushButton(tr("update.goto_download"))
            go_btn.clicked.connect(self._on_force_go)
            btn_row.addWidget(go_btn)
        root.addLayout(btn_row)

        self.setStyleSheet("""
            QPushButton#channelBtn {
                padding: 8px 12px; border-radius: 6px; text-align: left;
            }
            QPushButton#channelBtn:hover { background-color: rgba(255,255,255,0.08); }
            QTextBrowser {
                background: transparent; border: 1px solid rgba(128,128,128,0.25);
                border-radius: 8px; padding: 8px;
            }
        """)

    @staticmethod
    def _clean_notes(text: str) -> str:
        """去掉常见 Markdown 标记，纯文本展示。"""
        import re
        s = str(text or "")
        s = re.sub(r"```.*?```", "", s, flags=re.S)      # 代码块（含 olib 风格注释块）
        s = re.sub(r"<!--.*?-->", "", s, flags=re.S)      # HTML 注释
        s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)      # 标题
        s = re.sub(r"^\s*[-*]\s+", "", s, flags=re.M)     # 列表
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)            # 粗体
        s = re.sub(r"\*(.*?)\*", r"\1", s)                # 斜体
        s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)   # 链接
        return s.strip()

    @staticmethod
    def _open(url: str) -> None:
        if not url:
            return
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:  # noqa: BLE001
            webbrowser.open(url)

    def _on_force_go(self) -> None:
        """强制更新点「前往下载」：打开首选渠道后允许关闭。"""
        channels = self.result.get("channels") or []
        if channels:
            self._open(channels[0].get("url") or "")
        self._force_accepted = True
        super().accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        """强制更新时拦截右上角 X / Esc，只能通过「前往下载」离开。"""
        if self.result.get("force") and not self._force_accepted:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self.result.get("force") and not self._force_accepted:
            return
        super().reject()

    _force_accepted = False


def show_update_dialog(result: Dict[str, Any], parent=None) -> None:
    """便捷入口：弹出更新对话框。"""
    dlg = UpdateDialog(result, parent)
    dlg.exec()
