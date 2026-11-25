# -*- coding: utf-8 -*-
"""一键升级：下载新版 zip → 安全解压 → 拉起独立 updater → 主程序退出。

流程（与 gui/updater.py 配套）
==============================
1. 从 GitHub Releases API 找到与最新版本号匹配的 Windows zip asset，
   拿到 ``browser_download_url`` 与大小（不依赖 manifest 里手写文件名，避免
   Windows runner 中文文件名被吞导致拼接错误）。
2. 流式下载到系统临时目录，支持国内 GitHub 加速镜像与进度回调。
3. 校验大小 + 安全解压（防 zip-slip），得到新版本目录（含 exe 与 _internal）。
4. 用**新版 exe 自身**以 ``--do-update`` 参数拉起独立更新进程：
   ``<新exe> --do-update --src <新版目录> --dst <安装目录> --pid <本进程PID> --exe <exe名>``
   —— 关键点：one-folder 打包的 exe 复制到别处会因缺 _internal 无法独立运行，
   而"新下载解压出来的目录"本身就是一个完整可运行程序，用它当 updater 最稳。
5. 主程序在 updater 拉起后退出，由 updater 完成覆盖与重启。

本模块依赖 requests / PySide6（运行在主进程的 worker 线程里），
updater 模式那一侧（updater.py）才只依赖标准库。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from typing import Any, Dict, Optional

from .logutil import logger
from .netutil import safe_get

# GitHub Releases API / 加速镜像前缀
_API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
_API_BY_TAG = "https://api.github.com/repos/{repo}/releases/tags/{tag}"
# 国内下载 GitHub 大文件常需加速；这些前缀把原始 github.com 链接包裹一层。
# 按顺序尝试，失败自动回退下一个（含空串=直连）。
_MIRRORS = [
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "",  # 直连兜底
]
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "xingyu-novel-updater",
}

_CHUNK = 1024 * 512  # 512 KiB


def _brief(exc: Any) -> str:
    """把异常压缩成一行简短提示，避免把 SSL 证书原文整段弹给用户。"""
    if not exc:
        return "未知错误"
    name = type(exc).__name__
    text = str(exc).strip().splitlines()
    first = text[0] if text else ""
    return f"{name}: {first}"[:160]


def _load_repo() -> str:
    try:
        from .update_core import _load_repo as _r
        return _r()
    except Exception:  # noqa: BLE001
        return "ruyunyouzou/7mao-novel-downloader"


def find_asset(version: str = "", timeout: int = 10) -> Dict[str, Any]:
    """查找与 version 匹配的 Windows zip asset。

    返回 ``{'ok', 'name', 'size', 'url', 'tag', 'error'}``。
    version 为空时取 latest；否则按 tag ``v{version}`` 精确查，避免 latest 与新版本
    检查用的 manifest 不一致。
    """
    repo = _load_repo()
    url = _API_BY_TAG.format(repo=repo, tag=f"v{version}") if version else _API_LATEST.format(repo=repo)
    # API 也走镜像回退：先直连，失败再试 gh-proxy / ghfast 包裹的 api.github.com
    api_path = url.split("api.github.com/", 1)[-1]
    mirrors = [
        f"https://gh-proxy.com/https://api.github.com/{api_path}",
        f"https://ghfast.top/https://api.github.com/{api_path}",
    ]
    try:
        resp, err = safe_get(url, headers=_HEADERS, timeout=timeout, extra_urls=mirrors)
        if resp is None:
            logger.warning("查询更新 asset 失败（所有通道）", exc=err, tag="update")
            return {"ok": False, "error": "查询更新失败：无法连接到更新服务器，请稍后重试或手动下载"}
        if resp.status_code != 200:
            return {"ok": False, "error": f"查询 Release 失败：HTTP {resp.status_code}"}
        data = resp.json()
        assets = data.get("assets") or []
        # 优先 .zip；排除源码包 Source code
        zips = [a for a in assets
                if str(a.get("name", "")).lower().endswith(".zip")
                and "source" not in str(a.get("name", "")).lower()]
        if not zips:
            return {"ok": False, "error": "该版本没有可下载的 Windows zip 安装包"}
        # 选体积最大的（主程序包），避免选到小的附属包
        asset = max(zips, key=lambda a: int(a.get("size") or 0))
        return {
            "ok": True,
            "name": asset.get("name", ""),
            "size": int(asset.get("size") or 0),
            "url": asset.get("browser_download_url", ""),
            "tag": data.get("tag_name", ""),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询更新 asset 失败", exc=exc, tag="update")
        return {"ok": False, "error": "查询更新失败：无法连接到更新服务器，请稍后重试或手动下载"}


def _with_mirror(url: str, mirror: str) -> str:
    if not mirror:
        return url
    return mirror.rstrip("/") + "/" + url.lstrip("/")


def download_asset(asset: Dict[str, Any], dest: str,
                   progress_cb=None, status_cb=None,
                   timeout: int = 30) -> Dict[str, Any]:
    """把 asset 下载到 dest 文件，返回 ``{'ok','path','size','error'}``。

    依次尝试加速镜像；每个镜像先试 Range 探测（能否正常返回数据），再全量下载。
    progress_cb(0-100)；status_cb(str)。
    """
    raw_url = asset.get("url", "")
    expect = int(asset.get("size") or 0)
    if not raw_url:
        return {"ok": False, "error": "下载地址为空"}

    last_err = "未知错误"
    for mirror in _MIRRORS:
        url = _with_mirror(raw_url, mirror)
        label = mirror.rstrip("/") or "直连"
        if status_cb:
            status_cb(f"尝试下载源：{label}")
        tmp = dest + ".part"
        try:
            resp, err = safe_get(url, stream=True, timeout=(10, timeout),
                                 headers={"User-Agent": "xingyu-novel-updater"})
            if resp is None:
                last_err = f"{label} 连接失败：{_brief(err)}"
                continue
            with resp:
                if resp.status_code not in (200, 206):
                    last_err = f"{label} 返回 HTTP {resp.status_code}"
                    continue
                total = int(resp.headers.get("Content-Length") or 0) or expect
                done = 0
                last_report = 0.0
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=_CHUNK):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if progress_cb and (now - last_report) > 0.2:
                            pct = int(done * 100 / total) if total else 0
                            progress_cb(min(99, pct))
                            last_report = now
            # 大小校验
            got = os.path.getsize(tmp)
            if expect and abs(got - expect) > 1024:
                last_err = f"{label} 下载不完整（{got} / 应为 {expect}）"
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                continue
            os.replace(tmp, dest)
            if progress_cb:
                progress_cb(100)
            return {"ok": True, "path": dest, "size": got, "source": label}
        except Exception as exc:  # noqa: BLE001
            last_err = f"{label} 下载失败：{exc}"
            logger.warning(f"下载源 {label} 失败", exc=exc, tag="update")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            continue
    return {"ok": False, "error": last_err}


def safe_extract(zip_path: str, target_dir: str, progress_cb=None) -> Dict[str, Any]:
    """安全解压 zip 到 target_dir，防 zip-slip（路径穿越）。

    返回 ``{'ok','dir','exe','error'}``，exe 为解压出的主程序 exe 绝对路径。
    """
    try:
        os.makedirs(target_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.infolist()
            bad = [m.filename for m in members
                   if m.filename.startswith(("/", "\\")) or ".." in m.filename.replace("\\", "/").split("/")]
            if bad:
                return {"ok": False, "error": "安装包包含非法路径，已拒绝解压"}
            total = len(members)
            for i, m in enumerate(members):
                zf.extract(m, target_dir)
                if progress_cb and i % 20 == 0:
                    progress_cb(int(i * 100 / max(total, 1)))
        # 找 exe（可能带中文名，用扩展名匹配；排除卸载器等）
        exes = []
        for dp, _dirs, files in os.walk(target_dir):
            for fn in files:
                if fn.lower().endswith(".exe") and "unins" not in fn.lower():
                    exes.append(os.path.join(dp, fn))
        if not exes:
            return {"ok": False, "error": "安装包内找不到主程序 exe"}
        # 选体积最大或路径最浅的（one-folder 主 exe 在顶层）
        main_exe = min(exes, key=lambda p: (p.count(os.sep), -os.path.getsize(p)))
        return {"ok": True, "dir": target_dir, "exe": main_exe}
    except Exception as exc:  # noqa: BLE001
        logger.warning("解压更新包失败", exc=exc, tag="update")
        return {"ok": False, "error": f"解压失败：{exc}"}


def install_dir() -> str:
    """当前安装目录（exe 同级）。源码模式返回项目根，便于测试。

    源码模式：本文件在 <项目根>/src/gui/update_installer.py，上溯三级到项目根。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def launch_updater(new_dir: str, new_exe: str, pid: Optional[int] = None) -> Dict[str, Any]:
    """用新版 exe 拉起独立更新进程，随后主程序应退出。

    ``--src`` 新版目录、``--dst`` 安装目录、``--exe`` 主 exe 名、``--pid`` 主进程 PID。
    """
    dst = install_dir()
    exe_name = os.path.basename(new_exe)
    pid = pid or os.getpid()
    args = [
        new_exe, "--do-update",
        "--src", new_dir,
        "--dst", dst,
        "--pid", str(pid),
        "--exe", exe_name,
    ]
    try:
        if os.name == "nt":
            flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0))
            subprocess.Popen(args, cwd=new_dir, creationflags=flags,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(args, cwd=new_dir, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        logger.info(f"已拉起更新进程: {new_exe}", tag="update")
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"拉起更新进程失败: {exc}", tag="update")
        return {"ok": False, "error": f"启动更新失败：{exc}"}


def cleanup_temp(path: str) -> None:
    """尽力清理临时目录（更新进程的 exe 可能仍占用，失败忽略）。"""
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def make_temp_dir() -> str:
    return tempfile.mkdtemp(prefix="xingyu_update_")


# ------------------------------------------------------------------ UI Worker
def _qthread_base():
    """延迟导入，避免非 GUI 环境（测试/CLI）导入本模块时失败。"""
    from PySide6.QtCore import QThread, Signal
    return QThread, Signal


try:
    _QT, _SIG = _qthread_base()

    class UpgradeWorker(_QT):
        """后台执行：查找 asset → 下载 → 解压 → 拉起 updater。

        信号：
          progress(int) 0-100 总进度
          status(str)   阶段文字
          done(dict)    {'ok','need_restart','error'}
        """
        progress = _SIG(int)
        status = _SIG(str)
        done = _SIG(dict)

        def __init__(self, version: str = "", parent=None) -> None:
            super().__init__(parent)
            self.version = version
            self._cancelled = False
            self._tmp_dir = ""

        def cancel(self) -> None:
            self._cancelled = True

        def run(self) -> None:
            result: Dict[str, Any] = {"ok": False, "need_restart": False}
            try:
                self.status.emit("正在查询安装包…")
                asset = find_asset(self.version)
                if not asset.get("ok"):
                    result["error"] = asset.get("error", "查询安装包失败")
                    self.done.emit(result)
                    return

                self._tmp_dir = make_temp_dir()
                zip_path = os.path.join(self._tmp_dir, "update.zip")
                self.status.emit("正在下载新版安装包…")

                def dl_progress(p):
                    if not self._cancelled:
                        self.progress.emit(int(p * 0.7))  # 下载占 70%

                dl = download_asset(asset, zip_path, progress_cb=dl_progress,
                                    status_cb=lambda s: self.status.emit(s))
                if self._cancelled:
                    result["error"] = "已取消"
                    cleanup_temp(self._tmp_dir)
                    self.done.emit(result)
                    return
                if not dl.get("ok"):
                    result["error"] = dl.get("error", "下载失败")
                    cleanup_temp(self._tmp_dir)
                    self.done.emit(result)
                    return

                self.status.emit("正在解压安装包…")
                new_dir = os.path.join(self._tmp_dir, "new")

                def ex_progress(p):
                    if not self._cancelled:
                        self.progress.emit(70 + int(p * 0.2))  # 解压占 20%

                ex = safe_extract(zip_path, new_dir, progress_cb=ex_progress)
                if not ex.get("ok"):
                    result["error"] = ex.get("error", "解压失败")
                    cleanup_temp(self._tmp_dir)
                    self.done.emit(result)
                    return

                self.status.emit("正在准备安装…")
                self.progress.emit(92)
                up = launch_updater(ex["dir"], ex["exe"], pid=os.getpid())
                if not up.get("ok"):
                    result["error"] = up.get("error", "启动更新失败")
                    cleanup_temp(self._tmp_dir)
                    self.done.emit(result)
                    return

                self.progress.emit(100)
                # 成功：临时目录交给 updater 读取，不能在这里删（updater 还在用）
                result["ok"] = True
                result["need_restart"] = True
                result["src"] = ex["dir"]
                self.done.emit(result)
            except Exception as exc:  # noqa: BLE001
                logger.error("一键升级异常", exc=exc, tag="update")
                result["error"] = f"升级失败：{exc}"
                if self._tmp_dir:
                    cleanup_temp(self._tmp_dir)
                self.done.emit(result)

except ImportError:
    # 非 PySide6 环境（纯逻辑测试）不定义 UpgradeWorker
    UpgradeWorker = None  # type: ignore
