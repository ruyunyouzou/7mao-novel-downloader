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
from .i18n import tr, manager as i18n_manager


def get_account(store: Any) -> Optional[Dict[str, Any]]:
    """读取本地登记的账号信息；未配置返回 None。"""
    data = store.settings_get("account", None)
    return data if isinstance(data, dict) and data else None


class AccountDialog(QDialog):
    """账号配置对话框（本地配置，不做真实登录）。"""

    def __init__(self, store: Any, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(tr("account.title"))
        self.resize(560, 480)
        self.setMinimumWidth(480)
        self._build()
        self._load()
        try:
            i18n_manager().changed.connect(self.retranslate)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 标题
        self.title_widget = GroupTitleLabel(tr("account.title"), "snezhnaya", ACCENT, font_size=20)
        root.addWidget(self.title_widget)

        # 说明卡片
        notice = QFrame()
        notice.setStyleSheet(
            f"QFrame {{ background-color: {BG_CARD}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; }}"
        )
        nl = QVBoxLayout(notice)
        nl.setContentsMargins(14, 12, 14, 12)
        nl.setSpacing(6)
        self.info_label = QLabel(tr("account.notice"))
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.RichText)
        self.info_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px; line-height: 150%;")
        nl.addWidget(self.info_label)
        self.warn_label = QLabel(tr("account.warn"))
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(f"color: {WARN}; font-size: 12px;")
        nl.addWidget(self.warn_label)
        root.addWidget(notice)

        # 状态
        self.status_label = QLabel("当前状态：尚未配置")
        self.status_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
        root.addWidget(self.status_label)

        # 表单
        self.form = QFormLayout()
        self.form.setSpacing(10)

        self.nick_edit = QLineEdit()
        self.nick_edit.setPlaceholderText(tr("account.nick_ph"))
        self.form.addRow(tr("account.form_nick"), self.nick_edit)

        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText(tr("account.phone_ph"))
        self.form.addRow(tr("account.form_phone"), self.phone_edit)

        self.imei_edit = QLineEdit()
        self.imei_edit.setPlaceholderText(tr("account.imei_ph"))
        self.form.addRow(tr("account.form_imei"), self.imei_edit)

        self.cookie_edit = QTextEdit()
        self.cookie_edit.setPlaceholderText(tr("account.cookie_ph"))
        self.cookie_edit.setMinimumHeight(110)
        self.form.addRow(tr("account.form_cookie"), self.cookie_edit)
        root.addLayout(self.form)

        # 按钮
        row = QHBoxLayout()
        row.addStretch()
        self.btn_clear = QPushButton(tr("account.btn_clear"))
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        self.btn_cancel = QPushButton(tr("account.btn_cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        self.btn_save = QPushButton(tr("account.btn_save"))
        accent_button(self.btn_save)
        self.btn_save.clicked.connect(self._save)
        row.addWidget(self.btn_save)
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
        suffix = tr("account.status_updated", t=updated) if updated else ""
        self.status_label.setText(tr("account.status_configured", updated=suffix))
        self.status_label.setStyleSheet(f"color: {ACCENT}; font-size: 12px;")

    def _save(self) -> None:
        nickname = self.nick_edit.text().strip()
        phone = self.phone_edit.text().strip()
        imei = self.imei_edit.text().strip()
        cookie = self.cookie_edit.toPlainText().strip()

        if not any([nickname, phone, imei, cookie]):
            self.store.settings_set("account", None)
            self.status_label.setText(tr("account.status_empty"))
            self.status_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
            QMessageBox.information(self, tr("account.cleared_title"), tr("account.cleared_msg"))
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
            self, tr("account.saved_title"), tr("account.saved_msg"),
        )
        self.accept()

    def _clear(self) -> None:
        if QMessageBox.question(
            self, tr("account.confirm_clear_title"),
            tr("account.confirm_clear_msg"),
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.store.settings_set("account", None)
            self.accept()

    # ------------------------------------------------------------------ 语言

    def retranslate(self, *args) -> None:
        """界面语言切换后刷新所有文案。"""
        self.setWindowTitle(tr("account.title"))
        self.title_widget.setText(tr("account.title"))
        self.info_label.setText(tr("account.notice"))
        self.warn_label.setText(tr("account.warn"))
        self.nick_edit.setPlaceholderText(tr("account.nick_ph"))
        self.phone_edit.setPlaceholderText(tr("account.phone_ph"))
        self.imei_edit.setPlaceholderText(tr("account.imei_ph"))
        self.cookie_edit.setPlaceholderText(tr("account.cookie_ph"))
        self.form.labelForField(self.nick_edit).setText(tr("account.form_nick"))
        self.form.labelForField(self.phone_edit).setText(tr("account.form_phone"))
        self.form.labelForField(self.imei_edit).setText(tr("account.form_imei"))
        self.form.labelForField(self.cookie_edit).setText(tr("account.form_cookie"))
        self.btn_clear.setText(tr("account.btn_clear"))
        self.btn_cancel.setText(tr("account.btn_cancel"))
        self.btn_save.setText(tr("account.btn_save"))
        # 状态标签按是否已配置刷新（不改动已有用户输入）
        if get_account(self.store):
            account = get_account(self.store) or {}
            updated = str(account.get("updated_at", ""))
            suffix = tr("account.status_updated", t=updated) if updated else ""
            self.status_label.setText(tr("account.status_configured", updated=suffix))
        else:
            self.status_label.setText(tr("account.status_empty"))
