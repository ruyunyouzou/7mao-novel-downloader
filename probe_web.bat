@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo ============================================================
echo  Wenku8 WEB Channel Probe
echo  Copy the WHOLE output below to the developer.
echo ============================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 wenku8_web_probe.py
    goto done
)
python -c "import sys" >nul 2>nul
if %errorlevel%==0 (
    python wenku8_web_probe.py
    goto done
)
echo [ERROR] Python 3 not found.
:done
echo.
pause
