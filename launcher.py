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


def find_project_root() -> Path:
    """Megkeresi a projekt gyökérmappáját tetszőleges számítógépen és könyvtárban."""
    # 1. Ha a launcher.py fájl mellett van a forráskód
    current_dir = Path(__file__).resolve().parent
    if (current_dir / "src" / "ui" / "app.py").exists():
        return current_dir

    # 2. Ha PyInstaller onefile EXE fut, a futtatható fájl mappáját vizsgáljuk
    try:
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "src" / "ui" / "app.py").exists():
            return exe_dir
    except Exception:
        pass

    # 3. Aktuális munkakönyvtár vizsgálata
    try:
        cwd = Path.cwd()
        if (cwd / "src" / "ui" / "app.py").exists():
            return cwd
    except Exception:
        pass

    # 4. Keresés a felhasználó standard könyvtáraiban dinamikusan
    user_home = Path.home()
    for sub in ["PycharmProjects", "Documents", "Desktop", "Downloads"]:
        for cand_name in ["ncore-plex-downloader", "Ncore_Film&Sori_letöltő", "Ncore_Media_Letolto", "ncore_downloader"]:
            cand = user_home / sub / cand_name
            if (cand / "src" / "ui" / "app.py").exists():
                return cand

    # 5. Fix teszt útvonal fallback
    fixed_path = Path(r"C:\Users\gyuri\PycharmProjects\Ncore_Film&Sori_letöltő")
    if (fixed_path / "src" / "ui" / "app.py").exists():
        return fixed_path

    return current_dir


def find_python_interpreter(project_root: Path) -> str:
    """Megkeresi a megfelelő Python értelmezőt (.venv, venv, PATH vagy dinamikus AppData)."""
    # 1. .venv a projektben
    venv_py = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)

    # 2. venv a projektben
    venv_py2 = project_root / "venv" / "Scripts" / "python.exe"
    if venv_py2.exists():
        return str(venv_py2)

    # 3. Rendszer python a PATH-on
    which_py = shutil.which("python") or shutil.which("python3")
    if which_py:
        return which_py

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

    return "python"


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
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

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
    main()
