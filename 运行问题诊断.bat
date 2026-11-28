@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo ============================================================
echo  Wenku8 Issue Diagnose
echo  Copy the WHOLE output below to the developer.
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 wenku8_issue_check.py
    goto done
)

python -c "import sys; exit(0 if sys.version_info[0]==3 else 1)" >nul 2>nul
if %errorlevel%==0 (
    python wenku8_issue_check.py
    goto done
)

echo [ERROR] Python 3 not found. Install from python.org
echo         and CHECK "Add Python to PATH" during install.

:done
echo.
echo Done. Right-click title bar - Mark - Select All, then copy.
pause
