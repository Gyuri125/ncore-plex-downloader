import os
import sys
import time
import webbrowser
import threading
from pathlib import Path


def open_browser():
    """Vár 1.5 másodpercet, amíg a Streamlit szerver elindul, majd megnyitja a böngészőt."""
    time.sleep(1.5)
    webbrowser.open("http://localhost:8501")


def main():
    # Munka mappa beállítása a projekt gyökerére
    if getattr(sys, "frozen", False):
        # Ha PyInstaller által fordított .exe fut
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parent

    os.chdir(base_dir)

    # Elérési út a Streamlit felülethez
    app_script = base_dir / "src" / "ui" / "app.py"

    # Ha valamiért nem található relatívan (pl. az asztalra másolt önálló exe esetén),
    # keresse meg a Pycharm projekt mappát
    if not app_script.exists():
        fallback_dir = Path(r"c:\Users\gyuri\PycharmProjects\Ncore_Film&Sori_letöltő")
        if (fallback_dir / "src" / "ui" / "app.py").exists():
            base_dir = fallback_dir
            os.chdir(base_dir)
            app_script = base_dir / "src" / "ui" / "app.py"

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        str(app_script),
        "--server.port=8501",
        "--server.address=0.0.0.0",
        "--server.headless=true",
        "--global.developmentMode=false",
    ]

    # Böngésző automatikus megnyitása háttérszálban
    threading.Thread(target=open_browser, daemon=True).start()

    # Streamlit indítása
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
