@echo off
chcp 65001 >nul
title GUI-novel下载器 - Windows 一键打包
echo ============================================================
echo   GUI-novel下载器（七猫版）GUI  -  Windows 一键打包
echo ============================================================
echo.
cd /d "%~dp0"

REM ---- 1. 检查 Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10~3.12 并勾选 Add to PATH
    echo        下载地址: https://www.python.org/downloads/
    pause & exit /b 1
)

REM ---- 2. 安装依赖（含 PySide6 与 PyInstaller，使用清华镜像加速）----
echo [1/4] 安装依赖...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -r requirements.txt PySide6 pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause & exit /b 1
)

REM ---- 3. PyInstaller 打包 ----
echo [2/4] 正在打包（可能需要 3~10 分钟，请耐心等待）...
python -m PyInstaller build.spec --clean --noconfirm
if errorlevel 1 (
    echo [错误] 打包失败，请查看上方日志
    pause & exit /b 1
)

REM ---- 4. 生成绿色版压缩包 ----
echo [3/4] 打包完成，整理输出...
if exist "GUI-novel下载器_Windows.zip" del "GUI-novel下载器_Windows.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\GUI-novel下载器\*' -DestinationPath 'GUI-novel下载器_Windows.zip' -Force"

echo [4/4] 完成！
echo.
echo ============================================================
echo   打包成功！
echo   可执行文件: dist\GUI-novel下载器\GUI-novel下载器.exe
echo   绿色版压缩包: GUI-novel下载器_Windows.zip
echo   双击 exe 即可运行，无需安装 Python。
echo ============================================================
echo.
pause
