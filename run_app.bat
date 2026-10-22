@echo off
pushd "%~dp0."
title Ncore Film es Sorozat Letolto - Automata Media Rendszer

echo ==============================================================
echo       Ncore Film es Sorozat Letolto - Inditas...
echo ==============================================================
echo.

:: Virtualis kornyezet ellenorzese
if exist ".venv\Scripts\activate.bat" (
    echo [OK] .venv kornyezet aktivalasa...
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    echo [OK] venv kornyezet aktivalasa...
    call venv\Scripts\activate.bat
) else (
    echo [INFO] Rendszer szintu Python hasznalata...
)

:: Streamlit inditasa
echo.
echo [INFO] Alkalmazas inditasa a bongeszoben...
echo.
python launcher.py


popd
pause
