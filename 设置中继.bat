@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo ============================================================
echo  Wenku8 Relay Setup
echo ============================================================
echo.
set /p RELAY=Enter your Worker URL (https://xxx.workers.dev): 
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 wenku8_relay_setup.py %RELAY%
    goto done
)
python -c "import sys" >nul 2>nul
if %errorlevel%==0 (
    python wenku8_relay_setup.py %RELAY%
    goto done
)
echo [ERROR] Python 3 not found.

:done
echo.
pause
