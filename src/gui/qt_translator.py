# -*- coding: utf-8 -*-
"""Qt 标准控件文案翻译器（原生菜单方案）。

加载 qtbase_*.qm 后，Qt 内置控件文案随界面语言本地化，覆盖：
- 文本输入框（QLineEdit/QTextEdit）标准右键菜单：撤消/重做/剪切/复制/粘贴/删除/全选
- QMessageBox / QDialogButtonBox 按钮：确定/取消/是/否/保存/打开
- QFileDialog 按钮及其他 Qt 标准控件文案

这样无需自定义右键菜单，所有 Qt 标准弹窗/菜单一次性统一语言，
与「选择文件路径对话框」用的是同一套机制。

qm 查找顺序（关键修复）：
1. 软件自带资源 assets/translations —— 打包时由 build.spec 显式收集，
   不依赖 PySide6 hook 是否把 translations 目录带进包（历史失效根因）；
2. _MEIPASS 下 PySide6 hook 可能收集到的位置；
3. 源码环境 PySide6 安装目录下的 translations。

翻译器对象必须强引用保活，否则被 GC 后翻译失效。
加载失败静默降级为 Qt 默认文案，不影响主程序启动。
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication

from .i18n import LANG_ZH, LANG_RU

# 界面语言 -> qm 文件名；英文使用 Qt 默认文案（不加载翻译器）
_QM_FILES: Dict[str, str] = {
    LANG_ZH: "qtbase_zh_CN.qm",
    LANG_RU: "qtbase_ru.qm",
}

# 强引用：QTranslator 被 GC 后翻译立即失效
_translator: Optional[QTranslator] = None


def _bundled_dirs() -> List[str]:
    """软件自带的翻译资源目录（源码根/assets 与打包 _MEIPASS/assets 通用）。"""
    out: List[str] = []
    try:
        from .resource_path import resource_path, app_root
        # 打包后 assets 被收集到 <root>/assets/translations；源码模式同结构。
        out.append(resource_path("assets", "translations"))
        # 兼容：若将来把 qm 直接放到 <root>/translations。
        out.append(resource_path("translations"))
        root = app_root()
        out.append(os.path.join(root, "assets", "translations"))
    except Exception:  # noqa: BLE001
        pass
    return out


def _candidate_dirs() -> List[str]:
    """qm 查找目录，自带资源优先，兜底 PySide6 hook 收集位置与源码环境。"""
    dirs: List[str] = []

    # 1) 软件自带资源（最可靠）：build.spec 显式收集 assets/translations
    dirs.extend(_bundled_dirs())

    # 2) 打包模式：_MEIPASS 下 hook 可能收集到的位置
    meipass = getattr(sys, "_MEIPASS", "") or ""
    if meipass:
        dirs.append(os.path.join(meipass, "translations"))
        dirs.append(os.path.join(meipass, "PySide6", "Qt", "translations"))
        dirs.append(os.path.join(meipass, "Qt", "translations"))

    # 3) 源码模式：PySide6 安装目录
    try:
        import PySide6
        pkg = os.path.dirname(os.path.abspath(PySide6.__file__))
        dirs.append(os.path.join(pkg, "Qt", "translations"))
    except Exception:  # noqa: BLE001
        pass

    seen = set()
    out: List[str] = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _find_qm(name: str) -> Optional[str]:
    for d in _candidate_dirs():
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return None


def set_language(lang_code: str) -> None:
    """按界面语言安装/切换 Qt 翻译器；失败静默降级。

    可作为 ``i18n.manager().changed`` 的槽，实现语言切换时同步更新。
    """
    global _translator
    app = QApplication.instance()
    if app is None:
        return
    if _translator is not None:
        try:
            app.removeTranslator(_translator)
        except Exception:  # noqa: BLE001
            pass
        _translator = None
    name = _QM_FILES.get(lang_code)
    if not name:
        return  # 英文：Qt 默认文案
    qm = _find_qm(name)
    if not qm:
        return
    try:
        translator = QTranslator(app)
        if translator.load(qm):
            app.installTranslator(translator)
            _translator = translator  # 保活
    except Exception:  # noqa: BLE001
        pass
