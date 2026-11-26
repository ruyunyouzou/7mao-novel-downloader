# -*- coding: utf-8 -*-
"""内置「更新日志」页面。

数据源（本地打底 + 联网补充，全程容错）：
1. 本地 ``update-manifest.json`` 的 ``notes`` 字段——离线即可显示，作为基础；
2. 联网拉取 GitHub Releases 历史（``/releases?per_page=N``），补充更早版本；
   网络失败 / 证书问题自动降级用本地数据，不影响页面打开。

顶部：当前版本徽章 + 「检查更新」按钮（复用 update_core 四镜像渠道）。
主体：方案 A「时间轴流」——左侧渐变轴线 + 国徽节点，右侧版本卡片，
      把 ①②③ 序号自动拆成条目列表；按版本倒序排列。
底部：「查看完整历史」链接打开 GitHub Releases。
深浅主题自适应（apply_colors 接入主题链），文案三语。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, QUrl, Signal, Qt, QRectF
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .. import netutil
from ..datastore import DataStore
from ..i18n import tr
from ..resource_path import resource_path
from ..style import accent_color, modern_scrollbar_qss
from ..version import APP_VERSION, VERSION_LABEL

# GitHub Releases API（跟随 update_core 的仓库配置，避免写死第二处）
_RELEASES_API = "https://api.github.com/repos/{repo}/releases?per_page=20"
_DEFAULT_REPO = "ruyunyouzou/7mao-novel-downloader"

# 版本行正则：兼容「5.0.1：」「5.0.0（大版本）：」「v4.1.6 - 」 etc.
_VER_LINE = re.compile(r"^\s*v?(\d+\.\d+(?:\.\d+)?)([^\d].*)?$")
# ①②③… 圆序号
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
# 中/英/俄序号标记（如 "1." "1)" "（1）"）
_ITEM_MARK = re.compile(r"^\s*(?:\d+[\.、\)）]|[（(]\d+[)）]|[•·\-])\s*")
# 版本行前缀：形如「5.0.1：」「v5.0.0（大版本）：」「4.1.6 - 」
_VER_PREFIX = re.compile(
    r"^\s*v?\d+\.\d+(?:\.\d+)?(?:\s*[（(][^）)]*[）)])?\s*[：:\-–]\s*")


def _load_repo() -> str:
    """复用 update_config.json 的仓库配置（与 update_core 同源）。"""
    try:
        path = resource_path("update_config.json")
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            repo = str(data.get("repo") or data.get("repository") or "").strip()
            if repo and "/" in repo:
                return repo
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_REPO


def _local_notes() -> str:
    """读取本地 manifest 的 notes（离线基础数据）。"""
    try:
        path = resource_path("update-manifest.json")
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return str(json.load(f).get("notes") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def parse_notes(notes: str) -> List[Tuple[str, str]]:
    """把 notes 文本解析为 [(版本, 该版本说明), ...]。

    支持多版本行；每个版本内部把 ①②③ / 数字序号 / 分号拆成列表项，
    返回的是「原始文本块」，渲染时再按当前语言转成列表标签。
    """
    if not notes or not notes.strip():
        return []
    lines = [ln.rstrip() for ln in notes.splitlines()]
    blocks: List[Tuple[str, List[str]]] = []
    cur_ver = ""
    cur_lines: List[str] = []

    def flush() -> None:
        if cur_ver:
            body = "\n".join(x for x in cur_lines if x.strip()).strip()
            blocks.append((cur_ver, body))

    for line in lines:
        m = _VER_LINE.match(line)
        if m:
            flush()
            cur_ver = m.group(1)
            cur_lines = [line]  # 保留整行（含「（大版本）：」等前缀）供渲染
        elif cur_ver:
            cur_lines.append(line)
        # 版本行之前的散文本（如标题）忽略
    flush()

    # 整段只有一行 subtitle（无版本号）时，兜底为「当前版本」
    if not blocks and lines:
        text = "\n".join(x for x in lines if x.strip()).strip()
        if text:
            blocks.append((APP_VERSION, text))
    return [(v, body) for v, body in blocks if body]


def _split_items(body: str) -> Tuple[str, List[str]]:
    """把一个版本块拆成 ``(副标题, 条目列表)``。

    版本前缀（如 ``5.0.1：``）先剥离；第一个条目标记之前的文字作为副标题；
    条目按「圆序号 → 逐行数字序号/项目符号 → 分号 → 句号」逐级回退切分，
    保证 ① 格式、纯分号格式与 GitHub Release 的 Markdown 列表都能正确解析。
    """
    if not body:
        return "", []
    text = _VER_PREFIX.sub("", body.strip(), count=1).strip()

    def clean(seg: str) -> str:
        return seg.strip(" \n\t；;。")

    # 1) 圆序号 ①②③…（可跨行），第一个序号前的内容作为副标题
    circled = [i for i, ch in enumerate(text) if ch in _CIRCLED]
    if circled:
        subtitle = clean(text[:circled[0]])
        items: List[str] = []
        for n, start in enumerate(circled):
            end = circled[n + 1] if n + 1 < len(circled) else len(text)
            seg = clean(text[start + 1:end])
            if seg:
                items.append(seg)
        return subtitle, items

    # 2) 逐行的数字序号 / 项目符号（GitHub Release 常见 Markdown 列表）
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 1:
        items = []
        lead: List[str] = []
        for line in lines:
            if _ITEM_MARK.match(line):
                items.append(_ITEM_MARK.sub("", line).strip())
            elif items:
                items[-1] += line
            else:
                lead.append(line)
        if items:
            return " ".join(lead), items

    # 3) 分号（中文/英文）切分
    segs = [clean(s) for s in re.split(r"[；;]", text)]
    segs = [s for s in segs if s]
    if len(segs) >= 2:
        return "", segs

    # 4) 句号切分
    segs = [clean(s) for s in re.split(r"(?<=。)", text) if clean(s)]
    if len(segs) >= 2:
        return "", segs

    # 5) 整段纯文本，无明确条目
    return text, []


class _ReleaseFetcher(QThread):
    """后台拉取 GitHub Releases 历史（失败静默，不阻塞界面）。"""

    done = Signal(list)  # List[(version, body)]

    def run(self) -> None:
        blocks: List[Tuple[str, str]] = []
        try:
            api = _RELEASES_API.format(repo=_load_repo())
            resp = netutil.safe_get(api, timeout=8)
            if self.isInterruptionRequested():
                return
            data = resp.json() if resp is not None else None
            if isinstance(data, list):
                for rel in data:
                    if self.isInterruptionRequested():
                        break
                    tag = str(rel.get("tag_name") or "").lstrip("v")
                    body = str(rel.get("body") or "").strip()
                    if tag and body:
                        blocks.append((tag, body))
        except Exception:  # noqa: BLE001
            pass
        self.done.emit(blocks)


# 时间轴各国徽节点按版本轮换使用的国徽（与软件现有 8 国国徽体系一致）
_TIMELINE_NATIONS = (
    "mondstadt", "liyue", "inazuma", "sumeru", "fontaine", "natlan",
    "snezhnaya", "nodkrai",
)


def _nation_pixmap(nation: str, size: int, color: str = "") -> QPixmap:
    """加载并按主题色染色国徽图标（失败返回空图，不影响渲染）。"""
    try:
        from ..widgets import nation_pixmap as _np
        pm = _np(nation, color, size)
        if pm is not None and not pm.isNull():
            return pm
    except Exception:  # noqa: BLE001
        pass
    try:
        path = resource_path("assets", "nation_icons", f"{nation}.png")
        pm = QPixmap(path)
        if not pm.isNull():
            return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:  # noqa: BLE001
        pass
    return QPixmap()


class _TimelineRail(QWidget):
    """时间轴左侧竖条：一条竖直渐变线 + 一个国徽节点圆。

    纯 QWidget 自绘（Qt 富文本无法画轴线/圆点），高度随右侧卡片自适应。
    """

    def __init__(self, nation: str, accent: str, dot_bg: str,
                 line_color: str, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(54)
        self._pm = _nation_pixmap(nation, 22, accent)
        self._accent = accent
        self._dot_bg = dot_bg
        self._line_color = line_color

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        cx = w / 2.0
        dot_cy = 26.0
        r = 15.0
        # 竖线：从顶部贯穿到底部，底部用中性色渐弱（分段模拟渐变）
        line = QPen(QColor(self._line_color), 2)
        p.setPen(line)
        p.drawLine(int(cx), int(dot_cy), int(cx), h)
        if dot_cy > r:
            p.drawLine(int(cx), 0, int(cx), int(dot_cy - r))
        # 节点圆：底色填充 + 主题色描边
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._dot_bg))
        p.drawEllipse(QRectF(cx - r, dot_cy - r, r * 2, r * 2))
        p.setPen(QPen(QColor(self._accent), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - r, dot_cy - r, r * 2, r * 2))
        # 国徽
        if not self._pm.isNull():
            p.drawPixmap(int(cx - self._pm.width() / 2),
                         int(dot_cy - self._pm.height() / 2), self._pm)


class _VersionCard(QFrame):
    """时间轴右侧的版本卡片：版本号 + 当前徽章 + 副标题 + 条目列表。"""

    def __init__(self, version: str, subtitle: str, items: List[str],
                 is_current: bool, nation: str, colors: Dict[str, str],
                 parent=None) -> None:
        super().__init__(parent)
        c = colors
        self.setStyleSheet(
            f"_VersionCard {{ background:{c['card_bg']}; border:1px solid {c['border']};"
            f"border-radius:14px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 18, 14)
        lay.setSpacing(6)

        # 版本行
        head = QHBoxLayout()
        head.setSpacing(10)
        ver = QLabel(f"v{version}")
        ver.setStyleSheet(
            f"font-size:19px;font-weight:800;color:{c['accent']};")
        head.addWidget(ver)
        if is_current:
            cur = QLabel(tr("changelog.current"))
            cur.setStyleSheet(
                "background:#f5c542;color:#3a2c00;border-radius:11px;"
                "padding:2px 10px;font-size:11px;font-weight:800;")
            head.addWidget(cur)
        head.addStretch(1)
        lay.addLayout(head)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color:{c['dim']};font-size:12.5px;")
            lay.addWidget(sub)

        if items:
            tip = QLabel(tr("changelog.items"))
            tip.setStyleSheet(f"color:{c['dim']};font-size:11px;")
            lay.addWidget(tip)
            ol = QVBoxLayout()
            ol.setSpacing(3)
            for i, item in enumerate(items, 1):
                row = QHBoxLayout()
                row.setSpacing(6)
                idx = QLabel(f"{i:02d}")
                idx.setStyleSheet(f"color:{c['accent']};font-size:11px;font-weight:700;")
                idx.setFixedWidth(22)
                txt = QLabel(item.strip("。 "))
                txt.setWordWrap(True)
                txt.setStyleSheet(f"color:{c['fg']};font-size:12.5px;line-height:150%;")
                row.addWidget(idx, 0, Qt.AlignTop)
                row.addWidget(txt, 1)
                ol.addLayout(row)
            lay.addLayout(ol)
        elif subtitle:
            # 无条目时副标题已在上方展示，无需重复
            pass


class ChangelogView(QWidget):
    """更新日志页：本地 notes + 联网补充，富文本卡片展示。"""

    def __init__(self, main_window=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("changelogPage")
        self.window_title = tr("nav.changelog")
        self.setWindowTitle(self.window_title)
        self.main_window = main_window
        self.store = getattr(main_window, "store", None) or DataStore()
        self._fetcher: Optional[_ReleaseFetcher] = None
        self._local_blocks: List[Tuple[str, str]] = []
        self._remote_blocks: List[Tuple[str, str]] = []
        self._dark = True
        self._accent = accent_color("purple")
        # 先初始化颜色属性（apply_colors 内部会调用 _render），再拉本地数据渲染
        self._fg, self._dim = "#e6e6e6", "#9aa0a6"
        self._card_bg, self._border = "#232328", "#3a3a42"
        self._build_ui()
        self._load_local()
        self.apply_colors()
        self._fetch_remote()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # ---- 顶部：版本徽章 + 检查更新按钮 ----
        head = QHBoxLayout()
        head.setSpacing(10)
        self.title_label = QLabel(tr("changelog.title"))
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(self.title_label)

        self.version_badge = QLabel(VERSION_LABEL)
        self.version_badge.setStyleSheet(
            "padding: 3px 10px; border-radius: 10px; font-size: 12px; font-weight: bold;")
        head.addWidget(self.version_badge)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px;")
        head.addWidget(self.status_label, 1)

        self.check_btn = QPushButton(tr("changelog.check_update"))
        self.check_btn.setObjectName("primaryBtn")
        self.check_btn.clicked.connect(self._check_update)
        head.addWidget(self.check_btn)
        root.addLayout(head)

        # ---- 主体：时间轴滚动区（方案 A）----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self._list_host = QWidget()
        self._list_host.setObjectName("changelogListHost")
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(2, 2, 8, 2)
        self._list_lay.setSpacing(14)
        self._list_lay.addStretch(1)
        self.scroll.setWidget(self._list_host)
        root.addWidget(self.scroll, 1)

        # ---- 底部：查看完整历史 ----
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addStretch(1)
        self.history_btn = QPushButton(tr("changelog.full_history"))
        self.history_btn.setFlat(True)
        self.history_btn.clicked.connect(self._open_releases)
        foot.addWidget(self.history_btn)
        root.addLayout(foot)

    # ------------------------------------------------------------------ 数据
    def _load_local(self) -> None:
        self._local_blocks = parse_notes(_local_notes())

    def _fetch_remote(self) -> None:
        """后台补充远端历史（离线时静默跳过）。"""
        try:
            self._fetcher = _ReleaseFetcher(self)
            self._fetcher.done.connect(self._on_remote_done)
            self._fetcher.start()
        except Exception:  # noqa: BLE001
            pass

    def _on_remote_done(self, blocks: list) -> None:
        if not blocks:
            return
        # blocks 已是结构化 (版本, body)，直接合并，避免把多行 Release
        # 正文重新拼成文本走 parse_notes 时被版本行正则误切。
        self._remote_blocks = [
            (str(v).strip(), str(b).strip()) for v, b in blocks if str(v).strip()
        ]
        self._render()

    def _clear_list(self) -> None:
        """清空时间轴列表中的旧 widget（保留末尾 stretch）。"""
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            else:
                lay = item.layout()
                if lay is not None:
                    self._clear_layout(lay)

    @staticmethod
    def _clear_layout(lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    ChangelogView._clear_layout(sub)

    def _render(self) -> None:
        """合并本地+远端数据，按版本倒序构建时间轴卡片（功能逻辑不变）。"""
        merged: Dict[str, str] = {}
        # 远端优先（更全），本地补缺
        for v, body in self._local_blocks:
            merged.setdefault(v, body)
        for v, body in self._remote_blocks:
            merged[v] = body or merged.get(v, "")

        self._clear_list()

        if not merged:
            empty = QLabel(tr("changelog.empty"))
            empty.setStyleSheet(f"color:{self._dim};font-size:13px;padding:20px;")
            self._list_lay.insertWidget(0, empty)
            return

        def vkey(v: str) -> tuple:
            nums = [int(x) for x in re.findall(r"\d+", v)]
            return tuple(nums + [0] * (3 - len(nums)))[:3]

        versions = sorted(merged.keys(), key=vkey, reverse=True)
        colors = {
            "fg": self._fg,
            "dim": self._dim,
            "accent": self._accent,
            "card_bg": self._card_bg,
            "border": self._border,
        }
        line_color = "#4a3a7a" if self._dark else "#d8cfe8"

        for idx, ver in enumerate(versions):
            subtitle, items = _split_items(merged[ver])
            nation = _TIMELINE_NATIONS[idx % len(_TIMELINE_NATIONS)]
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(6)
            rail = _TimelineRail(nation, self._accent, self._card_bg, line_color)
            card = _VersionCard(
                ver, subtitle, items, ver == APP_VERSION, nation, colors)
            rail.setSizePolicy(
                rail.sizePolicy().horizontalPolicy(), QSizePolicy.Expanding)
            row_lay.addWidget(rail, 0)
            row_lay.addWidget(card, 1)
            self._list_lay.insertWidget(idx, row)

    # ------------------------------------------------------------------ 操作
    def _check_update(self) -> None:
        """检查更新：复用 update_core 四镜像渠道，结果交给现有更新对话框。"""
        self.check_btn.setEnabled(False)
        self.status_label.setText(tr("changelog.checking"))
        try:
            from ..update_core import check_update
            result = check_update(force_refresh=True)

            def _done(r: Dict[str, Any]) -> None:
                self.check_btn.setEnabled(True)
                self.status_label.setText(r.get("message") or "")
                if r.get("has_update"):
                    from ..update_dialog import show_update_dialog
                    show_update_dialog(r, self)

            # check_update 是同步的，这里在调用后直接处理结果
            _done(result)
        except Exception as exc:  # noqa: BLE001
            self.check_btn.setEnabled(True)
            self.status_label.setText(tr("changelog.check_fail", err=str(exc)[:80]))

    def _open_releases(self) -> None:
        try:
            QDesktopServices.openUrl(
                QUrl(f"https://github.com/{_load_repo()}/releases"))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 主题/语言
    def apply_colors(self) -> None:
        """深浅主题与强调色同步（接入主窗口 apply_colors 链）。"""
        main = self.main_window
        self._dark = bool(getattr(main, "_dark_theme", True)) if main else True
        self._accent = accent_color("purple")
        if self._dark:
            self._fg, self._dim = "#e6e6e6", "#9aa0a6"
            self._card_bg, self._border = "#232328", "#3a3a42"
            page_bg = "#181818"
        else:
            self._fg, self._dim = "#2a2a2a", "#666666"
            self._card_bg, self._border = "#ffffff", "#e0e0e0"
            page_bg = "#f7f7f8"
        self.setStyleSheet(f"QWidget#changelogPage {{ background: {page_bg}; }}")
        self.title_label.setStyleSheet(
            f"font-size:18px;font-weight:bold;color:{self._fg};")
        self.version_badge.setStyleSheet(
            f"background:{self._accent};color:#1a1a1a;border-radius:10px;"
            f"padding:3px 10px;font-size:12px;font-weight:bold;")
        self.status_label.setStyleSheet(f"color:{self._dim};font-size:12px;")
        self.check_btn.setStyleSheet(
            f"background:{self._accent};color:#1a1a1a;border:none;border-radius:8px;"
            f"padding:6px 14px;font-weight:bold;")
        self.history_btn.setStyleSheet(f"color:{self._accent};font-size:12px;")
        self.scroll.setStyleSheet(
            modern_scrollbar_qss() +
            f"QScrollArea {{ background:{page_bg}; border:none; }}"
            f"QWidget#changelogListHost {{ background:{page_bg}; }}")
        self._render()

    def retranslate(self) -> None:
        """语言切换后刷新文案。"""
        self.window_title = tr("nav.changelog")
        self.setWindowTitle(self.window_title)
        self.title_label.setText(tr("changelog.title"))
        self.check_btn.setText(tr("changelog.check_update"))
        self.history_btn.setText(tr("changelog.full_history"))
        self.version_badge.setText(VERSION_LABEL)
        if self.check_btn.isEnabled():
            self.status_label.setText("")
        self._render()
