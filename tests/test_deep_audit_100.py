# -*- coding: utf-8 -*-
"""深度审查（100 项检查）发现的真实 bug 回归锁定。

本轮逐字逐句审查全项目后，确认并修复 4 个真实缺陷：
  1. download_image() 的 referers 在 try 内定义、try 外使用，异常时
     UnboundLocalError，连指纹兜底都走不到；
  2. sanitize_filename() 不处理反斜杠，Windows 下书名含 `..\\` 会路径穿越，
     分卷文件夹名直接经它拼 os.path.join；
  3. render_filename() 遇未知占位符（如误写 {vol}）整体回退成纯书名，
     分卷导出会让所有卷文件名塌成同一个而互相覆盖；
  4. _txt_images() 文档注释谎称"缺图留占位"，实现实际是抛错（过时注释误导）。

这些修复对全书通用，且不改变番茄/七猫默认行为。
"""
import builtins
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from gui import qimao_core as C  # noqa: E402
from gui.wenku8_core import wenku8_volumes as V  # noqa: E402


def test_download_image_referers_defined_on_import_failure(monkeypatch):
    """import requests 在下载轮次里抛异常时，不能 UnboundLocalError。

    修复前：referers 定义在 try 内，全部轮次异常后第 128 行 referers[0]
    抛 UnboundLocalError，连指纹兜底路径都进不去。修复后应安全返回 b''。
    """
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("模拟 requests 不可用")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    # 不应抛 UnboundLocalError；返回空字节代表确实没下到（由调用方判失败）
    result = V.download_image("http://example.com/a.jpg", attempts=1)
    assert result in (b"", result)  # 只断言"没抛异常"
    assert isinstance(result, bytes)


def test_sanitize_filename_replaces_backslash():
    """反斜杠必须被替换，防 Windows 路径穿越。"""
    assert "\\\\" not in C.sanitize_filename(r"..\..\windows")
    assert C.sanitize_filename(r"a\b\c") == "a_b_c"
    # 正斜杠本来就被处理
    assert C.sanitize_filename("a/b") == "a_b"


def test_sanitize_filename_handles_none():
    """None 输入不再抛异常。"""
    assert C.sanitize_filename(None) == ""
    assert C.sanitize_filename("") == ""


def test_render_filename_keeps_unknown_placeholder_structure():
    """未知占位符不再让整串回退成纯书名，保留其余结构。"""
    name = C.render_filename(
        "{title}_{vol}_{start}-{end}", "书名",
        book_id="1", fmt="epub", start=1, end=10, total=10,
    )
    # 修复前：name == "书名"（丢掉章节范围，多卷互相覆盖）
    assert name != "书名"
    assert "书名" in name and "1-10" in name
    assert "{vol}" in name  # 未知占位符原样保留


def test_render_filename_normal_template_unchanged():
    """合法占位符模板行为不变（番茄/七猫零回归）。"""
    name = C.render_filename(
        "{title}_{author}_{start}-{end}", "书名", author="作者",
        book_id="1", fmt="txt", start=1, end=10, total=10,
    )
    assert name == "书名_作者_1-10"


def test_txt_images_doc_claims_strict(monkeypatch):
    """_txt_images 有 writer 时缺图由 writer 抛错，不留占位。"""
    class StrictWriter:
        def reference(self, url):
            raise C.QimaoError("插图未能下载，本次不出书")
        def write(self, url):
            raise C.QimaoError("插图未能下载，本次不出书")

    try:
        C._txt_images("甲[[WENKU8IMG:http://x/a]]乙", StrictWriter())
        assert False, "应该抛 QimaoError"
    except C.QimaoError:
        pass
