# -*- coding: utf-8 -*-
"""
设置页扩展卡片：WebDAV 备份、更新检查、EPUB 排版预设、下载后自动动作、
诊断日志、连续阅读间隔。

做成独立 QWidget，主窗口只需 layout.addWidget(build_extras(main_window))，
避免主窗口文件继续膨胀。所有配置写入 DataStore.settings。
"""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QCheckBox, QSpinBox, QMessageBox, QInputDialog,
)

from .datastore import DataStore
from .i18n import tr
from .style import TEXT_DIM, ACCENT


def _title(text: str, nation: str = "") -> QLabel:
    from .widgets import GroupTitleLabel
    lab = GroupTitleLabel(text, nation, ACCENT, font_size=14)
    return lab


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {TEXT_DIM};")
    lab.setWordWrap(True)
    return lab


class _WebdavWorker(QThread):
    done = Signal(dict)

    def __init__(self, mode: str, store: DataStore, cfg: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.mode, self.store, self.cfg = mode, store, cfg

    def run(self) -> None:
        try:
            from .webdav_core import WebDavClient
            client = WebDavClient(self.cfg["url"], self.cfg["user"],
                                  self.cfg["password"])
            if self.mode == "test":
                ok, msg = client.test_connection()
                self.done.emit({"success": ok, "message": msg})
                return
            files = [f"{self.store.base_dir}/{n}" for n in self.store.files()]
            if self.mode == "backup":
                r = client.backup(files, self.cfg.get("dir", "qimao_backup"))
                self.done.emit({"success": r["success"],
                                "message": f"备份完成：成功 {r['ok']}，失败 {r['fail']}" +
                                (("\n" + "\n".join(r["errors"][:5])) if r["errors"] else "")})
            else:
                r = client.restore(files, self.cfg.get("dir", "qimao_backup"))
                self.done.emit({"success": r["success"],
                                "message": f"恢复完成：成功 {r['ok']}，跳过 {r.get('skipped',0)}，失败 {r['fail']}" +
                                (("\n" + "\n".join(r["errors"][:5])) if r["errors"] else "")})
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"success": False, "message": str(exc)})


class _UpdateWorker(QThread):
    done = Signal(dict)

    def __init__(self, parent=None, force_refresh: bool = False):
        super().__init__(parent)
        self.force_refresh = force_refresh

    def run(self) -> None:
        from .update_core import check_update
        self.done.emit(check_update(force_refresh=self.force_refresh))


class _ScrapeWorker(QThread):
    progress = Signal(int, int, str)
    done = Signal(dict)

    def __init__(self, store: DataStore, parent=None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        from .metadata import scrape_many
        r = scrape_many(self.store.shelf_all(), self.store,
                        lambda i, t, name: self.progress.emit(i, t, name))
        self.done.emit(r)


def build_extras(win) -> QWidget:
    """构建全部扩展卡片，返回可加入设置页布局的 QWidget。"""
    store: DataStore = win.store
    host = QWidget()
    root = QVBoxLayout(host)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(16)

    # ---------- EPUB 排版预设 ----------
    from .widgets import Card
    preset_card = Card()
    pl = QVBoxLayout(preset_card)
    pl.setContentsMargins(16, 14, 16, 14)
    pl.setSpacing(8)
    pl.addWidget(_title(tr("extras.preset_title"), "mondstadt"))
    pl.addWidget(_hint(tr("extras.preset_hint")))
    row = QHBoxLayout()
    win.preset_combo = QComboBox()
    win.preset_combo.addItem(tr("extras.preset_none"), "")
    win.preset_status = QLabel("")
    win.preset_status.setStyleSheet(f"color: {TEXT_DIM};")
    row.addWidget(win.preset_combo, 1)
    row.addWidget(win.preset_status)
    pl.addLayout(row)
    row2 = QHBoxLayout()
    win.btn_save_preset = QPushButton(tr("extras.preset_save"))
    win.btn_del_preset = QPushButton(tr("extras.preset_delete"))
    win.btn_apply_preset = QPushButton(tr("extras.preset_apply"))
    row2.addWidget(win.btn_save_preset)
    row2.addWidget(win.btn_del_preset)
    row2.addWidget(win.btn_apply_preset)
    pl.addLayout(row2)
    root.addWidget(preset_card)

    def reload_presets():
        current = store.settings_get("epub_preset", "") or ""
        win.preset_combo.blockSignals(True)
        win.preset_combo.clear()
        win.preset_combo.addItem(tr("extras.preset_none"), "")
        for p in store.preset_all():
            win.preset_combo.addItem(p.get("name", ""), p.get("id", ""))
        idx = win.preset_combo.findData(current)
        win.preset_combo.setCurrentIndex(max(0, idx))
        win.preset_combo.blockSignals(False)

    def save_preset():
        name, ok = QInputDialog.getText(win, tr("extras.preset_save"), tr("extras.preset_name_hint"))
        if not ok or not name.strip():
            return
        # 读取当前设置页里的字体/颜色作为预设内容
        preset = {
            "name": name.strip(),
            "chapter_font": store.settings_get("epub_chapter_font", "builtin:mashan"),
            "volume_font": store.settings_get("epub_volume_font", "builtin:zhimang"),
            "chapter_color": store.settings_get("epub_chapter_color", "#c62828"),
            "volume_color": store.settings_get("epub_volume_color", "#1565c0"),
            "colorize": store.settings_get("epub_colorize_dialogue", True),
        }
        saved = store.preset_save(preset)
        store.settings_set("epub_preset", saved["id"])
        reload_presets()
        QMessageBox.information(win, tr("common.saved", value=name), tr("extras.preset_saved"))

    def delete_preset():
        pid = win.preset_combo.currentData()
        if not pid:
            return
        store.preset_remove(pid)
        if store.settings_get("epub_preset", "") == pid:
            store.settings_set("epub_preset", "")
        reload_presets()

    def apply_preset():
        pid = win.preset_combo.currentData()
        if not pid:
            win.preset_status.setText(tr("extras.preset_none_hint"))
            return
        preset = next((p for p in store.preset_all() if p.get("id") == pid), None)
        if not preset:
            return
        for k in ("chapter_font", "volume_font", "chapter_color", "volume_color", "colorize"):
            if k in preset:
                # colorize 的持久化键是 epub_colorize_dialogue（下载内核读这个键），
                # 不能按 epub_{k} 拼成无人读取的 epub_colorize
                key = "epub_colorize_dialogue" if k == "colorize" else f"epub_{k}"
                store.settings_set(key, preset[k])
        store.settings_set("epub_preset", pid)
        win.preset_status.setText(tr("extras.preset_applied"))
        QMessageBox.information(win, tr("extras.preset_apply"), tr("extras.preset_applied_restart"))

    win.btn_save_preset.clicked.connect(save_preset)
    win.btn_del_preset.clicked.connect(delete_preset)
    win.btn_apply_preset.clicked.connect(apply_preset)
    win.preset_combo.currentIndexChanged.connect(
        lambda: store.settings_set("epub_preset", win.preset_combo.currentData() or ""))
    reload_presets()

    # ---------- 下载后自动动作 ----------
    auto_card = Card()
    al = QVBoxLayout(auto_card)
    al.setContentsMargins(16, 14, 16, 14)
    al.setSpacing(8)
    al.addWidget(_title(tr("extras.autorun_title"), "liyue"))
    al.addWidget(_hint(tr("extras.autorun_hint")))
    win.auto_calibre_check = QCheckBox(tr("extras.autorun_calibre"))
    win.auto_calibre_check.setChecked(bool(store.settings_get("auto_calibre", False)))
    win.auto_calibre_check.stateChanged.connect(
        lambda s: store.settings_set("auto_calibre", bool(s)))
    al.addWidget(win.auto_calibre_check)
    win.auto_weread_check = QCheckBox(tr("extras.autorun_weread"))
    win.auto_weread_check.setChecked(bool(store.settings_get("auto_weread", False)))
    win.auto_weread_check.stateChanged.connect(
        lambda s: store.settings_set("auto_weread", bool(s)))
    al.addWidget(win.auto_weread_check)
    win.auto_verify_check = QCheckBox(tr("extras.autorun_verify"))
    win.auto_verify_check.setChecked(bool(store.settings_get("auto_verify_epub", True)))
    win.auto_verify_check.stateChanged.connect(
        lambda s: store.settings_set("auto_verify_epub", bool(s)))
    al.addWidget(win.auto_verify_check)
    root.addWidget(auto_card)

    # ---------- 阅读与界面 ----------
    read_card = Card()
    rl = QVBoxLayout(read_card)
    rl.setContentsMargins(16, 14, 16, 14)
    rl.setSpacing(8)
    rl.addWidget(_title(tr("extras.read_title"), "inazuma"))
    interval_row = QHBoxLayout()
    interval_row.addWidget(_hint(tr("extras.auto_interval")))
    win.auto_interval_spin = QSpinBox()
    win.auto_interval_spin.setRange(300, 10000)
    win.auto_interval_spin.setSingleStep(100)
    win.auto_interval_spin.setValue(int(store.settings_get("auto_read_interval", 1200) or 1200))
    win.auto_interval_spin.setSuffix(" ms")
    win.auto_interval_spin.valueChanged.connect(
        lambda v: store.settings_set("auto_read_interval", v))
    interval_row.addWidget(win.auto_interval_spin)
    rl.addLayout(interval_row)
    win.tray_check = QCheckBox(tr("extras.tray"))
    win.tray_check.setChecked(bool(store.settings_get("tray_enabled", True)))
    win.tray_check.setToolTip(tr("extras.tray_tip"))
    win.tray_check.stateChanged.connect(lambda s: store.settings_set("tray_enabled", bool(s)))
    rl.addWidget(win.tray_check)
    # 关闭窗口即退出程序：默认开启，避免进程常驻占用安装目录导致旧版本无法删除/覆盖。
    # 关闭此项且启用托盘时，关闭窗口才会最小化到托盘、后台继续下载。
    win.quit_on_close_check = QCheckBox(tr("extras.quit_on_close"))
    win.quit_on_close_check.setChecked(bool(store.settings_get("quit_on_close", True)))
    win.quit_on_close_check.setToolTip(tr("extras.quit_on_close_tip"))
    win.quit_on_close_check.stateChanged.connect(
        lambda s: store.settings_set("quit_on_close", bool(s)))
    rl.addWidget(win.quit_on_close_check)
    # 界面动效（页面过渡 / 滑动指示条 / 平滑滚动 / 进度条缓动）
    win.motion_check = QCheckBox(tr("extras.motion_enable"))
    win.motion_check.setChecked(bool(store.settings_get("ui_motion", True)))
    win.motion_check.stateChanged.connect(
        lambda s: store.settings_set("ui_motion", bool(s)))
    rl.addWidget(win.motion_check)
    level_row = QHBoxLayout()
    level_row.addWidget(_hint(tr("extras.motion_level")))
    win.motion_level_combo = QComboBox()
    for key, label in (("low", "extras.motion_low"),
                       ("normal", "extras.motion_normal"),
                       ("high", "extras.motion_high")):
        win.motion_level_combo.addItem(tr(label), key)
    cur_level = str(store.settings_get("ui_motion_level", "normal") or "normal")
    li = win.motion_level_combo.findData(cur_level)
    win.motion_level_combo.setCurrentIndex(max(0, li))
    win.motion_level_combo.currentIndexChanged.connect(
        lambda: store.settings_set("ui_motion_level",
                                   str(win.motion_level_combo.currentData() or "normal")))
    level_row.addWidget(win.motion_level_combo)
    rl.addLayout(level_row)
    win.low_power_check = QCheckBox(tr("extras.motion_low_power"))
    win.low_power_check.setChecked(bool(store.settings_get("ui_low_power", False)))
    win.low_power_check.stateChanged.connect(
        lambda s: store.settings_set("ui_low_power", bool(s)))
    rl.addWidget(win.low_power_check)
    # 界面抗锯齿（图标/圆角/渐变光效/文字边缘更精细，默认开）
    from . import render_quality
    win.antialias_check = QCheckBox(tr("extras.antialias"))
    win.antialias_check.setChecked(bool(store.settings_get("ui_antialias", True)))
    win.antialias_check.setToolTip(tr("extras.antialias_tip"))
    def _on_antialias_changed(_state, _store=store):
        _store.settings_set("ui_antialias", bool(win.antialias_check.isChecked()))
        render_quality.refresh_fonts()  # 即时刷新字体策略与窗口重绘
    win.antialias_check.stateChanged.connect(_on_antialias_changed)
    rl.addWidget(win.antialias_check)
    # 玻璃质感（半透明卡片 / 柔光边 / 大圆角，默认开；不改配色方案）
    win.glass_check = QCheckBox(tr("extras.glass"))
    win.glass_check.setChecked(bool(store.settings_get("ui_glass", True)))
    win.glass_check.setToolTip(tr("extras.glass_tip"))
    def _on_glass_changed(_state, _win=win):
        _win.store.settings_set("ui_glass", bool(_win.glass_check.isChecked()))
        _win._apply_stylesheet()  # 卡片面板即时重绘（补丁随开关增减）
        cards = getattr(_win, "card_list", None)
        if cards is not None and hasattr(cards, "refresh_glass"):
            cards.refresh_glass()  # 书卡内联样式即时刷新
    win.glass_check.stateChanged.connect(_on_glass_changed)
    rl.addWidget(win.glass_check)
    root.addWidget(read_card)

    # ---------- WebDAV 备份 ----------
    dav_card = Card()
    dl = QVBoxLayout(dav_card)
    dl.setContentsMargins(16, 14, 16, 14)
    dl.setSpacing(8)
    dl.addWidget(_title(tr("extras.webdav_title"), "sumeru"))
    dl.addWidget(_hint(tr("extras.webdav_hint")))
    win.dav_url = QLineEdit(str(store.settings_get("webdav_url", "") or ""))
    win.dav_url.setPlaceholderText("https://dav.jianguoyun.com/dav/")
    win.dav_user = QLineEdit(str(store.settings_get("webdav_user", "") or ""))
    win.dav_user.setPlaceholderText(tr("extras.webdav_user"))
    win.dav_pass = QLineEdit(str(store.settings_get("webdav_pass", "") or ""))
    win.dav_pass.setEchoMode(QLineEdit.Password)
    win.dav_pass.setPlaceholderText(tr("extras.webdav_pass"))
    win.dav_dir = QLineEdit(str(store.settings_get("webdav_dir", "qimao_backup") or "qimao_backup"))
    dl.addWidget(_hint(tr("extras.webdav_url")))
    dl.addWidget(win.dav_url)
    dl.addWidget(win.dav_user)
    dl.addWidget(win.dav_pass)
    dl.addWidget(win.dav_dir)
    row_dav = QHBoxLayout()
    win.btn_dav_test = QPushButton(tr("extras.webdav_test"))
    win.btn_dav_backup = QPushButton(tr("extras.webdav_backup"))
    win.btn_dav_restore = QPushButton(tr("extras.webdav_restore"))
    row_dav.addWidget(win.btn_dav_test)
    row_dav.addWidget(win.btn_dav_backup)
    row_dav.addWidget(win.btn_dav_restore)
    dl.addLayout(row_dav)
    root.addWidget(dav_card)

    def _save_dav():
        store.settings_set("webdav_url", win.dav_url.text().strip())
        store.settings_set("webdav_user", win.dav_user.text().strip())
        store.settings_set("webdav_pass", win.dav_pass.text())
        store.settings_set("webdav_dir", win.dav_dir.text().strip() or "qimao_backup")

    for w in (win.dav_url, win.dav_user, win.dav_pass, win.dav_dir):
        w.textChanged.connect(_save_dav)

    def dav_cfg():
        return {"url": win.dav_url.text().strip(), "user": win.dav_user.text().strip(),
                "password": win.dav_pass.text(), "dir": win.dav_dir.text().strip() or "qimao_backup"}

    def run_dav(mode: str):
        _save_dav()
        if not win.dav_url.text().strip():
            QMessageBox.warning(win, tr("extras.webdav_title"), tr("extras.webdav_need_url"))
            return
        worker = _WebdavWorker(mode, store, dav_cfg(), win)
        def on_done(r):
            if r["success"]:
                QMessageBox.information(win, tr("extras.webdav_title"), r["message"])
            else:
                QMessageBox.warning(win, tr("extras.webdav_title"), r["message"])
        worker.done.connect(on_done)
        worker.start()

    win.btn_dav_test.clicked.connect(lambda: run_dav("test"))
    win.btn_dav_backup.clicked.connect(lambda: run_dav("backup"))
    win.btn_dav_restore.clicked.connect(lambda: run_dav("restore"))

    # ---------- 更新 / 日志 / 刮削 ----------
    misc_card = Card()
    ml = QVBoxLayout(misc_card)
    ml.setContentsMargins(16, 14, 16, 14)
    ml.setSpacing(8)
    ml.addWidget(_title(tr("extras.misc_title"), "fontaine"))
    win.auto_update_check = QCheckBox(tr("extras.auto_update"))
    win.auto_update_check.setChecked(bool(store.settings_get("auto_update_check", False)))
    win.auto_update_check.stateChanged.connect(
        lambda s: store.settings_set("auto_update_check", bool(s)))
    ml.addWidget(win.auto_update_check)
    row_misc = QHBoxLayout()
    win.btn_check_update = QPushButton(tr("extras.check_update"))
    win.btn_open_log = QPushButton(tr("extras.open_log"))
    win.btn_scrape = QPushButton(tr("extras.scrape"))
    win.log_level_combo = QComboBox()
    win.log_level_combo.addItems(["INFO", "WARN", "ERROR", "DEBUG"])
    lv = store.settings_get("log_level", "INFO")
    ix = win.log_level_combo.findText(str(lv))
    win.log_level_combo.setCurrentIndex(max(0, ix))
    def on_level():
        from .logutil import logger
        level = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}.get(
            win.log_level_combo.currentText(), 20)
        logger.set_level(level)
        store.settings_set("log_level", win.log_level_combo.currentText())
    win.log_level_combo.currentTextChanged.connect(on_level)
    row_misc.addWidget(win.btn_check_update)
    row_misc.addWidget(win.btn_scrape)
    row_misc.addWidget(win.btn_open_log)
    row_misc.addWidget(win.log_level_combo)
    ml.addLayout(row_misc)
    win.extras_status = QLabel("")
    win.extras_status.setStyleSheet(f"color: {TEXT_DIM};")
    ml.addWidget(win.extras_status)
    root.addWidget(misc_card)

    def do_check_update():
        win.extras_status.setText(tr("extras.update_checking"))
        w = _UpdateWorker(win, force_refresh=True)
        def on_done(r):
            if r["has_update"]:
                win.extras_status.setText(r["message"])
                from .update_dialog import show_update_dialog
                show_update_dialog(r, win)
            else:
                win.extras_status.setText(r["message"] or tr("extras.update_fail"))
        w.done.connect(on_done)
        w.start()

    def do_scrape():
        entries = store.shelf_all()
        if not entries:
            QMessageBox.information(win, tr("extras.scrape"), tr("extras.scrape_empty"))
            return
        w = _ScrapeWorker(store, win)
        w.progress.connect(lambda i, t, name: win.extras_status.setText(
            tr("extras.scraping", i=i, t=t, name=name)))
        def on_done(r):
            win.extras_status.setText(tr("extras.scrape_done", ok=r["ok"], fail=r["fail"]))
            if hasattr(win, "shelf_view") and win.shelf_view:
                win.shelf_view.refresh()
        w.done.connect(on_done)
        w.start()

    def do_open_log():
        from .logutil import logger
        logger.open_log_dir()

    win.btn_check_update.clicked.connect(do_check_update)
    win.btn_scrape.clicked.connect(do_scrape)
    win.btn_open_log.clicked.connect(do_open_log)

    return host
