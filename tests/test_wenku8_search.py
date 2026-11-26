# -*- coding: utf-8 -*-
"""文库8搜索修复单元测试：归一化模糊匹配 / 相关性 / 页码 / 站内解析。

全部离线、无网络依赖，严格验证 v5.0.7 的 A′~D′ 逻辑正确性。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.wenku8_core.wenku8_match import (  # noqa: E402
    normalize, to_halfwidth, terms_of, matches, relevance,
)


class TestNormalize:
    def test_halfwidth(self):
        assert to_halfwidth("ＡＢＣ１２３") == "ABC123"
        assert to_halfwidth("　") == " "

    def test_strip_punctuation(self):
        assert normalize("刀剑・神域") == normalize("刀剑神域")
        assert normalize("邻家的天使大人！") == normalize("邻家的天使大人")

    def test_drop_filler(self):
        # 关键：去「的」后「邻家天使」==「邻家的天使」
        assert normalize("邻家的天使") == "邻家天使"
        assert normalize("邻家天使") == "邻家天使"
        assert normalize("天地之玄") == "天地玄"

    def test_case_insensitive(self):
        assert normalize("ONE piece") == normalize("one piece")

    def test_terms_and(self):
        assert terms_of("川原 冒险") == ["川原", "冒险"]
        assert terms_of("  邻家  天使 ") == ["邻家", "天使"]


class TestMatches:
    TITLE = "关于我在无意间被隔壁的天使变成废柴这件事 (关于邻家的天使大人不知不觉把我惯成了废人这档子事)"

    def test_user_case_neighbor_angel(self):
        # 用户本次查询：少一个「的」，旧版纯子串必然漏掉
        assert matches("邻家天使", self.TITLE)

    def test_subsequence_fallback(self):
        assert matches("邻家天使大人", self.TITLE)
        assert matches("废柴天使", self.TITLE)  # 非连续但按序

    def test_single_char_no_false_positive_on_other_field(self):
        # 单字不会因子序列兜底误命中无关标题
        assert not matches("雪", self.TITLE)

    def test_multi_term_and(self):
        assert matches("邻家 天使", self.TITLE)
        assert not matches("邻家 雪", self.TITLE)

    def test_author_and_summary(self):
        assert matches("佐伯", "", author="佐伯さん", summary="")
        assert matches("藤宫", "", summary="藤宫周的故事")

    def test_empty_query_matches_all(self):
        assert matches("", self.TITLE)

    def test_fullwidth_query(self):
        assert matches("邻家天使", "邻家的天使")
        assert matches("Ｄｉｓｎｅｙ", "Disney")


class TestRelevance:
    def test_substring_scores_higher_than_subsequence(self):
        assert relevance("邻家天使", "邻家的天使") >= 2
        assert relevance("邻家天使", "邻X家Y天Z使") >= 1

    def test_sort(self):
        items = [("邻家的天使",), ("完全无关的书",)]
        items.sort(key=lambda x: -relevance("邻家天使", x[0]))
        assert items[0][0] == "邻家的天使"


class TestSearchStructure:
    def test_endpoints_cover_both_params(self):
        from gui.wenku8_core.wenku8_search import _SEARCH_ENDPOINTS
        joined = " ".join(_SEARCH_ENDPOINTS)
        assert "searchkey" in joined and "keyword" in joined
        assert "search.php" in joined and "so.php" in joined

    def test_search_wenku8_accepts_page(self):
        import inspect
        from gui.wenku8_core.wenku8_search import search_wenku8
        params = inspect.signature(search_wenku8).parameters
        assert "page" in params
        assert params["max_pages"].default >= 8  # 扩大覆盖

    def test_dynamic_base_candidates(self):
        from gui.wenku8_core.wenku8_auth import _BASE_CANDIDATES, get_base_url
        assert any(".net" in b for b in _BASE_CANDIDATES)
        assert any(".cc" in b for b in _BASE_CANDIDATES)
        assert get_base_url().startswith("https://")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
