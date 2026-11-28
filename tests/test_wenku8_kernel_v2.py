# -*- coding: utf-8 -*-
"""文库8 v2 内核回归：指纹网络层 + 全站目录 + 搜索参数纠偏。

这些用例锁定本机诊断报告里的三条硬结论：
  1. 带 charset=gbk 的搜索会被拦，所有通道必须移除该参数；
  2. 搜索单结果会 302 到详情页，必须解析主书而非相关推荐；
  3. 随包全站目录能按括号别名命中任意书，并自带完整元数据。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.wenku8_core import wenku8_http as http  # noqa: E402
from gui.wenku8_core import wenku8_index as idx  # noqa: E402
from gui.wenku8_core import wenku8_search as search  # noqa: E402


def test_all_search_channels_drop_charset_param():
    assert search._SEARCH_ENDPOINTS
    assert all("charset" not in tpl for tpl in search._SEARCH_ENDPOINTS)

    class _Resp:
        content = b""
        encoding = None
        text = ""
        url = ""

    class _Sess:
        def __init__(self):
            self.body = None

        def post(self, url, data=None, **kwargs):
            self.body = data
            return _Resp()

    sess = _Sess()
    search._search_via_so_post("紫罗兰", sess, "https://www.wenku8.cc")
    assert sess.body is not None
    assert b"charset" not in sess.body


def test_bundled_catalog_matches_alias_with_full_metadata():
    assert os.path.exists(idx.catalog_path()), "全站目录 JSON 必须随包分发"
    assert "2353" in idx.search_index("紫罗兰")
    book = idx.search_index_book("2353")
    assert book is not None
    assert "紫罗兰永恒花园" in book["title"]
    assert book["author"]
    assert book["tags"]
    assert book["cover"].startswith("https://")


def test_redirected_detail_page_parses_main_book():
    html = (
        "<html><title>薇尔莉特·伊芙加登(紫罗兰永恒花园) - 晓佳奈 - "
        "其他文库 - 轻小说文库</title>"
        '<a href="/book/999.htm">相关推荐</a></html>'
    )
    book = search._extract_detail_result(
        html, "https://www.wenku8.net",
        "https://www.wenku8.net/book/2353.htm",
    )
    assert book is not None
    assert book["id"] == "2353"
    assert "紫罗兰永恒花园" in book["title"]
    assert book["author"] == "晓佳奈"


def test_cloak_response_decodes_gbk_after_encoding_set():
    class _Raw:
        status_code = 200
        url = "https://www.wenku8.cc/"
        headers = {}
        reason = "OK"
        content = "轻小说文库".encode("gbk")

    resp = http.CloakResponse(_Raw())
    resp.encoding = "gbk"
    assert resp.ok
    assert resp.text == "轻小说文库"


def test_make_session_uses_fingerprint_when_available():
    if http.network_mode() != "httpcloak":
        pytest.skip("httpcloak 未安装，当前环境走 requests 兼容通道")
    from gui.wenku8_core.wenku8_auth import make_session

    sess = make_session("jieqiUserInfo=uid%3D7", base="https://www.wenku8.cc")
    assert hasattr(sess, "_session"), "应返回 httpcloak 指纹会话"
    assert sess.cookies.get_dict().get("jieqiUserInfo") == "uid%3D7"


def test_build_spec_collects_httpcloak_native_lib():
    """挑刺 #49：httpcloak 原生库不在 package_data 里，必须显式收集。

    collect_data_files('httpcloak') 实测返回 0 项；若 build.spec 只靠它，
    打包后的 exe 会因找不到 libhttpcloak-*.dll 而静默回退裸 requests，
    导致指纹内核在发布版完全失效。这里锁定 spec 里的显式收集规则。
    """
    spec_path = os.path.join(ROOT, "build.spec")
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = f.read()
    assert '"lib"' in spec, "build.spec 必须含 httpcloak/lib 目标目录"
    assert "httpcloak/lib" in spec, "必须把原生库收集到 httpcloak/lib"
    # 确认不是只有 collect_data_files 一句（那是已证伪的做法）
    assert 'datas.append((_hc_lib, "httpcloak/lib"))' in spec


def test_selftest_report_passes_with_working_httpcloak():
    """自检在指纹通道可用时必须报 PASS 且 network_mode=httpcloak。"""
    report = http.selftest_report()
    assert report["httpcloak_imported"] == "True"
    assert report["session_ok"] == "True"
    assert report["network_mode"] == "httpcloak"
    assert report["result"] == "PASS"
    assert report["native_libs"], "报告必须列出检测到的原生库文件名"


def test_selftest_report_fails_when_httpcloak_missing(monkeypatch):
    """核心：模拟发布版漏收原生库时，自检必须报 FAIL。

    CI 依赖这个 FAIL 让构建红灯。若 httpcloak 模块不可用，network_mode
    必然回退 requests，result 必须是 FAIL。
    """
    monkeypatch.setattr(http, "_hc", None)
    monkeypatch.setattr(http, "_MODE_CACHE", None)
    report = http.selftest_report()
    assert report["network_mode"] == "requests"
    assert report["result"] == "FAIL"


def test_selftest_report_fails_when_native_lib_missing(monkeypatch):
    """模拟 httpcloak 已导入但原生库加载失败（打包漏 lib 的典型症状）。

    构造 CloakSession 抛异常时 session_ok=False，network_mode 回退 requests，
    自检必须 FAIL——这正是 #49 要拦截的场景。
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("libhttpcloak-*.dll not found")

    monkeypatch.setattr(http, "CloakSession", _boom)
    monkeypatch.setattr(http, "_MODE_CACHE", None)
    report = http.selftest_report()
    assert report["session_ok"] == "False"
    assert "not found" in report.get("error", "")
    assert report["network_mode"] == "requests"
    assert report["result"] == "FAIL"


def test_entry_has_selftest_branch_and_ci_guards_it():
    """入口必须先于 GUI 导入分发自检；CI 必须用打包出的 exe 调用它。"""
    with open(os.path.join(ROOT, "启动GUI.py"), encoding="utf-8") as f:
        entry = f.read()
    assert "--wenku8-selftest" in entry
    assert "_run_selftest" in entry
    # 自检分支必须出现在 gui.main 导入之前，否则无显示环境会误判
    assert entry.index("--wenku8-selftest") < entry.index("from gui.main import main")

    wf = os.path.join(ROOT, ".github", "workflows", "Build Executables.yml")
    with open(wf, encoding="utf-8") as f:
        workflow = f.read()
    assert "--wenku8-selftest" in workflow
    assert "network_mode=httpcloak" in workflow
    assert "Start-Process" in workflow  # console=False 必须等待 exe 退出
    assert "exit 1" in workflow  # 自检失败必须让构建红灯
