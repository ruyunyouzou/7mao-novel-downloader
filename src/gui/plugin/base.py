# -*- coding: utf-8 -*-
"""
插件基类与挂载点定义。

插件扩展机制说明
================
插件是一个放在 ``src/plugins/`` 目录下的 Python 文件（或包），
模块中必须定义一个继承自 :class:`Plugin` 的类。程序启动时
:class:`~gui.plugin.manager.PluginManager` 会自动扫描该目录并加载。

插件通过实现 :class:`Hook` 中定义的方法（挂载点）参与主程序流程。
例如实现 ``provide_downloader`` 可以为本项目缺失的"下载内核"提供实现；
实现 ``register_menus`` / ``register_tabs`` 可以往主窗口加菜单或标签页。

示例见 ``src/plugins/example_plugin.py``。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Callable, List, Dict
from enum import Enum


class Hook(str, Enum):
    """插件可实现的挂载点名称。字符串枚举，便于插件直接用方法名实现。"""

    ON_LOAD = "on_load"                      # 插件加载后调用，参数: manager
    ON_UNLOAD = "on_unload"                  # 程序退出时调用
    REGISTER_MENUS = "register_menus"        # 注册菜单，参数: main_window
    REGISTER_TABS = "register_tabs"          # 注册标签页，返回 QWidget 列表，参数: main_window
    ON_SEARCH = "on_search"                  # 搜索完成后，参数: keyword, results
    ON_BOOK_SELECTED = "on_book_selected"    # 选中书籍后，参数: book_info(dict)
    ON_DOWNLOAD_START = "on_download_start"  # 下载开始，参数: book_info(dict)
    ON_DOWNLOAD_PROGRESS = "on_download_progress"  # 下载进度，参数: book_info, progress(0-100)
    ON_DOWNLOAD_COMPLETE = "on_download_complete"  # 下载完成，参数: result(DownloadResult)
    PROVIDE_DOWNLOADER = "provide_downloader"      # 提供下载内核，返回 DownloaderBase 实例
    PROVIDE_BACKGROUND = "provide_background"      # 提供背景样式，返回 BackgroundProvider 实例


@dataclass
class PluginMeta:
    """插件元信息。"""

    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    enabled: bool = True


@dataclass
class DownloadResult:
    """下载结果，传给 ON_DOWNLOAD_COMPLETE 挂载点。"""

    book_id: str
    title: str
    success: bool
    file_path: str = ""
    message: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class DownloaderBase:
    """
    下载内核抽象接口。

    本项目开源版的内核下载逻辑被作者移除（见 SLQimao/book.py 的私有说明），
    因此 GUI 层不直接耦合任何具体实现，而是通过插件的 ``provide_downloader``
    挂载点获取一个下载器实例。任何插件只要返回一个本类的子类实例，
    即可接管下载行为——这就是"内核即插件"的设计。

    子类需要实现 :meth:`download`。
    """

    name: str = "未命名下载器"
    priority: int = 0  # 优先级，越高越优先被选中（真实内核应高于演示内核）

    def download(
        self,
        book_id: str,
        save_path: str,
        fmt: str = "txt",
        progress_cb: Optional[Callable[[int], None]] = None,
        **kwargs: Any,
    ) -> DownloadResult:
        """
        下载一本书。

        :param book_id: 书籍 ID
        :param save_path: 保存路径（文件或目录，由下载器决定）
        :param fmt: 格式，txt / epub / chapter
        :param progress_cb: 进度回调，接收 0-100 的整数
        :return: 下载结果
        """
        raise NotImplementedError("下载器插件必须实现 download 方法")


class BackgroundProvider:
    """
    背景样式提供者。插件通过 ``provide_background`` 返回一个实例来注入背景。

    设计原则：只改"空白背景区域"，不碰任何控件自身的前景色/背景色/边框。

    支持两类实现方式：
    1. 静态背景：实现 :meth:`stylesheet` 返回一段追加到全局 QSS 末尾的样式片段。
    2. 动态背景：实现 :meth:`create_widget` 返回一个铺满内容区的 QWidget，
       由它自行绘制动画（如流光渐变）。主窗口会把它放到内容区最底层，
       所有页面/控件覆盖其上。两类可同时提供，也可只提供其一。

    默认实现均返回空/None，子类按需覆写。
    """

    name: str = "未命名背景"
    key: str = "default"          # 唯一标识，用于设置页选择
    description: str = ""

    def stylesheet(self, dark: bool) -> str:
        """返回要追加的背景 QSS 片段。dark=True 为深色主题。"""
        return ""

    def create_widget(self, parent=None, dark: bool = True):
        """返回动态背景 widget（铺满内容区底层）；None 表示不使用动态背景。"""
        return None


class Plugin:
    """
    插件基类。所有插件必须继承本类。

    用法::

        class MyPlugin(Plugin):
            meta = PluginMeta(name="我的插件", version="1.0", description="演示")

            def register_menus(self, main_window):
                ...

            def provide_downloader(self):
                return MyDownloader()

    子类只需实现自己关心的挂载点方法，其余会被管理器忽略。
    """

    meta: PluginMeta = PluginMeta(name="未命名插件")

    # 由管理器在加载时注入，插件可用它访问主窗口与全局状态
    manager: Any = None
    app: Any = None

    def log(self, message: str) -> None:
        """向主窗口状态栏/日志输出信息（若主窗口提供 log 方法）。"""
        if self.app is not None and hasattr(self.app, "log"):
            self.app.log(f"[{self.meta.name}] {message}")

    # ---- 以下均为可选挂载点，插件按需覆写 ----

    def on_load(self, manager: Any) -> None:
        """插件加载后调用。"""

    def on_unload(self) -> None:
        """程序退出时调用，用于清理资源。"""

    def register_menus(self, main_window: Any) -> None:
        """注册菜单项。"""

    def register_tabs(self, main_window: Any) -> List[Any]:
        """注册标签页，返回 QWidget 列表。"""
        return []

    def on_search(self, keyword: str, results: List[Dict[str, Any]]) -> None:
        """搜索完成后调用，可就地修改 results。"""

    def on_book_selected(self, book_info: Dict[str, Any]) -> None:
        """选中书籍后调用。"""

    def on_download_start(self, book_info: Dict[str, Any]) -> None:
        """下载开始时调用。"""

    def on_download_progress(self, book_info: Dict[str, Any], progress: int) -> None:
        """下载进度更新时调用。"""

    def on_download_complete(self, result: DownloadResult) -> None:
        """下载完成后调用。"""

    def provide_downloader(self) -> Optional[DownloaderBase]:
        """提供下载内核实例；返回 None 表示不提供。"""
        return None

    def provide_background(self) -> Optional[BackgroundProvider]:
        """提供背景样式；返回 None 表示不提供。"""
        return None
