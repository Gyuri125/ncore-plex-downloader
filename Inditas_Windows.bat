@echo off
chcp 65001 >nul
pushd "%~dp0."
title Ncore Film & Sorozat Letolto - Gyors Inditas

echo ==============================================================
echo       🎬 NCORE PLEX DOWNLOADER - GYORS INDITAS 🎬
echo ==============================================================
echo.

:: 0. Naplozasi konyvtar elokeszitese
if not exist "logs" mkdir "logs"
set "INSTALL_LOG=logs\telepites_log.txt"

:: 1. Python ellenorzese a szamitogepen
where python >nul 2>&1
if errorlevel 1 (
    echo [%DATE% %TIME%] [HIBA] A Python nincs telepitve vagy nincs a PATH-ban! >> "%INSTALL_LOG%"
    echo [HIBA] A Python nincs telepitve a gepeden vagy nincs a PATH-ban!
    echo.
    echo Kerd meg a rendszert a Python telepitesere:
    echo 1. Nyisd meg: https://www.python.org/downloads/
    echo 2. Toltsd le es inditsd el a legfrissebb Python telepitot!
    echo 3. FONTOS: A telepito legelso ablakaban pipald be az alul levo
    echo    "Add Python.exe to PATH" jelolonegyzetet!
    echo.
    echo Reszletek mentve ide: %INSTALL_LOG%
    echo.
    pause
    popd
    exit /b 1
)

:: 2. Konfiguracio (.env) ellenorzese es automatikus megorzese
if not exist ".env" (
    if exist "%APPDATA%\NcoreMediaDownloader\.env" (
        echo [OK] Korabbi Plex, nCore es rendszerbeallitasok automatikus visszatoltese...
        copy /y "%APPDATA%\NcoreMediaDownloader\.env" ".env" >nul
        echo [%DATE% %TIME%] [OK] .env automatikusan visszatoltve a kozponti AppData-bol. >> "%INSTALL_LOG%"
    ) else if exist ".env.example" (
        echo [INFO] .env konfiguracio letrehozasa a sablonbol...
        copy /y ".env.example" ".env" >nul
        echo [OK] .env fajl elkeszitve! (Kesobb a feluleten vagy a fajlban szerkesztheted)
        echo [%DATE% %TIME%] [OK] .env fajl letrehozva a sablonbol. >> "%INSTALL_LOG%"
    )
) else (
    :: Ha letezik, keszitsunk rola csendes biztonsagi masolatot az AppData-ba
    if not exist "%APPDATA%\NcoreMediaDownloader" mkdir "%APPDATA%\NcoreMediaDownloader" >nul 2>&1
    copy /y ".env" "%APPDATA%\NcoreMediaDownloader\.env" >nul 2>&1
)

:: 3. Virtualis kornyezet (.venv) ellenorzese es automatikus felkeszitese
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Elso inditas erzekelve! Kornyezet automatikus elokeszitese...
    echo [%DATE% %TIME%] [INFO] Elso inditas: .venv letrehozasa... >> "%INSTALL_LOG%"
    echo [INFO] 1/2: Python virtualis kornyezet (.venv) letrehozasa...
    python -m venv .venv >> "%INSTALL_LOG%" 2>&1
    if errorlevel 1 (
        echo [FIGYELEM] Nem sikerult letrehozni a .venv-et, rendszer Python hasznalata...
        echo [%DATE% %TIME%] [FIGYELEM] Nem sikerult letrehozni a .venv-et! >> "%INSTALL_LOG%"
    ) else (
        echo [INFO] 2/2: Szukseges fuggosegek telepitese (requirements.txt)...
        echo        (Ez csak a legelso inditasnal tart kb. 1 percig, kerlek varj...)
        echo [%DATE% %TIME%] [INFO] Pip frissites es requirements telepites... >> "%INSTALL_LOG%"
        .venv\Scripts\python.exe -m pip install --upgrade pip >> "%INSTALL_LOG%" 2>&1
        .venv\Scripts\pip install -r requirements.txt >> "%INSTALL_LOG%" 2>&1
        if errorlevel 1 (
            echo.
            echo [HIBA] A Python fuggosegek telepitese soran hiba lepett fel!
            echo A reszletes hibauzenetet megtekintheted itt: %INSTALL_LOG%
            echo.
            pause
            popd
            exit /b 1
        )
        echo [%DATE% %TIME%] [OK] Minden csomag telepitve. >> "%INSTALL_LOG%"
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
