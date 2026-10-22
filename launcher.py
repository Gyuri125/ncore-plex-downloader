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
    """Megkeresi a projekt gyökérmappáját."""
    # 1. Ha a projekt mappájából fut
    current_dir = Path(__file__).resolve().parent
    if (current_dir / "src" / "ui" / "app.py").exists():
        return current_dir

    # 2. Ha az Asztalról vagy máshonnan fut az exe, fix projekt mappa
    fixed_path = Path(r"C:\Users\gyuri\PycharmProjects\Ncore_Film&Sori_letöltő")
    if (fixed_path / "src" / "ui" / "app.py").exists():
        return fixed_path

    # 3. Keresés a felhasználó PycharmProjects mappájában
    user_home = Path.home()
    candidate = user_home / "PycharmProjects" / "Ncore_Film&Sori_letöltő"
    if (candidate / "src" / "ui" / "app.py").exists():
        return candidate

    return current_dir


def find_python_interpreter(project_root: Path) -> str:
    """Megkeresi a megfelelő Python értelmezőt (.venv vagy rendszer szintű)."""
    # 1. .venv a projektben
    venv_py = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)

    # 2. venv a projektben
    venv_py2 = project_root / "venv" / "Scripts" / "python.exe"
    if venv_py2.exists():
        return str(venv_py2)

    # 3. Rendszer python a PATH-on
    which_py = shutil.which("python")
    if which_py:
        return which_py

    # 4. Standard Python 3.12 telepítési mappa
    default_py312 = Path(r"C:\Users\gyuri\AppData\Local\Programs\Python\Python312\python.exe")
    if default_py312.exists():
        return str(default_py312)

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
