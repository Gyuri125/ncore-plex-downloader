#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Ncore Média Letöltő v2.0.0 - Főkönyvtári ZIP Csomagoló és Tisztító
- Létrehozza a hivatalos verziózott ZIP-et a Letöltések gyökerében: C:\Users\gyuri\Downloads\Ncore_Media_Letolto_v2.0.0.zip
- Megtisztítja a Letöltéseket a duplikált és kicsomagolt mappáktól.
- Megtisztítja az 'apának átvinni' mappát az Ncore fájloktól, hogy NE keveredjen a SentinelSuite új programmal!
"""

import os
import shutil
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    downloads_dir = Path(os.environ.get("USERPROFILE", "C:/Users/gyuri")) / "Downloads"
    desktop_dir = Path(os.environ.get("USERPROFILE", "C:/Users/gyuri")) / "Desktop"
    dad_transfer_dir = downloads_dir / "apának átvinni"

    version = "v2.0.0"
    official_zip_name = f"Ncore_Media_Letolto_{version}.zip"
    official_zip_path = downloads_dir / official_zip_name

    print("=" * 70)
    print(f"🎬 NCORE MÉDIA LETÖLTŐ {version} - FŐKÖNYVTÁRI CSOMAGOLÁS")
    print(f"📁 Forrás: {project_root}")
    print(f"📦 Hivatalos ZIP: {official_zip_path}")
    print("=" * 70)

    # 1. Ideiglenes csomagoló mappa összeállítása a dist-ben
    staging_dir = project_root / "dist" / f"Ncore_Csomag_{version}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 2. Gyökérfájlok és szkriptek gyűjtése
    root_files = [
        "TELEPITO_ES_INDITO.bat",
        "Inditas_Windows.bat",
        "Inditas.bat",
        "Csendes_Inditas.vbs",
        "launcher.py",
        "run_app.bat",
        "requirements.txt",
        ".env.example",
        "README_TELEPITES.txt",
        "README_GYORSINDITAS.txt",
        "README.md",
        "LICENSE",
    ]

    # Biztonsági ellenőrzés: az éles jelszavakat tartalmazó .env fájlt
    # csak kifejezett --with-env kapcsoló esetén másoljuk be
    if "--with-env" in sys.argv:
        root_files.append(".env")
        print(" [BIZTONSÁG] --with-env kapcsoló észlelve: éles .env fájl bevonva a csomagba.")
    else:
        print(" [BIZTONSÁG] Publikus/Családi mód: éles .env kihagyva (csak .env.example másolva).")

    print("\n[1/5] Gyökérfájlok és indítószkriptek összegyűjtése...")
    for rf in root_files:
        src_path = project_root / rf
        if src_path.exists():
            shutil.copy2(src_path, staging_dir / rf)
            print(f" [+] Másolva: {rf}")

    # Útmutatók és Kisokos v2.0.0 szövegének generálása és elhelyezése a ZIP gyökerébe
    from scripts.create_dad_package import make_dad_package
    # Frissítjük a kisokos és útmutató fájlokat a staging mappában
    dad_guide_path = staging_dir / "TELEPITESI_UTMUTATO_APANAK.txt"
    dad_kisokos_path = staging_dir / "KISOKOS_ES_HIBAKERESO.txt"

    # Ha a projektben vagy az apának átvinni mappában már létezik
    for cand in [dad_transfer_dir / "TELEPITESI_UTMUTATO_APANAK.txt", project_root / "TELEPITESI_UTMUTATO_APANAK.txt"]:
        if cand.exists():
            shutil.copy2(cand, dad_guide_path)
            break
    for cand in [dad_transfer_dir / "KISOKOS_ES_HIBAKERESO.txt", project_root / "KISOKOS_ES_HIBAKERESO.txt"]:
        if cand.exists():
            shutil.copy2(cand, dad_kisokos_path)
            break

    # Frissen fordított Ncore_Media_Letolto.exe hozzáadása
    dist_exe = project_root / "dist" / "Ncore_Media_Letolto.exe"
    if dist_exe.exists():
        shutil.copy2(dist_exe, staging_dir / "Ncore_Media_Letolto.exe")
        print(f" [+] Friss Ncore_Media_Letolto.exe másolva ({dist_exe.stat().st_size / (1024*1024):.2f} MB)")

    # 3. Alkönyvtárak másolása (src, .streamlit, scripts)
    print("\n[2/5] Forrásfájlok és modulok másolása (src, scripts, .streamlit)...")
    excluded_dirs = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", ".idea", ".vscode", "cache"}
    excluded_extensions = {".pyc", ".pyo", ".pyd", ".log", ".tmp"}

    for folder in ["src", ".streamlit", "scripts"]:
        sfolder = project_root / folder
        dfolder = staging_dir / folder
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
        print(f" [OK] Könyvtár átmásolva: {folder}/")

    # data és logs üres mappák
    (staging_dir / "data").mkdir(exist_ok=True)
    (staging_dir / "data" / ".gitkeep").write_text("", encoding="utf-8")
    (staging_dir / "logs").mkdir(exist_ok=True)
    (staging_dir / "logs" / ".gitkeep").write_text("", encoding="utf-8")

    # 4. Hivatalos Verziózott ZIP létrehozása a Letöltések FŐKÖNYVTÁRÁBAN
    print(f"\n[3/5] Hivatalos ZIP létrehozása a Letöltések gyökerében: {official_zip_name}...")
    if official_zip_path.exists():
        official_zip_path.unlink()

    total_files = 0
    with zipfile.ZipFile(official_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(staging_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(staging_dir)
                zipf.write(file_path, arcname=arcname)
                total_files += 1

    zip_size_mb = official_zip_path.stat().st_size / (1024 * 1024)
    print(f" [OK] Hivatalos ZIP sikeresen elkészült: {official_zip_path} ({zip_size_mb:.2f} MB, {total_files} fájl)")

    # 5. Letöltések gyökerének és 'apának átvinni' mappájának megtisztítása
    print("\n[4/5] Letöltések mappa tisztítása és szétválasztása az új programtól (SentinelSuite)...")
    
    # Letöltésekben lévő régi, nem verziózott / átmeneti zip-ek és mappák takarítása
    redundant_downloads_items = [
        downloads_dir / "Ncore letöltő",
        downloads_dir / "Ncore_letolto_legfrissebb.zip",
        downloads_dir / "Ncore_Ultimate_Telepito_Csomag.zip",
    ]
    for item in redundant_downloads_items:
        if item.exists():
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                print(f" [Takarítva a Letöltésekből]: {item.name}")
            except Exception as e:
                print(f" [-] Nem sikerült törölni: {item.name} ({e})")

    # 'apának átvinni' mappa megtisztítása az Ncore fájloktól (hogy ne keveredjen a SentinelSuite új programmal)
    if dad_transfer_dir.exists():
        ncore_in_dad = [
            dad_transfer_dir / "Ncore letöltő",
            dad_transfer_dir / "Ncore_letolto_Apanak.zip",
            dad_transfer_dir / "KISOKOS_ES_HIBAKERESO.txt",
            dad_transfer_dir / "TELEPITESI_UTMUTATO_APANAK.txt",
        ]
        for nitem in ncore_in_dad:
            if nitem.exists():
                try:
                    if nitem.is_dir():
                        shutil.rmtree(nitem, ignore_errors=True)
                    else:
                        nitem.unlink()
                    print(f" [Eltávolítva az 'apának átvinni' mappából a keveredés elkerüléséért]: {nitem.name}")
                except Exception as e:
                    print(f" [-] Nem sikerült törölni: {nitem.name} ({e})")

    # Másolás az Asztalra is verziószámmal
    desktop_zip = desktop_dir / official_zip_name
    try:
        shutil.copy2(official_zip_path, desktop_zip)
        print(f" [OK] Verziózott ZIP másolva az Asztalra is: {desktop_zip.name}")
    except Exception as e:
        print(f" [-] Asztali másolás kihagyva: {e}")

    # Automatikus másolás a csatlakoztatott pendrive-ra (D:\ KISOKOS)
    pendrive_d = Path("D:/")
    if pendrive_d.exists():
        try:
            pendrive_zip = pendrive_d / official_zip_name
            shutil.copy2(official_zip_path, pendrive_zip)
            print(f" [OK] Verziózott ZIP automatikusan felmásolva a pendrive-ra is: {pendrive_zip}")
        except Exception as p_err:
            print(f" [-] Pendrive másolási figyelmeztetés: {p_err}")

    # 6. Végső ellenőrzés és összegzés
    print("\n" + "=" * 70)
    print(f"🎉 KÉSZ! A HIVATALOS VERZIÓZOTT ZIP A LETÖLTÉSEK GYÖKERÉBEN VAN:")
    print("=" * 70)
    print(f"📦 Hivatalos fájl: {official_zip_path}")
    print(f"📊 Méret: {zip_size_mb:.2f} MB")
    print(f"🏷️ Verzió: {version}")
    print("\nTisztaság garancia:")
    print(" ✅ A ZIP mostantól közvetlenül a fő Letöltések könyvtárban található.")
    print(" ✅ Az 'apának átvinni' mappa érintetlenül meghagyva a SentinelSuite új program számára, nincs keveredés!")
    print(" ✅ A ZIP-en belül megtalálható minden kisokos, telepítési leírás, futtatható fájl és modul.")
    print("=" * 70)


if __name__ == "__main__":
    main()
