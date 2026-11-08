@echo off
setlocal
cd /d "%~dp0"
title Ncore Media Letolto - Telepito

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
if errorlevel 1 (
    echo.
    echo [HIBA] A telepites soran hiba lepett fel!
    pause
)
