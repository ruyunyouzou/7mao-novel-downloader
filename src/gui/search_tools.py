# -*- coding: utf-8 -*-
"""
搜索页扩展工具：书链接/ID 直达、批量下载、搜索历史、书签。

方案 C：拆成两个独立面板（直达 / 历史书签），由主窗口的标签栏在同一区域切换复用。
通过回调 win._load_book_by_id / win._bulk_download / win._start_search 与主窗口协作。
"""

from __future__ import annotations


from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QMessageBox, QInputDialog, QComboBox

from .datastore import DataStore
from .i18n import tr
from .style import TEXT_DIM, ACCENT
from .book_link import extract_book_id, detect_source, extract_book_title
from .widgets import apply_nation_icon


def _chip(text: str, callback, color: str = ACCENT) -> QPushButton:
    b = QPushButton(text)
    b.setFlat(True)
    b.setStyleSheet(
        f"QPushButton{{color:{color};font-size:12px;border:1px solid {TEXT_DIM};"
        f"border-radius:10px;padding:2px 8px;}}")
    b.clicked.connect(callback)
    return b


def build_search_tools(win) -> tuple:
    """返回 (direct_widget, history_widget) 两个面板，供标签栏切换复用。

    仍沿用原先挂在 win 上的属性（link_edit / history_chips / reload_search_tools 等），
    因此主窗口其余逻辑无需改动。
    """
    store: DataStore = win.store

    # ---------- 面板 A：链接/ID 直达 + 批量下载 ----------
    direct = QWidget()
    dv = QHBoxLayout(direct)
    dv.setContentsMargins(0, 0, 0, 0)
    dv.setSpacing(8)

    win.link_edit = QLineEdit()
    win.link_edit.setPlaceholderText(tr("searchtools.link_placeholder"))
    win.link_edit.returnPressed.connect(lambda: _goto(win))
    dv.addWidget(win.link_edit, 1)

    win.btn_link_goto = QPushButton(tr("searchtools.goto"))
    win.btn_link_goto.clicked.connect(lambda: _goto(win))
    apply_nation_icon(win.btn_link_goto, "snezhnaya", ACCENT, 16)
    dv.addWidget(win.btn_link_goto)

    # 格式选择：与搜索页 format_combo 双向同步，直达/批量下载都能就地选格式
    win.direct_format_label = QLabel(tr("search.format"))
    dv.addWidget(win.direct_format_label)
    win.direct_format_combo = QComboBox()
    win.direct_format_combo.addItems(["txt", "epub", "chapter"])
    default_fmt = str(store.settings_get("default_format", "txt") or "txt")
    win.direct_format_combo.setCurrentText(default_fmt)
    dv.addWidget(win.direct_format_combo)

    win.btn_bulk = QPushButton(tr("searchtools.bulk"))
    win.btn_bulk.clicked.connect(lambda: _bulk(win))
    apply_nation_icon(win.btn_bulk, "inazuma", ACCENT, 16)
    dv.addWidget(win.btn_bulk)

    # ---------- 面板 B：搜索历史 + 书签 ----------
    history = QWidget()
    hv = QVBoxLayout(history)
    hv.setContentsMargins(0, 0, 0, 0)
    hv.setSpacing(6)

    # 搜索历史
    hist_row = QHBoxLayout()
    hist_row.setSpacing(4)
    hist_title = QLabel(tr("searchtools.history"))
    hist_title.setStyleSheet(f"color:{TEXT_DIM};font-size:12px;")
    hist_row.addWidget(hist_title)
    win.history_chips = QWidget()
    win.history_chips_layout = QHBoxLayout(win.history_chips)
    win.history_chips_layout.setContentsMargins(0, 0, 0, 0)
    win.history_chips_layout.setSpacing(4)
    hist_row.addWidget(win.history_chips, 1)
    btn_clear_hist = QPushButton(tr("searchtools.clear_history"))
    btn_clear_hist.setFlat(True)
    btn_clear_hist.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;border:none;")
    btn_clear_hist.clicked.connect(lambda: (store.search_history_clear(), reload_hist(win)))
    hist_row.addWidget(btn_clear_hist)
    hv.addLayout(hist_row)

    # 书签
    bm_row = QHBoxLayout()
    bm_row.setSpacing(4)
    bm_title = QLabel(tr("searchtools.bookmarks"))
    bm_title.setStyleSheet(f"color:{TEXT_DIM};font-size:12px;")
    bm_row.addWidget(bm_title)
    win.bookmark_chips = QWidget()
    win.bookmark_chips_layout = QHBoxLayout(win.bookmark_chips)
    win.bookmark_chips_layout.setContentsMargins(0, 0, 0, 0)
    win.bookmark_chips_layout.setSpacing(4)
    bm_row.addWidget(win.bookmark_chips, 1)
    hv.addLayout(bm_row)

    def reload_hist(_=None):
        _clear_layout(win.history_chips_layout)
        for kw in store.search_history_all(limit=12):
            win.history_chips_layout.addWidget(
                _chip(kw, lambda k=kw: (win.search_edit.setText(k), win.do_search())))
        win.history_chips_layout.addStretch()

    def reload_bookmarks(_=None):
        _clear_layout(win.bookmark_chips_layout)
        bms = store.bookmark_all()
        if not bms:
            lab = QLabel(tr("searchtools.no_bookmarks"))
            lab.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
            win.bookmark_chips_layout.addWidget(lab)
        for b in bms[:12]:
            win.bookmark_chips_layout.addWidget(
                _chip(f"⭐{b.get('title','')}",
                      lambda bid=b.get('id'), src=b.get('source', ''):
                          win._load_book_by_id(bid, src)))
        win.bookmark_chips_layout.addStretch()

    win.reload_search_tools = lambda: (reload_hist(), reload_bookmarks())
    reload_hist()
    reload_bookmarks()
    return direct, history


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()


def _goto(win) -> None:
    text = win.link_edit.text().strip()
    bid = extract_book_id(text)
    source = detect_source(text)

    # 短链（/t/<token>）可能已失效，服务端返回 404 无法还原 ID；
    # 若口令文本里带《书名》，回退用番茄搜索接口按书名找到第一条结果直达。
    if not bid and source == "fanqie":
        title = extract_book_title(text)
        if title:
            try:
                from gui.fanqie_core.fanqie_search import search_fanqie
                results = search_fanqie(title, page=1) or []
                if results:
                    bid = str(results[0].get("id", "") or "").strip()
                    if bid:
                        win.log(f"短链失效，已按书名《{title}》匹配到书籍直达。")
            except Exception:  # noqa: BLE001 - 搜索失败仍走原报错路径
                bid = ""

    if not bid:
        QMessageBox.warning(win, tr("searchtools.goto"), tr("searchtools.bad_link"))
        return
    win._load_book_by_id(bid, source=source)


def _bulk(win) -> None:
    text, ok = QInputDialog.getMultiLineText(
        win, tr("searchtools.bulk"), tr("searchtools.bulk_hint"), "")
    if not ok or not text.strip():
        return
    import re
    ids, sources = [], []
    for line in re.split(r"[\s,，;；]+", text.strip()):
        bid = extract_book_id(line)
        if bid and bid not in ids:
            ids.append(bid)
            sources.append(detect_source(line))
    if not ids:
        QMessageBox.warning(win, tr("searchtools.bulk"), tr("searchtools.bad_link"))
        return
    win._bulk_download(ids, sources=sources)
