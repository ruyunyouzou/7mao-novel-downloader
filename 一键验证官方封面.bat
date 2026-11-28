@echo off
cd /d "%~dp0"
chcp 65001 >nul
title 文库8官方封面 - 本机验证
echo ============================================================
echo   文库8 官方正版高清封面 - 本机真机验证
echo   自动安装依赖 + 批量测试 12 本代表书
echo ============================================================
echo.

REM ---- 选择 Python 启动器 ----
set PY=
where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
    goto run
)
python -c "import sys" >nul 2>nul
if %errorlevel%==0 (
    set PY=python
    goto run
)
echo [错误] 未找到 Python 3，请先安装 Python 3.10~3.12
echo        安装时务必勾选 "Add Python to PATH"
echo        下载地址: https://www.python.org/downloads/
pause
exit /b

:run
echo [1/2] 检查并安装依赖（requests、pillow）...
%PY% -m pip install -q requests pillow
echo.
echo [2/2] 开始批量验证 12 本代表书（约 30~60 秒），请稍候 ...
echo.
%PY% 文库8官方封面检测.py --batch
echo.
echo ============================================================
echo   验证完成！
echo   完整结果已保存到本目录： 文库8封面检测结果.txt
echo   请把这个文件发回给开发者。
echo ============================================================
echo.
pause
