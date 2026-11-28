# -*- coding: utf-8 -*-
"""文库8下载回归：整本成功路径 NameError + 目录抖动重试。

用户反馈（v5.0.22）：①天使书「卡 40% 进度条进不了」——实为整本下载
渲染到 100% 后引用已删除的 missing_imgs 抛 NameError，任务永远完不成；
②紫罗兰「目录为空，无法下载」——fetch_catalog 详情页请求无异常保护、
候选页 attempts=2，用户端网络抖动时全部失败返回空。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def test_whole_download_success_has_no_nameerror(monkeypatch, tmp_path):
    """整本下载成功后构造结果不得抛 NameError（missing_imgs 已删除）。"""
    import plugins.wenku8_downloader_plugin as W

    def fake_build(**kw):
        p = str(tmp_path / "book.epub")
        with open(p, "wb") as f:
            f.write(b"fake")
        return {
            "file_path": p, "title": "测试书", "author": "作者",
            "chapters": 3, "total": 3, "total_chapters": 3,
            "start_chapter": 1, "end_chapter": 3,
        }
    # 插件模块里把 build_txt 换掉（它 import 时绑定了自己的引用）
    monkeypatch.setattr(W, "build_txt", fake_build)
    monkeypatch.setattr(W.Wenku8CookieProbe, "ok", lambda: True)

    d = W.Wenku8Downloader()
    res = d.download("12345", str(tmp_path), "epub", None, title="测试书")
    assert res.success is True, f"不应失败: {res.message}"
    assert res.extra["images_failed"] == 0


