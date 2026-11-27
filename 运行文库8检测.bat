@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Wenku8 Diagnose

echo ============================================================
echo  Wenku8 local diagnose -- copy the WHOLE output to developer
echo  Login is skipped by default. Focus on section [6] App API.
echo ============================================================
echo.

rem Prefer the "py" launcher: it bypasses the Microsoft Store alias
rem that hijacks the plain "python" command on many Windows installs.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 wenku8_diagnose.py --skip-login
    goto done
)

rem Fallback: a real python on PATH (not the Store stub).
python -c "import sys; exit(0 if sys.version_info[0]==3 else 1)" >nul 2>nul
if %errorlevel%==0 (
    python wenku8_diagnose.py --skip-login
    goto done
)

echo [ERROR] Python 3 launcher not found.
echo Try:  py -3 wenku8_diagnose.py --skip-login
echo Or install Python 3 from python.org and CHECK "Add Python to PATH".
echo If "python" opens Microsoft Store, disable it in:
echo Settings ^> Apps ^> Advanced app settings ^> App execution aliases

:done
echo.
echo Done. Select all in window (right-click - Mark - Select All) and copy.
pause
