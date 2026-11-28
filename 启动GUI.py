# -*- coding: utf-8 -*-
"""双击/命令行运行：python 启动GUI.py"""

import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, SRC_DIR)


def _run_selftest() -> int:
    """打包自检入口（#49）：验证发布版 exe 里 httpcloak 原生库加载成功。

    CI 在 Windows 打包后用 ``exe --wenku8-selftest=<结果文件>`` 调用。
    不导入任何 PySide6 / GUI 模块，避免无显示环境或插件缺失干扰判定。
    结果以 UTF-8 文本写入指定文件（exe 是 console=False，无 stdout）。
    返回进程退出码：0=PASS，1=FAIL。
    """
    out_path = ""
    for arg in sys.argv:
        if arg.startswith("--wenku8-selftest="):
            out_path = arg.split("=", 1)[1]
            break
    try:
        from gui.wenku8_core.wenku8_http import selftest_report
        report = selftest_report()
    except Exception as exc:  # noqa: BLE001
        report = {"network_mode": "unknown", "result": "FAIL",
                  "error": f"{type(exc).__name__}: {exc}"}

    lines = [f"{k}={v}" for k, v in report.items()]
    text = "\n".join(lines) + "\n"
    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception:  # noqa: BLE001
            pass
    # 控制台模式下也打印，便于本地/开发机直接查看
    try:
        print(text)
    except Exception:  # noqa: BLE001
        pass
    return 0 if report.get("result") == "PASS" else 1


def _bootstrap() -> None:
    # 打包自检：必须最先分发，先于任何 PySide6 / GUI 导入。
    if any(a.startswith("--wenku8-selftest") for a in sys.argv):
        raise SystemExit(_run_selftest())

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
