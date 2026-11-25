# -*- coding: utf-8 -*-
"""
插件管理页（方案一：分组卡片墙 · 单列版）。

- 顶部工具条：标题 + 统计徽章（已加载 / 错误 / 内核）+ 添加插件 / 刷新 / 插件目录
- 按功能分类分组（国徽标题），每组内每个插件独占一行宽卡片
- 每张卡片：国徽图标 + 名称 + 版本 + 作者 + 描述 + 启用开关 + 卸载按钮
- 添加插件：选择本地 .py → 校验 → 复制 → 重载 → 刷新；支持拖拽
- 深浅主题 + 主题色全跟随
"""

from __future__ import annotations

import os
import shutil
import hashlib
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, QUrl, QEvent
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QScrollArea, QMessageBox,
)

from .. import filedialogs as _fd
from ..i18n import tr
from ..style import accent_color
from ..widgets import nation_icon


# ------------------------------------------------------------------ 配色
def _palette(dark: bool, accent: str) -> Dict[str, str]:
    from .. import nation_theme
    if dark:
        p = dict(
            bg="#17131d", card="#241e2e", card2="#2b2438", hover="#3a3048",
            text="#f5f0fa", dim="#8e859c", faint="#645b74", line="#332a42",
            accent=accent, badge_bg="#3a2f52", badge_text="#c9bfff",
            err="#ff6b6b", ok="#57d9a3", warn="#ffb454",
        )
    else:
        p = dict(
            bg="#f4f2f7", card="#ffffff", card2="#f3eff6", hover="#ece7f1",
            text="#241f2c", dim="#6e6679", faint="#9a92a5", line="#ddd8e3",
            accent=accent, badge_bg="#ece7f6", badge_text="#6b5cc4",
            err="#d64550", ok="#1f9d6b", warn="#c47f1a",
        )
    # 功能背景：用户自定义卡片色 + 派生层次色
    p["card"], p["card2"] = nation_theme.resolved_card_pair(dark, p["card"], p["card2"])
    return p


# 插件名 -> (分类 key, 国徽, emoji 兜底图标)。已知插件用固定映射，保证与旧版一致。
_PLUGIN_META: Dict[str, Tuple[str, str, str]] = {
    "Calibre 书库": ("extension", "fontaine", "📚"),
    "微信读书接入": ("extension", "natlan", "💬"),
    "七猫真实下载内核": ("core", "mondstadt", "🐱"),
    "使用说明": ("doc", "liyue", "📖"),
    "功能说明": ("doc", "inazuma", "📋"),
    "背景美化": ("beauty", "sumeru", "🌙"),
    "示例插件": ("example", "nodkrai", "🧪"),
}

# 全部 8 个国徽，用于给"未知/新增"插件稳定分配一个国徽图标。
_ALL_NATIONS = (
    "mondstadt", "liyue", "inazuma", "sumeru",
    "fontaine", "natlan", "nodkrai", "snezhnaya",
)
_FALLBACK_EMOJIS = ("🗡️", "🏮", "⚡", "🌿", "⛲", "🔥", "🧭", "❄️")


def _auto_meta(name: str) -> Tuple[str, str, str]:
    """未知插件：按名称哈希稳定分配分类 + 国徽 + emoji，保证同名插件始终同一图标。"""
    digest = hashlib.md5(name.encode("utf-8", "ignore")).hexdigest()
    number = int(digest, 16)
    nation = _ALL_NATIONS[number % len(_ALL_NATIONS)]
    emoji = _FALLBACK_EMOJIS[(number // len(_ALL_NATIONS)) % len(_FALLBACK_EMOJIS)]
    return "other", nation, emoji

# 分类顺序与标题 i18n key、国徽
_CATEGORIES: List[Tuple[str, str, str]] = [
    ("extension", "plugins.cat_extension", "fontaine"),
    ("core", "plugins.cat_core", "mondstadt"),
    ("doc", "plugins.cat_doc", "liyue"),
    ("beauty", "plugins.cat_beauty", "sumeru"),
    ("example", "plugins.cat_example", "nodkrai"),
    ("other", "plugins.cat_other", "snezhnaya"),
]


def _lbl(text: str = "", color: str = "", size: int = 12, bold: bool = False,
         wrap: bool = False) -> QLabel:
    q = QLabel(text)
    css = f"color:{color};font-size:{size}px;background:transparent;border:none;padding:0;"
    if bold:
        css += "font-weight:bold;"
    q.setStyleSheet(css)
    q.setWordWrap(wrap)
    return q


class DropZone(QFrame):
    """可见的拖拽安装区：虚线框 + 国徽图标 + 提示，支持点击与拖入 .py。"""

    def __init__(self, on_click, on_drop, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("plugDropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(62)
        self._on_click = on_click
        self._on_drop = on_drop
        self._normal = ""
        self._hover = ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(30, 30)
        self.icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon)
        self.text = QLabel(tr("plugins.dropzone"))
        self.text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(self.text, 1)

    def apply_colors(self, p: Dict[str, str]) -> None:
        self._normal = (
            f"QFrame#plugDropZone{{background-color:{p['card']};"
            f"border:1px dashed {p['line']};border-radius:10px;}}"
            f"QFrame#plugDropZone:hover{{background-color:{p['card2']};"
            f"border:1px dashed {p['accent']};}}")
        self._hover = (
            f"QFrame#plugDropZone{{background-color:{p['badge_bg']};"
            f"border:2px dashed {p['accent']};border-radius:10px;}}")
        self.setStyleSheet(self._normal)
        self.text.setStyleSheet(
            f"color:{p['dim']};font-size:12px;background:transparent;border:none;padding:0;")
        pix = nation_icon("snezhnaya", p["accent"], 30).pixmap(30, 30)
        if not pix.isNull():
            self.icon.setPixmap(pix)

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(self._hover if active else self._normal)
        if active:
            self.text.setText(tr("plugins.drop_active"))
        else:
            self.text.setText(tr("plugins.dropzone"))

    def mousePressEvent(self, event) -> None:
        self._on_click()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(
                u.toLocalFile().lower().endswith(".py")
                for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self.set_active(True)

    def dragLeaveEvent(self, event) -> None:
        self.set_active(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self.set_active(False)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".py"):
                self._on_drop(path)
        event.acceptProposedAction()

    def retranslate(self) -> None:
        self.text.setText(tr("plugins.dropzone"))


class PluginCard(QFrame):
    """单个插件行卡片（独占一行）。"""

    def __init__(self, meta, category: str, nation: str, emoji: str,
                 enabled: bool, builtin: bool, source_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pluginCard")
        self.meta = meta
        self.category = category
        self.nation = nation
        self.emoji = emoji
        self.enabled = enabled
        self.builtin = builtin
        self.source_path = source_path
        self._accent = "#9b8ce8"

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(12)

        # 图标
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(34, 34)
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon_lbl)

        # 主信息
        info = QVBoxLayout()
        info.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.name_lbl = _lbl(meta.name, "", 13, bold=True)
        title_row.addWidget(self.name_lbl)
        self.ver_lbl = _lbl(f"v{meta.version}", "", 10)
        title_row.addWidget(self.ver_lbl)
        title_row.addStretch()
        info.addLayout(title_row)

        self.author_lbl = _lbl(
            (tr("plugins.author", author=meta.author) if meta.author else ""), "", 10)
        info.addWidget(self.author_lbl)
        self.desc_lbl = _lbl(meta.description or "", "", 11, wrap=True)
        info.addWidget(self.desc_lbl)
        lay.addLayout(info, 1)

        # 操作区
        ops = QVBoxLayout()
        ops.setSpacing(6)
        ops.setAlignment(Qt.AlignVCenter)
        self.toggle_btn = QPushButton()
        self.toggle_btn.setObjectName("plugToggle")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setFixedWidth(76)
        ops.addWidget(self.toggle_btn, 0, Qt.AlignRight)
        self.uninstall_btn = QPushButton(tr("plugins.uninstall"))
        self.uninstall_btn.setObjectName("plugUninstall")
        self.uninstall_btn.setCursor(Qt.PointingHandCursor)
        self.uninstall_btn.setFixedWidth(76)
        ops.addWidget(self.uninstall_btn, 0, Qt.AlignRight)
        lay.addLayout(ops)

        self._refresh_toggle_text()

    def _refresh_toggle_text(self) -> None:
        self.toggle_btn.setText(
            tr("plugins.enabled") if self.enabled else tr("plugins.disabled"))

    def set_toggled(self, callback) -> None:
        self.toggle_btn.clicked.connect(callback)

    def set_uninstall(self, callback) -> None:
        self.uninstall_btn.clicked.connect(callback)

    def apply_colors(self, p: Dict[str, str]) -> None:
        self._accent = p["accent"]
        self.setStyleSheet(
            f"QFrame#pluginCard{{background-color:{p['card']};"
            f"border:1px solid {p['line']};border-radius:10px;}}"
            f"QFrame#pluginCard:hover{{background-color:{p['card2']};"
            f"border:1px solid {p['accent']};}}"
        )
        self.name_lbl.setStyleSheet(
            f"color:{p['text']};font-size:13px;font-weight:bold;"
            "background:transparent;border:none;padding:0;")
        self.ver_lbl.setStyleSheet(
            f"color:{p['badge_text']};font-size:10px;background-color:{p['badge_bg']};"
            "border-radius:8px;padding:1px 7px;")
        self.author_lbl.setStyleSheet(
            f"color:{p['dim']};font-size:10px;background:transparent;border:none;padding:0;")
        self.desc_lbl.setStyleSheet(
            f"color:{p['dim']};font-size:11px;background:transparent;border:none;padding:0;")
        if self.enabled:
            self.toggle_btn.setStyleSheet(
                f"QPushButton#plugToggle{{background-color:{p['accent']};color:{p['bg']};"
                "border:none;border-radius:7px;padding:4px 0;font-size:10px;font-weight:bold;}")
        else:
            self.toggle_btn.setStyleSheet(
                f"QPushButton#plugToggle{{background-color:{p['card2']};color:{p['dim']};"
                f"border:1px solid {p['line']};border-radius:7px;padding:4px 0;font-size:10px;}}")
        if self.builtin:
            self.uninstall_btn.setEnabled(False)
            self.uninstall_btn.setStyleSheet(
                f"QPushButton#plugUninstall{{background-color:transparent;color:{p['faint']};"
                f"border:1px solid {p['line']};border-radius:7px;padding:4px 0;font-size:10px;}}")
        else:
            self.uninstall_btn.setEnabled(True)
            self.uninstall_btn.setStyleSheet(
                f"QPushButton#plugUninstall{{background-color:transparent;color:{p['err']};"
                f"border:1px solid {p['line']};border-radius:7px;padding:4px 0;font-size:10px;}}"
                f"QPushButton#plugUninstall:hover{{border:1px solid {p['err']};}}")
        # 国徽图标
        pix = nation_icon(self.nation, p["accent"], 34)
        if not pix.isNull():
            self.icon_lbl.setPixmap(pix.pixmap(34, 34))
        else:
            self.icon_lbl.setText(self.emoji)
            self.icon_lbl.setStyleSheet("font-size:22px;background:transparent;border:none;")


class PluginsView(QWidget):
    """插件管理页。"""

    def __init__(self, manager, store, main_window=None, parent=None) -> None:
        super().__init__(parent)
        self.pm = manager
        self.store = store
        self.main_window = main_window
        self.setObjectName("pluginsPage")
        self.setAcceptDrops(True)
        self._p: Dict[str, str] = {}
        self._cards: List[PluginCard] = []
        self._build_ui()
        self.apply_colors()

    # ---------------------------------------------------------------- 主题
    def _read_palette(self) -> Dict[str, str]:
        mode = str(self.store.settings_get("theme_mode", "dark") or "dark")
        key = str(self.store.settings_get("accent_color", "purple") or "purple")
        return _palette(mode != "light", accent_color(key))

    def apply_colors(self) -> None:
        self._p = self._read_palette()
        p = self._p
        self.setStyleSheet(f"QWidget#pluginsPage{{background-color:{p['bg']};}}")
        # 顶部工具条
        self.title_lbl.setStyleSheet(
            f"color:{p['text']};font-size:17px;font-weight:bold;"
            "background:transparent;border:none;padding:0;")
        for badge in (self.badge_loaded, self.badge_error, self.badge_core):
            badge.setStyleSheet(
                f"color:{p['badge_text']};background-color:{p['badge_bg']};"
                "border-radius:10px;padding:3px 10px;font-size:10px;")
        self._style_tool_btn(self.btn_add, primary=True)
        self._style_tool_btn(self.btn_refresh)
        self._style_tool_btn(self.btn_dir)
        self.hint_lbl.setStyleSheet(
            f"color:{p['faint']};font-size:10px;background:transparent;border:none;padding:0;")
        self.drop_zone.apply_colors(p)
        self.scroll.setStyleSheet(
            "QScrollArea{background-color:transparent;border:none;}"
            "QWidget#plugHost{background-color:transparent;}")
        # 分组标题与卡片
        for cat_key, wid, hdr in self._group_widgets:
            hdr._text.setStyleSheet(
                f"color:{p['text']};font-size:13px;font-weight:bold;"
                "background:transparent;border:none;padding:0;")
            hdr._icon.setPixmap(
                nation_icon(self._nation_for_cat(cat_key), p["accent"], 20).pixmap(20, 20))
        for card in self._cards:
            card.apply_colors(p)

    def _style_tool_btn(self, btn: QPushButton, primary: bool = False) -> None:
        p = self._p
        if primary:
            btn.setStyleSheet(
                f"QPushButton{{background-color:{p['accent']};color:{p['bg']};border:none;"
                "border-radius:8px;padding:7px 14px;font-size:11px;font-weight:bold;}"
                f"QPushButton:hover{{background-color:{p['accent']};}}")
        else:
            btn.setStyleSheet(
                f"QPushButton{{background-color:{p['card2']};color:{p['text']};"
                f"border:1px solid {p['line']};border-radius:8px;padding:7px 12px;font-size:11px;}}"
                f"QPushButton:hover{{background-color:{p['hover']};}}")

    def _accent_for_cat(self, key: str) -> str:
        return self._p["text"]

    def _nation_for_cat(self, key: str) -> str:
        for k, _, nation in _CATEGORIES:
            if k == key:
                return nation
        return "snezhnaya"

    # ---------------------------------------------------------------- 构建
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 10)
        root.setSpacing(10)

        # 顶部工具条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.title_lbl = _lbl(tr("plugins.title"), "", 17, bold=True)
        bar.addWidget(self.title_lbl)
        self.badge_loaded = _lbl("", "", 10)
        self.badge_error = _lbl("", "", 10)
        self.badge_core = _lbl("", "", 10)
        for b in (self.badge_loaded, self.badge_error, self.badge_core):
            bar.addWidget(b)
        bar.addStretch()
        self.btn_add = QPushButton(tr("plugins.add"))
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self._on_add_plugin)
        bar.addWidget(self.btn_add)
        self.btn_refresh = QPushButton(tr("plugins.refresh"))
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.reload)
        bar.addWidget(self.btn_refresh)
        self.btn_dir = QPushButton(tr("plugins.open_dir"))
        self.btn_dir.setCursor(Qt.PointingHandCursor)
        self.btn_dir.clicked.connect(self._open_dir)
        bar.addWidget(self.btn_dir)
        root.addLayout(bar)

        self.hint_lbl = _lbl(tr("plugins.hint"), "", 10)
        root.addWidget(self.hint_lbl)

        # 可见的拖拽安装区
        self.drop_zone = DropZone(self._on_add_plugin, self.install_plugin)
        root.addWidget(self.drop_zone)

        # 滚动区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setObjectName("plugHost")
        self.host_lay = QVBoxLayout(host)
        self.host_lay.setContentsMargins(2, 4, 2, 4)
        self.host_lay.setSpacing(8)
        self.host_lay.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(host)
        # 让滚动区的 viewport 也接收拖拽，避免拖到内容区时事件被吞掉
        self.scroll.viewport().setAcceptDrops(True)
        self.scroll.viewport().installEventFilter(self)
        root.addWidget(self.scroll, 1)

        self._group_widgets: List[Tuple[str, QWidget, QLabel]] = []
        self.reload()

    # ---------------------------------------------------------------- 数据
    def _enabled_map(self) -> Dict[str, bool]:
        raw = self.store.settings_get("plugins_enabled", "") or ""
        result: Dict[str, bool] = {}
        for part in str(raw).split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                result[k] = v == "1"
        return result

    def _save_enabled_map(self, m: Dict[str, bool]) -> None:
        self.store.settings_set(
            "plugins_enabled", "|".join(f"{k}={1 if v else 0}" for k, v in m.items()))

    def _classify(self, name: str) -> Tuple[str, str, str]:
        for key, meta in _PLUGIN_META.items():
            if key in name or name in key:
                return meta
        # 新增/未知插件：也稳定分配一个国徽图标（不再是空图标）
        return _auto_meta(name)

    def _is_builtin(self, plugin) -> bool:
        """是否内置插件：来源路径不在"外部安装记录"里即为内置。"""
        src = str(getattr(plugin, "_source_path", "") or "")
        installed = str(self.store.settings_get("plugins_installed", "") or "")
        for path in installed.split("|"):
            if path and os.path.abspath(path) == os.path.abspath(src):
                return False
        return True

    def reload(self) -> None:
        # 清空旧分组
        while self.host_lay.count():
            it = self.host_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()
        self._group_widgets.clear()

        plugins = list(getattr(self.pm, "plugins", []))
        errors = list(getattr(self.pm, "errors", []))
        # 统计下载内核数量：调用 provide_downloader 钩子返回真正的 DownloaderBase
        from ..plugin.base import DownloaderBase
        from ..plugin import Hook
        core_count = 0
        for d in self.pm.collect(Hook.PROVIDE_DOWNLOADER):
            if isinstance(d, DownloaderBase):
                core_count += 1

        self.badge_loaded.setText(tr("plugins.badge_loaded", n=len(plugins)))
        self.badge_error.setText(tr("plugins.badge_error", n=len(errors)))
        self.badge_core.setText(tr("plugins.badge_core", n=core_count))

        if not plugins:
            self.host_lay.addWidget(_lbl(
                tr("plugins.empty"), self._p.get("dim", ""), 12))
            return

        enabled_map = self._enabled_map()
        buckets: Dict[str, List] = {k: [] for k, _, _ in _CATEGORIES}
        for p in plugins:
            cat, nation, emoji = self._classify(p.meta.name)
            buckets.setdefault(cat, []).append((p, nation, emoji))

        for cat_key, title_key, _nation in _CATEGORIES:
            items = buckets.get(cat_key) or []
            if not items:
                continue
            # 分组标题：国徽图标 + 文字（两个独立控件，避免图标覆盖文字）
            hdr = QWidget()
            hdr.setObjectName(f"cat_{cat_key}")
            hdr_lay = QHBoxLayout(hdr)
            hdr_lay.setContentsMargins(2, 2, 2, 2)
            hdr_lay.setSpacing(6)
            hdr._icon = QLabel()
            hdr._icon.setFixedSize(20, 20)
            hdr_lay.addWidget(hdr._icon)
            hdr._text = QLabel(tr(title_key))
            hdr_lay.addWidget(hdr._text)
            hdr_lay.addStretch()
            self.host_lay.addWidget(hdr)
            holder = QWidget()
            holder.setObjectName(f"holder_{cat_key}")
            holder_lay = QVBoxLayout(holder)
            holder_lay.setContentsMargins(0, 0, 0, 0)
            holder_lay.setSpacing(8)
            for p, nation, emoji in items:
                name = p.meta.name
                enabled = enabled_map.get(name, p.meta.enabled)
                builtin = self._is_builtin(p)
                card = PluginCard(p.meta, cat_key, nation, emoji, enabled, builtin,
                                  source_path=str(getattr(p, "_source_path", "") or ""))
                card.set_toggled(lambda _c=card: self._toggle_plugin(_c))
                card.set_uninstall(lambda _c=card: self._uninstall_plugin(_c))
                holder_lay.addWidget(card)
                self._cards.append(card)
            self.host_lay.addWidget(holder)
            self._group_widgets.append((cat_key, holder, hdr))

        # 主题色套用（reload 可能早于 apply_colors 首次调用）
        if self._p:
            self.apply_colors()

    def _toggle_plugin(self, card: PluginCard) -> None:
        card.enabled = not card.enabled
        m = self._enabled_map()
        m[card.meta.name] = card.enabled
        self._save_enabled_map(m)
        card._refresh_toggle_text()
        card.apply_colors(self._p)
        if self.main_window and hasattr(self.main_window, "log"):
            self.main_window.log(
                f"插件「{card.meta.name}」已{'启用' if card.enabled else '禁用'}（重启后生效）")

    def _uninstall_plugin(self, card: PluginCard) -> None:
        if card.builtin:
            QMessageBox.information(self, tr("plugins.title"), tr("plugins.builtin_no_uninstall"))
            return
        ret = QMessageBox.question(
            self, tr("plugins.title"),
            tr("plugins.confirm_uninstall", name=card.meta.name),
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        # 精确删除这张卡片对应的文件（不再误删第一个）
        installed = str(self.store.settings_get("plugins_installed", "") or "")
        paths = [x for x in installed.split("|") if x]
        target = card.source_path or None
        if target and os.path.exists(target):
            try:
                os.remove(target)
                self.store.settings_set(
                    "plugins_installed", "|".join(x for x in paths if x != target))
                self.pm.load_all()
                self.reload()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, tr("plugins.title"), str(exc))

    # ---------------------------------------------------------------- 添加插件
    def _open_dir(self) -> None:
        from PySide6.QtGui import QDesktopServices
        path = getattr(self.pm, "plugin_dir", "")
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_add_plugin(self) -> None:
        path = _fd.open_file(
            self, tr("plugins.add"), "", "Python 插件 (*.py)")
        if path:
            self.install_plugin(path)

    def install_plugin(self, src_path: str) -> None:
        """安装插件：校验 → 复制到插件目录 → 重载 → 刷新。"""
        if not src_path or not os.path.isfile(src_path):
            return
        if not src_path.lower().endswith(".py"):
            QMessageBox.warning(self, tr("plugins.title"), tr("plugins.only_py"))
            return
        # Python 语法校验
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                compile(f.read(), src_path, "exec")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("plugins.title"),
                                tr("plugins.syntax_error", msg=str(exc)))
            return
        plugin_dir = getattr(self.pm, "plugin_dir", "")
        if not plugin_dir:
            return
        os.makedirs(plugin_dir, exist_ok=True)
        dst = os.path.join(plugin_dir, os.path.basename(src_path))
        if os.path.abspath(src_path) == os.path.abspath(dst):
            QMessageBox.information(self, tr("plugins.title"), tr("plugins.already_installed"))
            return
        try:
            shutil.copy2(src_path, dst)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("plugins.title"), str(exc))
            return
        # 记录外部安装路径，供卸载使用
        installed = str(self.store.settings_get("plugins_installed", "") or "")
        paths = [x for x in installed.split("|") if x]
        if dst not in paths:
            paths.append(dst)
        self.store.settings_set("plugins_installed", "|".join(paths))
        # 重新扫描加载
        self.pm.load_all()
        self.reload()
        if self.main_window and hasattr(self.main_window, "log"):
            self.main_window.log(f"已安装插件：{os.path.basename(dst)}")
        QMessageBox.information(
            self, tr("plugins.title"),
            tr("plugins.installed", name=os.path.basename(dst)))

    # ---------------------------------------------------------------- 拖拽
    def eventFilter(self, obj, event) -> bool:
        """把滚动区 viewport 上的拖拽转发给安装流程。"""
        if obj is self.scroll.viewport():
            if event.type() == QEvent.DragEnter:
                if event.mimeData().hasUrls() and any(
                        u.toLocalFile().lower().endswith(".py")
                        for u in event.mimeData().urls()):
                    event.acceptProposedAction()
                    self.drop_zone.set_active(True)
                return True
            if event.type() == QEvent.DragLeave:
                self.drop_zone.set_active(False)
                return True
            if event.type() == QEvent.Drop:
                self.drop_zone.set_active(False)
                handled = False
                for url in event.mimeData().urls():
                    path = url.toLocalFile()
                    if path.lower().endswith(".py"):
                        self.install_plugin(path)
                        handled = True
                if handled:
                    event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(u.toLocalFile().lower().endswith(".py") for u in urls):
                event.acceptProposedAction()
                self.drop_zone.set_active(True)

    def dragLeaveEvent(self, event) -> None:
        self.drop_zone.set_active(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self.drop_zone.set_active(False)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".py"):
                self.install_plugin(path)

    def retranslate(self) -> None:
        self.title_lbl.setText(tr("plugins.title"))
        self.hint_lbl.setText(tr("plugins.hint"))
        self.drop_zone.retranslate()
        self.btn_add.setText(tr("plugins.add"))
        self.btn_refresh.setText(tr("plugins.refresh"))
        self.btn_dir.setText(tr("plugins.open_dir"))
        for cat_key, _, hdr in self._group_widgets:
            for k, title_key, _ in _CATEGORIES:
                if k == cat_key:
                    hdr._text.setText(tr(title_key))
                    break
        self.reload()
