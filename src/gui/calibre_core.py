# -*- coding: utf-8 -*-
"""
Calibre 接入核心：检测本机 Calibre、读取书库、用 ebook-edit 编辑 EPUB、格式转换。

设计要点
========
1. 只读读取 Calibre 书库的 metadata.db（SQLite），不依赖 calibredb 命令的
   不稳定输出格式，也不修改书库结构。
2. 编辑 EPUB 调用 Calibre 自带的 ebook-edit（即 Calibre Editor，可视化修改
   文字/图片/目录/元数据，保存后原位写回 EPUB）。
3. 转换格式调用 ebook-convert，导入书库调用 calibredb add。
4. 全平台路径探测：Windows / macOS / Linux，并允许用户手动指定 Bin 目录。

所有操作失败都用 CalibreError 抛出可读信息，不吞异常。
仅供个人管理自己的电子书使用。
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def _no_window_flags() -> int:
    """Windows 下隐藏子进程控制台窗口，避免调用 calibredb 等外部程序时闪黑框。

    非 Windows 返回 0（creationflags 在 POSIX 上会被 subprocess 忽略）。
    """
    if os.name == "nt":
        # CREATE_NO_WINDOW = 0x08000000；部分 Python 版本未导出该常量，故硬编码兜底
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


# ---------------------------------------------------------------- 常量

CALIBRE_BIN_NAMES = {
    "win32": {
        "editor": "ebook-edit.exe",
        "convert": "ebook-convert.exe",
        "db": "calibredb.exe",
        "main": "calibre.exe",
    },
    "darwin": {
        "editor": "ebook-edit",
        "convert": "ebook-convert",
        "db": "calibredb",
        "main": "calibre",
    },
    "linux": {
        "editor": "ebook-edit",
        "convert": "ebook-convert",
        "db": "calibredb",
        "main": "calibre",
    },
}

# 常见安装目录（按平台）
COMMON_BIN_DIRS: Dict[str, List[str]] = {
    "win32": [
        r"C:\Program Files\Calibre2",
        r"C:\Program Files (x86)\Calibre2",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Calibre2"),
    ],
    "darwin": [
        "/Applications/Calibre.app/Contents/MacOS",
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ],
    "linux": [
        "/usr/bin",
        "/usr/local/bin",
        "/opt/calibre",
        os.path.expanduser("~/calibre"),
    ],
}

# 常见书库位置
COMMON_LIBRARY_DIRS = [
    os.path.expanduser("~/Calibre Library"),
    os.path.expanduser("~/calibre"),
    os.path.expanduser("~/Documents/Calibre Library"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Calibre Library"),
]

SUPPORTED_READ = (".epub", ".mobi", ".azw3", ".pdf", ".txt", ".fb2", ".azw", ".prc")
EDITABLE = (".epub",)  # ebook-edit 主要编辑 EPUB；其它格式先转换


class CalibreError(Exception):
    """Calibre 操作错误。"""


@dataclass
class CalibreInstall:
    """一台机器上的 Calibre 安装信息。"""

    bin_dir: str = ""
    editor: str = ""
    converter: str = ""
    db_cli: str = ""
    main: str = ""
    version: str = ""

    @property
    def available(self) -> bool:
        return bool(self.editor and os.path.isfile(self.editor))

    def summary(self) -> str:
        if not self.available:
            return "未检测到 Calibre"
        ver = f" v{self.version}" if self.version else ""
        return f"Calibre{ver}（{self.bin_dir}）"


@dataclass
class BookEntry:
    """书库中的一本书。"""

    book_id: int
    title: str
    authors: str
    formats: List[str] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)  # format -> 绝对路径
    timestamp: str = ""
    size: int = 0

    @property
    def format_text(self) -> str:
        return " / ".join(self.formats)


# ---------------------------------------------------------------- 探测

def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _candidate_bin_dirs(extra: Optional[str] = None) -> List[str]:
    key = _platform_key()
    names = CALIBRE_BIN_NAMES[key]
    dirs: List[str] = []
    if extra:
        dirs.append(os.path.expanduser(extra.strip()))
    dirs.extend(COMMON_BIN_DIRS.get(key, []))
    # PATH 上直接有的
    for exe in (names["editor"], names["convert"], names["db"]):
        found = shutil.which(exe)
        if found:
            parent = os.path.dirname(found)
            if parent not in dirs:
                dirs.append(parent)
    # 去重
    seen: set = set()
    return [d for d in dirs if d and not (d in seen or seen.add(d))]  # type: ignore


def detect_calibre(extra_bin_dir: str = "") -> CalibreInstall:
    """探测本机 Calibre 安装。extra_bin_dir 为用户手动指定的 bin 目录。"""
    key = _platform_key()
    names = CALIBRE_BIN_NAMES[key]
    install = CalibreInstall()
    for d in _candidate_bin_dirs(extra_bin_dir):
        ed = os.path.join(d, names["editor"])
        conv = os.path.join(d, names["convert"])
        dbc = os.path.join(d, names["db"])
        main = os.path.join(d, names["main"])
        if os.path.isfile(ed) or os.path.isfile(conv) or os.path.isfile(dbc):
            install.bin_dir = d
            install.editor = ed if os.path.isfile(ed) else ""
            install.converter = conv if os.path.isfile(conv) else ""
            install.db_cli = dbc if os.path.isfile(dbc) else ""
            install.main = main if os.path.isfile(main) else ""
            break
    # 取版本
    ver_exe = install.db_cli or install.converter or install.editor
    if ver_exe and os.path.isfile(ver_exe):
        try:
            out = subprocess.run(
                [ver_exe, "--version"], capture_output=True, text=True,
                timeout=8, creationflags=_no_window_flags(),
            )
            m = re.search(r"(\d+\.\d+\.\d+)", (out.stdout or "") + (out.stderr or ""))
            if m:
                install.version = m.group(1)
        except Exception:  # noqa: BLE001
            pass
    return install


def detect_library(extra: str = "") -> str:
    """自动探测 Calibre 书库目录（含 metadata.db 的目录）。"""
    candidates: List[str] = []
    if extra:
        candidates.append(os.path.expanduser(extra.strip()))
    candidates.extend(COMMON_LIBRARY_DIRS)
    # Calibre 配置文件里记录的最后书库（ini 格式）
    cfg = os.path.expanduser(
        "~/.calibre/calibre.ini" if not sys.platform.startswith("win")
        else os.path.join(os.environ.get("APPDATA", ""), "calibre", "calibre.ini")
    )
    if os.path.isfile(cfg):
        try:
            import configparser
            cp = configparser.ConfigParser()
            cp.read(cfg, encoding="utf-8")
            for section in cp.sections():
                for k, v in cp.items(section):
                    if "library" in k.lower() and v and os.path.isfile(os.path.join(v, "metadata.db")):
                        candidates.insert(0, v)
        except Exception:  # noqa: BLE001
            pass
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, "metadata.db")):
            return d
    return ""


# ---------------------------------------------------------------- 书库读取

def list_books(library: str, limit: int = 500, keyword: str = "") -> List[BookEntry]:
    """只读解析 metadata.db，返回书库书籍列表。"""
    db_path = os.path.join(library, "metadata.db")
    if not os.path.isfile(db_path):
        raise CalibreError(f"书库不存在：{db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CalibreError(f"无法打开书库（可能被 Calibre 占用）：{exc}") from exc
    try:
        # 表名/列名按 Calibre 固定 schema
        sql = """
            SELECT b.id, b.title, b.timestamp,
                   GROUP_CONCAT(DISTINCT a.name) AS authors
            FROM books b
            LEFT JOIN books_authors_link bal ON bal.book = b.id
            LEFT JOIN authors a ON a.id = bal.author
            GROUP BY b.id
            ORDER BY b.timestamp DESC
            LIMIT ?
        """
        rows = conn.execute(sql, (limit,)).fetchall()
        books: List[BookEntry] = []
        for book_id, title, timestamp, authors in rows:
            data_rows = conn.execute(
                "SELECT format, name FROM data WHERE book = ?", (book_id,)
            ).fetchall()
            fmt_map: Dict[str, str] = {}
            for fmt, name in data_rows:
                if not fmt or not name:
                    continue
                # name 可能是相对路径或绝对路径
                p = name if os.path.isabs(name) else os.path.join(library, name)
                fmt_map[str(fmt).lower()] = p
            entry = BookEntry(
                book_id=int(book_id),
                title=str(title or "未命名"),
                authors=str(authors or "").replace("|", ", "),
                formats=sorted(fmt_map.keys()),
                files=fmt_map,
                timestamp=str(timestamp or "")[:10],
                size=0,
            )
            if keyword:
                kw = keyword.lower()
                if kw not in entry.title.lower() and kw not in entry.authors.lower():
                    continue
            books.append(entry)
        return books
    except sqlite3.Error as exc:
        raise CalibreError(f"读取书库失败：{exc}") from exc
    finally:
        conn.close()


def book_file(entry: BookEntry, prefer: Tuple[str, ...] = (".epub",)) -> str:
    """取一本书的可编辑文件（优先 EPUB）。"""
    for fmt in prefer:
        p = entry.files.get(fmt.lstrip("."))
        if p and os.path.isfile(p):
            return p
    for fmt, p in entry.files.items():
        if p and os.path.isfile(p):
            return p
    return ""


# ---------------------------------------------------------------- 操作

def _run(cmd: List[str], timeout: int = 120) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=_no_window_flags(),
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as exc:
        raise CalibreError(f"找不到可执行文件：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CalibreError(f"命令超时：{' '.join(cmd[:2])}") from exc


def open_editor(install: CalibreInstall, file_path: str) -> None:
    """用 Calibre Editor 打开 EPUB 进行可视化编辑（保存即写回原文件）。"""
    if not install.editor or not os.path.isfile(install.editor):
        raise CalibreError("未检测到 ebook-edit，请在设置中指定 Calibre 安装目录")
    if not os.path.isfile(file_path):
        raise CalibreError(f"文件不存在：{file_path}")
    if os.path.splitext(file_path)[1].lower() != ".epub":
        raise CalibreError("ebook-edit 仅支持直接编辑 EPUB，请先转换格式")
    try:
        subprocess.Popen(
            [install.editor, file_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_no_window_flags(),
        )
    except Exception as exc:  # noqa: BLE001
        raise CalibreError(f"启动 ebook-edit 失败：{exc}") from exc


def convert_format(install: CalibreInstall, src: str, dst: str,
                   timeout: int = 180) -> str:
    """用 ebook-convert 转换格式，返回输出文件路径。"""
    if not install.converter or not os.path.isfile(install.converter):
        raise CalibreError("未检测到 ebook-convert，请在设置中指定 Calibre 安装目录")
    if not os.path.isfile(src):
        raise CalibreError(f"源文件不存在：{src}")
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    code, out, err = _run([install.converter, src, dst], timeout=timeout)
    if code != 0 or not os.path.isfile(dst):
        raise CalibreError(f"转换失败：{(err or out).strip()[:300]}")
    return dst


def add_to_library(install: CalibreInstall, library: str, file_path: str) -> str:
    """用 calibredb add 把书加入 Calibre 书库。"""
    if not install.db_cli or not os.path.isfile(install.db_cli):
        raise CalibreError("未检测到 calibredb，请在设置中指定 Calibre 安装目录")
    if not os.path.isfile(file_path):
        raise CalibreError(f"文件不存在：{file_path}")
    if not os.path.isfile(os.path.join(library, "metadata.db")):
        raise CalibreError(f"书库无效：{library}")
    code, out, err = _run([
        install.db_cli, "add", file_path,
        "--with-library", library,
    ], timeout=120)
    if code != 0:
        raise CalibreError(f"加入书库失败：{(err or out).strip()[:300]}")
    return out.strip()


def export_from_library(install: CalibreInstall, library: str,
                        book_id: int, out_dir: str) -> List[str]:
    """用 calibredb export 导出书籍文件。"""
    if not install.db_cli or not os.path.isfile(install.db_cli):
        raise CalibreError("未检测到 calibredb")
    os.makedirs(out_dir, exist_ok=True)
    code, out, err = _run([
        install.db_cli, "export", str(book_id),
        "--with-library", library,
        "--dir", out_dir,
    ], timeout=120)
    if code != 0:
        raise CalibreError(f"导出失败：{(err or out).strip()[:300]}")
    return [os.path.join(out_dir, f) for f in os.listdir(out_dir)]
