# -*- coding: utf-8 -*-
"""
背景美化插件：为内容区提供多套渐变 / 纹理背景。

设计要点（"只变背景，其他什么都不影响"）
============================================
- 样式只命中两个背景锚点：``#appCentral`` 与 ``#appStack``。
- 只设置 background 简写（渐变/纯色），不碰 color / border / padding
  等任何会影响控件观感的属性。
- 注：本环境 PySide6 中 gradient 必须用 background: 简写才会渲染，
  background-image: qlineargradient(...) 会被忽略。
- 卡片、按钮、输入框、表格、文字等实体控件都有各自独立的 background-color，
  会覆盖在背景之上，因此它们的样式与可读性完全不受影响。
- 左侧导航栏（独立 QFrame 内联样式）与标题栏也不受影响。

切换入口在「设置 → 背景」卡片；选择保存到 ~/.qimao_gui/settings.json。
"""

import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import Plugin, PluginMeta, BackgroundProvider


class _GradientBackground(BackgroundProvider):
    """基于 Qt 渐变的背景，深色/浅色各一套。"""

    def __init__(self, key: str, name: str, description: str,
                 dark_css: str, light_css: str) -> None:
        self.key = key
        self.name = name
        self.description = description
        self._dark = dark_css
        self._light = light_css

    def stylesheet(self, dark: bool) -> str:
        # 背景配色中心：用户改过这套背景的配色就用新色，否则用出厂默认
        from gui import ui_background_theme as ub
        return ub.background_stylesheet(self.key, dark)


def _wrap(decls: str) -> str:
    """把背景声明包裹到两个锚点容器选择器上。"""
    return (
        "/* 背景插件：只作用于内容区锚点，不影响任何控件 */\n"
        "QWidget#appCentral, QStackedWidget#appStack {\n"
        f"    {decls}\n"
        "}\n"
    )


# 每套背景：qlineargradient / qradialgradient 为 Qt 原生渐变，无需图片资源
BACKGROUNDS: List[BackgroundProvider] = [
    _GradientBackground(
        key="aurora",
        name="极光紫",
        description="淡紫到深灰的斜向渐变，呼应主题强调色",
        dark_css=_wrap(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #332550, stop:0.5 #221c33, stop:1 #17171f);"
        ),
        light_css=_wrap(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #e6dcf7, stop:0.5 #f2eefb, stop:1 #f7f7f8);"
        ),
    ),
    _GradientBackground(
        key="ocean",
        name="深空蓝",
        description="冷蓝径向光晕，沉静克制",
        dark_css=_wrap(
            "background: qradialgradient(cx:0.5, cy:0.0, radius:1.2,"
            " fx:0.5, fy:0.0, stop:0 #26405e, stop:0.55 #1a2635, stop:1 #161719);"
        ),
        light_css=_wrap(
            "background: qradialgradient(cx:0.5, cy:0.0, radius:1.1,"
            " fx:0.5, fy:0.0, stop:0 #e3eefb, stop:0.55 #f1f5fa, stop:1 #f7f7f8);"
        ),
    ),
    _GradientBackground(
        key="sunset",
        name="暮光橙",
        description="暖橙渐变，柔和温暖",
        dark_css=_wrap(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #33241d, stop:0.5 #241c1a, stop:1 #171718);"
        ),
        light_css=_wrap(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #fbeede, stop:0.5 #faf5ef, stop:1 #f7f7f8);"
        ),
    ),
    _GradientBackground(
        key="forest",
        name="墨绿",
        description="低饱和青绿渐变，护眼耐看",
        dark_css=_wrap(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #1c2b27, stop:0.5 #1a2220, stop:1 #171717);"
        ),
        light_css=_wrap(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #e2f0ea, stop:0.5 #f0f7f4, stop:1 #f7f7f8);"
        ),
    ),
    _GradientBackground(
        key="violet-glow",
        name="星云辉光",
        description="中心紫色辉光向四周散开",
        dark_css=_wrap(
            "background: qradialgradient(cx:0.5, cy:0.4, radius:0.9,"
            " fx:0.5, fy:0.4, stop:0 #2f2542, stop:0.5 #211d2e, stop:1 #171718);"
        ),
        light_css=_wrap(
            "background: qradialgradient(cx:0.5, cy:0.4, radius:0.9,"
            " fx:0.5, fy:0.4, stop:0 #e9e1f6, stop:0.5 #f3effa, stop:1 #f7f7f8);"
        ),
    ),
]


class _FlowBackground(BackgroundProvider):
    """动态流光背景：由纯 Qt widget 绘制动画，不依赖任何外部引擎。

    与静态渐变不同，动态背景需要一个铺满内容区底层的 widget 来绘制，
    因此 stylesheet 只负责把内容区各层设为透明，让底层 widget 透出。
    """

    def __init__(self) -> None:
        self.key = "flow"
        self.name = "流光渐变（动态）"
        self.description = "纯 Qt 绘制的缓慢流动光晕，零依赖、低占用，可暂停。"

    def stylesheet(self, dark: bool) -> str:
        from gui import ui_background_theme as ub
        return ub.background_stylesheet(self.key, dark)

    def create_widget(self, parent=None, dark: bool = True):
        # 延迟导入，避免静态背景加载时也依赖动画模块
        from gui.flow_background import FlowGradientWidget
        widget = FlowGradientWidget(parent, dark=dark)
        # 注入用户自定义的流光光团颜色（配色中心，未改则用默认）
        try:
            from gui import ui_background_theme as ub
            widget.set_blob_colors(ub.effect_colors("flow", dark))
        except Exception:  # noqa: BLE001
            pass
        return widget


class _DynamicBackground(BackgroundProvider):
    """通用动态背景 provider：绘制交给 dynamic_backgrounds 引擎。

    每个效果的颜色数量不同（3~4），由配色中心逐色注入；
    stylesheet 只把内容区设为透明，动画 widget 铺在底层。
    """

    def __init__(self, key: str, name: str, description: str) -> None:
        self.key = key
        self.name = name
        self.description = description

    def stylesheet(self, dark: bool) -> str:
        from gui import ui_background_theme as ub
        return ub.background_stylesheet(self.key, dark)

    def create_widget(self, parent=None, dark: bool = True):
        try:
            from gui.dynamic_backgrounds import EFFECTS
            from gui import ui_background_theme as ub
            cls = EFFECTS.get(self.key)
            if cls is None:
                return None
            widget = cls(parent, dark=dark)
            widget.set_colors(ub.effect_colors(self.key, dark))
            return widget
        except Exception:  # noqa: BLE001
            return None


BACKGROUNDS.append(_FlowBackground())

# 新增 4 个纯 Qt 动态背景（与配色中心 PRESET_DEFAULTS 中 shape=dynamic 的项一一对应）
for _key, _name, _desc in (
    ("aurora-wave", "极光波浪（动态）", "彩色光带沿正弦曲线缓慢起伏、渐隐。"),
    ("starfield", "星河漂流（动态）", "星点横向漂移并呼吸闪烁，静谧深邃。"),
    ("ripple", "涟漪扩散（动态）", "同心圆环从多个中心缓缓扩散淡出。"),
    ("particle", "浮光粒子（动态）", "光点缓缓上升、轻微摆动，循环再生。"),
):
    BACKGROUNDS.append(_DynamicBackground(_key, _name, _desc))
del _key, _name, _desc


class BackgroundPlugin(Plugin):
    meta = PluginMeta(
        name="背景美化",
        version="1.2.0",
        author="qimao-gui",
        description="提供多套内容区背景（5 套静态渐变 + 5 种纯 Qt 动态光效），只改变背景，不影响任何控件与功能。",
    )

    def provide_background(self) -> List[BackgroundProvider]:
        return BACKGROUNDS
