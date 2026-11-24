# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：星隅小说下载器（七猫版）GUI
在 Windows 上执行： pyinstaller build.spec --clean --noconfirm

说明：
- PySide6 由 PyInstaller 内置 hook 自动收集（Qt 平台插件/样式/图像格式），
  无需手动 collect_all，体积更小且无冗余告警。
- 插件 .py 是 importlib 动态加载的，必须作为数据文件显式收集。
- 字体、图标同理，作为数据文件收集到 _MEIPASS，运行时由 resource_path 定位。
"""
from PyInstaller.utils.hooks import collect_data_files

# 项目资源：字体 / 插件源码 / 图标
datas = [
    ("assets", "assets"),
    # CLI 模式使用的字体与样式表位于 src/assets，需一并收集到 assets 根目录
    ("src/assets/HarmonyOS_Sans_SC_Regular.ttf", "assets"),
    ("src/assets/page_styles.css", "assets"),
    ("src/assets/stylesheet.css", "assets"),
    ("src/plugins", "src/plugins"),
    ("main.ico", "."),
    ("使用说明书.md", "."),
    # 更新源配置与 manifest 模板：随包分发，用户改 repo 即可换更新源，无需重打包
    ("update_config.json", "."),
    ("update-manifest.json", "."),
]

# 三方库数据（ebooklib 的模板等）
datas += collect_data_files("ebooklib")

# 隐藏导入（动态加载或 hook 未覆盖的模块）
# 关键：gui / SLQimao 是 src 下的本地包，入口脚本靠运行时 sys.path.insert 加载，
# PyInstaller 静态分析看不到，必须显式声明，否则打包后闪退报 No module named 'gui'。
hiddenimports = [
    # 本地包（src 下，运行时动态加入 sys.path）——必须显式收集其全部子模块
    "gui", "SLQimao",
    "ebooklib", "ebooklib.epub", "ebooklib.plugins", "ebooklib.plugins.standard",
    "bs4", "lxml", "Crypto", "Crypto.Cipher", "Crypto.Util.Padding",
    "yaml", "requests", "packaging", "rich", "colorama",
    # 听书与扫码登录（动态导入/隐式后端）
    "edge_tts", "qrcode", "qrcode.image.pil",
    "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
    "PySide6.QtMultimedia", "PySide6.QtTextToSpeech", "PySide6.QtNetwork",
]

# 递归收集 gui / SLQimao 的所有子模块（views、plugin、weread_core 等），
# 避免只收顶层包、子模块仍缺失。
import sys as _sys
_sys.path.insert(0, "src")  # 让 collect_submodules 能定位到 src 下的本地包
from PyInstaller.utils.hooks import collect_submodules
hiddenimports += collect_submodules("gui")
hiddenimports += collect_submodules("SLQimao")
hiddenimports = list(dict.fromkeys(hiddenimports))  # 去重并保序

a = Analysis(
    ["启动GUI.py"],
    # 必须把 src 加入 pathex：入口用 sys.path.insert(..., "src") 动态定位 gui/SLQimao，
    # 静态分析阶段看不到，需在这里补上，打包器才能解析到这两个包。
    pathex=[".", "src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PyQt5", "PySide2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="星隅小说下载器",
    icon="main.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="星隅小说下载器",
)
