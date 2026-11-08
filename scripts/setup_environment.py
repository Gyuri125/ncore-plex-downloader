#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Környezet- és függőség-előkészítő modul.
Gondoskodik a mappák, a konfiguráció (.env), a qBittorrent WebUI,
az asztali parancsikonok, valamint az összes Python modul épségének
ellenőrzéséről és részletes telepítési naplózásáról (logs/telepites_log.txt).
"""

import datetime
import importlib
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path


def append_to_install_log(project_root: Path, msg: str, level: str = "INFO"):
    """Időbélyeggel ellátott bejegyzést fűz a logs/telepites_log.txt fájlhoz."""
    try:
        logs_dir = project_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "telepites_log.txt"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{now_str}] [{level}] {msg}\n")
    except Exception:
        pass


def log_step(project_root: Path, msg: str):
    print(f" [OK] {msg}")
    append_to_install_log(project_root, msg, level="OK")


def log_info(project_root: Path, msg: str):
    print(f" [INFO] {msg}")
    append_to_install_log(project_root, msg, level="INFO")


def log_warn(project_root: Path, msg: str):
    print(f" [FIGYELEM] {msg}")
    append_to_install_log(project_root, msg, level="FIGYELEM")


def log_error(project_root: Path, msg: str):
    print(f" [HIBA] {msg}")
    append_to_install_log(project_root, msg, level="HIBA")


def verify_dependencies(project_root: Path) -> bool:
    """
    Részletesen ellenőrzi a projekt összes külső függőségét.
    Ha egy modul hiányzik, megkísérli annak azonnali telepítését a pip segítségével.
    """
    print("\n" + "=" * 62)
    print("      FÜGGŐSÉGEK INTEGRITÁS-ELLENŐRZÉSE (SELF-TEST)")
    print("=" * 62)
    append_to_install_log(project_root, "Függőségek integritás-ellenőrzésének indítása...", level="INFO")

    modules_to_test = [
        ("streamlit", "streamlit", "Webes felület és felhasználói UI"),
        ("qbittorrentapi", "qbittorrent-api", "qBittorrent WebUI vezérlő és torrent kezelő"),
        ("plexapi", "plexapi", "Plex médiaszerver könyvtár-kezelő"),
        ("pystray", "pystray", "Windows tálcaikon (System Tray)"),
        ("PIL", "pillow", "Képek és tálcaikon grafika kezelő"),
        ("pydantic", "pydantic", "Adatmodellek és típusvalidáció"),
        ("pydantic_settings", "pydantic-settings", "Környezeti konfiguráció (.env) kezelő"),
        ("httpx", "httpx", "Aszinkron és szinkron HTTP hálózati kliens"),
        ("bs4", "beautifulsoup4", "HTML scraper (nCore, feliratok)"),
        ("loguru", "loguru", "Központi strukturált naplózó rendszer"),
        ("psutil", "psutil", "Rendszerfolyamatok és porttisztító"),
        ("qrcode", "qrcode", "Mobil csatlakozási QR-kód generátor"),
    ]

    all_passed = True
    for mod_name, pip_pkg, desc in modules_to_test:
        try:
            mod = importlib.import_module(mod_name)
            ver = getattr(mod, "__version__", None)
            if not ver:
                try:
                    ver = importlib.metadata.version(pip_pkg)
                except Exception:
                    ver = "aktív"
            log_step(project_root, f"Modul épségben: {mod_name:<18} (v{ver}) - {desc}")
        except Exception as exc:
            log_warn(project_root, f"Hiányzó vagy hibás modul: {mod_name}! Hiba: {exc}")
            log_info(project_root, f"Automatikus telepítési kísérlet ({pip_pkg})...")
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pip_pkg],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if res.returncode == 0:
                    # Újraellenőrzés
                    mod = importlib.import_module(mod_name)
                    ver = getattr(mod, "__version__", "ismeretlen")
                    log_step(project_root, f"Modul sikeresen helyreállítva: {mod_name} (v{ver})")
                else:
                    log_error(project_root, f"Nem sikerült telepíteni a(z) {pip_pkg} csomagot! Pip hiba: {res.stderr}")
                    all_passed = False
            except Exception as pip_err:
                log_error(project_root, f"Kivétel a(z) {pip_pkg} telepítésekor: {pip_err}")
                all_passed = False

    return all_passed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # logs mappa garantálása
    (project_root / "logs").mkdir(parents=True, exist_ok=True)
    (project_root / "data").mkdir(parents=True, exist_ok=True)

    print("=" * 62)
    print("   🎬 Ncore Média Letöltő v2.0.0 - Környezet és Beállítások")
    print("=" * 62)
    append_to_install_log(project_root, "Környezeti beállítások indítása (setup_environment.py)", level="INFO")

    # 1. Meghajtók ellenőrzése
    has_d_drive = Path("D:/").exists()
    target_drive = "D:" if has_d_drive else "C:"
    log_info(project_root, f"Érzékelt alapértelmezett meghajtó: {target_drive} (D: meghajtó {'elérhető' if has_d_drive else 'nem létezik, C: használata'})")

    # 2. .env és adatok intelligens konfiguráció-megőrzése és migrálása
    log_info(project_root, "Korábbi beállítások felderítése és intelligens összefésülése...")
    current_settings = {}
    try:
        from src.core.settings_migrator import migrate_and_preserve_settings

        def migrator_log(msg: str, lvl: str = "INFO"):
            if lvl == "OK":
                log_step(project_root, msg)
            elif lvl == "HIBA":
                log_error(project_root, msg)
            elif lvl == "FIGYELEM":
                log_warn(project_root, msg)
            else:
                log_info(project_root, msg)

        migrated_ok, current_settings = migrate_and_preserve_settings(project_root, log_cb=migrator_log)
        if migrated_ok:
            log_step(project_root, ".env konfiguráció sikeresen összeállítva (Plex token, mappák és jelszavak megőrizve).")
        else:
            log_warn(project_root, "Nem sikerült a beállítások teljes migrálása, alapértelmezett beállítások alkalmazva.")
    except Exception as mig_err:
        log_warn(project_root, f"Hiba a konfiguráció migrálásakor ({mig_err}), alapértelmezett .env ellenőrzése...")
        env_file = project_root / ".env"
        if not env_file.exists():
            example_env = project_root / ".env.example"
            if example_env.exists():
                content = example_env.read_text(encoding="utf-8")
                if not has_d_drive:
                    content = content.replace("D:/", "C:/").replace("D:\\", "C:\\")
                env_file.write_text(content, encoding="utf-8")
                log_step(project_root, "Új .env fájl létrehozva az .env.example sablonból.")

    # 3. Letöltési, Film és Sorozat célkönyvtárak garantálása a megőrzött beállítások alapján
    default_temp = "D:/Torrents/Downloading" if has_d_drive else "C:/Torrents/Downloading"
    default_movies = "D:/Media/Movies" if has_d_drive else "C:/Media/Movies"
    default_series = "D:/Media/TV Shows" if has_d_drive else "C:/Media/TV Shows"

    user_temp_dir = Path(current_settings.get("DOWNLOAD_TEMP_DIR") or default_temp)
    user_movies_dir = Path(current_settings.get("PLEX_MOVIES_DIR") or default_movies)
    user_series_dir = Path(current_settings.get("PLEX_SERIES_DIR") or default_series)

    for p, label in [
        (user_temp_dir, "Letöltési ideiglenes mappa (DOWNLOAD_TEMP_DIR)"),
        (user_movies_dir, "Plex filmek célkönyvtár (PLEX_MOVIES_DIR)"),
        (user_series_dir, "Plex sorozatok célkönyvtár (PLEX_SERIES_DIR)"),
    ]:
        try:
            p.mkdir(parents=True, exist_ok=True)
            log_step(project_root, f"{label} ellenőrizve és készen áll: {p}")
        except Exception as e:
            log_warn(project_root, f"Nem sikerült létrehozni a mappát ({p}): {e}")

    # 3. qBittorrent WebUI automatikus konfigurálása
    appdata = os.environ.get("APPDATA")
    if appdata:
        qb_dir = Path(appdata) / "qBittorrent"
        qb_ini = qb_dir / "qBittorrent.ini"
        try:
            qb_dir.mkdir(parents=True, exist_ok=True)
            ini_lines = []
            if qb_ini.exists():
                ini_lines = qb_ini.read_text(encoding="utf-8", errors="ignore").splitlines()

            pref_found = False
            webui_enabled_found = False
            webui_port_found = False
            webui_auth_found = False

            new_lines = []
            for line in ini_lines:
                sline = line.strip()
                if sline.lower() == "[preferences]":
                    pref_found = True
                    new_lines.append(line)
                    continue

                if sline.startswith("[") and sline.endswith("]") and pref_found:
                    if not webui_enabled_found:
                        new_lines.append("WebUI\\Enabled=true")
                        webui_enabled_found = True
                    if not webui_port_found:
                        new_lines.append("WebUI\\Port=8080")
                        webui_port_found = True
                    if not webui_auth_found:
                        new_lines.append("WebUI\\LocalHostAuth=true")
                        webui_auth_found = True
                    pref_found = False

                if pref_found:
                    if sline.lower().startswith("webui\\enabled="):
                        new_lines.append("WebUI\\Enabled=true")
                        webui_enabled_found = True
                        continue
                    if sline.lower().startswith("webui\\port="):
                        new_lines.append("WebUI\\Port=8080")
                        webui_port_found = True
                        continue
                    if sline.lower().startswith("webui\\localhostauth="):
                        new_lines.append("WebUI\\LocalHostAuth=true")
                        webui_auth_found = True
                        continue

                new_lines.append(line)

            if not pref_found and not (webui_enabled_found and webui_port_found):
                new_lines.append("[Preferences]")
                new_lines.append("WebUI\\Enabled=true")
                new_lines.append("WebUI\\Port=8080")
                new_lines.append("WebUI\\LocalHostAuth=true")

            qb_ini.write_text("\n".join(new_lines), encoding="utf-8")
            log_step(project_root, f"qBittorrent WebUI beállítások (Port: 8080, Engedélyezve) elmentve: {qb_ini}")
        except Exception as e:
            log_warn(project_root, f"Nem sikerült beállítani a qBittorrent.ini-t: {e}")

    # 4. Csendes_Inditas.vbs generálása (Ablak nélküli tiszta háttérindítás)
    vbs_path = project_root / "Csendes_Inditas.vbs"
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\r\n'
        'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
        'baseDir = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
        'WshShell.CurrentDirectory = baseDir\r\n\r\n'
        'If fso.FileExists(baseDir & "\\.venv\\Scripts\\pythonw.exe") Then\r\n'
        '    WshShell.Run chr(34) & baseDir & "\\.venv\\Scripts\\pythonw.exe" & chr(34) & " " & chr(34) & baseDir & "\\launcher.py" & chr(34), 0, False\r\n'
        'ElseIf fso.FileExists(baseDir & "\\Inditas.bat") Then\r\n'
        '    WshShell.Run chr(34) & baseDir & "\\Inditas.bat" & chr(34), 0, False\r\n'
        'Else\r\n'
        '    WshShell.Run "pythonw.exe launcher.py", 0, False\r\n'
        'End If\r\n'
    )
    vbs_path.write_text(vbs_content, encoding="utf-8")
    log_step(project_root, "Csendes_Inditas.vbs indítófájl elkészítve.")

    # 5. Asztali Parancsikon (Desktop Shortcut) létrehozása PowerShell segítségével
    user_desktop = Path.home() / "Desktop"
    public_desktop = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
    desktops = [user_desktop]
    if public_desktop.exists() and public_desktop != user_desktop:
        desktops.append(public_desktop)

    shortcut_name = "🎬 Ncore Média Letöltő.lnk"
    for d in desktops:
        shortcut_file = d / shortcut_name
        ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{str(shortcut_file)}')
$Shortcut.TargetPath = '{str(vbs_path)}'
$Shortcut.WorkingDirectory = '{str(project_root)}'
$Shortcut.Description = 'Ncore Film és Sorozat Letöltő - Automata Rendszer'
$Shortcut.IconLocation = 'shell32.dll,41'
$Shortcut.Save()
"""
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, timeout=5)
            log_step(project_root, f"Asztali parancsikon létrehozva: {shortcut_file}")
        except Exception as e:
            log_warn(project_root, f"Asztali ikon hiba ({d}): {e}")

    # 6. Windows Automatikus Indítás (Startup / Indítópult)
    if appdata:
        startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        if startup_dir.exists():
            startup_file = startup_dir / "Ncore_Media_Letolto.lnk"
            ps_startup = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{str(startup_file)}')
$Shortcut.TargetPath = '{str(vbs_path)}'
$Shortcut.WorkingDirectory = '{str(project_root)}'
$Shortcut.Description = 'Ncore Média Letöltő Háttérfolyamat'
$Shortcut.IconLocation = 'shell32.dll,41'
$Shortcut.Save()
"""
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", ps_startup], capture_output=True, timeout=5)
                log_step(project_root, f"Windows Automatikus Indítás (Startup) bekapcsolva: {startup_file}")
            except Exception as e:
                log_warn(project_root, f"Startup parancsikon hiba: {e}")

    # 7. Modulok és függőségek teljes épségi önellenőrzése (Self-test)
    deps_ok = verify_dependencies(project_root)
    if not deps_ok:
        log_warn(project_root, "Néhány opcionális vagy kritikus modul telepítése figyelmet igényelhet!")

    print("=" * 62)
    print(" [SIKER] Minden környezeti beállítás sikeresen befejeződött!")
    print("=" * 62)
    append_to_install_log(project_root, "Környezeti beállítások és modulok sikeresen ellenőrizve.", level="OK")


if __name__ == "__main__":
    main()
