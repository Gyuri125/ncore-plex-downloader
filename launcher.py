import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from loguru import logger


def hide_console_window():
    """Elrejti a fekete konzol parancssor ablakot Windows alatt."""
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                # 0 = SW_HIDE (ablak elrejtése a háttérbe)
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass


def open_browser(url: str):
    """Vár 1.8 másodpercet a szerver éledésére, majd megnyitja a böngészőt."""
    time.sleep(1.8)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def show_error_dialog(title: str, message: str):
    """Natív Windows hibaablakot jelenít meg a felhasználónak."""
    if sys.platform == "win32":
        try:
            # 0x10 = MB_ICONERROR, 0x00 = MB_OK, 0x10000 = MB_SETFOREGROUND
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10 | 0x10000)
            return
        except Exception:
            pass
    print(f"[{title}] {message}", file=sys.stderr)


def find_project_root() -> Path:
    """Megkeresi a projekt gyökérmappáját tetszőleges számítógépen és könyvtárszerkezetben."""
    search_bases = []

    # 1. launcher.py saját mappája
    try:
        search_bases.append(Path(__file__).resolve().parent)
    except Exception:
        pass

    # 2. PyInstaller futtatható állomány (.exe) mappája
    try:
        search_bases.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass

    # 3. Aktuális munkakönyvtár
    try:
        search_bases.append(Path.cwd().resolve())
    except Exception:
        pass

    # Ellenőrizzük a bázis mappákat, valamint közvetlen gyermekeiket és szülőjüket
    for base in list(search_bases):
        # Közvetlen egyezés
        if (base / "src" / "ui" / "app.py").is_file():
            return base

        # Alkonyvtárak ellenőrzése (pl. ncore-plex-downloader, src, stb.)
        try:
            if base.is_dir():
                for child in base.iterdir():
                    if child.is_dir() and (child / "src" / "ui" / "app.py").is_file():
                        return child
        except Exception:
            pass

        # Szülőkönyvtár és szülő testvérkönyvtárainak ellenőrzése
        try:
            parent = base.parent
            if (parent / "src" / "ui" / "app.py").is_file():
                return parent
            if parent.is_dir():
                for child in parent.iterdir():
                    if child.is_dir() and (child / "src" / "ui" / "app.py").is_file():
                        return child
        except Exception:
            pass

    # 4. Keresés standard könyvtárakban
    user_home = Path.home()
    standard_dirs = [
        Path(r"C:\Segedprogramok\Ncore"),
        Path(r"C:\Segedprogramok"),
        user_home / "PycharmProjects",
        user_home / "Desktop",
        user_home / "Documents",
        user_home / "Downloads",
        Path(r"C:\Media"),
    ]
    cand_names = [
        "ncore-plex-downloader",
        "Ncore_Film&Sori_letöltő",
        "Ncore_Media_Letolto",
        "ncore_downloader",
    ]

    for sdir in standard_dirs:
        if not sdir.exists():
            continue
        for cname in cand_names:
            target = sdir / cname
            if (target / "src" / "ui" / "app.py").is_file():
                return target
            if (target / "ncore-plex-downloader" / "src" / "ui" / "app.py").is_file():
                return target / "ncore-plex-downloader"

    # Ha sehol nem találta, dobjunk egyértelmű hibát
    current = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    if (current / "src" / "ui" / "app.py").is_file():
        return current

    raise FileNotFoundError(
        "Nem található a program forráskódja ('src/ui/app.py')!\n\n"
        "Kérlek bizonyosodj meg róla, hogy a letöltött ZIP csomagot teljes egészében kicsomagoltad, "
        "és a fájlok egy mappában találhatók."
    )


def find_python_interpreter(project_root: Path) -> str:
    """Megkeresi a megfelelő Python értelmezőt (.venv, venv, PATH vagy dinamikus telepítések)."""
    # 1. Helyi virtuális környezet (.venv vagy venv a projektben vagy a szülőben)
    for cand in [
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / "venv" / "Scripts" / "python.exe",
        project_root.parent / ".venv" / "Scripts" / "python.exe",
        project_root.parent / "venv" / "Scripts" / "python.exe",
    ]:
        if cand.exists():
            return str(cand)

    # 2. Rendszer python a PATH-on
    for cmd in ["python", "python3", "py"]:
        which_py = shutil.which(cmd)
        if which_py:
            return which_py

    # 3. Standard 64-bites és 32-bites Program Files könyvtárak
    for base_pf in [r"C:\Program Files", r"C:\Program Files (x86)"]:
        pf_path = Path(base_pf)
        if pf_path.exists():
            try:
                candidates = sorted(pf_path.glob("Python3*/python.exe"), reverse=True)
                for cand in candidates:
                    if cand.exists():
                        return str(cand)
            except Exception:
                pass

    # 4. Dinamikus keresés a felhasználó AppData\Local\Programs\Python mappájában
    try:
        user_python_dir = Path.home() / "AppData" / "Local" / "Programs" / "Python"
        if user_python_dir.exists():
            candidates = sorted(user_python_dir.glob("Python*/python.exe"), reverse=True)
            for cand in candidates:
                if cand.exists():
                    return str(cand)
    except Exception:
        pass

    return sys.executable or "python"


def kill_port_occupants(port: int = 8501):
    """Leállítja a portot (pl. 8501) foglaló régi processeket."""
    try:
        import psutil
        current_pid = os.getpid()
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid and conn.pid != current_pid:
                try:
                    p = psutil.Process(conn.pid)
                    logger.info(f"Port {port} foglalt, korábbi folyamat leállítása (PID: {conn.pid}, Név: {p.name()})...")
                    p.kill()
                    p.wait(timeout=2)
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Port tisztítási hiba: {e}")


def main():
    project_root = find_project_root()
    os.chdir(project_root)

    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Ha a szülő mappában van .env, de a gyökérben nincs, másoljuk át automatikusan!
    if not (project_root / ".env").exists():
        for cand_env in [
            project_root.parent / ".env",
            Path(sys.executable).resolve().parent / ".env",
            Path.cwd().parent / ".env"
        ]:
            if cand_env.exists() and cand_env != (project_root / ".env"):
                try:
                    shutil.copy2(cand_env, project_root / ".env")
                    break
                except Exception:
                    pass

    # Naplózás inicializálása
    from src.core.logger import setup_logging
    setup_logging(project_root)

    # Konzol ablak elrejtése, hogy ne zavarja a felhasználót
    hide_console_window()

    # Port ellenőrzése és korábbi beragadt folyamatok tisztítása
    kill_port_occupants(8501)

    app_script = project_root / "src" / "ui" / "app.py"
    python_bin = find_python_interpreter(project_root)
    target_url = "http://localhost:8501"

    # Szolgáltatások importálása
    from src.core.background_worker import BackgroundWorker
    from src.core.orchestrator import Orchestrator
    from src.services.torrent_service import TorrentService
    from src.services.tray_service import TrayService

    # 1. qBittorrent automatikus indítása ha még nem fut
    logger.info("qBittorrent ellenőrzése és automatikus indítása...")
    TorrentService.ensure_qbittorrent_running()

    # 2. Központi munkafolyamat vezérlő
    orchestrator = Orchestrator()

    # Streamlit folyamat referencia
    streamlit_proc: list = [None]
    worker_ref: list = [None]
    tray_ref: list = [None]

    def cleanup_and_exit():
        """Tisztán leállítja a szervert és a háttérfolyamatokat."""
        logger.info("Alkalmazás leállítása folyamatban...")
        if worker_ref[0]:
            try:
                worker_ref[0].stop()
            except Exception:
                pass

        if streamlit_proc[0]:
            try:
                streamlit_proc[0].terminate()
                streamlit_proc[0].wait(timeout=2)
            except Exception:
                try:
                    streamlit_proc[0].kill()
                except Exception:
                    pass

        if tray_ref[0]:
            try:
                tray_ref[0].stop()
            except Exception:
                pass

        os._exit(0)

    # 3. Tálcaikon inicializálása
    tray = TrayService(
        web_url=target_url,
        on_exit=cleanup_and_exit,
        on_refresh_plex=lambda: orchestrator.plex_service.refresh_library(),
    )
    tray_ref[0] = tray

    # 4. Önálló Háttérfolyamat elindítása (30 mp-es ciklus a kész letöltések Plexbe rendezéséhez)
    worker = BackgroundWorker(
        orchestrator=orchestrator,
        tray_service=tray,
        interval_seconds=15,
    )
    worker_ref[0] = worker
    worker.start()

    # 5. Böngésző automatikus megnyitása
    threading.Thread(target=open_browser, args=(target_url,), daemon=True).start()

    # 6. Streamlit indítása ablak nélküli háttérfolyamatként
    cmd = [
        python_bin,
        "-m",
        "streamlit",
        "run",
        str(app_script),
        "--server.port=8501",
        "--server.address=0.0.0.0",
        "--browser.gatherUsageStats=false",
    ]

    creation_flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            creationflags=creation_flags,
        )
        streamlit_proc[0] = proc
    except Exception as e:
        logger.error(f"Hiba a Streamlit indításakor: {e}")
        tray.notify("Hiba az indításkor", f"Nem sikerült a felület indítása: {e}")

    # 7. Tálcaikon futtatása a főszálon (blokkoló eseményhurok a Windows számára)
    try:
        tray.start(blocking=True)
    except KeyboardInterrupt:
        cleanup_and_exit()
    except Exception as e:
        logger.error(f"Váratlan hiba a tálcaikon futásakor: {e}")
        cleanup_and_exit()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import datetime
        import traceback
        err_msg = traceback.format_exc()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            logger.error(f"Kritikus hiba az indításkor: {err_msg}")
        except Exception:
            pass

        # Naplózás gyökér és logs/ mappába
        try:
            logs_dir = Path("logs")
            logs_dir.mkdir(parents=True, exist_ok=True)
            with open("startup_error.log", "w", encoding="utf-8") as f:
                f.write(err_msg)
            with open(logs_dir / "startup_error.log", "w", encoding="utf-8") as f:
                f.write(err_msg)
            with open(logs_dir / "telepites_log.txt", "a", encoding="utf-8") as f:
                f.write(f"\n[{now_str}] [KRITIKUS INDÍTÁSI HIBA]\n{err_msg}\n")
        except Exception:
            pass

        show_error_dialog(
            "Ncore Média Letöltő - Indítási Hiba",
            f"Nem sikerült az alkalmazás elindítása:\n\n{exc}\n\n"
            f"Részletes indítási napló mentve ide:\n"
            f" - logs\\startup_error.log\n"
            f" - logs\\telepites_log.txt\n\n"
            "Tipp: Futtasd a TELEPITO_ES_INDITO.bat fájlt a környezet és a függőségek automatikus helyreállításához!"
        )
        sys.exit(1)
