@echo off
chcp 65001 >nul
pushd "%~dp0."
title Ncore Film & Sorozat Letolto - Gyors Inditas

echo ==============================================================
echo       🎬 NCORE PLEX DOWNLOADER - GYORS INDITAS 🎬
echo ==============================================================
echo.

:: 1. Python ellenorzese a szamitogepen
where python >nul 2>&1
if errorlevel 1 (
    echo [HIBA] A Python nincs telepitve a gepeden vagy nincs a PATH-ban!
    echo.
    echo Kerd meg a rendszert a Python telepitesere:
    echo 1. Nyisd meg: https://www.python.org/downloads/
    echo 2. Toltsd le es inditsd el a legfrissebb Python telepitot!
    echo 3. FONTOS: A telepito legelso ablakaban pipald be az alul levo
    echo    "Add Python.exe to PATH" jelolonegyzetet!
    echo.
    pause
    popd
    exit /b 1
)

:: 2. Konfiguracio (.env) ellenorzese
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] .env konfiguracio letrehozasa a sablonbol...
        copy /y ".env.example" ".env" >nul
        echo [OK] .env fajl elkeszitve! (Kesobb a feluleten vagy a fajlban szerkesztheted)
    )
)

:: 3. Virtualis kornyezet (.venv) ellenorzese es automatikus felkeszitese
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Elso inditas erzekelve! Kornyezet automatikus elokeszitese...
    echo [INFO] 1/2: Python virtualis kornyezet (.venv) letrehozasa...
    python -m venv .venv
    if errorlevel 1 (
        echo [FIGYELEM] Nem sikerult letrehozni a .venv-et, rendszer Python hasznalata...
    ) else (
        echo [INFO] 2/2: Szukseges fuggosegek telepitese (requirements.txt)...
        echo        (Ez csak a legelso inditasnal tart kb. 1 percig, kerlek varj...)
        .venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
        .venv\Scripts\pip install -r requirements.txt
    )
)

:: 4. Alkalmazas inditasa
echo.
echo [OK] Alkalmazas inditasa!
echo [INFO] A bongeszo (http://localhost:8501) es a talcaikon par masodpercen belul megerkezik...
echo.

if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe launcher.py
) else (
    python launcher.py
)

popd
