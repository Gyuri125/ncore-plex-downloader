@echo off
setlocal
cd /d "%~dp0"
title Gemini Argon Projekt Export és Szinkronizálás

echo =======================================================
echo    Gemini Argon Projekt Export és Szinkronizálás
echo =======================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\generate_gemini_argon.py
) else (
    python scripts\generate_gemini_argon.py
)

echo.
echo =======================================================
echo A frissites befejezodott! A fo mappa: gemini_argon\
echo =======================================================
pause

