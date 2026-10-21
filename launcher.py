import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def open_browser(url: str):
    """Vár 1.8 másodpercet, majd automatikusan megnyitja az alapértelmezett böngészőt."""
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

    # 2. Ha az Asztalról vagy máshonnan fut az exe, a fix projekt mappa
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


def main():
    project_root = find_project_root()
    app_script = project_root / "src" / "ui" / "app.py"
    python_bin = find_python_interpreter(project_root)

    print("==============================================================")
    print("      Ncore Media Letolto - Inditas...")
    print("==============================================================")
    print(f"[OK] Projekt mappa: {project_root}")
    print(f"[OK] Python: {python_bin}")
    print("[OK] Grafikus felulet megnyitasa a bongeszoben...")
    print("--------------------------------------------------------------")
    print("A felulet automatikusan megnyilik a bongeszodben.")
    print("Helyi cim: http://localhost:8501")
    print("A leallitashoz zard be ezt az ablakot.")
    print("==============================================================")
    print()

    target_url = "http://localhost:8501"

    # Böngésző megnyitása a háttérben
    threading.Thread(target=open_browser, args=(target_url,), daemon=True).start()

    # Streamlit indítása a megfelelő munkamappában
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

    try:
        process = subprocess.run(cmd, cwd=str(project_root))
        sys.exit(process.returncode)
    except KeyboardInterrupt:
        print("\nLeállítás...")
        sys.exit(0)
    except Exception as e:
        print(f"\nHiba az indításkor: {e}")
        input("\nNyomj Entert a kilepeshez...")
        sys.exit(1)


if __name__ == "__main__":
    main()
