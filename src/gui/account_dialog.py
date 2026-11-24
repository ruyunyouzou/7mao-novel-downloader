# -*- coding: utf-8 -*-
"""
账号入口对话框。

现实约束
========
七猫官方及本项目所用的公开接口均未提供「第三方账号登录」API，
且当前下载链路走的是免登录的缓存 ZIP 通道，下载全书并不需要账号。

因此这里不伪造账号密码登录框，而是提供：
1. 账号信息的本地登记（昵称 / 手机号 / IMEI）；
2. Cookie 的手动配置，供将来需要鉴权的接口复用。

所有信息只保存在本机 ~/.qimao_gui/settings.json，不会上传。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QFormLayout, QMessageBox, QFrame,
)

from .style import BG_CARD, BORDER, TEXT_DIM, ACCENT, WARN, accent_button
from .widgets import GroupTitleLabel


def get_account(store: Any) -> Optional[Dict[str, Any]]:
    """读取本地登记的账号信息；未配置返回 None。"""
    data = store.settings_get("account", None)
    return data if isinstance(data, dict) and data else None


class AccountDialog(QDialog):
    """账号配置对话框（本地配置，不做真实登录）。"""

    def __init__(self, store: Any, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("账号")
        self.resize(560, 480)
        self.setMinimumWidth(480)
        self._build()
        self._load()

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 标题
        title = GroupTitleLabel("账号", "snezhnaya", ACCENT, font_size=20)
        root.addWidget(title)

        # 说明卡片
        notice = QFrame()
        notice.setStyleSheet(
            f"QFrame {{ background-color: {BG_CARD}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; }}"
        )
        nl = QVBoxLayout(notice)
        nl.setContentsMargins(14, 12, 14, 12)
        nl.setSpacing(6)
        info = QLabel(
            "七猫官方目前没有开放第三方账号登录接口，本下载器走的是<b>免登录</b>的缓存下载通道，"
            "不登录也能下载全书。<br><br>"
            "如果你有自己的七猫账号 Cookie（用于将来需要鉴权的接口），可以在下方手动填写。"
            "这些信息<b>只保存在本机</b>，不会上传。"
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        info.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px; line-height: 150%;")
        nl.addWidget(info)
        warn = QLabel("⚠ 本对话框不会向七猫服务器发起登录请求，仅作本地账号信息登记。")
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {WARN}; font-size: 12px;")
        nl.addWidget(warn)
        root.addWidget(notice)

        # 状态
        self.status_label = QLabel("当前状态：尚未配置")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        root.addWidget(self.status_label)

        # 表单
        form = QFormLayout()
        form.setSpacing(10)

        self.nick_edit = QLineEdit()
        self.nick_edit.setPlaceholderText("给自己起的备注名，例如：我的七猫号")
        form.addRow("备注昵称", self.nick_edit)

        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("可选，仅本地记录")
        form.addRow("手机号", self.phone_edit)

        self.imei_edit = QLineEdit()
        self.imei_edit.setPlaceholderText("可选，某些七猫接口需要 imei")
        form.addRow("IMEI", self.imei_edit)

        self.cookie_edit = QTextEdit()
        self.cookie_edit.setPlaceholderText(
            "可选：粘贴浏览器中的七猫 Cookie（如 Hm_lvt_...; token=...）"
        )
        self.cookie_edit.setMinimumHeight(110)
        form.addRow("Cookie", self.cookie_edit)
        root.addLayout(form)

        # 按钮
        row = QHBoxLayout()
        row.addStretch()
        btn_clear = QPushButton("清除")
        btn_clear.clicked.connect(self._clear)
        row.addWidget(btn_clear)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_cancel)
        btn_save = QPushButton("保存到本机")
        accent_button(btn_save)
        btn_save.clicked.connect(self._save)
        row.addWidget(btn_save)
        root.addLayout(row)

    # ------------------------------------------------------------------ 数据

    def _load(self) -> None:
        account = get_account(self.store)
        if not account:
            return
        self.nick_edit.setText(str(account.get("nickname", "")))
        self.phone_edit.setText(str(account.get("phone", "")))
        self.imei_edit.setText(str(account.get("imei", "")))
        self.cookie_edit.setPlainText(str(account.get("cookie", "")))
        updated = str(account.get("updated_at", ""))
        self.status_label.setText(
            f"当前状态：已配置本地账号信息{('（更新于 ' + updated + '）') if updated else ''}"
        )
        self.status_label.setStyleSheet(f"color: {ACCENT}; font-size: 12px;")

    def _save(self) -> None:
        nickname = self.nick_edit.text().strip()
        phone = self.phone_edit.text().strip()
        imei = self.imei_edit.text().strip()
        cookie = self.cookie_edit.toPlainText().strip()

        if not any([nickname, phone, imei, cookie]):
            self.store.settings_set("account", None)
            self.status_label.setText("当前状态：尚未配置")
            self.status_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
            QMessageBox.information(self, "已清除", "本地账号信息已清除。")
            self.accept()
            return

        self.store.settings_set("account", {
            "provider": "qimao",
            "nickname": nickname,
            "phone": phone,
            "imei": imei,
            "cookie": cookie,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        QMessageBox.information(
            self, "已保存",
            "账号信息已保存到本机。\n\n"
            "提示：这不会向七猫服务器发起登录；下载仍然使用免登录的缓存通道。",
        )
        self.accept()

    def _clear(self) -> None:
        if QMessageBox.question(
            self, "确认清除",
            "确定清除本机保存的账号信息？",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.store.settings_set("account", None)
            self.accept()
