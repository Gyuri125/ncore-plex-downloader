@echo off
chcp 65001 > nul
title Ncore Film & Sorozat Letöltő - Apa-barát Média Rendszer

echo ==============================================================
echo       🎬 Ncore Film & Sorozat Letöltő - Indítás...
echo ==============================================================
echo.

:: Virtuális környezet ellenőrzése
if exist ".venv\Scripts\activate.bat" (
    echo [OK] .venv környezet aktiválása...
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    echo [OK] venv környezet aktiválása...
    call venv\Scripts\activate.bat
) else (
    echo [FIGYELMEZTETÉS] Virtuális környezet nem található, rendszer szintű Python használata...
)

:: Streamlit indítása
echo.
echo [INFÓ] Alkalmazás indítása a böngészőben...
echo.
streamlit run src/ui/app.py --server.port 8501 --server.address 0.0.0.0

pause
