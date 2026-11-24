# -*- coding: utf-8 -*-
"""独立更新执行器（updater mode）。

背景
====
Windows 上正在运行的 exe 文件被系统占用，无法被覆盖。因此「一键升级」不能
在主进程里直接替换自身，必须借助一个**已经在临时目录运行起来的独立进程**来
完成「等待主程序退出 → 用新版本目录覆盖旧安装目录 → 重启主程序」。

实现
====
主程序（update_installer.py）在下载并解压新包后，会：
  1. 把当前正在运行的 exe 复制到系统临时目录（一份副本）；
  2. 以 ``<临时exe> --do-update --src <新包目录> --dst <安装目录>
     --pid <主进程PID> --exe <主exe名>`` 启动本模块；
  3. 主程序随即退出。

本模块在更新模式下运行（由 启动GUI.py 检测到 --do-update 后分发），流程：
  1. 等待主进程 PID 退出（轮询，超时则放弃）；
  2. 把 <新包目录> 的内容整体覆盖到 <安装目录>（逐文件复制 + 占用重试）；
  3. 重启 <安装目录>/<主exe名>；
  4. 写一份日志到 ~/.qimao_gui/update_install.log 便于排错。

本模块**只依赖 Python 标准库**，因为它在 PyInstaller 打包环境里运行，
此时没有独立的 python.exe，不能依赖第三方库或 GUI。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import traceback
from typing import List

_LOG_PATH = os.path.join(os.path.expanduser("~"), ".qimao_gui", "update_install.log")


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    # 同时输出到 stderr（更新模式可能带控制台，便于调试）
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


def _pid_alive(pid: int) -> bool:
    """跨平台判断进程是否还活着。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            # STILL_ACTIVE == 259
            return bool(ok and code.value == 259)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _wait_pid_exit(pid: int, timeout: float = 60.0) -> bool:
    """等待主进程退出。返回 True=已退出, False=超时放弃。"""
    if pid <= 0:
        return True
    deadline = time.time() + timeout
    _log(f"等待主进程 {pid} 退出…")
    while time.time() < deadline:
        if not _pid_alive(pid):
            _log("主进程已退出")
            return True
        time.sleep(0.4)
    _log("等待主进程退出超时，放弃安装")
    return False


def _copy_dir_merge(src: str, dst: str, skip_names: set[str] | None = None) -> None:
    """把 src 目录内容覆盖合并到 dst（不删除 dst 独有文件）。

    逐文件复制并对占用错误重试（Windows 上刚退出的进程可能短暂仍占用 DLL）。
    """
    skip_names = skip_names or set()
    if not os.path.isdir(dst):
        os.makedirs(dst, exist_ok=True)
    for entry in os.listdir(src):
        if entry in skip_names:
            continue
        s = os.path.join(src, entry)
        d = os.path.join(dst, entry)
        if os.path.isdir(s):
            _log(f"复制目录 {entry}/")
            _copy_dir_merge(s, d, skip_names)
        else:
            _copy_file_retry(s, d)


def _copy_file_retry(src: str, dst: str, retries: int = 12, delay: float = 0.5) -> None:
    """复制单个文件，遇到占用/权限错误时重试。"""
    last_exc: Exception | None = None
    for i in range(retries):
        try:
            parent = os.path.dirname(dst)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            # 目标存在且只读时先去掉只读属性（Windows 常见）
            if os.path.exists(dst):
                try:
                    os.chmod(dst, 0o666)
                except OSError:
                    pass
            shutil.copy2(src, dst)
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(delay)
    raise RuntimeError(f"复制失败 {os.path.basename(dst)}: {last_exc}")


def _restart(app_exe: str, install_dir: str) -> None:
    """重启主程序。"""
    exe_path = os.path.join(install_dir, app_exe)
    if not os.path.isfile(exe_path):
        _log(f"重启失败：找不到 {exe_path}")
        return
    try:
        if os.name == "nt":
            # Windows: 用 CREATE_NEW_PROCESS_GROUP 让新进程脱离 updater
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen([exe_path], cwd=install_dir, creationflags=creationflags)
        else:
            subprocess.Popen([exe_path], cwd=install_dir,
                             start_new_session=True)
        _log(f"已重启 {app_exe}")
    except Exception as exc:  # noqa: BLE001
        _log(f"重启失败: {exc}")


def run_update_mode(argv: List[str]) -> None:
    """更新模式入口：等待 → 覆盖 → 重启。argv 为完整 sys.argv。"""
    def arg(name: str, default: str = "") -> str:
        if name in argv:
            i = argv.index(name)
            if i + 1 < len(argv):
                return argv[i + 1]
        return default

    src = arg("--src")
    dst = arg("--dst")
    pid = int(arg("--pid", "0") or 0)
    app_exe = arg("--exe", "星隅小说下载器.exe")

    _log("=" * 56)
    _log("更新模式启动")
    _log(f"  src={src}")
    _log(f"  dst={dst}")
    _log(f"  pid={pid}")
    _log(f"  exe={app_exe}")
    _log(f"  my_exe={sys.executable}")

    try:
        if not src or not os.path.isdir(src):
            _log(f"新包目录不存在: {src}")
            return
        if not dst or not os.path.isdir(dst):
            _log(f"安装目录不存在: {dst}")
            return

        if not _wait_pid_exit(pid):
            return

        _log("开始覆盖安装目录…")
        # 跳过 updater 自己的日志等无需覆盖的用户数据；新包里没有这些，
        # 但保险起见列出。注意不能跳过主 exe —— 它就是要被更新的对象。
        _copy_dir_merge(src, dst)
        _log("覆盖完成")

        # 短暂等待，让文件系统刷新
        time.sleep(1.0)
        _restart(app_exe, dst)
        _log("更新流程结束（成功）")
    except Exception:  # noqa: BLE001
        _log("更新流程异常：\n" + traceback.format_exc())
    finally:
        # 注意：不要删除临时 exe / src —— 本进程正从临时 exe 运行，
        # 无法删除自身。让主程序下次启动时清理临时残留。
        pass


def is_update_mode(argv: List[str] | None = None) -> bool:
    argv = list(sys.argv if argv is None else argv)
    return "--do-update" in argv
