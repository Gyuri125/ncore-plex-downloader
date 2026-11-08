import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    dist_dir = project_root / "dist"
    dist_dir.mkdir(exist_ok=True)

    print("=" * 65)
    print("      🎬 NCORE ULTIMATE TELEPÍTŐ CSOMAGOLÓ 🎬")
    print("=" * 65)
    print()

    # 1. Ncore_Media_Letolto.exe újrafordítása a legfrissebb hibakezeléssel és keresővel
    print("[1/4] Legfrissebb Ncore_Media_Letolto.exe fordítása (PyInstaller)...")
    build_cmd = [
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
    res = subprocess.run(build_cmd)
    if res.returncode != 0:
        print("[FIGYELEM] PyInstaller fordítás sikertelen volt, korábbi EXE vagy forráskód használata...")
    else:
        print(" [OK] Ncore_Media_Letolto.exe sikeresen lefordítva.")

    # 2. Csomag mappa összeállítása
    pkg_dir = dist_dir / "Ncore_Ultimate_Telepito_Csomag"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    print("\n[2/4] Fájlok másolása a telepítő mappába...")

    # Kizárandó mappák és kiterjesztések
    excluded_dirs = {
        "__pycache__",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        ".git",
        ".pytest_cache",
        "build",
        "cache",
    }
    excluded_extensions = {".pyc", ".pyo", ".pyd", ".log", ".tmp"}

    # Fájlok közvetlenül a gyökérbe
    root_files = [
        "TELEPITO_ES_INDITO.bat",
        "Inditas.bat",
        "Csendes_Inditas.vbs",
        "README_TELEPITES.txt",
        "launcher.py",
        "requirements.txt",
        ".env.example",
    ]

    for rf in root_files:
        src_path = project_root / rf
        if src_path.exists():
            shutil.copy2(src_path, pkg_dir / rf)
            print(f" [+] Másolva: {rf}")

    # .env másolása (csak ha a felhasználó kifejezetten kéri a --with-env kapcsolóval)
    include_secrets = "--with-env" in sys.argv or "--include-secrets" in sys.argv
    local_env = project_root / ".env"
    if include_secrets and local_env.exists():
        shutil.copy2(local_env, pkg_dir / ".env")
        print(" [+] Másolva: .env (Személyes konfigurációval - VIGYÁZAT: titkos adatokat tartalmaz!)")
    else:
        print(" [i] Éles .env kihagyva (biztonságos csomag; használd a --with-env kapcsolót családi pendrive-hoz)")

    # Ncore_Media_Letolto.exe másolása a gyökérbe
    dist_exe = dist_dir / "Ncore_Media_Letolto.exe"
    if dist_exe.exists():
        shutil.copy2(dist_exe, pkg_dir / "Ncore_Media_Letolto.exe")
        print(" [+] Másolva: Ncore_Media_Letolto.exe")

    # Mappák másolása
    for folder in ["src", ".streamlit", "scripts"]:
        sfolder = project_root / folder
        dfolder = pkg_dir / folder
        if not sfolder.exists():
            continue

        for root, dirs, files in os.walk(sfolder):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            rel = Path(root).relative_to(sfolder)
            target_sub = dfolder / rel
            target_sub.mkdir(parents=True, exist_ok=True)

            for f in files:
                fpath = Path(root) / f
                if fpath.suffix.lower() in excluded_extensions:
                    continue
                shutil.copy2(fpath, target_sub / f)
        print(f" [+] Mappa átmásolva: {folder}/")

    # 3. ZIP Archívum készítése
    print("\n[3/4] ZIP archívum tömörítése...")
    zip_path = dist_dir / "Ncore_Ultimate_Telepito_Csomag.zip"
    if zip_path.exists():
        zip_path.unlink()

    total_files = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(pkg_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(pkg_dir)
                zipf.write(file_path, arcname=arcname)
                total_files += 1

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f" [OK] ZIP elkészült: {zip_path.name} ({zip_size_mb:.2f} MB, {total_files} fájl)")

    # 4. Másolás az Asztalra a gyors pendrive-ra húzáshoz
    desktop_dir = Path.home() / "Desktop"
    desktop_zip = desktop_dir / "Ncore_Ultimate_Telepito_Csomag.zip"
    try:
        shutil.copy2(zip_path, desktop_zip)
        print(f"\n[4/4] Másolva az Asztalra a kényelmes pendrive-ra másoláshoz:\n      {desktop_zip}")
    except Exception as e:
        print(f" [FIGYELEM] Nem sikerült az Asztalra másolni: {e}")

    print("\n" + "=" * 65)
    print("      SIKERES CSOMAGOLÁS! MINDEN KÉSZEN ÁLL!")
    print("=" * 65)
    print(f"\nTelepítő Mappa: {pkg_dir}")
    print(f"Telepítő ZIP:   {zip_path}")
    print(f"Asztalon lévő:  {desktop_zip}")
    print("\nHogyan használd apa gépén:")
    print(" 1. Másold át a 'Ncore_Ultimate_Telepito_Csomag.zip' fájlt pendrive-on.")
    print(" 2. Csomagold ki egy tetszőleges mappába (pl. C:\\Segedprogramok\\Ncore).")
    print(" 3. Kattints jobb gombbal a 'TELEPITO_ES_INDITO.bat' fájlra -> 'Futtatás rendszergazdaként'!")
    print(" 4. Minden magától feltelepül, és a böngésző megnyílik!\n")


if __name__ == "__main__":
    main()
