#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Context & Export Generátor.
Összegyűjti a projekt teljes forráskódját, tesztjeit, leírásait és teszteredményeit
egy tiszta, könnyen másolható és feltölthető mappába (gemini_context/).
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Windows konzol UTF-8 kompatibilitás biztosítása
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def generate_export():
    project_root = Path(__file__).resolve().parent.parent
    export_dir = project_root / "gemini_context"

    print("==================================================")
    print("🚀 Gemini Projekt Kontextus és Export Generálása")
    print(f"📁 Gyökérkönyvtár: {project_root}")
    print(f"📦 Célmappa: {export_dir}")
    print("==================================================")

    # 1. Célmappa előkészítése / takarítása
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    # 2. Releváns fájlok listájának összeállítása
    include_extensions = {".py", ".md", ".txt", ".json", ".bat", ".vbs", ".example"}
    
    # Kizárt könyvtárak és fájlok
    exclude_dirs = {
        ".git", ".venv", "venv", "__pycache__", ".idea", ".pytest_cache",
        "gemini_context", "build", "dist", "downloads", "data", "logs"
    }
    exclude_files = {
        ".env", "tasks_state.json", "movie_radar.json", "watchlist.json",
        "desktop.ini", "thumbs.db"
    }

    collected_files = []
    
    # Végigjárjuk a projektet
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in files:
            f_lower = f.lower()
            if f_lower in exclude_files:
                continue
            if f_lower.endswith(".pyc") or f_lower.endswith(".tmp"):
                continue

            file_path = Path(root) / f
            rel_path = file_path.relative_to(project_root)

            # Csak a releváns mappák és fájlok kellenek
            top_level = rel_path.parts[0]
            if top_level in {"src", "tests", "scripts"} or len(rel_path.parts) == 1:
                if file_path.suffix in include_extensions or f in {".env.example", ".gitignore"}:
                    collected_files.append((rel_path, file_path))

    collected_files.sort(key=lambda x: str(x[0]))
    print(f"✅ Összesen {len(collected_files)} db forrás- és dokumentációs fájl azonosítva.")

    # 3. Fájlok másolása tükrözött mappastruktúrába
    print("\n📂 Fájlok másolása a gemini_context mappába...")
    for rel_path, file_path in collected_files:
        dest_path = export_dir / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(file_path, dest_path)
        except Exception as e:
            print(f"⚠️ Hiba a másoláskor ({rel_path}): {e}")

    # 4. Tesztek futtatása és eredmény mentése
    print("\n🧪 Tesztek futtatása a friss eredmény rögzítéséhez...")
    test_output_file = export_dir / "TEST_RESULTS.txt"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-v"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        test_summary = proc.stdout
        if proc.stderr:
            test_summary += "\n--- STDERR ---\n" + proc.stderr
        test_output_file.write_text(test_summary, encoding="utf-8")
        print("✅ Teszteredmények sikeresen elmentve (TEST_RESULTS.txt).")
    except Exception as e:
        test_summary = f"Nem sikerült a tesztek futtatása: {e}"
        test_output_file.write_text(test_summary, encoding="utf-8")
        print(f"⚠️ Tesztfuttatási figyelmeztetés: {e}")

    # 5. Teljes, egybefüggő ALL_IN_ONE_GEMINI_CONTEXT.md fájl generálása
    print("\n📄 Egybefüggő ALL_IN_ONE_GEMINI_CONTEXT.md generálása...")
    all_in_one_path = export_dir / "ALL_IN_ONE_GEMINI_CONTEXT.md"
    
    with open(all_in_one_path, "w", encoding="utf-8") as out:
        out.write("# 🎬 Ncore Film & Sorozat Letöltő - Teljes Projekt Kontextus\n\n")
        out.write(f"*Generálva: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        out.write("Ez a dokumentum a projekt teljes forráskódját, architektúráját, szabályait és teszteredményeit tartalmazza egyetlen fájlban, a Gemini AI számára optimalizálva.\n\n")
        
        # Architektúra és fájlstruktúra áttekintés
        out.write("## 1. Projekt Fájlstruktúra\n\n```text\n")
        for rel_path, _ in collected_files:
            out.write(f"- {rel_path}\n")
        out.write("```\n\n")

        # Legutóbbi teszteredmények
        out.write("## 2. Legutóbbi Teszteredmények (Pytest)\n\n```text\n")
        out.write(test_summary.strip() + "\n```\n\n")

        # Fájlok tartalma fejezetenként
        out.write("## 3. Teljes Forráskód és Konfigurációk\n\n")
        for rel_path, file_path in collected_files:
            out.write(f"### 📄 Fájl: `{rel_path}`\n\n")
            
            # Kód blokk kiterjesztés meghatározása
            ext = file_path.suffix.lower()
            code_lang = "python" if ext == ".py" else ("markdown" if ext == ".md" else ("json" if ext == ".json" else ("batch" if ext == ".bat" else "text")))
            
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                out.write(f"```{code_lang}\n")
                out.write(content)
                if not content.endswith("\n"):
                    out.write("\n")
                out.write("```\n\n")
            except Exception as read_err:
                out.write(f"*(Nem sikerült beolvasni: {read_err})*\n\n")

    # Készítünk egy könnyen használható útmutató README-t a mappába
    readme_path = export_dir / "README_GEMINI_HASZNALAT.txt"
    readme_path.write_text(
        "ÚTMUTATÓ A GEMINI PROJEKT MAPPÁHOZ:\n"
        "====================================\n\n"
        "1. EGYBEN TÖRTÉNŐ BEMÁSOLÁS / FELTÖLTÉS (AJÁNLOTT):\n"
        "   - A mappában található 'ALL_IN_ONE_GEMINI_CONTEXT.md' fájl tartalmazza a teljes\n"
        "     projektet, minden kódot, teszteredményt és struktúrát egyetlen fájlban.\n"
        "   - Ezt a fájlt egyszerűen húzd rá (Drag & Drop) a Gemini chat ablakára, vagy nyisd meg\n"
        "     Notepadben és másold be a tartalmát!\n\n"
        "2. KÜLÖN FÁJLOK / MAPPÁK:\n"
        "   - A mappa tartalmazza a teljes 'src' és 'tests' könyvtárat is tükrözve,\n"
        "     így ha csak egyetlen modult (pl. src/ui/app.py) szeretnél bemásolni Gemini-nek,\n"
        "     innen bármikor tisztán elérheted.\n\n"
        "3. FRISSÍTÉS:\n"
        "   - Ha módosítod a projektet, a 'Frissits_Gemini_Exportot.bat' dupla kattintásával\n"
        "     bármikor másodpercek alatt újra tudod generálni a legfrissebb állapotot!\n",
        encoding="utf-8"
    )

    print(f"\n🎉 Kész! A másolható Gemini kontextus sikeresen elkészült:")
    print(f"👉 Mappa elérési útja: {export_dir}")
    print(f"📄 Egybefüggő összesítő: {all_in_one_path}")


if __name__ == "__main__":
    generate_export()
