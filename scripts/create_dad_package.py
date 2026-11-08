#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Csomagkészítő Apának való átvitelhez.
Létrehozza a 'C:\\Users\\gyuri\\Downloads\\apának átvinni' mappát,
bemásolja az 'Ncore letöltő' komplett alkalmazáscsomagot a szükséges függőségekkel,
szkriptekkel és konfigurációval, melléhelyezi a könnyen olvasható telepítési útmutatót,
valamint egy készre csomagolt ZIP fájlt is létrehoz a pendrive-os átvitelhez.
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


def make_dad_package():
    project_root = Path(__file__).resolve().parent.parent
    downloads_dir = Path(os.environ.get("USERPROFILE", "C:/Users/gyuri")) / "Downloads"
    target_base_dir = downloads_dir / "apának átvinni"
    target_app_dir = target_base_dir / "Ncore letöltő"

    print("==================================================")
    print("📦 NCORE MÉDIA LETÖLTŐ - CSOMAGOLÁS APÁNAK")
    print(f"📁 Forrás: {project_root}")
    print(f"🎯 Célmappa: {target_base_dir}")
    print("==================================================")

    # 1. Célmappák előkészítése
    if target_app_dir.exists():
        print(" [i] Korábbi célmappa tisztítása...")
        shutil.rmtree(target_app_dir, ignore_errors=True)
    target_app_dir.mkdir(parents=True, exist_ok=True)

    # 2. Gyökérfájlok másolása
    root_files = [
        "TELEPITO_ES_INDITO.bat",
        "Inditas_Windows.bat",
        "Inditas.bat",
        "Csendes_Inditas.vbs",
        "launcher.py",
        "run_app.bat",
        "requirements.txt",
        ".env.example",
        ".env",
        "README_TELEPITES.txt",
        "README_GYORSINDITAS.txt",
        "README.md",
        "LICENSE",
    ]

    print("\n[1/4] Gyökérfájlok és indítószkriptek másolása...")
    for fname in root_files:
        src_file = project_root / fname
        if src_file.exists():
            shutil.copy2(src_file, target_app_dir / fname)
            print(f" [+] Másolva: {fname}")
        else:
            print(f" [-] Nem található: {fname}")

    # 3. Alkönyvtárak másolása (src, .streamlit, scripts, data)
    print("\n[2/4] Alkönyvtárak másolása (src, .streamlit, scripts)...")
    subdirs_to_copy = ["src", ".streamlit", "scripts"]
    excluded_dirs = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", ".idea", ".vscode"}
    excluded_extensions = {".pyc", ".pyo", ".pyd", ".log", ".tmp"}

    for sdir_name in subdirs_to_copy:
        src_sdir = project_root / sdir_name
        dst_sdir = target_app_dir / sdir_name
        if not src_sdir.exists():
            continue

        for root, dirs, files in os.walk(src_sdir):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            rel_path = Path(root).relative_to(src_sdir)
            target_curr = dst_sdir / rel_path
            target_curr.mkdir(parents=True, exist_ok=True)

            for f in files:
                if Path(f).suffix.lower() not in excluded_extensions:
                    src_f = Path(root) / f
                    shutil.copy2(src_f, target_curr / f)

        print(f" [OK] Könyvtár átmásolva: {sdir_name}")

    # data és logs mappák előkészítése kezdeti állapotban
    target_data_dir = target_app_dir / "data"
    target_data_dir.mkdir(parents=True, exist_ok=True)
    (target_data_dir / ".gitkeep").write_text("", encoding="utf-8")

    target_logs_dir = target_app_dir / "logs"
    target_logs_dir.mkdir(parents=True, exist_ok=True)
    (target_logs_dir / ".gitkeep").write_text("", encoding="utf-8")

    # 4. Részletes Telepítési Útmutató és Kisokos Apának
    print("\n[3/4] Telepítési útmutató és Kisokos létrehozása Apának...")
    dad_guide_content = r"""========================================================================
    🎬 NCORE FILM ÉS SOROZAT LETÖLTŐ v2.0.0 - TELEPÍTÉSI ÚTMUTATÓ 🎬
========================================================================

FIGYELEM: Ez az útmutató kizárólag a Ncore Média Letöltő v2.0.0 programhoz
tartozik (teljesen különálló a SentinelSuite vagy egyéb alkalmazásoktól)!

Kedves Apa!

Ezzel a kis programmal végtelenül egyszerűen tudsz filmeket és sorozatokat
letölteni a számítógépedre. Nem kell a torrent oldalakat böngészni:
csak beírod a film címét a keresőbe, rákattintasz a letöltésre, és
a rendszer MINDENT automatikusan megcsinál helyetted:
  1. Letölti a legjobb minőségű magyar szinkronos kiadást.
  2. Ha nincs szinkron, megkeresi és letölti hozzá a magyar feliratot.
  3. Átteszi a filmet a kész filmek mappájába (Plexhez előkészítve).
  4. Betartja az nCore szabályokat (seedelés), így nem kapsz figyelmeztetést!

------------------------------------------------------------------------
🚀 TELEPÍTÉS ÉS ELSŐ INDÍTÁS (CSAK EGYETLEN LÉPÉS!)
------------------------------------------------------------------------

1. Másold át ezt az egész "Ncore letöltő" mappát a számítógépedre
   (például a C:\ mappába vagy a Dokumentumok közé).

2. Nyisd meg a mappát, és keresd meg benne ezt a fájlt:
   👉 TELEPITO_ES_INDITO.bat

3. Kattints rá JOBB EGÉRGOMBBAL, majd válaszd:
   👉 "Futtatás rendszergazdaként" (Run as administrator)

4. Amikor a Windows rákérdez, kattints az "IGEN" gombra!

✨ MIT CSINÁL EZ A TELEPÍTŐ AUTOMATIKUSAN?
   - Ellenőrzi a gépedet. Ha hiányzik a Python vagy a qBittorrent,
     magától letölti és csendben feltelepíti őket.
   - Előkészíti a letöltési és média mappákat (C:\ vagy D:\ meghajtón).
   - Feltelepíti az összes szükséges Python csomagot, és automatikus
     épségtesztet (self-test) futtat az összes modulon.
   - Létrehoz egy "🎬 Ncore Média Letöltő" ikont az Asztalodon.
   - Elindítja a programot a háttérben, és azonnal megnyitja a
     kezelőfelületet a böngésződben (http://localhost:8501)!

------------------------------------------------------------------------
🔄 FRISSÍTÉS ÚJ VERZIÓRA (A BEÁLLÍTÁSOK MEGŐRZÉSÉVEL):
------------------------------------------------------------------------
Ha egy újabb verziót másolsz át a gépre (akár felülírod a meglévő mappát,
akár egy új mappába teszed):
✨ A RENDSZER AUTOMATIKUSAN MEGŐRZI AZ ÖSSZES BEÁLLÍTÁSODAT!
- A film célmappa (PLEX_MOVIES_DIR), sorozat mappa (PLEX_SERIES_DIR),
  letöltési mappa (DOWNLOAD_TEMP_DIR), Plex token, jelszavak és nCore belépés
  mind megmaradnak, nem kell őket újra megadni vagy kikeresni!
- A folyamatban lévő letöltések, a Mozi-Radar és a Sorozat-Radar
  adatai sem vesznek el, automatikusan átkerülnek az új verzióba.

------------------------------------------------------------------------
🔍 RÉSZLETES TELEPÍTÉSI ÉS HIBAKERESÉSI NAPLÓ (LOG):
------------------------------------------------------------------------
A telepítő és az indító minden lépést másodpercre pontosan rögzít a
mappán belüli:
👉 logs\telepites_log.txt
fájlban. A naplóban pontosan ellenőrizheted a korábbi beállítások és mappák
sikeres megőrzését. Ha bármi elakadna, a telepítőablak nem záródik be,
hanem pontosan megmutatja a hiba okát.

További részletekért olvasd el a KISOKOS_ES_HIBAKERESO.txt fájlt!

Jó szórakozást és kellemes mozizást kívánunk! 🍿
========================================================================
"""

    kisokos_content = r"""========================================================================
     📖 NCORE MÉDIA LETÖLTŐ v2.0.0 - KISOKOS ÉS HIBAKERESÉSI ÚTMUTATÓ 📖
========================================================================

FIGYELEM: Ez a kisokos kizárólag a Ncore Média Letöltő v2.0.0 programhoz
tartozik (nem keverendő össze a SentinelSuite vagy más rendszerekkel)!

Ez a kisokos bemutatja az alkalmazás működését, a mappák és beállítások
megjegyzésének garanciáját, a feliratkezelést, valamint a hibakeresést.

------------------------------------------------------------------------
1. 📁 CÉL MAPPÁK ÉS BEÁLLÍTÁSOK MEGŐRZÉSE (NEM CSAK PLEX TOKEN!)
------------------------------------------------------------------------
A rendszer a Windows központi felhasználói profiljában
(%APPDATA%\NcoreMediaDownloader) automatikus biztonsági mentést készít.

Mit jegyez meg a program automatikusan?
  🎬 Filmek célmappája:        (pl. D:\Media\Movies vagy E:\SajatFilmek)
  📺 Sorozatok célmappája:     (pl. D:\Media\TV Shows vagy E:\Sorozatok)
  📥 Letöltési mappa:          (pl. D:\Torrents\Downloading)
  🔑 Plex token és szerver cím (Plex médiaszerver elérés)
  👤 nCore felhasználónév és jelszó
  🧹 Seedelés utáni letöltési mappa takarítási opció (be/ki)

MI TÖRTÉNIK ÚJ VERZIÓ TELEPÍTÉSEKOR VAGY FRISSÍTÉSKOR?
- Amikor egy újabb verziót másolsz át a gépre (akár egy új mappába,
  akár pendrive-ról), a telepítő ELŐSZÖR megkeresi a korábban mentett
  adataidat.
- Az ÖSSZES célmappát, jelszót és Plex tokent automatikusan átveszi!
- Semmit NEM állít vissza alapértelmezettre, a saját meghajtóid és mappáid
  érintetlenek maradnak!
- A telepítő ellenőrzi és ha kell, automatikusan létrehozza a megadott mappákat.

------------------------------------------------------------------------
2. 🔍 TELJES KÖRŰ NAPLÓZÁS (LOGOK) ÉS HIBAKÖVETÉS
------------------------------------------------------------------------
Minden egyes folyamatról pontos, időbélyeggel ellátott napló készül:

📄 logs\telepites_log.txt:
   - A telepítés minden lépését rögzíti (Python verzió, csomagok,
     qBittorrent WebUI konfiguráció, mappák előkészítése).
   - Itt láthatod a beállítások sikeres megőrzésének visszaigazolását.

📄 logs\app.log:
   - A mindennapi működés naplója: filmkeresés, letöltés indítása,
     feliratkeresés, Plexbe mozgatás és a seedelés ellenőrzése.

📄 logs\startup_error.log:
   - Ha a program indításakor bármilyen hiba lépne fel (pl. foglalt port),
     a teljes technikai hibaüzenet ide mentődik, és a Windows egy
     magyar nyelvű hibaablakban tájékoztat a teendőkről.

💻 ÉLŐ NAPLÓ MEGTEKINTÉSE A BÖNGÉSZŐBŐL:
   - A webes felületen (http://localhost:8501) kattints a
     "⚙️ Rendszer & Beállítások" fülre.
   - Az oldal alján lévő "📋 Rendszernapló & Hibakeresés" dobozban
     gépelés nélkül, azonnal elolvashatod a legutóbbi naplóbejegyzéseket.

------------------------------------------------------------------------
3. 💬 FELIRATOK KEZELÉSE ÉS KÉZI CSATOLÁSA
------------------------------------------------------------------------
A letöltéseknél a rendszer 3 egyértelmű jelvénnyel tájékoztat:
  🔊 Magyar szinkron ✅  -> Nincs teendő, magyarul fog szólni a TV-n.
  💬 Magyar felirat ✅   -> A rendszer letöltötte hozzá a .hu.srt feliratot.
  ⚠️ Nincs magyar felirat -> Nem található még felirat az adatbázisokban.

HOGYAN TÖLTHETSZ FEL SAJÁT FELIRATOT (.srt / .zip)?
1. A letöltési kártyán kattints a "💬 Felirat Kezelő & Saját Felirat Feltöltése"
   lenyíló dobozra!
2. Húzd be (drag & drop) a letöltött .srt vagy .zip fájlt a böngészőbe.
3. Kattints a "💾 Csatolás a filmhez és Plex frissítése" gombra!
4. A rendszer automatikusan UTF-8-ra kódolja (nem lesznek hibás ékezetek a TV-n),
   a film neve mellé másolja (.hu.srt), és azonnal frissíti a Plexet!

------------------------------------------------------------------------
4. 🧹 AUTOMATIKUS TÁRHELY-TAKARÍTÁS (HnR UTÁN)
------------------------------------------------------------------------
- Miután a film letöltődött és átkerült a Plex könyvtárba, a qBittorrent
  továbbra is seedeli azt a kötelező 48 óráig (vagy 1.0 arányig).
- Amint az nCore Hit & Run szabály teljesül, a rendszer a qBittorrent
  ideiglenes mappájából automatikusan törli a duplikált torrent fájlokat,
  felszabadítva a merevlemez helyét.
- A Plex mappádban lévő film mindvégig 100%-ban biztonságban megmarad!

------------------------------------------------------------------------
5. ❓ GYAKORI KÉRDÉSEK ÉS GYORS MEGOLDÁSOK
------------------------------------------------------------------------
• Nem nyílik meg a felület a böngészőben?
  👉 Nyisd meg kézzel bármelyik böngészőben ezt a címet:
     http://localhost:8501

• Hogyan indíthatom újra a programot?
  👉 Kattints duplán az Asztalon lévő "🎬 Ncore Média Letöltő" ikonra,
     vagy a mappában a "Csendes_Inditas.vbs" / "Inditas_Windows.bat" fájlra.

• Hogyan változtathatok célmappát vagy meghajtót?
  👉 A böngészőben a "⚙️ Rendszer & Beállítások" fülön írd át a kívánt
     mappa elérési útját (pl. E:\Filmek), majd kattints a Mentés gombra.

========================================================================
"""

    guide_files = [
        ("TELEPITESI_UTMUTATO_APANAK.txt", dad_guide_content),
        ("KISOKOS_ES_HIBAKERESO.txt", kisokos_content),
    ]

    for fname, content in guide_files:
        (target_base_dir / fname).write_text(content, encoding="utf-8")
        (target_app_dir / fname).write_text(content, encoding="utf-8")
        print(f" [+] Útmutató elkészítve: {fname}")

    # 5. Készre csomagolt ZIP létrehozása (pendrive-ra másoláshoz)
    print("\n[4/4] Pendrive-kész ZIP archívum tömörítése...")
    zip_output_path = target_base_dir / "Ncore_letolto_Apanak.zip"

    with zipfile.ZipFile(zip_output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Útmutatók a zip gyökerébe
        for fname, _ in guide_files:
            zipf.write(target_base_dir / fname, fname)
        for root, dirs, files in os.walk(target_app_dir):
            for file in files:
                file_path = Path(root) / file
                archive_name = Path("Ncore letöltő") / file_path.relative_to(target_app_dir)
                zipf.write(file_path, archive_name)

    zip_size_mb = round(zip_output_path.stat().st_size / (1024 * 1024), 2)
    print(f" [OK] ZIP archívum sikeresen létrehozva: {zip_output_path.name} ({zip_size_mb} MB)")

    print("\n" + "=" * 65)
    print("🎉 KÉSZ! A CSOMAG SIKERESEN ELKÉSZÜLT APÁNAK:")
    print(f"📁 Mappa: {target_base_dir}")
    print(f"   ├── 📂 Ncore letöltő (kicsomagolt, azonnal használható mappa)")
    print(f"   ├── 📄 TELEPITESI_UTMUTATO_APANAK.txt (egyszerű 1-lépéses leírás)")
    print(f"   ├── 📄 KISOKOS_ES_HIBAKERESO.txt (részletes kisokos és hibakereső)")
    print(f"   └── 📦 Ncore_letolto_Apanak.zip (egyetlen fájl pendrive-ra)")
    print("=" * 65)


if __name__ == "__main__":
    make_dad_package()

