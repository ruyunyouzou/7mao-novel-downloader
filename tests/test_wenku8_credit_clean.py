# -*- coding: utf-8 -*-
"""文库8录入组「卷首信息 / 转载信息」清洗回归测试。

契约：
1. 真实卷首信息块（第1卷 / 第6卷 / 网络完整模板）整块删除；
2. 全书通用——规则只认字段标签与录入组标记，不绑定书名 / 人名；
3. 绝不误删正文——普通章、单行「作者：」、无标记的字段行、章节中间的块都不动。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gui.wenku8_core import wenku8_reader as R  # noqa: E402


def test_strip_real_vol1_credit_block():
    """真实第1卷第1话开头的录入组块整块删除，正文完整保留。"""
    lines = [
        "    台版 转自 轻之国度",
        "    图源：轻之国度录入组",
        "    扫图：撸管娘",
        "    录入：kid",
        "    修图：撸管娘",
        "    「你在干嘛？」",
        "    藤宫周和她第一次说话。",
    ]
    assert R.strip_credit_block(lines) == [
        "    「你在干嘛？」",
        "    藤宫周和她第一次说话。",
    ]


def test_strip_real_vol6_credit_block():
    """用户截图的第6卷块（轻之国度×天使动漫录入组）整块删除。"""
    lines = [
        "    台版 转自 轻之国度",
        "    轻之国度×天使动漫录入组",
        "    图源：音无",
        "    扫图：linpop",
        "    录入：勤奋的懒惰的羊",
        "    修图：轻之国度录入组",
        "    耳中听到某处传来的鸟鸣声。",
    ]
    assert R.strip_credit_block(lines) == ["    耳中听到某处传来的鸟鸣声。"]


def test_strip_full_template_with_disclaimer():
    """网络通行完整模板（分隔线 + 免责声明）整块删除。"""
    lines = [
        "    ────────────────────────────",
        "    轻之国度录入组录入",
        "    作者：镜游",
        "    插画：MIKEOU",
        "    译者：xxx",
        "    图源：终焉之月",
        "    扫图：方俱灭",
        "    录入：方俱灭",
        "    修图：lulu男",
        "    轻之国度：http://www.lightnovel.cn",
        "    仅供个人学习交流使用，禁作商业用途",
        "    下载后请在24小时内删除，LK不负担任何责任",
        "    请尊重翻译、扫图、录入、校对的辛勤劳动，转载请保留信息",
        "    本文特别严禁转载至SF轻小说频道和轻小说文库",
        "    所录入的每一本书里，扫图者有很大一部分功劳！",
        "    ────────────────────────────",
        "    第一章 正文开始。",
    ]
    assert R.strip_credit_block(lines) == ["    第一章 正文开始。"]


def test_strip_credit_with_blank_lines():
    """块内夹空行也能整块删除。"""
    lines = [
        "    台版 转自 轻之国度",
        "",
        "    图源：音无",
        "    扫图：linpop",
        "",
        "    录入：勤奋的懒惰的羊",
        "    修图：轻之国度录入组",
        "    正文。",
    ]
    assert R.strip_credit_block(lines) == ["    正文。"]


def test_normal_chapter_unchanged():
    """普通章节正文开头不误删。"""
    lines = [
        "    「周，你鼻子好吵。」",
        "    「你才吵。」",
        "    结果第二天，感冒的人是周。",
    ]
    assert R.strip_credit_block(lines) == lines


def test_single_author_line_not_removed():
    """正文里偶然的单行「作者：xxx」不触发删除。"""
    lines = ["    作者：我", "    今天天气很好。"]
    assert R.strip_credit_block(lines) == lines


def test_fields_without_credit_mark_not_removed():
    """只有字段、没有录入组标记的块一律不删——宁可漏删，绝不误删正文。"""
    lines = [
        "    作者：我",
        "    图源：网络",
        "    扫图：某人",
        "    修图：某人",
        "    正文开始。",
    ]
    assert R.strip_credit_block(lines) == lines


def test_credit_block_mid_chapter_not_removed():
    """块出现在章节中间时不处理——只在章首前 12 行识别。"""
    lines = ["    正文第一行。"] * 20 + [
        "    台版 转自 轻之国度",
        "    图源：音无",
        "    扫图：linpop",
    ]
    assert R.strip_credit_block(lines) == lines


def test_image_marker_preserved():
    """图片标记不是信息行，不会被顺带删掉。"""
    lines = [
        "    台版 转自 轻之国度",
        "    图源：轻之国度录入组",
        "    扫图：撸管娘",
        "    录入：kid",
        "    [[WENKU8IMG:https://x.test/a.jpg]]",
    ]
    assert R.strip_credit_block(lines) == [
        "    [[WENKU8IMG:https://x.test/a.jpg]]"]


def test_credit_block_at_very_start_only():
    """块前面若有空行，跳过空行后仍能识别。"""
    lines = [
        "",
        "    台版 转自 轻之国度",
        "    图源：音无",
        "    扫图：linpop",
        "    录入：abc",
        "    正文。",
    ]
    assert R.strip_credit_block(lines) == ["", "    正文。"]


def test_strip_baidu_tieba_credit_block():
    """百度贴吧系：全角斜杠字段（扫瞄／校对／首发）+ 网译版转自标记。"""
    lines = [
        "    网译版 转自 百度贴吧",
        "    翻译／YUI 汐未 Schalke04",
        "    校对／汐未",
        "    扫瞄／BErind",
        "    修图／理工科的千反田",
        "    首发／百度京蜜京蜜京吧",
        "    「自动书记人偶」。",
    ]
    assert R.strip_credit_block(lines) == ["    「自动书记人偶」。"]


def test_strip_bahamut_minimal_credit_block():
    """巴哈姆特精简块：只有 1 个字段 + 转自标记也要删。"""
    lines = [
        "    网译版 转自 巴哈姆特",
        "    翻译：只成一事",
        "    ·人物介绍",
    ]
    assert R.strip_credit_block(lines) == ["    ·人物介绍"]


def test_strip_hanhuazu_credit_block():
    """汉化组署名块：无「转自」，靠汉化组名当标记。"""
    lines = [
        "    翻译：大括号不换行汉化组",
        "    校对：大括号不换行汉化组",
        "    真昼的心情不太好。",
    ]
    assert R.strip_credit_block(lines) == ["    真昼的心情不太好。"]
