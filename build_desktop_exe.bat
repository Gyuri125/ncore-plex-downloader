@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title Apa Média Letöltő - Friss EXE Készítése az Asztalra

echo ==============================================================
echo       🎬 Apa Média Letöltő - Automata Asztali EXE Fordító
echo ==============================================================
echo.

set "PROJECT_DIR=%~dp0"
set "DESKTOP_DIR=%USERPROFILE%\Desktop"
set "EXE_NAME=Apa_Media_Letolto.exe"
set "TARGET_EXE=%DESKTOP_DIR%\%EXE_NAME%"

echo [1/4] Futó folyamatok ellenőrzése és bezárása...
taskkill /f /im %EXE_NAME% >nul 2>&1

:: Virtuális környezet aktiválása ha létezik
if exist "%PROJECT_DIR%.venv\Scripts\activate.bat" (
    echo [OK] .venv környezet aktiválása...
    call "%PROJECT_DIR%.venv\Scripts\activate.bat"
) else if exist "%PROJECT_DIR%venv\Scripts\activate.bat" (
    echo [OK] venv környezet aktiválása...
    call "%PROJECT_DIR%venv\Scripts\activate.bat"
)

echo.
echo [2/4] Legfrissebb forráskód fordítása (PyInstaller)...
cd /d "%PROJECT_DIR%"
pyinstaller --noconfirm --clean --onefile --console --name "Apa_Media_Letolto" launcher.py

if errorlevel 1 (
    echo.
    echo ❌ [HIBA] A PyInstaller fordítás sikertelen volt!
    pause
    exit /b 1
)

echo.
echo [3/4] Az új EXE másolása az Asztalra és a régi felülírása...
if exist "%PROJECT_DIR%dist\%EXE_NAME%" (
    copy /y "%PROJECT_DIR%dist\%EXE_NAME%" "%TARGET_EXE%" > nul
    if errorlevel 1 (
        echo ❌ [HIBA] Nem sikerült felülírni a fájlt az Asztalon!
    ) else (
        echo.
        echo ==============================================================
        echo ✅ SIKER! Az új verzió elkészült és felülírta a régit az Asztalon:
        echo    "%TARGET_EXE%"
        echo ==============================================================
    )
) else (
    echo ❌ [HIBA] A generált exe nem található a dist mappában!
)

echo.
echo [4/4] Befejezve.
echo Mostantól az Asztalról közvetlenül duplakattintással indíthatod a legfrissebb verziót!
echo.
pause
