# 番茄内核上游实现·同步记录

- 上游仓库：https://github.com/xiaocse/Fanqie-novel-Downloader
- 许可证：MIT
- 同步分支：main
- 当前 commit：`1df475da3ef81b45571a7640506efc334bcb3c79`
- 提交时间：2025-06-01T12:53:57Z
- 提交说明：同步内容
- 最近同步：2026-11-25 12:30:13 UTC

## 同步方式

```bash
python3 src/gui/fanqie_core/fanqie_upstream/sync_upstream.py
```

脚本只覆盖 `request_handler.py` / `config.py`，适配层 `__init__.py` 和 `../fanqie_downloader_plugin.py` 不会被修改。

## 目录约定

- `request_handler.py`：上游原样核心接口（书籍信息 / 目录 / 正文）。
- `config.py`：上游原样配置（请求超时、重试、UA）。
- `__init__.py`：导入适配（不污染全局 `config` 模块名）。
- 本目录任何上游文件都不要手工修改；需要改动请改适配层。

## 已知风险与兜底

上游 `down_text()` 指向第三方公开 API（`https://fanqie.tutuxka.top/`），并非番茄官方接口。实测该域名 DNS 已无解析，正文无法获取。

因此适配层默认使用 `../fanqie_reader.py`：从番茄官网读者页 `window.__INITIAL_STATE__` 提取正文，解析 woff2 字体的 glyph gid 并用内置 `FONT_MAP` 还原被 PUA 替换的汉字。映射表来源：`zhoulianglen/fanqiexiaoshuo-Download`（其源自 `romcere/fanqienovel-decryptor` / RChow 博客），按字体 URL 缓存，不依赖第三方 API。

若上游日后修复正文接口，可在 DataStore 设置 `fanqie_text_backend=upstream` 切回上游实现；书籍信息与目录仍始终取自上游原样代码，随本目录同步自动更新。
