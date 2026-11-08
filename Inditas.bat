@echo off
setlocal
cd /d "%~dp0"
title Ncore Media Letolto

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" launcher.py
) else if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" launcher.py
) else (
    start "" python launcher.py
)
