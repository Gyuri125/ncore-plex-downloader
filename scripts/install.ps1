# ==============================================================
#        NCORE MEDIA LETOLTO - ULTIMATE TELEPITO (POWERSHELL)
# ==============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[INFO] Rendszergazdai jogosultsag szukseges. UAC ablak inditasa..." -ForegroundColor Yellow
    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# Naplozasi konyvtar es fajl elokeszitese
$LogsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$InstallLogFile = Join-Path $LogsDir "telepites_log.txt"

function Write-InstallLog {
    param (
        [string]$Message,
        [string]$Level = "INFO",
        [ConsoleColor]$Color = [ConsoleColor]::White
    )
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $formattedLog = "[$timestamp] [$Level] $Message"
    
    # Kiiras konzolra
    Write-Host " $Message" -ForegroundColor $Color
    
    # Mentés a telepitesi naplofajlba
    try {
        Add-Content -Path $InstallLogFile -Value $formattedLog -Encoding UTF8
    } catch {}
}

# Fejlec a naploba
$sep = "=" * 70
Add-Content -Path $InstallLogFile -Value "`r`n$sep`r`n[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] [INFO] TELEPITES INDITASA`r`n$sep" -Encoding UTF8

Write-Host "==============================================================" -ForegroundColor Green
Write-Host "      NCORE MEDIA LETOLTO v2.0.0 - TELEPITES ES BEALLITAS     " -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green
Write-Host ""
Write-InstallLog "Telepitesi gyokerkonyvtar: $ProjectRoot" "INFO" ([ConsoleColor]::Gray)
Write-InstallLog "Telepitesi naplofajl: $InstallLogFile" "INFO" ([ConsoleColor]::DarkGray)
Write-Host ""

# -------------------------------------------------------------
# 0. RENDSZERDIAGNOSZTIKA ÉS MEGLÉVŐ BEÁLLÍTÁSOK BIZTOSÍTÁSA
# -------------------------------------------------------------
try {
    $osInfo = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue)
    $osName = if ($osInfo) { "$($osInfo.Caption) ($($osInfo.OSArchitecture), Build $($osInfo.BuildNumber))" } else { [System.Environment]::OSVersion.VersionString }
    $psVer = $PSVersionTable.PSVersion.ToString()
    Write-InstallLog "Operacios rendszer: $osName" "INFO" ([ConsoleColor]::DarkGray)
    Write-InstallLog "PowerShell verzio: $psVer" "INFO" ([ConsoleColor]::DarkGray)
    
    # Szabad lemezterulet ellenorzese
    $drives = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -ne $null }
    foreach ($d in $drives) {
        $freeGB = [math]::Round($d.Free / 1GB, 1)
        Write-InstallLog "Meghajto $($d.Name): szabad kapacitas: $freeGB GB" "INFO" ([ConsoleColor]::DarkGray)
    }

    # Perzisztens AppData mappa elokeszitese
    $AppDataNcore = Join-Path $env:APPDATA "NcoreMediaDownloader"
    if (-not (Test-Path $AppDataNcore)) {
        New-Item -ItemType Directory -Path $AppDataNcore -Force | Out-Null
    }
    $AppDataEnv = Join-Path $AppDataNcore ".env"
    $LocalEnv = Join-Path $ProjectRoot ".env"

    # Ha a helyi .env letezik, mentjuk el az AppData-ba es keszitsunk .bak masolatot
    if (Test-Path $LocalEnv) {
        Copy-Item $LocalEnv -Destination $AppDataEnv -Force -ErrorAction SilentlyContinue
        Copy-Item $LocalEnv -Destination "$LocalEnv.bak" -Force -ErrorAction SilentlyContinue
        Write-InstallLog "[OK] Helyi beallitasok (.env) lementve a kozponti biztonsagi taroloba ($AppDataEnv)." "OK" ([ConsoleColor]::DarkGreen)
    }
    # Ha a helyi .env hianyzik (uj verzio kulon mappaban), de az AppData-ban megvan, hozzuk at azonnal!
    elseif (Test-Path $AppDataEnv) {
        Copy-Item $AppDataEnv -Destination $LocalEnv -Force -ErrorAction SilentlyContinue
        Write-InstallLog "[OK] Meglevo Plex, nCore es egyedi beallitasok automatikusan atmasolva az uj verzioba!" "OK" ([ConsoleColor]::Green)
    }
} catch {
    Write-InstallLog "Rendszerdiagnosztika es beallitasmentes reszlegesen elerheto." "INFO" ([ConsoleColor]::DarkGray)
}
Write-Host ""

# -------------------------------------------------------------
# 1. PYTHON ELLENORZESE ES TELEPITESE
# -------------------------------------------------------------
Write-Host "[1/5] Python kornyezet ellenorzese..." -ForegroundColor Cyan
Write-InstallLog "[1/5] Python keresese a rendszerben..." "INFO" ([ConsoleColor]::Cyan)

$pythonExe = $null

# 1.1 Meglevo .venv
if (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $pythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
    Write-InstallLog "Meglevo projekt .venv Python megtalalva: $pythonExe" "INFO" ([ConsoleColor]::Gray)
}

# 1.2 PATH-ban levo Python (Windows Store dummy kizarasaval)
if (-not $pythonExe) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $ver = & $cmd.Source -c "import sys; print(sys.version_info[0])" 2>$null
            if ($ver -eq "3") {
                $pythonExe = $cmd.Source
                Write-InstallLog "PATH-ban levo Python megtalalva: $pythonExe" "INFO" ([ConsoleColor]::Gray)
            }
        } catch {}
    }
}

# 1.3 Standard telepitesi helyek
if (-not $pythonExe) {
    $candidates = Get-Item "C:\Program Files\Python3*\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending
    if ($candidates) {
        $pythonExe = $candidates[0].FullName
        Write-InstallLog "Standard Program Files Python megtalalva: $pythonExe" "INFO" ([ConsoleColor]::Gray)
    }
}

# 1.4 Ha nincs Python: Automatikus letoltes es csendes telepites
if (-not $pythonExe) {
    Write-InstallLog "A Python nincs telepitve! Python 3.11 automatikus letoltese python.org-rol..." "FIGYELEM" ([ConsoleColor]::Yellow)
    $installerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    $tempInstaller = "$env:TEMP\python-3.11.9-amd64.exe"
    
    $downloadSuccess = $false
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-InstallLog "Letoltes innen: $installerUrl" "INFO" ([ConsoleColor]::Gray)
        (New-Object Net.WebClient).DownloadFile($installerUrl, $tempInstaller)
        Write-InstallLog "Letoltes befejezodott: $tempInstaller" "OK" ([ConsoleColor]::Green)
        $downloadSuccess = $true
    } catch {
        Write-InstallLog "Kozvetlen letoltes sikertelen: $_" "FIGYELEM" ([ConsoleColor]::Yellow)
    }

    if ($downloadSuccess -and (Test-Path $tempInstaller)) {
        Write-InstallLog "Python 3.11 csendes telepitese folyamatban (kerlek varj 1-2 percet)..." "INFO" ([ConsoleColor]::Yellow)
        $p = Start-Process -FilePath $tempInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0" -Wait -PassThru
        Write-InstallLog "Python installer kilepesi kod: $($p.ExitCode)" "INFO" ([ConsoleColor]::Gray)
        Remove-Item $tempInstaller -Force -ErrorAction SilentlyContinue
    } else {
        Write-InstallLog "Kiserlet Python telepitesere winget csomagkezelovel..." "INFO" ([ConsoleColor]::Yellow)
        $wingetProc = Start-Process "winget" -ArgumentList "install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
        Write-InstallLog "Winget kilepesi kod: $($wingetProc.ExitCode)" "INFO" ([ConsoleColor]::Gray)
    }

    # Ujraellenorzes
    $candidates = Get-Item "C:\Program Files\Python311\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -ErrorAction SilentlyContinue
    if ($candidates) {
        $pythonExe = $candidates[0].FullName
    } else {
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($cmd) { $pythonExe = $cmd.Source }
    }
}

if (-not $pythonExe) {
    Write-InstallLog "HIBA: Nem sikerult a Python automatikus telepitese!" "HIBA" ([ConsoleColor]::Red)
    Write-InstallLog "Kerlek telepitsd a Python 3.11-et vagy ujabbat a https://www.python.org oldalrol!" "HIBA" ([ConsoleColor]::Yellow)
    Write-InstallLog "FONTOS: A telepito elso ablakaban pipald be az 'Add Python to PATH' opciot!" "HIBA" ([ConsoleColor]::Yellow)
    Write-Host ""
    Read-Host "Nyomj Entert a kilepeshez..."
    exit 1
}

try {
    $pyVerOutput = & $pythonExe --version 2>&1
    Write-InstallLog "[OK] Python sikeresen azonositva: $pythonExe ($pyVerOutput)" "OK" ([ConsoleColor]::Green)
} catch {
    Write-InstallLog "[OK] Python elerheto: $pythonExe" "OK" ([ConsoleColor]::Green)
}
Write-Host ""

# -------------------------------------------------------------
# 2. QBITTORRENT ELLENORZESE
# -------------------------------------------------------------
Write-Host "[2/5] qBittorrent kliens ellenorzese..." -ForegroundColor Cyan
Write-InstallLog "[2/5] qBittorrent kliens keresese..." "INFO" ([ConsoleColor]::Cyan)

$qbPaths = @(
    "C:\Program Files\qBittorrent\qbittorrent.exe",
    "C:\Program Files (x86)\qBittorrent\qbittorrent.exe",
    "$env:LOCALAPPDATA\Programs\qBittorrent\qbittorrent.exe"
)
$qbFound = $false
foreach ($p in $qbPaths) {
    if (Test-Path $p) {
        $qbFound = $true
        Write-InstallLog "[OK] qBittorrent megtalalva: $p" "OK" ([ConsoleColor]::Green)
        break
    }
}

if (-not $qbFound) {
    Write-InstallLog "qBittorrent nem talalhato, automatikus telepites winget segitsegevel..." "INFO" ([ConsoleColor]::Yellow)
    try {
        $qbProc = Start-Process "winget" -ArgumentList "install --id qBittorrent.qBittorrent -e --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
        Write-InstallLog "Winget qBittorrent kilepesi kod: $($qbProc.ExitCode)" "INFO" ([ConsoleColor]::Gray)
        # Ujraellenorzes
        foreach ($p in $qbPaths) {
            if (Test-Path $p) {
                Write-InstallLog "[OK] qBittorrent sikeresen telepitve: $p" "OK" ([ConsoleColor]::Green)
                $qbFound = $true
                break
            }
        }
    } catch {
        Write-InstallLog "qBittorrent winget telepitesi hiba: $_" "FIGYELEM" ([ConsoleColor]::Yellow)
    }
}
Write-Host ""

# -------------------------------------------------------------
# 3. VIRTUÁLIS KÖRNYEZET (.venv) ÉS REQUIREMENTS TELEPÍTÉSE
# -------------------------------------------------------------
Write-Host "[3/5] Python virtualis kornyezet (.venv) es fuggosegek telepitese..." -ForegroundColor Cyan
Write-InstallLog "[3/5] Virtualis kornyezet (.venv) elokeszitese..." "INFO" ([ConsoleColor]::Cyan)

$venvPython = "$ProjectRoot\.venv\Scripts\python.exe"
$venvPip = "$ProjectRoot\.venv\Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Write-InstallLog ".venv letrehozasa folyamatban ide: $ProjectRoot\.venv..." "INFO" ([ConsoleColor]::Yellow)
    $venvOutput = & $pythonExe -m venv "$ProjectRoot\.venv" 2>&1
    if ($venvOutput) {
        Add-Content -Path $InstallLogFile -Value "venv kimenet: $venvOutput" -Encoding UTF8
    }
}

if (-not (Test-Path $venvPython)) {
    Write-InstallLog "KRITIKUS HIBA: Nem sikerult letrehozni a .venv kornyezetet!" "HIBA" ([ConsoleColor]::Red)
    Write-InstallLog "Kiserlet a rendszer Pythonnal valo futtatasra vagy venv csomag hianyara..." "HIBA" ([ConsoleColor]::Yellow)
    Read-Host "Nyomj Entert a kilepeshez..."
    exit 1
}

Write-InstallLog "[OK] .venv virtualis kornyezet keszen all: $venvPython" "OK" ([ConsoleColor]::Green)

# Pip frissítése részletes naplózással
Write-InstallLog "Pip csomagkezelo frissitese..." "INFO" ([ConsoleColor]::Gray)
$pipUpgradeOutput = & $venvPython -m pip install --upgrade pip 2>&1
foreach ($line in $pipUpgradeOutput) {
    Add-Content -Path $InstallLogFile -Value "pip upgrade: $line" -Encoding UTF8
}

# Függőségek telepítése
Write-InstallLog "Szukseges Python fuggosegek telepitese (requirements.txt)..." "INFO" ([ConsoleColor]::Yellow)
Write-InstallLog "(Ez az elso alkalommal eltarthat 1-2 percig az internet sebessegetol fuggoen...)" "INFO" ([ConsoleColor]::Gray)

$requirementsFile = "$ProjectRoot\requirements.txt"
if (-not (Test-Path $requirementsFile)) {
    Write-InstallLog "HIBA: Nem talalhato a requirements.txt fajl itt: $requirementsFile" "HIBA" ([ConsoleColor]::Red)
    Read-Host "Nyomj Entert a kilepeshez..."
    exit 1
}

$pipProcess = Start-Process -FilePath $venvPip -ArgumentList "install -r `"$requirementsFile`"" -NoNewWindow -Wait -PassThru -RedirectStandardOutput "$env:TEMP\pip_stdout.txt" -RedirectStandardError "$env:TEMP\pip_stderr.txt"

# Pip kimenet atmasolasa a naplofajlba es megjelenites ha szukseges
if (Test-Path "$env:TEMP\pip_stdout.txt") {
    $stdoutContent = Get-Content "$env:TEMP\pip_stdout.txt" -Raw -ErrorAction SilentlyContinue
    if ($stdoutContent) {
        Add-Content -Path $InstallLogFile -Value "`r`n--- PIP INSTALL KIMENET ---`r`n$stdoutContent`r`n---------------------------" -Encoding UTF8
    }
    Remove-Item "$env:TEMP\pip_stdout.txt" -Force -ErrorAction SilentlyContinue
}

$pipError = $null
if (Test-Path "$env:TEMP\pip_stderr.txt") {
    $pipError = Get-Content "$env:TEMP\pip_stderr.txt" -Raw -ErrorAction SilentlyContinue
    if ($pipError -and $pipError.Trim() -ne "") {
        Add-Content -Path $InstallLogFile -Value "`r`n--- PIP HIBA KIMENET ---`r`n$pipError`r`n------------------------" -Encoding UTF8
    }
    Remove-Item "$env:TEMP\pip_stderr.txt" -Force -ErrorAction SilentlyContinue
}

if ($pipProcess.ExitCode -ne 0) {
    Write-InstallLog "KRITIKUS HIBA: A Python fuggosegek telepitese meghiúsult (Pip Exit Code: $($pipProcess.ExitCode))!" "HIBA" ([ConsoleColor]::Red)
    if ($pipError) {
        Write-Host ""
        Write-Host "Hiba reszletei:" -ForegroundColor Red
        Write-Host $pipError -ForegroundColor DarkRed
    }
    Write-Host ""
    Write-Host "Tippek a hiba elharitasahoz:" -ForegroundColor Yellow
    Write-Host " 1. Ellenorizd az internetkapcsolatot!" -ForegroundColor White
    Write-Host " 2. Ellenorizd, hogy a tuzfal vagy virusirto nem blokkolja-e a pypi.org elereset!" -ForegroundColor White
    Write-Host " 3. Reszletes telepitesi naplo: $InstallLogFile" -ForegroundColor Cyan
    Write-Host ""
    Read-Host "Nyomj Entert a kilepeshez..."
    exit 1
}

Write-InstallLog "[OK] Minden Python csomag sikeresen telepitve!" "OK" ([ConsoleColor]::Green)
Write-Host ""

# -------------------------------------------------------------
# 4. KÖRNYEZET, MAPPÁK, WEBMI ÉS ASZTALI IKONOK KONFIGURÁLÁSA + SELF-TEST
# -------------------------------------------------------------
Write-Host "[4/5] Rendszer, WebUI, Asztali ikonok es modulok integritas-ellenorzese..." -ForegroundColor Cyan
Write-InstallLog "[4/5] scripts/setup_environment.py futtatasa es modul self-test..." "INFO" ([ConsoleColor]::Cyan)

$setupScript = "$ProjectRoot\scripts\setup_environment.py"
$setupProc = Start-Process -FilePath $venvPython -ArgumentList "`"$setupScript`"" -NoNewWindow -Wait -PassThru

if ($setupProc.ExitCode -ne 0) {
    Write-InstallLog "FIGYELEM: A kornyezet beallito szkript figyelmeztetessel vagy hibaval zart (Exit Code: $($setupProc.ExitCode))!" "FIGYELEM" ([ConsoleColor]::Yellow)
    Write-InstallLog "Ellenorizd a naplofajlt a reszletekert: $InstallLogFile" "FIGYELEM" ([ConsoleColor]::Yellow)
} else {
    Write-InstallLog "[OK] Kornyezeti beallitasok es modulok integritasa 100%-ban ellenorizve!" "OK" ([ConsoleColor]::Green)
}
Write-Host ""

# -------------------------------------------------------------
# 5. ALKALMAZÁS INDÍTÁSA
# -------------------------------------------------------------
Write-Host "[5/5] Alkalmazas inditasa a hatterben..." -ForegroundColor Cyan
Write-InstallLog "[5/5] Alkalmazas inditasa..." "INFO" ([ConsoleColor]::Cyan)

$vbsPath = "$ProjectRoot\Csendes_Inditas.vbs"
if (Test-Path $vbsPath) {
    Write-InstallLog "Inditas VBS script segitsegevel (ablakmentes futtatas): $vbsPath" "INFO" ([ConsoleColor]::Gray)
    Start-Process "wscript.exe" -ArgumentList "`"$vbsPath`""
} else {
    Write-InstallLog "Inditas kozvetlenul Pythonnal: $venvPython launcher.py" "INFO" ([ConsoleColor]::Gray)
    Start-Process $venvPython -ArgumentList "`"$ProjectRoot\launcher.py`""
}

Write-InstallLog "TELEPITES ES INDITAS SIKERESEN BEFEJEZODOTT." "OK" ([ConsoleColor]::Green)

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "      SIKERES TELEPITES ES INDITAS!                          " -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green
Write-Host ""
Write-Host " - A bongeszoben par masodpercen belul megnyilik a felulet:" -ForegroundColor White
Write-Host "   http://localhost:8501" -ForegroundColor Cyan
Write-Host " - Az Asztalon elkeszult a parancsikon: '🎬 Ncore Média Letöltő'" -ForegroundColor White
Write-Host " - A program a hatterben fut a Windows talcan (az ora mellett)." -ForegroundColor White
Write-Host " - Reszletes telepitesi naplo mentve ide:" -ForegroundColor White
Write-Host "   $InstallLogFile" -ForegroundColor Gray
Write-Host ""
Write-Host "Ez az ablak 5 masodperc mulva automatikusan bezarul..." -ForegroundColor Gray
Start-Sleep -Seconds 5
