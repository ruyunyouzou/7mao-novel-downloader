# -*- coding: utf-8 -*-
"""
插件管理器：扫描 plugins/ 目录，动态加载插件，派发挂载点事件。

加载规则
========
1. 扫描 ``src/plugins/`` 下的 ``*.py`` 文件与含 ``__init__.py`` 的子目录。
2. 用 ``importlib`` 动态导入每个模块。
3. 在模块中查找所有继承自 :class:`Plugin` 且不是抽象基类的类，实例化。
4. 若实例的 ``meta.enabled`` 为 False 则跳过。
5. 调用 ``on_load(self)``，并把 ``manager`` / ``app`` 注入实例。

事件派发
========
:meth:`emit` 按挂载点名称调用所有已加载插件的同名方法，
异常会被捕获并记录，不会因单个插件出错而中断主程序。
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from .base import Plugin, Hook, DownloaderBase, BackgroundProvider


class PluginManager:
    """插件管理器。"""

    def __init__(self, plugin_dir: str, app: Any = None) -> None:
        """
        :param plugin_dir: 插件目录路径
        :param app: 主程序实例（会注入到每个插件的 ``app`` 属性）
        """
        self.plugin_dir = plugin_dir
        self.app = app
        self.plugins: List[Plugin] = []
        self.errors: List[str] = []
        self._module_names: List[str] = []

    # ------------------------------------------------------------------ 加载

    def load_all(self) -> List[Plugin]:
        """扫描并加载所有插件，返回成功加载的实例列表。"""
        self.plugins.clear()
        self.errors.clear()

        if not os.path.isdir(self.plugin_dir):
            os.makedirs(self.plugin_dir, exist_ok=True)
            return self.plugins

        # 保证 plugins 目录的父目录在 sys.path 中，便于插件 import 兄弟模块
        parent = os.path.dirname(self.plugin_dir)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        targets = self._discover_targets()
        for name, path in targets:
            try:
                self._load_one(name, path)
            except Exception as exc:  # noqa: BLE001
                msg = f"插件 {name} 加载失败: {exc}\n{traceback.format_exc()}"
                self.errors.append(msg)
                self._log(msg)

        # 按元信息中的 name 排序，保证顺序稳定
        self.plugins.sort(key=lambda p: p.meta.name)
        for plugin in self.plugins:
            self._safe_call(plugin, Hook.ON_LOAD, self)
        return self.plugins

    def _discover_targets(self):
        """发现插件文件与包，返回 [(模块名, 路径), ...]。"""
        targets = []
        for entry in sorted(os.listdir(self.plugin_dir)):
            full = os.path.join(self.plugin_dir, entry)
            if entry.startswith("_") or entry == "vendor":
                # vendor 是上游实现隔离目录，不是插件
                continue
            if entry.endswith(".py"):
                targets.append((entry[:-3], full))
            elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                targets.append((entry, os.path.join(full, "__init__.py")))
        return targets

    def _load_one(self, name: str, path: str) -> None:
        """加载单个插件模块。"""
        mod_name = f"qimao_gui_plugins.{name}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法创建模块 spec: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        self._module_names.append(mod_name)
        spec.loader.exec_module(module)

        found = 0
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, Plugin):
                continue
            if obj is Plugin or obj.__module__ != mod_name:
                continue
            try:
                instance = obj()
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"插件类 {obj.__name__} 实例化失败: {exc}")
                continue
            if not instance.meta.enabled:
                continue
            instance.manager = self
            instance.app = self.app
            instance._source_path = path  # 供插件页判断内置/外部安装
            self.plugins.append(instance)
            found += 1
        if found == 0:
            self._log(f"模块 {name} 中未找到有效的 Plugin 子类")

    # ------------------------------------------------------------------ 事件

    def emit(self, hook: Hook, *args: Any, **kwargs: Any) -> List[Any]:
        """向所有插件派发事件，返回各插件返回值的列表（None 也会被保留）。"""
        results = []
        hook_name = hook.value if isinstance(hook, Hook) else str(hook)
        for plugin in self.plugins:
            func = getattr(plugin, hook_name, None)
            if func is None or not callable(func):
                continue
            results.append(self._safe_call(plugin, hook_name, *args, **kwargs))
        return results

    def collect(self, hook: Hook, *args: Any, **kwargs: Any) -> List[Any]:
        """派发事件并收集非空返回值（用于 register_tabs、provide_downloader 等）。"""
        return [r for r in self.emit(hook, *args, **kwargs) if r is not None]

    def get_downloader(self, book_info: Optional[Dict[str, Any]] = None,
                       source: Optional[str] = None) -> Optional[DownloaderBase]:
        """获取下载内核实例（priority 越大越优先）。

        - 未传参数：返回全局优先级最高的内核（保持原有行为，默认仍是七猫）。
        - 传 source：优先选 source_key 匹配的内核（如 fanqie）。
        - 传 book_info：再用内核的 supports() 过滤，避免番茄书落到七猫内核。
        """
        candidates = [
            d for d in self.collect(Hook.PROVIDE_DOWNLOADER)
            if isinstance(d, DownloaderBase)
        ]
        if not candidates:
            return None
        if source:
            matched = [d for d in candidates
                       if str(getattr(d, "source_key", "") or "") == source]
            if matched:
                candidates = matched
        if book_info is not None:
            matched = []
            for d in candidates:
                try:
                    if d.supports(book_info):
                        matched.append(d)
                except Exception:  # noqa: BLE001
                    continue
            if matched:
                candidates = matched
        return max(candidates, key=lambda d: getattr(d, "priority", 0))

    def get_backgrounds(self) -> List[BackgroundProvider]:
        """收集所有插件提供的背景样式。

        插件可返回单个 BackgroundProvider，也可返回其列表/元组。
        """
        result: List[BackgroundProvider] = []
        for item in self.collect(Hook.PROVIDE_BACKGROUND):
            if isinstance(item, BackgroundProvider):
                result.append(item)
            elif isinstance(item, (list, tuple)):
                result.extend(x for x in item if isinstance(x, BackgroundProvider))
        return result

    def unload_all(self) -> None:
        """卸载所有插件。"""
        for plugin in self.plugins:
            self._safe_call(plugin, Hook.ON_UNLOAD)
        self.plugins.clear()

    # ------------------------------------------------------------------ 工具

    def _safe_call(self, plugin: Plugin, func_name: str, *args: Any, **kwargs: Any) -> Any:
        """安全调用插件方法，捕获异常并记录。"""
        func = getattr(plugin, func_name, None)
        if func is None or not callable(func):
            return None
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            msg = f"插件 {plugin.meta.name} 的 {func_name} 执行出错: {exc}"
            self.errors.append(msg)
            self._log(msg)
            return None

    def _log(self, message: str) -> None:
        if self.app is not None and hasattr(self.app, "log"):
            self.app.log(f"[插件管理器] {message}")
        else:
            print(f"[插件管理器] {message}")

    def summary(self) -> str:
        """返回插件列表摘要文本，用于「插件管理」标签页展示。"""
        if not self.plugins:
            return "未加载任何插件。\n\n将 .py 插件文件放入 src/plugins/ 目录后重启程序。"
        lines = [f"已加载 {len(self.plugins)} 个插件：\n"]
        for p in self.plugins:
            lines.append(
                f"  • {p.meta.name} v{p.meta.version}"
                + (f" — {p.meta.author}" if p.meta.author else "")
                + (f"\n      {p.meta.description}" if p.meta.description else "")
            )
        if self.errors:
            lines.append(f"\n加载/运行错误 {len(self.errors)} 条：")
            for e in self.errors:
                lines.append(f"  ! {e.splitlines()[0]}")
        return "\n".join(lines)
