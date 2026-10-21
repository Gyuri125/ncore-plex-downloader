@echo off
chcp 65001 > nul
cd /d "%~dp0"
python -u scripts\build_exe.py
if errorlevel 1 (
    echo.
    echo ❌ Hiba történt a fordítás során!
    pause
    exit /b 1
)
pause
