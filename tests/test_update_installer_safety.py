# -*- coding: utf-8 -*-
"""更新安装器安全测试：zip-slip 路径穿越防护（离线，构造内存 zip）。"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gui.update_installer import safe_extract  # noqa: E402


def _make_zip(names: list[str]) -> str:
    """在临时目录构造一个含指定成员名的 zip，返回其路径。"""
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return path


def _cleanup(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def test_reject_path_traversal() -> None:
    """含 .. 的成员必须被拒绝，不落盘。"""
    zp = _make_zip(["../../evil.exe"])
    target = tempfile.mkdtemp()
    try:
        result = safe_extract(zp, target)
        assert result.get("ok") is False
        assert "非法路径" in result.get("error", "")
    finally:
        _cleanup(zp)


def test_reject_absolute_windows_path() -> None:
    """Windows 盘符绝对路径（C:\\evil.exe）必须被拒绝。

    回归：pyinstaller 在 Windows 上解压更新包，若不拦截盘符路径，
    恶意包可能写入系统盘任意位置。
    """
    zp = _make_zip(["C:\\evil.exe"])
    target = tempfile.mkdtemp()
    try:
        result = safe_extract(zp, target)
        assert result.get("ok") is False
        assert "非法路径" in result.get("error", "")
    finally:
        _cleanup(zp)


def test_reject_leading_slash() -> None:
    """以 / 或 \\ 开头的绝对路径成员必须被拒绝。"""
    for name in ("/evil.exe", "\\evil.exe"):
        zp = _make_zip([name])
        try:
            result = safe_extract(zp, tempfile.mkdtemp())
            assert result.get("ok") is False, f"{name} 应被拒绝"
        finally:
            _cleanup(zp)


def test_allow_safe_relative_paths() -> None:
    """正常相对路径成员应能成功解压并找到 exe。"""
    zp = _make_zip(["app/Main.exe", "app/lib/readme.txt"])
    target = tempfile.mkdtemp()
    try:
        result = safe_extract(zp, target)
        assert result.get("ok") is True
        assert result.get("exe", "").endswith("Main.exe")
    finally:
        _cleanup(zp)
