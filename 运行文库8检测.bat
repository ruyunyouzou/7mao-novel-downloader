@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 文库8本机诊断
echo ============================================================
echo  文库8本机诊断 - 请把运行结果整段复制给开发者
echo  说明：默认跳过登录交互，一次跑完；重点看 [6] App API 那段
echo ============================================================
echo.
where python >nul 2>nul
if %errorlevel%==0 (
    python wenku8_diagnose.py --skip-login
    goto done
)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 wenku8_diagnose.py --skip-login
    goto done
)
echo [错误] 未找到 Python。请先安装 Python 3，安装时勾选 "Add Python to PATH"。
:done
echo.
echo 检测结束。请在窗口内选中全部内容（右键-标记-全选）后复制给开发者。
pause
