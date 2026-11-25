# 待发版 Bug 清单

> **发版约定**：每完成 **10** 次修改才发布一个新版本（2026-11-25 用户明确要求，由原「5 个 bug」调整为「10 次修改」）。
> 每次改完先提交到 `main`，在此登记；累计满 10 条才升版本号 + 打 tag + 构建发布。
> 规则详见《项目结构与功能说明》15.2.1 节。

## 当前状态

- **基线版本**：v5.0.0（已发布，2026-11-25，Windows 安装包已上传 GitHub Release）
- **已修未发版**：**2 / 10**（新周期从 v5.0.0 之后重新计数；用户要求先不发版）
- **下一个待发布版本**：v5.0.1（满 10 条或用户明确发版指令才启用）
- **对齐补充**：微信读书两页横条对齐（B+C 方案）为用户追加项，另行登记于下方第 11 条，不计入本轮 10 条
- **圆滑补充**：微信读书进度条/柱状图圆滑化（选定方案 B）为用户追加项，另行登记于下方第 12 条，不计入本轮 10 条
- **阅读器补充**：阅读器专注模式退出入口 + EPUB 章节名 + 字号即时生效修复为用户追加项，另行登记于下方第 15 条，不计入本轮 10 条
- **转场补充**：退出专注按钮形态定稿（纯金国徽无圆底）+ 进出丝滑转场为用户追加项，另行登记于下方第 17 条，不计入本轮 10 条
- **悬停修复**：微信读书书籍笔记卡片悬停整块消失为用户追加项，另行登记于下方第 13 条，不计入本轮 10 条
- **趋势细化**：划线趋势逐日化 + 历史月份切换（方案 A，口径一）为用户追加项，另行登记于下方第 14 条，不计入本轮 10 条
- **队列/历史改造**：任务队列与历史记录两页由表格改为 B2 任务卡片流（信息行右对齐 + 每项前国徽）为用户追加项，另行登记于下方第 18 条，不计入本轮 10 条
- **进度条圆滑高亮动效**：队列卡片进度条改为自绘 GlowProgressBar（胶囊 + 渐变 + 柔光 + 高光 + 流光动画）为用户追加项，另行登记于下方第 19 条，不计入本轮 10 条
- **代码结构优化**：队列/历史两页卡片重复代码抽取为公共基类与工具（纯重构、行为等价）为用户追加项，另行登记于下方第 20 条，不计入本轮 10 条

## 待发版条目（修一个记一条）

<!-- 格式：
| 编号 | 简述 | 涉及文件 | 修复日期 |
|------|------|----------|----------|
| 1    | ...  | ...      | ...      |
-->

> **本轮 10 条（用户 2026-11-25 要求从 1/10 重新计数，先不发版）**：以下均为上一轮误读「做出 10 次修改发版」指令时实际完成的真实改动，代码已通过测试，现按用户要求正式登记、暂不发版。

| 编号 | 简述 | 涉及文件 | 修复日期 |
|------|------|----------|----------|
| 1 | 搜索页硬编码中文纳入 i18n：推荐标题、详情提示、模式提示、推荐/分页结果提示改为 `tr()`，新增 `search.detail_hint` / `search.result_hint_tpl` / `search.mode_hint` / `search.recommend_result_hint` 等三语键 | `main_window.py`、`i18n.py` | 2026-11-25 |
| 2 | 阅读器听书/对话识别/自动阅读按钮 tooltip 与文案纳入 i18n，自动阅读按钮去掉 emoji、文字随状态在 `reader.auto` / `reader.auto_stop` 间切换，新增 `reader.speak_no_engine` / `reader.dialogue_tip` / `reader.auto_tip` | `reader_view.py`、`i18n.py` | 2026-11-25 |
| 3 | 微信读书同步状态 6 处硬编码中文改为 i18n，部分失败文案支持 `{errors}` 参数：新增 `weread.sync_busy` / `weread.sync_partial` / `weread.need_key` | `weread_view.py`、`i18n.py` | 2026-11-25 |
| 4 | 微信读书笔记页 3 处状态文案纳入 i18n：新增 `notebook.loading` / `notebook.need_key` / `notebook.exporting` | `notebook_view.py`、`i18n.py` | 2026-11-25 |
| 5 | 功能说明占位页窗口标题纳入 i18n：`setWindowTitle` 改用 `common.feature_info` | `limitations_view.py`、`i18n.py` | 2026-11-25 |
| 6 | 账号本地配置弹窗全面 i18n：标题/说明/警告/占位符/表单标签/按钮/状态/消息框全部走 `tr()`，控件存为成员并新增 `retranslate()` 接入 `i18n.changed`，切换语言实时刷新 | `account_dialog.py`、`i18n.py` | 2026-11-25 |
| 7 | 标题栏账号 tooltip、主题 tooltip、版本标签纳入 i18n，新增 `titlebar.account_tip` / `titlebar.theme_tip` / `titlebar.version`，`TitleBar.retranslate()` 接入语言切换信号 | `widgets.py`、`i18n.py` | 2026-11-25 |
| 8 | 升级按钮去掉 ⚡ emoji，改用纳塔国徽图标（`nation_icon("natlan", ...)`），符合全局国徽图标规范 | `update_dialog.py` | 2026-11-25 |
| 9 | 文件选择对话框上次目录跨重启持久化：`filedialogs.py` 新增 `configure_store()` 接入 DataStore，`settings_get/set` 读写 `last_dialog_dir`，主窗口注入 store，下次打开对话框直接定位到上次目录 | `filedialogs.py`、`main_window.py` | 2026-11-25 |
| 10 | i18n 三语键扩充与对齐：本批新增约 45 个三语键，`zh_CN/en_US/ru_RU` 三语完全对齐（718 / 718 / 718），保证上述文案切换语言不缺键 | `i18n.py` | 2026-11-25 |
| 11 | **（用户追加项，不计入本轮 10 条）** 微信读书「阅读偏好 / 书籍划线统计」两页横条对齐改造：选定 B+C 结合方案——名称独立成上排（国徽序号徽章 + 名称左 + 数值右对齐）、进度条在下方通栏铺满；两页共用同一套 `_aligned_bar_row` 构造，条起点/数值列完全一致；序号徽章用 8 国国徽图标铺底、右下角序号角标，前三名高亮；长书名省略号截断 + tooltip 全文 | `weread_view.py` | 2026-11-25 |
| 12 | **（用户追加项，不计入本轮 10 条）** 微信读书进度条/柱状图圆滑化（选定方案 B）：新增自绘 `SoftProgressBar`（胶囊轨道两端全圆角 + inset 阴影凹陷感 + 橙色横向三段渐变 + 柔光外发光，接口兼容 QProgressBar）替换原 7/8px 尖锐进度条；`BarChart` 柱子改顶部 6px 圆角 + 渐变填充、底部贴轴方角；主题切换经 `apply_colors()` 同步重绘 | `weread_view.py` | 2026-11-25 |
| 13 | **（用户追加项，不计入本轮 10 条）** 修复书籍笔记页鼠标悬停卡片时整块变空白消失：`NotebookCard.enterEvent` 原用 `QGraphicsDropShadowEffect` 做投影，在 Qt6 + QScrollArea 网格中动态挂 `QGraphicsEffect` 会让该卡片离屏合成失败、渲染成空白。改为纯 QSS 悬停高亮（橙色描边 + 背景提亮），加 `_hovered` 标志使主题切换时保留悬停态，不再挂任何图形特效 | `notebook_view.py` | 2026-11-25 |
| 14 | **（用户追加项，不计入本轮 10 条）** 划线趋势逐日化 + 历史月份切换（方案 A / 口径一）：后端 `weread_core` 新增 `note_day_counts`，把全量划线按真实日期（本地时区 `YYYY-MM-DD`）聚合，历史月零额外请求；前端 `Sparkline` 重构为复合控件——按所选月真实天数（28/29/30/31）逐日生成序列、醒目星点（外圈光晕 + 描边圆点 + 峰值放大）、国徽 ◀▶ 翻月（到本月时下一月禁用）、主题/语言切换保留当前月；标题改「本月划线趋势」，新增 3 个三语键（721/721 对齐） | `weread_core.py`、`weread_view.py`、`i18n.py` | 2026-11-25 |
| 15 | **（用户追加项，不计入本轮 10 条）** 修复阅读器"所有功能都不管用"的系统性问题：根因是专注模式 `_toggle_focus` 把整个工具栏（含自身）全隐藏且只靠未提示的 Esc 退出，用户看不到任何退出入口、误以为字号/音色试听/听书等功能全失效。①新增常驻悬浮「退出专注」圆钮（选定方案 D：右下角 46px 主题色圆钮 + 白色璃月国徽、无文字，点击旋转一圈后退出；Esc 快捷键只注册一次不累积），新增 `_exit_focus` 统一入口；②重写 `_parse_epub`：按 spine 顺序、跳过 nav/toc 文档，标题按 h1~h6 → `<title>` → 正文首行章节正则 → 第N章 提取真实章名（修复全是"章节"），目录项与正文标题统一带章数，去掉正文顶部重复章名；③`_apply_font` 同步 document 默认字体并即时重渲染当前章（保留滚动位置），字号拖动即时生效；④retranslate 同步悬浮按钮文案，新增 1 个三语键（722/722 对齐） | `reader_view.py`、`i18n.py` | 2026-11-25 |
| 16 | **（用户追加项，不计入本轮 10 条）** 退出专注按钮外观定稿为方案 D + 点击旋转特效：新增 `_SpinExitButton`（带 `spinAngle` 属性的 QPushButton 子类，QPixmap.transformed 旋转国徽 + QPropertyAnimation 360°），按钮改为右下角圆形钮、无文字，`resizeEvent` 定位在状态栏上方；点击先播旋转动画、结束回调退出；另修复目录章节显示 bug：`CHAPTER_RE` 补 `卷X` 分支（原注释称支持"卷一 xxx"但正则要求"第X卷"，导致卷名整段丢失），实测"卷一/卷二"样本现正确拆章 | `reader_view.py` | 2026-11-25 |
| 17 | **（用户追加项，不计入本轮 10 条）** 退出专注按钮形态定稿 + 进出丝滑转场：去掉紫色圆形背景，改为**直接显示金色璃月国徽**（`nation_icon("liyue", None, 40)` 走官方金色 #DEBD6C，透明背景、无文字）；国徽尺寸放大到 40px。`_SpinExitButton` 增加淡入淡出：进入专注时透明度 0→1 + 图标由 0.5 倍放大（OutBack 回弹），退出时旋转一圈 + 透明度 1→0 + 缩小（InCubic），动画结束再恢复工具栏（点击/Esc/工具栏按钮三条退出路径统一走淡出转场）；旋转时长 0.42s、淡入淡出 0.26s。修复 `QPropertyAnimation.finished` 重复 disconnect 告警（改为构造时只连一次、用 opacity 值判断淡入/淡出） | `reader_view.py` | 2026-11-25 |

| 18 | **（用户追加项，不计入本轮 10 条）** 任务队列 / 历史记录两页由 7 列 `QTableWidget` 改为 **B2 任务卡片流**：每条任务/记录一张圆角卡片，卡片左侧是「书名首字」封面位（按用户要求不加国徽），右侧每一项信息（格式/作者/状态或结果/进度/章节/耗时/时间）前都带一个国徽（8 国国徽分工：璃月=格式、纳塔=作者、蒙德=状态、须弥=进度、枫丹=章节、稻妻=耗时、至冬=时间），章节/耗时/时间三项信息行整体右对齐、与上方用细分隔线隔开；状态/结果做彩色胶囊徽章。`_small_nation` 用 lru_cache 缓存小尺寸国徽避免高频刷新重复染色；卡片按 id 复用只更新内容、避免进度刷新闪烁。完整保留原功能：队列并发调度/暂停/继续/取消/重试/右键菜单，历史筛选/重试/导出 CSV/清空/统计胶囊，选中态改为按卡片 id（`_selected_id`）驱动，两页 `retranslate()` 跟随语言切换；style.py 深浅两套 QSS 补齐卡片样式 | `queue_view.py`、`history_view.py`、`style.py` | 2026-11-25 |

| 19 | **（用户追加项，不计入本轮 10 条）** 队列卡片进度条改圆滑 + 高亮 + 动效：新增自绘 `GlowProgressBar` 替换标准 `QProgressBar`——胶囊轨道（深浅主题自适应）、主题色三段横向渐变填充、半透明柔光外发光、顶部高光细带、斜向流光循环扫过动画（`flowPhase` 属性 + QPropertyAnimation，仅 `downloading` 状态启动，其余态静止省资源）、`setValue` 经 `displayValue` 属性动画平滑过渡；`apply_colors()` 跟随主题色/深浅主题，主窗口 `_sync_inline_accent` 加一处 findChildren 同步刷新；`_bar_colors()` 读 `store.settings_get("accent_color")` 取色、无 `_dark_theme` 属性时按调色板亮度兜底判断深浅 | `queue_view.py`、`main_window.py` | 2026-11-25 |

| 20 | **（用户追加项，不计入本轮 10 条，纯重构/行为等价）** 队列 / 历史两页代码结构优化：①新增 `views/_card_base.py`，把两页卡片 60% 重复的骨架（封面位/标题/标签行/状态胶囊/分隔线/右对齐元信息行、`set_selected`/`mousePressEvent`/`contextMenuEvent`）收敛为 `_CardBase` 基类 + `CardChip` 句柄 + `DEFAULT_NATIONS` 国徽映射，`TaskCard`/`HistoryCard` 只保留各自字段与数据填充（两文件合计减少约 210 行重复）；②`_small_nation` 两页各一份 → 提升为 `widgets.nation_pixmap`（lru_cache 128）供全局复用；③`_go_search` 两页各一份 → 提升为 `widgets.goto_search_page`；④`_note_text` 的 `import re` 提到模块顶部。**验证**：44 项逐控件等价性断言全通过（objectName/文本/右对齐与靠左聚拢布局规则/选中态/卡片复用）、历史页渲染逐像素 0 差异、队列页差异仅为流光动画相位、全项目 compileall + pyflakes 0 告警、主窗口导入链正常无循环依赖 | `views/_card_base.py`（新增）、`queue_view.py`、`history_view.py`、`widgets.py` | 2026-11-25 |

| 21 | **（用户追加项，不计入本轮 10 条）** 接入番茄小说下载内核：①上游 `xiaocse/Fanqie-novel-Downloader`（MIT，commit `1df475d`）原样隔离到 `src/plugins/vendor/fanqie_upstream/`，附 `sync_upstream.py` 一键同步 + `UPSTREAM_INFO.md` 记录 SHA，适配层不改上游一行；②`qimao_core` 新增 `BookSource` / `QimaoBookSource`，`build_txt` 把「取详情/目录/章节正文」改为注入式（七猫路径逐行等价），TXT/分章/EPUB/题图尾图/字体颜色/对话着色/章节范围/文件名模板全部共用；③新增 `fanqie_downloader_plugin.py`（priority=50，默认内核仍七猫），`DownloaderBase` 加 `source_key`/`supports()`，`PluginManager.get_downloader(book_info, source)` 按书源自动选核，队列按书的 source 选内核；④链接解析新增番茄域名识别，直达/批量下载传书源并按源拉详情；⑤上游正文依赖的第三方域名 `fanqie.tutuxka.top` 已 DNS 失效，新增 `vendor/fanqie_reader.py`——从官网读者页取正文 + woff2 字体 gid 映射解密（映射表源自 fanqienovel-decryptor），默认走官网，`fanqie_text_backend=upstream` 可切回上游；⑥`requirements.txt` / `build.spec` 补 `fonttools` / `brotli` 保证打包可用。**验证**：真实书《无限掠夺》信息/276 章目录/正文解密全链路通过，前 2 章 TXT+EPUB 端到端下载成功且正文可读、进度回调正常；三路内核自动选择正确、插件加载 0 错误、主窗口离屏构造正常。**版本仍 4.1.6，未打 tag、未上传、未发版** | `qimao_core.py`、`book_link.py`、`search_tools.py`、`main_window.py`、`plugin/base.py`、`plugin/manager.py`、`views/queue_view.py`、`plugins/fanqie_downloader_plugin.py`、`plugins/vendor/*`、`requirements.txt`、`build.spec` | 2026-11-25 |

| 22 | **（用户追加项，不计入本轮 10 条）** 全功能对照检查与缺口修复：①书源落库——`shelf_view.add_entry` / `history_view.add_record` / 历史重试 / 书架右键重新下载均补 `source`（此前丢失，会导致番茄书重试/重下误落七猫内核）；②追更适配——`follow_core.check_one` / `follow_txt` 改为按 `source` 走 `book_source_for()`，`follow_dialog` 的 EPUB 重建注入对应内核、`_load` 记录带 source，TXT 增量追加与 EPUB 重建均支持番茄；③纯数字番茄 ID（13~24 位）自动识别书源；④协议自检扩展为七猫搜索/目录 + 番茄目录双路，结果标注哪路异常；⑤修复取消异常被内核通用 `except Exception` 吞掉导致任务误判「失败」的既有缺陷——取消异常真源移到 `plugin.base.DownloadCancelled`，`DownloadWorker` 重试循环与两个内核均 re-raise。**验证**：真实番茄书追更（远端 276 章 + 成功追加 1 章）、取消信号正确发出、双路协议自检全绿、书源落库后自动选番茄内核、全量编译/pyflakes/三语 722 对齐/离屏主窗口 14 页全通过。**版本仍 4.1.6，未打 tag、未上传、未发版** | `plugin/base.py`、`workers.py`、`qimao_core.py`、`follow_core.py`、`follow_dialog.py`、`book_link.py`、`views/shelf_view.py`、`views/history_view.py`、两个真实内核插件 | 2026-11-25 |

| 23 | **（用户追加项，不计入本轮 10 条）** 自实现番茄关键词搜索 + 三轮全功能复查：①自写番茄搜索——签名 `a_bogus` 纯 Python 实现（移植自 amxsa/FQWeb，依赖 gmssl）隔离到 `vendor/fanqie_search_upstream/`，`vendor/fanqie_search.py` 调官方 `/api/author/search/search_book/v1` 并用搜索专用 `SEARCH_MAP` 解码 PUA（与正文 `FONT_MAP` 不同，勿混用），输出与七猫搜索同构；②搜索栏加「七猫/番茄」书源下拉框，`SearchWorker`/`_start_search`/分页全链路透传 source，番茄详情直接复用已解码搜索结果（避免七猫接口误查与 PUA 残留），三语各加 3 键；③修复快速连续搜索（换书源/连点）时 `CoverLoader` 已被 deleteLater 回收却又被 `dispose()` 访问导致的 `RuntimeError` 崩溃（用 shiboken isValid 校验 + 捕获 RuntimeError）；④`requirements.txt`/`build.spec` 补 `gmssl` 与 `collect_submodules("plugins")` 保证打包可用。**三轮复查**：第一轮静态审查全入口/详情/内核/落库链路；第二轮真实网络端到端（搜索→详情→目录→正文→TXT/EPUB 下载→追更→取消→协议自检→书源落库→GUI 搜索链路）；第三轮 GUI 14 页切换/主题/三语/编译/pyflakes。**版本仍 4.1.6，未打 tag、未上传、未发版** | `plugins/vendor/fanqie_search.py`、`plugins/vendor/fanqie_search_upstream/*`、`workers.py`、`main_window.py`、`book_card.py`、`i18n.py`、`requirements.txt`、`build.spec` | 2026-11-25 |

| 24 | **（用户追加项，不计入本轮 10 条）** 第四轮严格复查（AST 穷举全部入口 + 冷门功能/边界实测）：新发现并修复 2 处书源丢失缺口——①`follow_dialog._follow_next` 追更成功回写 `follow_upsert` 时未写 `source`，而 `follow_core.check_one/follow_txt` 按 `rec["source"]` 选章节源，番茄书追更一次后 source 变空、下次回退七猫串源；补全 source 回写并实测两轮追更 source 保持。②`datastore.bookmark_add` 只存固定字段不存 source，书签点击走 `_load_book_by_id(bid)` 靠 `detect_source` 推断；补存 source 并在 `search_tools` 书签 chip 回调透传，避免长/短番茄 ID 误判。边界验证：番茄搜索空结果/分页无重复/网络失败抛异常、下载暂停→继续→完成、七猫内核下载回归、统计/说明书三级导航/微信读书/插件页构造、PyInstaller `collect_submodules("plugins")` 实际枚举 19 模块含全部番茄 vendor 子模块。**版本仍 4.1.6，未打 tag、未上传、未发版** | `follow_dialog.py`、`datastore.py`、`search_tools.py` | 2026-11-25 |

| 25 | **（用户追加项，不计入本轮 10 条）** 番茄全形态链接解析加固，做到像七猫那样「粘贴即直达」：①根因定位——番茄 `/reader/{章节ID}` 里的 ID 是**章节 ID**，直接拼 `/page/{id}` 实测返回 HTTP 404、`page.bookId` 为空；旧解析器只提取数字就当书 ID，章节链接/口令里的 reader 链接必然直达失败。②`book_link.py` 纯解析扩展——番茄改为任意子域（`m./www./任意 *.fanqienovel.com`）+ 任意路径层级（`page/reader/book/chapter/discovery/book/shares/share/detail` 等）抓 host 后第一个独立长数字，新增 `item_id/itemId/chapter_id/chapterId/book-id` 等 query key，支持无 scheme 与口令文本。③`vendor/fanqie_reader.py` 新增 `resolve_book_id()`（lru_cache 64）——先按 bookId 请求 `/page/{id}`，`page.bookId` 非空即返回；未命中则请求 `/reader/{id}`，从 `reader.chapterData.bookId` 反查真实书籍 ID。④`main_window._book_from_id` 番茄分支统一归一化，用归一化后的 ID 作为 book id，直达/批量下载/口令/纯数字全链路覆盖，且不动上游源码（同步机制不受影响）。**验证**：纯解析 21/21（16 番茄形态 + 5 七猫回归）、真实网络归一化 6/6（bookId/章节 ID/page/reader/m 站/口令）、端到端 7/7（7 种链接均拿到正确书名《无限掠夺，从斩妖除魔开始成神》与 ID）、py_compile 通过、七猫零回归。**版本仍 4.1.6，未打 tag、未上传、未发版** | `book_link.py`、`plugins/vendor/fanqie_reader.py`、`main_window.py` | 2026-11-25 |

| 26 | **（用户追加项，不计入本轮 10 条）** 全项目全面体检：覆盖 93 文件 / 3.3 万行 / 全部功能 / 全部代码 / 全部接口。①静态检查全绿——compileall 通过、pyflakes 0 告警、三语 725/725/725 对齐无空值、90 模块逐个导入（仅上游 `request_handler.py` 直导报错属预期，须经隔离包装加载）、隔离包装正常、`collect_submodules` 19 模块含 10 番茄子模块。②GUI 实测——主窗口构造 OK、14 页全切换 OK、深浅主题切换 OK、三语切换 OK。③插件内核——8 插件全加载 0 错误、内核自动选择矩阵正确、`DownloadCancelled` 真源可导入。④接口实测（真实网络）——七猫搜索 11 本/详情/目录 712 章/TXT 19126 字节/EPUB 有效；番茄搜索 10 本/详情/目录 276 章/正文解密可读/TXT 26190 字节/EPUB 有效/章节 ID 归一化正确/追更 276/2→新增 274。⑤数据存储——书架/历史/书签/追更四类 source 透传全验证正确。⑥辅助——CSV 导出、build_stats 九项指标、normalize_notebook_book 嵌套提升、协议自检双路均衡、filedialogs 原生+目录记忆、sync_upstream 就位、取消链路 raise+2 捕获+2 内核 re-raise 严谨。⑦边界——空/坏输入、空结果、超页码均不崩。⑧收尾——清理 6 core 转储（529MB）+175 .pyc+12 __pycache__、清除测试脏数据保留真实数据、回归 4/4 通过。**结论：无遗留缺陷，版本仍 4.1.6，未打 tag、未上传、未发版** | `（全项目体检，无功能改动）` | 2026-11-25 |

| 27 | **（用户追加项，不计入本轮 10 条）** 修复启动时的终端黑框 / 两个一闪而过的空白框：用户从桌面快捷方式打开软件、且本机装了 Calibre，启动瞬间先弹黑框与两个空白框再进主界面。根因是 Windows 下 `subprocess` 调控制台程序未传 `CREATE_NO_WINDOW`——启动时 `CalibreView.__init__` 同步 `_detect()` → `detect_calibre()` → `subprocess.run([calibredb, "--version"])` 必然弹控制台窗口。修复：`calibre_core.py` 新增 `_no_window_flags()`（Windows 返回 `CREATE_NO_WINDOW=0x08000000`，带硬编码兜底；非 Windows 返回 0），应用到全部 3 处子进程调用（--version / `_run` / `open_editor`）；`epub_check.py` 的 java epubcheck 调用同样加 `creationflags`。主程序 `build.spec` 本就 `console=False`，弹框纯来自子进程。验证：模拟 Windows 返回 0x08000000、非 Windows 返回 0，pyflakes + compileall 通过。**版本仍 5.0.0，未打 tag、未上传、未发版** | `calibre_core.py`、`epub_check.py` | 2026-11-25 |

| 29 | **（v5.0.0 后新周期第 2 条，用户要求先不发版）** 新增多个下载镜像渠道（不改变功能，仅增加手动下载入口）：①`update-manifest.json` 的 `channels` 由单一「GitHub 下载」扩为 4 个——GitHub 官方 + ghfast / gh-proxy / ghproxy.net 三个镜像直链，均为实测 HTTP 206 可用（Gitee 仓库不存在故未加入）；②`update_core.py` 新增 `mirror_channels()`，Release 回退路径从 asset 的 `browser_download_url` 自动生成「官方 + 3 镜像」渠道，不写死版本号、不受 Windows runner 中文名被吞影响；③manifest 走文件内渠道（实时读取，现有 exe 无需重装即可看到多渠道）；④更新对话框原有渠道按钮渲染逻辑不变。**验证**：渠道生成 4 个、GUI 离屏 4 个渠道按钮渲染通过、compileall + pyflakes 0 告警、真实网络 check_update 正常（当前远端 manifest 未更新故仍读到旧单渠道，推送后生效）。**版本仍 5.0.0，未打 tag、未发版** | `update-manifest.json`、`update_core.py` | 2026-11-25 |

| 28 | **（v5.0.0 后新周期第 1 条，用户要求先不发版）** 修复内置「检查更新/一键升级」在国内代理环境下报 `SSL: CERTIFICATE_VERIFY_FAILED` / 连不上服务器：根因是 `update_installer.find_asset()` 与 `update_core._fetch_manifest/_fetch_release()` 直连 `api.github.com`/`raw.githubusercontent.com`，网络经代理/加速器/安全软件做 HTTPS 拦截后证书链不被 Python 信任，且无镜像回退。新增通用 `gui/netutil.py::safe_get()`（逐个通道尝试，先严格校验证书、证书失败同通道 `verify=False` 降级、403/429 匿名限流自动换镜像，静音 InsecureRequestWarning）；`find_asset` 的 API 查询加 gh-proxy/ghfast 镜像回退；`download_asset` 改用 safe_get 带证书容错并用 `_brief()` 压缩异常提示；manifest 加 jsDelivr/gh-proxy 回退，Release 加 gh-proxy/ghfast 回退；错误文案改为友好的「无法连接到更新服务器，请稍后重试或手动下载」不再弹 SSL 原文。**验证**：netutil 降级/回退/全失败 4 条单测通过、403 限流回退通过、真实网络 `check_update` 识别 5.0.0、`find_asset` 拿到 `_Windows_v5.0.0.zip`（205 MB）、全量 compileall + pyflakes 0 告警 + 相关模块离屏导入通过。注：此修复需随下一个版本打包才在用户 exe 生效；当前 v5.0.0 用户须手动下载安装包升级 | `netutil.py`（新增）、`update_core.py`、`update_installer.py` | 2026-11-25 |

<!-- ===== 以下 5 条已随 v4.1.6 发布，归档保留 ===== -->
| 1 | 修复所有下拉框（QComboBox）鼠标滚轮悬停时会切换选项、并导致页面滚不动的问题：在 QApplication 层新增全局滚轮守卫 `ComboWheelGuard`，拦截 combo 本体滚轮、吞掉切换并转发给最近的祖先滚动区；点击右侧箭头弹窗、弹窗列表内滚轮选择均不受影响 | `motion.py`、`main_window.py` | 2026-11-25 |
| 2 | 修复界面间歇性卡顿/未响应，提升流畅度：①动态背景引擎与流光背景在窗口最小化/无可见窗口时暂停重绘、还原时恢复（新增 `changeEvent`）；②统一降低重绘频率（50/60ms→80ms）；③流光背景 4 个大半径渐变改为预渲染柔光贴图缓存，每帧只 drawPixmap（实测 4.71→3.08ms/帧）；④切页透明化由全量遍历改为只处理当前页 | `dynamic_backgrounds.py`、`flow_background.py`、`side_nav.py`、`main_window.py` | 2026-11-25 |
| 3 | 下载选项对话框补全所有与下载相关的 EPUB 功能（对话彩色高亮、智能选题、章首题图开关/分配/固定图、章末尾图开关/分配/固定图），并把章名/卷名颜色由按钮弹菜单改为与字体一致的下拉选择（预设色块+渐变+自定义取色器）；所有选项随 `options()` 透传至内核且仅影响本次下载、不改全局设置；内容区加滚动保证按钮常驻 | `download_dialog.py`、`main_window.py`、`i18n.py` | 2026-11-25 |
| 4 | 修复微信读书扫码后刷新延迟特别高（要等许久数据才接入）：`build_dashboard()` 原串行执行最多 10 次网关请求（weekly/overall/shelf/progress/notebooks/5×highlights），RTT 累加；改为分两批并发（批1 四个顶层接口，批2 progress+highlights 依赖批1 bookId），并给 `WeReadClient` 加线程本地 Session 复用 TCP 连接、超时 20→12s；实测单次刷新 2.25s→0.50s（约 4.5×）。刷新进行中重复点击由静默忽略改为状态提示 | `weread_core.py`、`weread_view.py` | 2026-11-25 |
| 5 | 搜索结果改为分页展示并修复固定出现的「未知书名」空卡片：移除「换一批」，结果下方新增「上一页 / 当前页 / 页码输入跳转 / 下一页」，接口无总页数故用递增页码；翻页复用最近一次关键词/搜书类型/来源标签且不重复记历史，搜索中禁用分页控件并做代次过滤；同时过滤七猫接口混入的 `show_type="4"`「大家还在搜」等非书籍项（旧代码把无 id/书名的标签渲染成空卡片） | `main_window.py`、`api.py`、`workers.py`、`i18n.py` | 2026-11-25 |

<!-- ===== 以下 5 条已随 v4.1.5 发布，归档保留 ===== -->
| 1 | 资源瘦身：删除原神四星题图 27 张 + 火影尾图库 378 个文件，并清理相关代码、文案与文档 | `assets/chapter_hero/`、`assets/chapter_tail_naruto*/`、`chapter_hero.py`、`qimao_core.py`、`main_window.py`、`i18n.py`、`qimao_downloader_plugin.py`、两份说明文档 | 2026-11-25 |
| 2 | 章末尾图改用原神「地理志」风景库：按米游社 V7.2 口径建库共 275 张横屏风景图（26 MB，含补齐全的纳塔 42 + 挪德卡莱 41），恢复尾图功能并改为地理志库（题图/尾图双图库） | `assets/chapter_tail_genshin/`、`chapter_hero.py`、`qimao_core.py`、`main_window.py`、`i18n.py`、`qimao_downloader_plugin.py`、两份说明文档 | 2026-11-25 |
| 3 | 修复设置页背景两大问题：①动态光效背景被页面裸容器（viewport/表格/页面根）不透明底遮挡导致"一样且无动态"——挂载时透明化、卸载时恢复；②色块高亮白框在主题切换后漂移——同步选中态并让 preview/名称/编辑按钮点击都可靠切换；顺带修复星河漂流动效过弱、浮光粒子高频绘制触发 PySide6 引用计数崩溃（none_dealloc）改为预渲染贴图 | `side_nav.py`、`dynamic_backgrounds.py`、`flow_background.py`、`ui_background_widgets.py`、`main_window.py` | 2026-11-25 |
| 4 | 修复导航条高亮竖条偶尔不跟随切换：页面已切到设置但竖条仍停在功能说明。根因为滑动动画对象无强引用保活 + 切页/折叠/rresize 竞态时旧动画继续拉回旧位置；改为动画对象强引用保活、结束回调强制对齐最新目标、所有立即定位路径先停旧动画 | `side_nav.py`、`motion.py` | 2026-11-25 |
| 5 | 修复微信读书笔记页书名/封面全显示「未命名／未」、书籍划线统计条形图名称也全是「未命名」。根因为官方 Agent Gateway `/user/notebooks` 的 `title/author/cover` 嵌在 `book` 子对象，而 `noteCount` 在顶层；旧代码直接读顶层 title 导致全空、划线数正常。新增 `normalize_notebook_book()` 在 core 层一次性提升嵌套字段，笔记页卡片、详情、导出、划线统计、仪表盘联动全部修复 | `weread_core.py` | 2026-11-25 |

## 发布记录

| 版本 | 发布日期 | 包含的 bug 数 | 备注 |
|------|----------|---------------|------|
| v5.0.0 | 2026-11-25 | 大版本（追加项 21–26：番茄内核接入 + 全功能对照 + 自研搜索 + 四轮复查 + 番茄全链接解析 + 全面体检） | **大版本更新**：七猫+番茄双书源下载能力，为三书源架构奠基。版本真源 `APP_VERSION` 与 manifest 均升 5.0.0；已用系统 Python 3.12 打出 Linux 版 `星隅小说下载器_Linux_v5.0.0.zip`（可运行、番茄/七猫内核齐全）；Windows 安装包 `_Windows_v5.0.0.zip`（约 196 MB）已通过 GitHub Actions（windows-latest + PyInstaller + action-gh-release）自动打包并上传 Release v5.0.0 |
| v4.1.6 | 2026-11-25 | 5（下拉框滚轮守卫 + 卡顿/未响应优化 + 下载对话框补全 + 微信读书刷新延迟 + 搜索分页与未知书名修复） | 按 5-bug 约定正常发布；发布前已做代码 + 打包全流程体检 |
| v4.1.5 | 2026-11-25 | 5（资源瘦身 + 地理志尾图库 + 背景白框/动态光效 + 导航竖条 + 微信读书笔记名称） | 按 5-bug 约定正常发布 |
| v4.1.4 | 2026-11-25 | 1（搜索 terminate 崩溃/延迟/闪烁） | 例外发布：用户当时要求修后发布 |
| v4.1.3 | 2026-11-25 | 1（Calibre 路径区禁用） | 约定建立前的旧行为 |
| v4.1.2 | 2026-11-24 | 2（对话框失效 + 目录不持久化） | 约定建立前的旧行为 |
| v4.1.1 | 2026-11-24 | 3（启动闪退/短链识别/滚轮崩溃 + 一键升级功能） | 约定建立前的旧行为 |
