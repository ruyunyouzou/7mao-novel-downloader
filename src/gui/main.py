# -*- coding: utf-8 -*-
"""GUI 程序入口。"""

from __future__ import annotations

import os
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
    # 彻底退出兜底：Qt 事件循环结束后，CPython 解释器退出时仍会 join 所有
    # 非守护线程（concurrent.futures 的 _python_exit atexit、微信读书长轮询、
    # 未 terminate 干净的 QThread 等）。任一后台线程卡在 I/O 上，进程就不会
    # 真正结束——这正是「从托盘退出后任务管理器仍有残留进程」的根因。
    # 所有用户配置均由 DataStore 原子即时写盘、不依赖退出清理，因此这里用
    # os._exit 强制终结：跳过线程 join / atexit / GC，确保进程一定消失、
    # 解锁安装目录（_internal/*.dll），使旧版本可被删除覆盖。
    _hard_exit(code)


def _hard_exit(code: int = 0) -> None:
    """强制结束进程：先做轻量清理，再 os._exit 跳过线程等待。"""
    try:
        # 显式清理提示音临时文件（os._exit 不会触发 atexit）
        from . import notify_core
        cleanup = getattr(notify_core, "cleanup", None)
        if callable(cleanup):
            cleanup()
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(int(code) & 0xFF)


if __name__ == "__main__":
    main()
