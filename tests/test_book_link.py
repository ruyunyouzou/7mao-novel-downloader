# -*- coding: utf-8 -*-
"""书链接解析回归测试。

运行：
    python3 tests/test_book_link.py          # 纯解析测试（无需网络，秒级）
    python3 tests/test_book_link.py --net    # 追加真实网络归一化测试

覆盖番茄 page / reader / 移动端 / 无 scheme / 口令 / query / discovery /
shares / 纯 ID 等全形态，以及七猫原有链接的零回归。
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.book_link import detect_source, extract_book_id  # noqa: E402

BOOK = "7546406625741917209"
CHAP = "7546406778368442905"
TITLE = "无限掠夺，从斩妖除魔开始成神"


class ParseTestCase(unittest.TestCase):
    """纯解析：不依赖网络。"""

    FANQIE = [
        ("page 标准", f"https://fanqienovel.com/page/{BOOK}", BOOK),
        ("page 带参数", f"https://fanqienovel.com/page/{BOOK}?enter_from=share", BOOK),
        ("reader 章节", f"https://fanqienovel.com/reader/{CHAP}", CHAP),
        ("m 移动端", f"https://m.fanqienovel.com/page/{BOOK}", BOOK),
        ("www 前缀", f"https://www.fanqienovel.com/page/{BOOK}", BOOK),
        ("无 scheme", f"fanqienovel.com/page/{BOOK}", BOOK),
        ("口令文本", f"8.86 复制打开番茄小说，看看《{TITLE}》 https://fanqienovel.com/page/{BOOK} 复制此链接", BOOK),
        ("query id", f"https://fanqienovel.com/page?id={BOOK}", BOOK),
        ("query book_id", f"https://fanqienovel.com/page?book_id={BOOK}", BOOK),
        ("query item_id", f"https://fanqienovel.com/detail?item_id={BOOK}", BOOK),
        ("纯数字书 ID", BOOK, BOOK),
        ("纯数字章节 ID", CHAP, CHAP),
        ("discovery", f"https://fanqienovel.com/discovery/book/{BOOK}", BOOK),
        ("shares", f"https://fanqienovel.com/shares/share/{BOOK}", BOOK),
        ("chapter 路径", f"https://fanqienovel.com/chapter/{CHAP}", CHAP),
        ("多级子域", f"https://a.b.fanqienovel.com/page/{BOOK}", BOOK),
    ]

    QIMAO = [
        ("纯数字", "12345678", "12345678"),
        ("shares", "https://m.qimao.com/shares/12345678?x=1", "12345678"),
        ("book 页", "https://www.qimao.com/book/12345678", "12345678"),
        ("APP 短链", "https://app.share.wztxw.com/app-h5/freebook/article_detail/214789", "214789"),
        ("query", "https://www.qimao.com/book?id=87654321", "87654321"),
    ]

    def test_fanqie(self):
        for name, text, expect in self.FANQIE:
            with self.subTest(name=name):
                self.assertEqual(detect_source(text), "fanqie", name)
                self.assertEqual(extract_book_id(text), expect, name)

    def test_qimao_regression(self):
        for name, text, expect in self.QIMAO:
            with self.subTest(name=name):
                self.assertEqual(detect_source(text), "qimao", name)
                self.assertEqual(extract_book_id(text), expect, name)

    def test_fanqie_short_link(self):
        """番茄短链域名识别 + book-share query 提取（无需网络）。"""
        cases = [
            ("短链 detect_source",
             "https://changdunovel.com/t/4NZeXnfTfY/"),
            ("口令内短链 detect_source",
             "推荐一部好书《邻家妹妹恋上我》https://changdunovel.com/t/4NZeXnfTfY/"),
        ]
        for name, text in cases:
            with self.subTest(name=name):
                self.assertEqual(detect_source(text), "fanqie", name)
        # book-share 形态的 book_id 可直接提取
        self.assertEqual(
            extract_book_id(
                "https://changdunovel.com/ug/pages/book-share"
                "?share_type=11&aid=1967&book_id=7423591956359416856"),
            "7423591956359416856")

    def test_book_title_extract(self):
        """口令文本书名提取，供短链失效时按书名回退。"""
        from gui.book_link import extract_book_title
        text = "推荐一部好书《邻家妹妹恋上我》https://changdunovel.com/t/4NZeXnfTfY/"
        self.assertEqual(extract_book_title(text), "邻家妹妹恋上我")
        self.assertEqual(extract_book_title("无书名 https://x.com"), "")


@unittest.skipUnless("--net" in sys.argv, "需要 --net 参数，跳过真实网络测试")
class ResolveTestCase(unittest.TestCase):
    """真实网络：章节 ID 反查 + 端到端书名。"""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src", "plugins", "vendor"))
        from fanqie_reader import resolve_book_id
        self.resolve = resolve_book_id

    def test_resolve(self):
        for value in (BOOK, CHAP,
                      f"https://fanqienovel.com/page/{BOOK}",
                      f"https://fanqienovel.com/reader/{CHAP}",
                      f"https://m.fanqienovel.com/reader/{CHAP}",
                      f"8.86 复制打开番茄小说，看看《{TITLE}》 https://fanqienovel.com/reader/{CHAP}"):
            with self.subTest(value=value[:50]):
                self.assertEqual(self.resolve(value, timeout=25), BOOK)

    def test_end_to_end(self):
        from plugins.vendor.fanqie_upstream import RequestHandler
        for text in (f"https://fanqienovel.com/page/{BOOK}",
                     f"https://fanqienovel.com/reader/{CHAP}",
                     CHAP):
            with self.subTest(text=text[:50]):
                raw_id = extract_book_id(text)
                real_id = self.resolve(raw_id, timeout=25)
                name, _author, _intro = RequestHandler().get_book_info(real_id)
                self.assertEqual(real_id, BOOK)
                self.assertIn(TITLE, str(name))


if __name__ == "__main__":
    argv = [a for a in sys.argv if a != "--net"]
    unittest.main(argv=argv, verbosity=2)
