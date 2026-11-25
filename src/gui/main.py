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
    app.setApplicationName("GUI-novel下载器")
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

    # 单实例保护：若已有实例在运行，请求其激活窗口后本进程直接退出，
    # 避免反复双击 exe 累积多个常驻进程、锁定安装目录导致旧版本无法删除。
    from .single_instance import SingleInstance
    guard = SingleInstance()
    if not guard.acquire():
        # 已有实例；activate 请求已在 acquire 内发出，本进程无需创建窗口
        sys.exit(0)

    window = MainWindow(pm)
    # 第二个实例请求激活时，恢复并显示本窗口
    guard.activated.connect(window._restore_from_tray)
    window.show()
    # 窗口首次显示淡入（动效关闭时为 no-op）
    from . import motion
    motion.fade_in_window(window)
    try:
        code = app.exec()
    finally:
        # 无论正常退出还是异常，都释放单实例 server，避免僵尸连接
        guard.release()
    sys.exit(code)


if __name__ == "__main__":
    main()
