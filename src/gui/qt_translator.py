# -*- coding: utf-8 -*-
"""Qt 标准控件文案翻译器。

PySide6 自带 qtbase_*.qm，加载后可将 Qt 内置文案随界面语言本地化，覆盖：
- 文本输入框（QLineEdit/QTextEdit）标准右键菜单：撤消/重做/剪切/复制/粘贴/删除/全选
- QMessageBox / QDialogButtonBox 按钮：确定/取消/是/否/保存/打开
- QFileDialog 按钮及其他 Qt 标准控件文案

这样无需逐个控件自定义右键菜单，所有 Qt 标准弹窗一次性统一语言。

翻译器对象必须强引用保活，否则被 GC 后翻译会失效。
加载失败时静默降级为 Qt 默认文案，不影响主程序启动。
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication

from .i18n import LANG_ZH, LANG_RU

# 界面语言 -> Qt 自带 qm 文件名；英文使用 Qt 默认文案（不加载翻译器）
_QM_FILES: Dict[str, str] = {
    LANG_ZH: "qtbase_zh_CN.qm",
    LANG_RU: "qtbase_ru.qm",
}

# 强引用：QTranslator 被 GC 后翻译立即失效
_translator: Optional[QTranslator] = None


def _candidate_dirs() -> List[str]:
    """qm 文件可能所在目录：源码模式 + PyInstaller one-folder 模式。"""
    dirs: List[str] = []
    # 打包模式优先：frozen 下 PySide6.__file__ 路径可能与 _MEIPASS 不一致，
    # 先查 _MEIPASS 确保命中随包分发的 qm。
    meipass = getattr(sys, "_MEIPASS", "") or ""
    if meipass:
        dirs.append(os.path.join(meipass, "PySide6", "Qt", "translations"))
        dirs.append(os.path.join(meipass, "Qt", "translations"))
    try:
        import PySide6
        pkg = os.path.dirname(os.path.abspath(PySide6.__file__))
        dirs.append(os.path.join(pkg, "Qt", "translations"))
    except Exception:  # noqa: BLE001
        pass
    seen = set()
    out: List[str] = []
    for d in dirs:
        if d not in seen:
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
