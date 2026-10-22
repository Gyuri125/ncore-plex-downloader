import os
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def package_release():
    project_root = Path(__file__).resolve().parent.parent
    dist_dir = project_root / "dist"
    dist_dir.mkdir(exist_ok=True)

    zip_filename = "Ncore_Plex_Downloader_v1.0_Windows_Portable.zip"
    zip_path = dist_dir / zip_filename

    print("=" * 65)
    print("      Ncore Plex Downloader - Hordozható ZIP Csomagoló")
    print("=" * 65)
    print()

    # Kizárandó minták és mappák
    excluded_dirs = {
        "__pycache__",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        ".git",
        ".pytest_cache",
        "data",
        "logs",
        "build",
        "cache",
    }
    excluded_extensions = {".pyc", ".pyo", ".pyd", ".log", ".tmp"}

    # Fájlok listája, amik a csomagba kerülnek
    explicit_files = [
        "Inditas_Windows.bat",
        "README_GYORSINDITAS.txt",
        "README.md",
        "LICENSE",
        ".env.example",
        "requirements.txt",
        "launcher.py",
        "build_desktop_exe.bat",
        "run_app.bat",
    ]

    explicit_dirs = [
        "src",
        ".streamlit",
        "scripts",
    ]

    total_files = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # 1. Különálló fájlok hozzáadása
        for fname in explicit_files:
            fpath = project_root / fname
            if fpath.exists() and fpath.is_file():
                # Biztonsági ellenőrzés: semmiképp se csomagoljunk éles .env fájlt
                if fname == ".env" or fname.endswith(".env"):
                    continue
                zipf.write(fpath, arcname=f"ncore-plex-downloader/{fname}")
                total_files += 1
                total_uncompressed_bytes += fpath.stat().st_size
                print(f" [+] Hozzáadva: {fname}")

        # 2. Mappák rekurzív hozzáadása
        for dname in explicit_dirs:
            dpath = project_root / dname
            if not dpath.exists():
                continue
            for root, dirs, files in os.walk(dpath):
                # Kizárandó mappák szűrése a bejárásból
                dirs[:] = [d for d in dirs if d not in excluded_dirs]
                rel_root = Path(root).relative_to(project_root)

                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix.lower() in excluded_extensions:
                        continue
                    if file in [".env", "credentials.json", "cookies.txt"] or file.endswith(".session") or file.endswith(".token"):
                        continue

                    arcname = f"ncore-plex-downloader/{rel_root / file}"
                    zipf.write(file_path, arcname=arcname)
                    total_files += 1
                    total_uncompressed_bytes += file_path.stat().st_size

            print(f" [+] Mappa becsomagolva: {dname}/")

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    raw_size_mb = total_uncompressed_bytes / (1024 * 1024)

    print()
    print("=" * 65)
    print("SIKER! A hordozható ZIP csomag elkészült:")
    print(f" 📦 Archívum: {zip_path}")
    print(f" 📄 Fájlok száma: {total_files} db")
    print(f" 🗜️ Tömörített méret: {zip_size_mb:.2f} MB (eredeti: {raw_size_mb:.2f} MB)")
    print("=" * 65)
    print("\nEzt a ZIP fájlt feltöltheted a GitHub Releases oldalra,")
    print("vagy átküldheted bárkinek, aki duplakattintással azonnal használni tudja!")


if __name__ == "__main__":
    package_release()
