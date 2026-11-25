# -*- coding: utf-8 -*-
"""GUI 程序入口。"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .plugin import PluginManager
from .main_window import MainWindow
from . import render_quality


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("星弦小说下载器（七猫版）GUI")
    # Qt 标准控件文案（文本右键菜单、消息框按钮、文件对话框等）默认中文。
    # MainWindow 绑定语言设置后会再按用户实际语言同步一次。
    from . import qt_translator
    qt_translator.set_language("zh_CN")
    # 全局渲染质量：高 DPI pixmap + 字体抗锯齿（跟随 ui_antialias 设置）
    render_quality.apply_global(app)

    # 插件目录：源码=<项目>/src/plugins；打包=exe 同级 src/plugins 或内嵌
    from .resource_path import plugin_dir
    pm = PluginManager(plugin_dir(), app=None)
    pm.load_all()

    window = MainWindow(pm)
    window.show()
    # 窗口首次显示淡入（动效关闭时为 no-op）
    from . import motion
    motion.fade_in_window(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
