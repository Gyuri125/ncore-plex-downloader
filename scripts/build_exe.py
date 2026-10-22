import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    project_dir = Path(__file__).resolve().parent.parent
    os.chdir(project_dir)

    desktop_dir = Path.home() / "Desktop"
    exe_name = "Ncore_Media_Letolto.exe"
    target_exe = desktop_dir / exe_name

    print("=" * 62)
    print("      Ncore Media Letolto - Automata Asztali EXE Fordito")
    print("=" * 62)
    print()

    # 1. Futó folyamat leállítása
    print("[1/4] Futo folyamatok ellenorzese es bezarasa...")
    subprocess.run(["taskkill", "/f", "/im", exe_name], capture_output=True)
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == 8501 and conn.pid:
                try:
                    p = psutil.Process(conn.pid)
                    print(f"       -> Streamlit folyamat leallitasa (PID: {conn.pid})...")
                    p.kill()
                except Exception:
                    pass
    except Exception:
        pass

    # 2. PyInstaller fordítás
    print("[2/4] Legfrissebb forraskod forditasa (PyInstaller)...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--hidden-import=qrcode",
        "--hidden-import=psutil",
        "--hidden-import=loguru",
        "--name",
        "Ncore_Media_Letolto",
        "launcher.py",
    ]

    res = subprocess.run(cmd)
    if res.returncode != 0:
        print("\n[HIBA] A PyInstaller forditas nem sikerult!")
        sys.exit(1)

    # 3. Másolás Asztalra
    print("\n[3/4] Az uj EXE masolasa az Asztalra...")
    dist_exe = project_dir / "dist" / exe_name
    if not dist_exe.exists():
        print("[HIBA] A generalt fajl nem talalhato a dist mappaban!")
        sys.exit(1)

    try:
        shutil.copy2(dist_exe, target_exe)
        print("=" * 62)
        print("SIKER! Az uj verzio elkeszult es felulirta a regit az Asztalon:")
        print(f"   {target_exe}")
        print("=" * 62)
    except Exception as e:
        print(f"[HIBA] Nem sikerult felulirni a fajlt az Asztalon: {e}")
        sys.exit(1)

    print("\n[4/4] Befejezve.")
    print("Mostantol az Asztalrol duplakattintassal indithatod a legfrissebb verziot!\n")


if __name__ == "__main__":
    main()
