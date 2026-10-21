@echo off
pushd "%~dp0."
python scripts\build_exe.py
if errorlevel 1 (
    echo [HIBA] Nem sikerult az EXE letrehozasa.
)
popd
pause
