# -*- coding: utf-8 -*-
"""文库8分卷导出回归测试。

核心契约：
1. 按卷分组正确（vol 字段 → 卷，start/end 为 1-based 全局序号）；
2. 三种模式：整本 / 按卷分文件 / 仅所选卷；
3. **正文只抓一次**：全书预取后，多卷导出全部命中缓存，不重复请求正文；
4. 只对文库8生效：番茄/七猫插件没有 volume_mode 分支、对话框不显示分卷面板；
5. 文件命名带卷名与章节范围，正文不丢章。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.wenku8_core import wenku8_volumes as V  # noqa: E402
from plugins import wenku8_downloader_plugin as W  # noqa: E402


def _catalog(vol_chars=(3, 2, 2)):
    """构造带 vol 的目录，共 7 章，3 卷。"""
    cats = []
    names = ["第一卷", "第二卷", "第三卷"]
    idx = 0
    for vi, n in enumerate(vol_chars):
        for j in range(n):
            cats.append({
                "id": str(1000 + idx), "index": idx,
                "title": f"第{idx + 1}章", "vol": names[vi],
                "url": f"https://x.test/{idx}.htm",
            })
            idx += 1
    return cats


def test_group_volumes_splits_by_vol():
    vols = V.group_volumes(_catalog())
    assert [v["name"] for v in vols] == ["第一卷", "第二卷", "第三卷"]
    assert [v["start"] for v in vols] == [1, 4, 6]
    assert [v["end"] for v in vols] == [3, 5, 7]
    assert [v["count"] for v in vols] == [3, 2, 2]
    assert sum(v["count"] for v in vols) == 7


def test_group_volumes_same_name_reappears():
    """同名卷分开出现也应各成一卷（用 start 区分，命名带范围不冲突）。"""
    cats = [
        {"id": "1", "index": 0, "title": "a", "vol": "第一卷"},
        {"id": "2", "index": 1, "title": "b", "vol": "第二卷"},
        {"id": "3", "index": 2, "title": "c", "vol": "第一卷"},
    ]
    vols = V.group_volumes(cats)
    assert len(vols) == 3
    assert [v["name"] for v in vols] == ["第一卷", "第二卷", "第一卷"]


def test_group_volumes_empty_vol_fallback():
    cats = [{"id": "1", "index": 0, "title": "a", "vol": ""}]
    assert V.group_volumes(cats)[0]["name"] == "正文"


def test_select_modes():
    vols = V.group_volumes(_catalog())
    whole = V.select_volumes(vols, V.MODE_WHOLE)
    assert len(whole) == 1 and whole[0]["start"] == 1 and whole[0]["end"] == 7

    per = V.select_volumes(vols, V.MODE_PER_VOLUME)
    assert len(per) == 3

    sel = V.select_volumes(vols, V.MODE_SELECTED, ["第二卷"])
    assert len(sel) == 1 and sel[0]["start"] == 4 and sel[0]["end"] == 5

    empty = V.select_volumes(vols, V.MODE_SELECTED, [])
    assert empty == []


def test_filename_template_has_vol_and_range():
    tpl = V.volume_filename_template("{title}", "第一卷{a}")
    # 卷名里的花括号被剔除，避免 str.format 报错；范围占位符保留
    assert "第一卷a" in tpl and "{start}" in tpl and "{end}" in tpl
    from gui.qimao_core import render_filename
    name = render_filename(tpl, "测试书", start=1, end=3)
    assert "测试书" in name and "1-3" in name


def test_text_cache_reused_across_volumes(monkeypatch):
    """正文只抓一次：预取全书后，逐章抓取函数不再被真实调用。"""
    calls = {"n": 0}

    def fake_chapter_text(url_or_id, session=None, novel_prefix="", book_id=""):
        calls["n"] += 1
        return "    正文内容"

    import gui.wenku8_core.wenku8_reader as reader
    monkeypatch.setattr(reader, "fetch_chapter_text", fake_chapter_text)

    source = W.Wenku8BookSource()
    cats = _catalog()
    # 注入目录/详情缓存，避免 fetch_catalog 走网络
    source._catalog_cache["1"] = cats
    source._detail_cache["1"] = {"title": "测试", "author": "作者"}
    source._total = len(cats)

    # 第一次：预取全书，每章各抓一次
    texts1 = source.prefetch_texts(None, cats, None, None)
    assert len(texts1) == 7
    assert calls["n"] == 7

    # 第二次起：模拟分卷导出再抓同样章节，全部命中缓存，抓正文次数不增加
    for vol in V.group_volumes(cats):
        for ch in vol["chapters"]:
            t = source.chapter_text(None, ch, None, None)
            assert t == "    正文内容"
    assert calls["n"] == 7, "分卷导出不得重复抓正文"


def test_plugin_volume_branch_only_for_wenku8():
    """分卷分支只在文库8插件里，番茄/七猫插件源码不含 volume_mode。"""
    for mod_name in (
        "plugins.fanqie_downloader_plugin",
        "plugins.qimao_downloader_plugin",
    ):
        src_path = os.path.join(ROOT, "src", *mod_name.split(".")) + ".py"
        with open(src_path, encoding="utf-8") as f:
            code = f.read()
        assert "volume_mode" not in code, f"{mod_name} 不应含分卷逻辑"
        assert "prefetch_book" not in code, f"{mod_name} 不应含文库8预取"


def test_download_dialog_volume_panel_only_wenku8(monkeypatch):
    """对话框分卷面板仅文库8显示；番茄构造出的对象没有该控件。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.download_dialog import DownloadDialog

    fanqie = DownloadDialog({"id": "1", "title": "F", "source": "fanqie"})
    assert getattr(fanqie, "_volume_group", None) is None
    assert "volume_mode" not in fanqie.options()

    wenku8 = DownloadDialog({"id": "2", "title": "W", "source": "wenku8"})
    assert getattr(wenku8, "_volume_group", None) is not None
    assert wenku8.options().get("volume_mode") == V.MODE_WHOLE
    wenku8.close()
    fanqie.close()
    # 让 Qt 事件循环回收对象，避免进程退出时 Python GC 与 Qt 对象树双重析构
    wenku8.deleteLater()
    fanqie.deleteLater()
    app.processEvents()


# ---------- 严格模式：图和书必须一起成功（挑刺新增） ----------

def test_partial_volume_failure_fails_whole(monkeypatch, tmp_path):
    """任一卷渲染失败（含缺图抛错）→ 整单 success=False，不能部分成功。"""
    d = W.Wenku8Downloader()

    cats = _catalog((2, 2))
    texts = ["    正文A", "    正文B", "    正文C", "    正文D"]

    def fake_prefetch(self, book_id, session, on_progress=None):
        return ({"title": "测试书", "author": "作者", "cover": ""}, cats, texts)

    monkeypatch.setattr(W.Wenku8BookSource, "prefetch_book", fake_prefetch)

    # 让第一卷成功、第二卷抛错（模拟渲染阶段缺图 QimaoError）
    built = {"n": 0}

    def fake_build(**kw):
        built["n"] += 1
        if kw.get("start_chapter", 0) >= 3:
            raise W.QimaoError("插图未能下载")
        p = str(tmp_path / f"vol_{built['n']}.epub")
        with open(p, "wb") as f:
            f.write(b"fake epub")
        return {"file_path": p, "chapters": 2, "total": 4, "title": "测试书"}

    import gui.qimao_core as core
    monkeypatch.setattr(core, "build_txt", fake_build)

    res = d._download_volumes(
        "1", str(tmp_path), "epub", None, {"title": "测试书"},
        V.MODE_PER_VOLUME, None)
    assert res.success is False, "部分卷失败也必须整单失败"
    assert "一起成功" in res.message
    # 已写出的半成品要被清理
    assert not list(tmp_path.glob("vol_*.epub")), "失败时应删除已写出的残缺卷"


def test_missing_image_only_checks_selected_volumes(monkeypatch, tmp_path):
    """只导出第二卷时，未导出卷的缺图不应导致整单失败。"""
    d = W.Wenku8Downloader()

    cats = _catalog((2, 2))  # 第一卷1-2章，第二卷3-4章
    # 第一卷正文含一张缺图，第二卷正文无图
    texts = [
        "[[WENKU8IMG:https://x/missing.jpg]]", "    正文B",
        "    正文C", "    正文D",
    ]

    def fake_prefetch(self, book_id, session, on_progress=None):
        return ({"title": "测试书", "author": "作者", "cover": ""}, cats, texts)

    monkeypatch.setattr(W.Wenku8BookSource, "prefetch_book", fake_prefetch)

    def fake_build(**kw):
        p = str(tmp_path / "vol2.epub")
        with open(p, "wb") as f:
            f.write(b"fake epub")
        return {"file_path": p, "chapters": 2, "total": 4, "title": "测试书"}

    import gui.qimao_core as core
    monkeypatch.setattr(core, "build_txt", fake_build)

    res = d._download_volumes(
        "1", str(tmp_path), "epub", None, {"title": "测试书"},
        V.MODE_SELECTED, ["第二卷"])
    assert res.success is True, "只导出第二卷时，第一卷的缺图不应阻塞"


# ---------- 挑刺 #121：选卷名容错匹配（用户「分卷导出有可能会失败」） ----------

def test_select_volumes_fuzzy_match_suffix():
    """勾选名与实际卷名有上下册/空格差异时要能命中，不能「未选择任何卷」。"""
    vols = [
        {"name": "第一卷 上", "start": 1, "end": 10},
        {"name": "第二卷 下", "start": 11, "end": 20},
        {"name": "短篇", "start": 21, "end": 25},
        {"name": "杂志短篇", "start": 26, "end": 30},
    ]
    # 勾「第二卷」应命中「第二卷 下」（子串容错）
    assert [v["name"] for v in V.select_volumes(vols, V.MODE_SELECTED, ["第二卷"])] == ["第二卷 下"]
    # 精确存在时优先精确：「短篇」不应误带出「杂志短篇」
    assert [v["name"] for v in V.select_volumes(vols, V.MODE_SELECTED, ["短篇"])] == ["短篇"]
    # 空格差异也能命中
    assert len(V.select_volumes(vols, V.MODE_SELECTED, ["第 二 卷 下"])) == 1
    # 完全不存在返回空
    assert V.select_volumes(vols, V.MODE_SELECTED, ["不存在的卷"]) == []


def test_select_volumes_exact_still_works():
    """原本的精确匹配行为不能破坏（真实卷名勾真实名）。"""
    vols = [
        {"name": "第一卷", "start": 1, "end": 10},
        {"name": "第二卷", "start": 11, "end": 20},
    ]
    r = V.select_volumes(vols, V.MODE_SELECTED, ["第二卷"])
    assert [v["name"] for v in r] == ["第二卷"]
