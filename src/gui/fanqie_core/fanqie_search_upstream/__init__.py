# -*- coding: utf-8 -*-
"""番茄搜索上游隔离包（签名 a_bogus + 搜索字体映射 SEARCH_MAP）。

收录来源（原样保留，便于同步）：
  - https://github.com/yanygm/FQWeb  (abogus.py / searchmap.py)
原算法出处：amxsa/douyin_abogus_python、fanqienovel-decryptor。

本包不依赖全局 config，导入不会污染 sys.modules，包装层见 fanqie_search.py。
"""
