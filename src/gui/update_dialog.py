# -*- coding: utf-8 -*-
"""更新对话框：展示新版本说明、下载渠道，支持强制更新拦截与一键升级。

由设置页「检查更新」与启动时静默检查共用。强制更新（manifest 的
min_version 高于本地）时隐藏「稍后再说」按钮，并拦截关闭。

一键升级：点主按钮后后台下载新版 zip → 解压 → 拉起独立 updater → 本程序退出，
由 updater 完成文件替换与重启。任一环节失败都会给出明确提示，并保留原来的
「下载渠道」按钮让用户手动下载兜底。
"""

from __future__ import annotations

import webbrowser
from typing import Any, Dict

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
    QProgressBar, QMessageBox,
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
        self._worker = None  # UpgradeWorker 实例

        self.setWindowTitle(tr("update.title"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)

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
        notes.setMinimumHeight(120)
        root.addWidget(notes)

        # ---- 一键升级区 ----
        self.upgrade_bar = QVBoxLayout()
        from .widgets import nation_icon
        from PySide6.QtCore import QSize
        self.btn_upgrade = QPushButton(tr("update.upgrade_now"))
        self.btn_upgrade.setObjectName("upgradeBtn")
        self.btn_upgrade.setIcon(nation_icon("natlan", "#EC4923", 18))
        self.btn_upgrade.setIconSize(QSize(18, 18))
        self.btn_upgrade.setMinimumHeight(40)
        self.btn_upgrade.clicked.connect(self._start_upgrade)
        self.upgrade_bar.addWidget(self.btn_upgrade)

        self.upgrade_status = QLabel("")
        self.upgrade_status.setWordWrap(True)
        self.upgrade_status.setStyleSheet("font-size: 11px; color: #888;")
        self.upgrade_status.hide()
        self.upgrade_bar.addWidget(self.upgrade_status)

        self.upgrade_progress = QProgressBar()
        self.upgrade_progress.setRange(0, 100)
        self.upgrade_progress.hide()
        self.upgrade_bar.addWidget(self.upgrade_progress)
        root.addLayout(self.upgrade_bar)

        # 下载渠道按钮（手动兜底）
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
            QPushButton#upgradeBtn {
                padding: 8px 12px; border-radius: 8px; font-weight: 600;
                background-color: #4caf50; color: #fff;
            }
            QPushButton#upgradeBtn:hover { background-color: #43a047; }
            QPushButton#upgradeBtn:disabled { background-color: #666; color: #ccc; }
            QTextBrowser {
                background: transparent; border: 1px solid rgba(128,128,128,0.25);
                border-radius: 8px; padding: 8px;
            }
        """)

    # ---------------------------------------------------------------- 一键升级
    def _start_upgrade(self) -> None:
        """点击一键升级：启动后台下载并安装。"""
        from .update_installer import UpgradeWorker
        self.btn_upgrade.setEnabled(False)
        self.btn_upgrade.setText(self._tr("update.upgrading"))
        self.upgrade_status.show()
        self.upgrade_progress.show()
        self.upgrade_progress.setValue(0)
        self.upgrade_status.setText(self._tr("update.upgrade_preparing"))

        self._worker = UpgradeWorker(version=str(self.result.get("latest") or ""), parent=self)
        self._worker.progress.connect(self.upgrade_progress.setValue)
        self._worker.status.connect(self.upgrade_status.setText)
        self._worker.done.connect(self._on_upgrade_done)
        self._worker.start()

    def _on_upgrade_done(self, r: Dict[str, Any]) -> None:
        self._worker = None
        if r.get("ok") and r.get("need_restart"):
            # 更新进程已拉起，本程序即将退出；隐藏对话框，让主窗口走退出流程
            self.upgrade_status.setText(self._tr("update.upgrade_restarting"))
            self.upgrade_progress.setValue(100)
            QMessageBox.information(
                self, self._tr("update.title"),
                self._tr("update.upgrade_restart_hint"),
            )
            self.accept()  # 先关对话框
            # 请求主程序退出：updater 会等本进程结束后覆盖并重启。
            # 先标记主窗口 _force_quit，避免任何托盘/closeEvent 拦截导致进程不退出；
            # 再 quit() 结束事件循环。updater 侧轮询 PID 消失后开始覆盖。
            from PySide6.QtWidgets import QApplication
            win = self._find_main_window()
            if win is not None:
                win._force_quit = True
            QApplication.instance().quit()
            return

        # 失败：恢复按钮，给出提示，引导手动下载
        self.btn_upgrade.setEnabled(True)
        self.btn_upgrade.setText(self._tr("update.upgrade_now"))
        self.upgrade_progress.hide()
        err = r.get("error") or self._tr("update.upgrade_failed")
        self.upgrade_status.setText(err)
        QMessageBox.warning(
            self, self._tr("update.upgrade_failed"),
            f"{err}\n\n{self._tr('update.upgrade_fallback')}",
        )

    def _find_main_window(self):
        """沿 parent 链找主窗口（带 _force_quit 属性的那个）。"""
        w = self.parent()
        while w is not None:
            if hasattr(w, "_force_quit"):
                return w
            w = w.parent() if hasattr(w, "parent") else None
        # 兜底：遍历顶层窗口
        from PySide6.QtWidgets import QApplication
        for top in QApplication.topLevelWidgets():
            if hasattr(top, "_force_quit"):
                return top
        return None

    # ---------------------------------------------------------------- 静态工具
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
        if self.result.get("force") and not getattr(self, "_force_accepted", False):
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self.result.get("force") and not getattr(self, "_force_accepted", False):
            return
        super().reject()

    _force_accepted = False


def show_update_dialog(result: Dict[str, Any], parent=None) -> None:
    """便捷入口：弹出更新对话框。"""
    dlg = UpdateDialog(result, parent)
    dlg.exec()
