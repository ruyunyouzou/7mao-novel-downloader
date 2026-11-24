# -*- coding: utf-8 -*-
"""微信读书扫码登录对话框：二维码展示 + 状态轮询 + 凭证换取。"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QPalette
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QApplication, QMessageBox,
)

from .i18n import tr
from .weread_login import (
    WeReadQrLogin,
    WeReadLoginError,
    STATUS_CONFIRMED,
    STATUS_SCANNED,
    STATUS_WAITING,
    STATUS_EXPIRED,
    STATUS_DECLINED,
)


class QrLoginWorker(QThread):
    """完整扫码流程放在后台线程，避免长轮询卡住界面。"""

    qrReady = Signal(object)
    status = Signal(str)
    failed = Signal(str)
    succeeded = Signal(object)

    def __init__(self, api: WeReadQrLogin, parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _cancelled(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:  # noqa: C901
        try:
            qr = self.api.request_qr()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        if self._cancelled():
            return
        self.qrReady.emit(qr)
        self.status.emit(tr("weread.qr_login.waiting"))

        last: Optional[int] = None
        deadline = time.monotonic() + 300
        retry_count = 0
        while not self._cancelled() and time.monotonic() < deadline:
            try:
                result = self.api.poll_once(qr, last)
                retry_count = 0
            except WeReadLoginError as exc:
                message = str(exc)
                transient = ("超时" in message or "网络" in message)
                if transient and retry_count < 8 and not self._cancelled():
                    retry_count += 1
                    self.status.emit(tr("weread.qr_login.retrying"))
                    time.sleep(1.5)
                    continue
                self.failed.emit(message)
                return

            code = result.status
            if code == STATUS_CONFIRMED:
                self.status.emit(tr("weread.qr_login.confirmed"))
                try:
                    session = self.api.exchange_code(result.wx_code)
                except Exception as exc:  # noqa: BLE001
                    self.failed.emit(str(exc))
                    return
                if not self._cancelled():
                    self.succeeded.emit(session)
                return
            if code == STATUS_SCANNED:
                last = STATUS_SCANNED
                self.status.emit(tr("weread.qr_login.scanned"))
                time.sleep(0.8)
                continue
            if code == STATUS_WAITING:
                last = STATUS_WAITING
                self.status.emit(tr("weread.qr_login.waiting"))
                time.sleep(0.8)
                continue
            if code == STATUS_EXPIRED:
                self.failed.emit(tr("weread.qr_login.expired"))
                return
            if code == STATUS_DECLINED:
                self.failed.emit(tr("weread.qr_login.declined"))
                return
            self.failed.emit(tr("weread.qr_login.unknown_status", code=code))
            return

        if not self._cancelled():
            self.failed.emit(tr("weread.qr_login.timeout"))


class WereadLoginDialog(QDialog):
    """扫码登录窗口。成功后 session 属性即为可保存的登录态。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.api = WeReadQrLogin()
        self.worker: Optional[QrLoginWorker] = None
        self.session = None
        self._qr_file: Optional[str] = None
        self.setWindowTitle(tr("weread.qr_login.title"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self._build_ui()
        self._start()

    def _build_ui(self) -> None:
        palette = QApplication.palette()
        text = palette.color(QPalette.Text).name()
        dim = palette.color(QPalette.Disabled, QPalette.Text).name()

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel(tr("weread.qr_login.title"))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size:19px;font-weight:bold;color:{text};")
        root.addWidget(title)

        subtitle = QLabel(tr("weread.qr_login.subtitle"))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{dim};font-size:12px;")
        root.addWidget(subtitle)

        self.qr_label = QLabel()
        self.qr_label.setFixedSize(260, 260)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setStyleSheet(
            "background:#ffffff;border:1px solid #d8d8d8;border-radius:14px;"
        )
        self.qr_label.setText(tr("weread.qr_login.loading"))
        root.addWidget(self.qr_label, 0, Qt.AlignCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.status_label = QLabel(tr("weread.qr_login.loading"))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color:{text};font-size:13px;")
        root.addWidget(self.status_label)

        warning = QLabel(tr("weread.qr_login.warning"))
        warning.setAlignment(Qt.AlignCenter)
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color:{dim};font-size:11px;")
        root.addWidget(warning)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.btn_refresh = QPushButton(tr("weread.qr_login.refresh"))
        self.btn_refresh.clicked.connect(self._refresh)
        self.btn_cancel = QPushButton(tr("dlg.cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_cancel)
        root.addLayout(buttons)

    # ------------------------------------------------------------------ 流程

    def _start(self) -> None:
        self.btn_refresh.setEnabled(False)
        self.progress.show()
        self.qr_label.setText(tr("weread.qr_login.loading"))
        self.status_label.setText(tr("weread.qr_login.loading"))
        self.worker = QrLoginWorker(self.api, self)
        self.worker.qrReady.connect(self._on_qr_ready)
        self.worker.status.connect(self._on_status)
        self.worker.failed.connect(self._on_failed)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.start()

    def _refresh(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self._clean_qr_file()
        self.api = WeReadQrLogin()
        self._start()

    def _on_qr_ready(self, qr) -> None:
        try:
            pixmap, path = self._make_pixmap(qr.confirm_url)
        except Exception:  # noqa: BLE001
            self._on_failed(tr("weread.qr_login.qr_failed"))
            return
        if pixmap is None:
            self._on_failed(tr("weread.qr_login.qr_failed"))
            return
        self._clean_qr_file()
        self._qr_file = path
        self.qr_label.setPixmap(
            pixmap.scaled(244, 244, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.btn_refresh.setEnabled(True)

    def _on_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _on_failed(self, message: str) -> None:
        self.progress.hide()
        self.btn_refresh.setEnabled(True)
        self.status_label.setText(message)
        QMessageBox.warning(self, tr("weread.qr_login.title"), message)

    def _on_succeeded(self, session) -> None:
        self.progress.hide()
        self.status_label.setText(
            tr("weread.qr_login.success", vid=session.masked_vid)
        )
        self.session = session
        self.accept()

    def _make_pixmap(self, url: str):
        try:
            import qrcode
            from qrcode.constants import ERROR_CORRECT_M
        except Exception as exc:  # noqa: BLE001
            raise WeReadLoginError("缺少二维码依赖 qrcode") from exc
        code = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        code.add_data(url)
        code.make(fit=True)
        image = code.make_image(fill_color="black", back_color="white").convert("RGBA")
        fd, path = tempfile.mkstemp(prefix="weread_qr_", suffix=".png")
        os.close(fd)
        image.save(path, "PNG")
        return QPixmap(path), path

    def _clean_qr_file(self) -> None:
        if self._qr_file:
            try:
                os.unlink(self._qr_file)
            except OSError:
                pass
            self._qr_file = None

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self._clean_qr_file()
        super().closeEvent(event)
