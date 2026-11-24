# -*- coding: utf-8 -*-
"""双击/命令行运行：python 启动GUI.py"""

import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC_DIR)


def _bootstrap() -> None:
    # 更新模式：由 update_installer 从临时目录拉起本 exe 时进入。
    # 必须先于任何 PySide6 / GUI 导入分发，因为更新器只依赖标准库，
    # 且此时主程序应尽快退出以释放被占用的 exe 文件。
    if "--do-update" in sys.argv:
        from gui.updater import run_update_mode
        run_update_mode(sys.argv)
        return

    from gui.main import main
    main()


if __name__ == "__main__":
    _bootstrap()
