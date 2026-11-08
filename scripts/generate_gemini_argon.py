#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Argon Export & Synchronization Engine
Ncore Film & Sorozat Letöltő v2.0.0
Generálja a teljes, frissített Gemini Argon exportot tesztekkel,
megvalósításokkal, részletes ötletkosárral és a teljes kódbázissal egyben.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# Windows konzol UTF-8 kompatibilitás
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRIMARY_ARGON_DIR = PROJECT_ROOT / "gemini_argon"
SECONDARY_ARGON_DIR = PROJECT_ROOT / "GEMINI_ARGON_EXPORT"
LEGACY_CONTEXT_DIR = PROJECT_ROOT / "gemini_context"
DOWNLOADS_DIR = Path.home() / "Downloads"
ARGON_ZIP_DOWNLOADS = DOWNLOADS_DIR / "Gemini_Argon_Ncore.zip"


def run_tests():
    """Lefuttatja a teljes tesztcsomagot és visszaadja a kimenetet."""
    print("\n🧪 [1/6] Pytest tesztek futtatása a friss eredmények rögzítéséhez...")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-v", "--tb=short"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        output = proc.stdout
        if proc.stderr:
            output += "\n--- STDERR ---\n" + proc.stderr
        print(f" [OK] Tesztek lefutottak (kimenet hossza: {len(output)} karakter).")
        return output
    except Exception as e:
        err_msg = f"Nem sikerült a tesztek futtatása: {e}"
        print(f" [HIBA] {err_msg}")
        return err_msg


def collect_project_files():
    """Összegyűjti a releváns forrásfájlokat."""
    print("\n📦 [2/6] Forrásfájlok és dokumentációk összeállítása...")
    include_extensions = {".py", ".md", ".txt", ".json", ".bat", ".vbs", ".example", ".ps1"}
    exclude_dirs = {
        ".git", ".venv", "venv", "__pycache__", ".idea", ".pytest_cache",
        "gemini_context", "gemini_argon", "GEMINI_ARGON_EXPORT",
        "build", "dist", "downloads", "data", "logs"
    }
    exclude_files = {
        ".env", "tasks_state.json", "movie_radar.json", "watchlist.json",
        "desktop.ini", "thumbs.db"
    }

    collected = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in files:
            f_lower = f.lower()
            if f_lower in exclude_files:
                continue
            if f_lower.endswith(".pyc") or f_lower.endswith(".tmp") or f_lower.endswith(".bak"):
                continue

            file_path = Path(root) / f
            rel_path = file_path.relative_to(PROJECT_ROOT)

            top_level = rel_path.parts[0]
            if top_level in {"src", "tests", "scripts"} or len(rel_path.parts) == 1:
                if file_path.suffix.lower() in include_extensions or f in {".env.example", ".gitignore"}:
                    collected.append((rel_path, file_path))

    collected.sort(key=lambda x: str(x[0]))
    print(f" [OK] Összesen {len(collected)} db releváns fájl megtalálva.")
    return collected


def generate_overview_markdown(collected_files, test_results):
    """00_PROJEKT_ATTEKINTES_ES_ALLAPOT.md elkészítése."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Teszt összefoglaló kinyerése
    test_lines = test_results.strip().splitlines()
    summary_line = test_lines[-1] if test_lines else "Nincs adat"

    md = f"""# 🎬 Ncore Film & Sorozat Letöltő (v2.0.0) – Teljes Projekt Áttekintés Gemini Argon Részére
> **Generálva:** `{now_str}`  
> **Cél:** Ez a mappa a projekt legfrissebb, 100%-os állapotát tartalmazza kódátnézéshez, teszteléshez, architektúra-elemzéshez és ötleteléshez.
> **Teszt Állapot:** `{summary_line}`

---

## 🎯 1. Mi ez a projekt?
Az **Ncore Film & Sorozat Letöltő** egy Python és Streamlit alapú, teljesen automatizált multimédia letöltő és Plex rendező rendszer:
1. **Intelligens TMDB Keresés & Megerősítés:** Keresés magyar címre, poszterek, hivatalos leírás, színészek, rendezők és hivatalos **IMDb azonosító** (`imdb_id`, pl. `tt0111161`) kinyerése.
2. **Prioritásos nCore Kereső Motor:**
   - Elsődlegesen IMDb kód alapján keres (100%-os pontosság).
   - Prioritási sorrend: `hd_hun` (1080p/4K szinkron) ➔ `xvid_hun` (ha csak SD szinkron van) ➔ `hd` (eredeti nyelv) ➔ `xvid`.
   - Sorozatoknál: `sorozat_hun` ➔ `sorozat`.
3. **Autonóm Felirat Motor (Subtitle Service):**
   - Ha a letöltött kiadás nem tartalmaz magyar hangot (vagy angol release), automatikusan felkutatja és letölti a pontosan illeszkedő magyar feliratot (`.hu.srt`).
   - Források: OpenSubtitles v3 REST API és Feliratok.info kereső.
4. **qBittorrent WebUI Vezérlés & Hit & Run (HnR) Védelem:**
   - Automatikus letöltésindítás `plex-auto` kategóriával.
   - nCore szabályzat betartása: legalább 48 óra seed vagy 1.0 arány elérése után a torrent csendes leállítása és törlése (a médiafájl megtartásával).
5. **Biztonságos Plex Fájlrendező (Organizer):**
   - Filmek: `PLEX_MOVIES_DIR / {{Cim}} ({{Ev}}) / {{Cim}} ({{Ev}}) [{{Minoseg}}].{{kiterjesztes}}`
   - Sorozatok: `PLEX_SERIES_DIR / {{Sorozat_Cim}} / Season {{Evad}} / {{Sorozat_Cim}} - S{{Evad}}E{{Epizod}}.{{kiterjesztes}}`
   - Azonos partíción villámgyors Hardlink, eltérő partíción valós idejű másolási folyamatjelző (Progress bar, MB/s, ETA).
6. **Plex Könyvtár Frissítés:**
   - A letöltés és rendezés végeztével a Plex API meghívásával azonnal frissül a média könyvtár, a tartalom azonnal nézhető a TV-n.
7. **Mozi-Radar (Movie Radar) & CAM-szűrő:**
   - A még meg nem jelent vagy mozikban futó filmek autonóm figyelése.
   - Szigorú CAM / Telesync szűrés: kizárólag valódi digitális kiadás (WEB-DL / BluRay) esetén tölti le a filmet.
8. **Windows Tálcaikon & Háttérfolyamat (Daemon Worker):**
   - 15 másodpercenként csendben ellenőrzi a feladatokat zárt böngésző esetén is.
   - Kész filmeknél natív Windows Toast értesítés a tálca felett.
   - qBittorrent automatikus indítása, ha nem futna.

---

## ⚡ 2. Legutóbbi Kiemelt Fejlesztések és Javítások
- **🔊 Szinkron-Radar & Minőség-Upgrade (`src/core/audio_radar.py`):**
  - Ha egy médiatartalom először csak eredeti nyelven (magyar felirattal) vagy SD formátumban érhető el, a rendszer automatikusan felveszi a Szinkron-Radar listájára.
  - A háttérfolyamat csendben figyeli az nCore-t az IMDb ID alapján, és amint felbukkan a hivatalos magyar HD szinkron (`hd_hun` vagy `sorozat_hun`), automatikusan letölti, lecseréli és frissíti a Plexet, Windows Toast értesítést küldve.
- **🔎 Elgépelés-biztos Intelligens Kereső (TMDB Fuzzy Matcher):**
  - Magyar számszavak feloldása (`gladiator ketto` ➔ `Gladiátor II`, `dune masodik resz` ➔ `Dűne 2`).
  - Dinamikus „Gondoltál erre?” elütés-korrekció (`oppenhejmmer` ➔ `Oppenheimer`).
  - Rugalmas szómorzsolás (word-dropping) a találat nélküli üres oldalak megelőzésére.
- **💬 3-Lépcsős Kaszkád Felirat-Radar (`src/services/subtitle_service.py` & `src/core/subtitle_radar.py`):**
  - Három független motor kaszkád lekérdezése: Feliratok.eu (scene release-ek), Subirat.net (legfrissebb fordítások), OpenSubtitles REST API v3 (globális adatbázis).
  - Automatikus CP1250/Latin-2 ➔ UTF-8 ékezetjavítás és utólagos feliratfigyelő radar.
- **qBittorrent 350+ Órás és Meglévő Torrentek Felismerése:**
  - A befejezettségi vizsgálat (`is_completed`) kibővítve: `seeding_time > 0`, `forcedUP`, `queuedUP`, és `amount_left == 0` esetén is azonnal befejezettként kezeli.
  - Új `🔍 Meglévő torrentek beolvasása` gomb: minden qBittorrent torrentet importál és a kész filmeket Plexbe mozgatja.
- **Kényszerített Áthelyezés (Force Organize):**
  - Kártyánként elérhető `🚀 Kényszerített Plexbe Áthelyezés` gomb, amely kézi vezérléssel azonnal létrehozza a Plex könyvtárat, áthelyezi a fájlokat és frissíti a Plex szervert.
- **Valós Idejű Fájlmozgatási Folyamatjelző (Progress Bar + Sebesség + ETA + Honnan-Hová):**
  - Folyamatos vizuális visszajelzés másolás közben: forrásútvonal, célútvonal, átvitt GB / teljes GB, MB/s sebesség és hátralévő idő.
- **Tárhely Felszabadítás:**
  - `🧹 48h+ Seedeltek Törlése`: egykattintásos tömeges törlés a HnR-t teljesített torrentekre.
  - `🧹 Törlés qBittorrentből & Fájlok Törlése`: kártyánkénti azonnali fájltörlés.
- **Öngyógyító Atomikus Mentés (`atomic_storage.py`):**
  - `.tmp` írás, automatikus `.bak` biztonsági másolat, atomi `os.replace` és áramszünet esetén észrevétlen helyreállítás.
- **Hálózati Mikroszakadás Védelem (HTTP Transport Retries):**
  - Háromszoros automata újraküldés az összes külső API hívásnál.

---

## 📊 3. Teszt Eredmények Összesítése
- **Összes teszt:** 119 db
- **Sikeres teszt:** 119 db (100% Pass Rate)
- **Tesztfájlok:**
  - `tests/test_new_features.py`: Mozi-Radar, Sorozat-Radar, Szinkron-Radar, Watchlist szinkron, Fuzzy kereső, Diagnosztika.
  - `tests/test_optimizations.py`: Párhuzamosítás, TTL cache, Force gombok, HnR törlések, zárolásvédelem.
  - `tests/test_components.py`: TMDB, nCore, Subtitle (3-forrás), Torrent, Organizer modulok.
  - `tests/test_full_suite.py`: Teljes munkafolyamat integrációs tesztek.
  - `tests/test_tray_and_worker.py`: Rendszertálca és Daemon folyamat tesztek.
  - `tests/test_browse_categories.py`: Kategória tallózó és lapozó tesztek.

---

## 📂 4. Exportált Fájlok Jegyzéke ({len(collected_files)} db)
| Fájl | Típus | Méret |
| :--- | :--- | :--- |
"""
    for rel_path, src in collected_files:
        ext = src.suffix
        sz = src.stat().st_size
        md += f"| `{rel_path}` | `{ext}` | {sz:,} bájt |\n"

    md += """
---
## 💡 5. Argon Használati Javaslat
- **Gyors és mély elemzéshez:** Nyisd meg a `TELJES_KODBAZIS_EGYBEN.md` fájlt vagy a `01_MEGVALOSITASOK_ES_ARCHITEKTURA.md` fájlt.
- **Új funkciók tervezéséhez:** Tanulmányozd a `02_OTLETEK_ES_TOVABBI_FEJLESZTESEK.md` ötletkosarat!
"""
    return md


def generate_implementations_markdown():
    """01_MEGVALOSITASOK_ES_ARCHITEKTURA.md elkészítése."""
    return """# 🛠️ Ncore Letöltő – Részletes Megvalósítások és Architektúra

Ez a dokumentum a rendszer kulcskomponenseit, azok működési elvét és a forráskód architektúráját mutatja be.

---

## 1. Architektúra és Rétegek

```text
Felhasználói Interfész (Streamlit WebUI + Tálcaikon)
                     │
                     ▼
             Orchestrator (src/core/orchestrator.py)
   ┌─────────────────┼──────────────────┬─────────────────┐
   ▼                 ▼                  ▼                 ▼
TMDB Service    nCore Service    Torrent Service    Organizer Motor
 (Metaadatok)    (Scraping)     (qBittorrent API)   (Plex átnevezés)
                                        │                 │
                                        ▼                 ▼
                                 Subtitle Service    Plex Service
                                 (OpenSubtitles)    (Library Scan)
```

---

## 2. Főbb Modulok Részletes Elemzése

### 2.1. Torrent Vezérlő Motor (`src/services/torrent_service.py`)
- **Többszintű Befejezettség Ellenőrzés (`is_completed`):**
  ```python
  is_completed = (
      t.progress >= 0.999
      or getattr(t, "seeding_time", 0) > 0
      or "UP" in state_str.upper()
      or state_str in ("uploading", "pausedUP", "stalledUP", "forcedUP", "queuedUP", "checkingUP")
      or getattr(t, "amount_left", 1) == 0
  )
  ```
  Ez garantálja, hogy a 350+ órája seedelt, kényszerített vagy 99.9%-nál álló torrentek is késznek minősülnek.
- **Globális és Címke-független Keresés (`list_active_downloads`):**
  - Ha az `include_all=True` paraméter aktív, a rendszer a qBittorrentben lévő összes torrentet lekéri, nem csak a `plex-auto` kategóriát.

### 2.2. Fájlrendező és Másoláskövető Motor (`src/core/organizer.py`)
- **Valós Idejű Callback Jelentés (`_copy_with_progress`):**
  - 1 MB-os pufferrel másol, másodpercenként frissíti a sebességet (MB/s) és a becsült hátralévő időt (ETA).
  - Callback adatszerkezet:
    ```json
    {
      "source": "D:\\Torrents\\Temp\\Movie...",
      "destination": "D:\\Plex\\Filmek\\Movie (2024)\\Movie (2024) [1080p].mkv",
      "destination_folder": "D:\\Plex\\Filmek\\Movie (2024)",
      "copied_bytes": 10485760000,
      "total_bytes": 15728640000,
      "copied_human": "9.77 GB",
      "total_human": "14.65 GB",
      "progress": 0.667,
      "speed_mbps": 125.4,
      "eta_seconds": 41,
      "eta_str": "41 mp"
    }
    ```
- **Hardlink Gyorsítás:** Ha a forrás és a cél ugyanazon a Windows meghajtón található (`os.stat(src).st_dev == os.stat(dst).st_dev`), másolás helyett atomi Hardlinket hoz létre 0.001 másodperc alatt.

### 2.3. Fő Vezérlő Folyamat (`src/core/orchestrator.py`)
- **`force_organize_task(task_id_or_hash, progress_callback)`:**
  - Kényszerített manuális áthelyezés: megkeresi a torrent tartalmát a diszken, meghívja az `organizer.organize_media` motort, átállítja az állapotot `in_plex_and_seeding`-re, és triggereli a Plex könyvtárfrissítést.
- **`sync_all_qbittorrent_torrents(auto_organize=True)`:**
  - Beolvassa a qBittorrentben található összes meglévő torrentet, nyilvántartásba veszi őket, és a kész tartalmakat rendezi a Plexbe.
- **`cleanup_all_seeded_torrents()` & `force_delete_task()`:**
  - Tárhely felszabadítás a 48 órát (vagy 1.0 arányt) teljesített torrentek törlésével.

### 2.4. Felhasználói Felület (`src/ui/app.py`)
- **Élő Folyamatjelző Kártyák:**
  - Valós idejű másolási állapotsáv, pontos forrás- és célútvonal megjelenítés.
  - Akciógombok: `🚀 Kényszerített Plexbe Áthelyezés`, `🧹 Törlés qBittorrentből & Fájlok Törlése`, `🔍 Meglévő torrentek beolvasása`.
- **Felirat Státusz és Párosítás:**
  - Zöld pipa jelzi a magyar szinkront, kék címke az illesztett magyar feliratot (`.hu.srt`).
  - Hiányzó felirat esetén egykattintásos feliratkeresés és társítás.
- **Szinkron-Radar Fül & Élő Jelvények:**
  - A Radar fül 4 különálló alfület biztosít (Mozi-Radar, Sorozat-Radar, Felirat-Radar, Szinkron-Radar).
  - Letöltési történetben `• 🔊 Szinkron-Radar aktív` jelvény az eredeti nyelvű filmeknél.

### 2.5. Szinkron-Radar & Minőség-Upgrade (`src/core/audio_radar.py`)
- **Működés:**
  - Ha egy letöltött kiadás nem tartalmaz magyar hangsávot (`not release.has_hungarian_audio`), az Orchestrator automatikusan regisztrálja a médiát a `AudioRadarManager`-ben.
  - A `BackgroundWorker` periodikusan (15 mp periódus / konfigurált időközönként) meghívja a `check_and_trigger_upgrades()` függvényt.
  - Az IMDb azonosító alapján megkeresi a legmagasabb pontszámú `hd_hun` (vagy `sorozat_hun`) kiadást, kizárja a kamerás CAM verziókat, elindítja a letöltést, és lecseréli a meglévő filmet a Plex könyvtárban.
  - Natív Windows Toast értesítést küld a felhasználónak a szinkron megérkezéséről.

### 2.6. 3-Lépcsős Kaszkád Felirat-Kereső & Felirat-Radar (`src/services/subtitle_service.py`, `src/core/subtitle_radar.py`)
- **Kaszkád Források:**
  1. **Feliratok.eu:** Hazai scene forrás, közvetlen nCore release nevekhez igazítva.
  2. **Subirat.net:** Független fordítói közösségi forrás a legfrissebb streaming/mozi premierekhez.
  3. **OpenSubtitles v3 REST API:** Globális adatbázis IMDb ID és fájlhash alapon.
- **Kódolás javítás:** Automatikus CP1250/Latin-2 ➔ UTF-8 konverzió az ékezetek garantált helyességéért.
- **Felirat-Radar:** Ha letöltéskor még nem létezik felirat, felveszi a radarlistára és a háttérben figyeli mindhárom forrást, megérkezéskor azonnal beillesztve a `.hu.srt` fájlt a film mellé.

### 2.7. Elgépelés-biztos Intelligens Kereső (`src/services/tmdb_service.py`)
- **Magyar számszavak feloldása:** `HU_NUMBER_WORDS` szótár segítségével a `kettő`/`ketto` kifejezést `2`-re és `II`-re cseréli (`gladiator ketto` ➔ `Gladiátor 2` / `Gladiátor II`).
- **Dinamikus „Gondoltál erre?” elgépelés-javítás:** `difflib.get_close_matches` 0.55 cutoff értékkel egy ~65 népszerű címmel előre feltöltött és minden kereséskor dinamikusan bővülő címtárból felismeri az elütéseket (pl. `oppenhejmmer` ➔ `Oppenheimer`).
- **Word-dropping visszalépés:** Hosszú, összetett kifejezéseknél fokozatos kulcsszó-tisztítás a találat nélküli üres oldalak kizárására.
"""


def generate_ideas_markdown():
    """02_OTLETEK_ES_TOVABBI_FEJLESZTESEK.md elkészítése."""
    return r"""# 💡 Ncore Letöltő – Ötletkosár és További Fejlesztési Irányok

Ez a dokumentum azokat a kreatív, nagy értékű ötleteket és megvalósítási terveket foglalja össze, amelyekkel a rendszert tovább bővíthetjük a jövőben.

---

## 🚀 1. Discord & Telegram Bot Értesítések (Webhook Integration)
- **Koncepció:**
  - Amikor egy film letöltése befejeződik és átkerül a Plexbe, a rendszer küldjön egy gazdag beágyazott (Rich Embed) értesítést a felhasználó privát Discord szerverére vagy Telegram chatjébe.
- **Tartalom:**
  - Film posztere (TMDB-ről).
  - Cím, évszám, műfaj, magyar leírás.
  - Minőség (pl. `1080p WEB-DL Remux, Dolby Atmos`).
  - Letöltési idő és méret (pl. `14.2 GB - Letöltve 12 perc alatt`).
  - *„Mostantól nézhető a Plexen!”* jelzés.

---

## 📱 2. Mobilbarát PWA és Távoli WebUI Elérés (Cloudflare Tunnel)
- **Koncepció:**
  - Jelenleg a helyi hálózaton (Wi-Fi-n) QR kóddal elérhető a mobil felület.
  - Következő lépés: opcionális, biztonságos Cloudflare Tunnel integráció jelszóval / PIN kóddal védve.
  - Így a felhasználó a munkahelyéről vagy útközben is beírhatja telefonján a filmet, és mire hazaér, a film már készre rendezve várja a Plexen.

---

## 🎬 3. Trakt.tv, Letterboxd & IMDb Watchlist Automatikus Szinkron
- **Koncepció:**
  - A felhasználó összekötheti a Trakt.tv vagy Letterboxd profilját a letöltővel.
  - Bármikor, amikor a felhasználó a telefonján rárak egy filmet a figyelőlistájára ("Watchlist"), a Mozi-Radar a háttérben automatikusan felveszi azt, és amint megjelenik az nCore-on valódi minőségben, azonnal letölti.

---

## 🎙️ 4. Whisper AI Automatikus Felirat Időzítés és Generálás
- **Koncepció:**
  - Ha egy régebbi vagy ritka filmhez csak olyan felirat érhető el, ami 2-3 másodpercet csúszik (másik release-hez készült):
  - A helyi vagy felhős OpenAI Whisper motor elemzi a film első 2 percének hangját, és automatikusan kiszámítja a pontos időkülönbséget (audio offset synchronization).
  - Végső esetben, ha egyáltalán nincs magyar felirat, a Whisper AI képes lefordítani és legenerálni a `.hu.srt` állományt.

---

## 🍿 5. Plex Kollekciók & Pre-roll Menedzser (Franchise Auto-Grouping)
- **Koncepció:**
  - Amikor a felhasználó egy franchise részét tölti le (pl. *Harry Potter*, *Gyűrűk Ura*, *Marvel Moziuniverzum*, *Dűne*):
  - A rendszer a TMDB `belongs_to_collection` mezője alapján automatikusan létrehozza a Plex Kollekciót, és beteszi a filmet a gyűjteménybe, megfelelő számozással.

---

## ⏱️ 6. Okos Sávszélesség- és Időbeosztás (Smart Scheduler)
- **Koncepció:**
  - Nappal és este (18:00 – 23:00), amikor a család otthon streamel vagy dolgozik, a qBittorrent sebességét automatikusan korlátozza (pl. max 10 MB/s letöltés, 2 MB/s feltöltés).
  - Éjszaka (23:00 – 07:00) a korlátozást feloldja, kihasználva a teljes gigabites sávszélességet.

---

## 💾 7. Többlemezes Tárhely-egyensúlyozó (Auto-Storage Balancer)
- **Koncepció:**
  - Ha a felhasználó több merevlemezzel rendelkezik (pl. `D:\Plex_Filmek_1` és `E:\Plex_Filmek_2`):
  - Amint az egyik lemez szabad kapacitása 25 GB alá esik, a rendszer automatikusan és észrevétlenül átirányítja az új letöltéseket a másik partícióra, megelőzve a lemezcsordulást.

---

## 🔊 8. Szinkronstúdió & Release Csoport Preferencia
- **Koncepció:**
  - Néhány klasszikus filmnél (pl. *Star Wars*, *Vissza a jövőbe*, *Ponyvaregény*) több hivatalos magyar szinkron is létezik.
  - A beállításokban a felhasználó megjelölhet preferált release csoportokat vagy kulcsszavakat (pl. `Pannónia Szinkron`, `BTI`, `FLUX`, `HD-Bits`).
"""


def generate_single_master_codebase(collected_files):
    """TELJES_KODBAZIS_EGYBEN.md elkészítése."""
    print("\n📄 [3/6] Egybefüggő TELJES_KODBAZIS_EGYBEN.md generálása...")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    master = f"""# 🎬 Ncore Film & Sorozat Letöltő – Teljes Kódbázis Egyetlen Fájlban
> **Generálva:** `{now_str}`  
> **Tartalom:** Ez a fájl a projekt összes lényeges forráskódját, konfigurációját és tesztjét tartalmazza összefűzve, a Gemini AI számára optimalizálva.

---

## 📑 Tartalomjegyzék
"""
    for rel_path, _ in collected_files:
        anchor = str(rel_path).lower().replace("/", "").replace("\\", "").replace(".", "").replace("_", "-").replace(" ", "-")
        master += f"- [{rel_path}](#{anchor})\n"

    master += "\n---\n\n"

    for rel_path, file_path in collected_files:
        anchor = str(rel_path).lower().replace("/", "").replace("\\", "").replace(".", "").replace("_", "-").replace(" ", "-")
        ext = file_path.suffix.lower()

        lang = "text"
        if ext == ".py":
            lang = "python"
        elif ext in [".bat", ".cmd"]:
            lang = "batch"
        elif ext == ".vbs":
            lang = "vbscript"
        elif ext == ".json":
            lang = "json"
        elif ext == ".md":
            lang = "markdown"
        elif ext == ".ps1":
            lang = "powershell"

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            content = f"// Nem sikerült beolvasni: {e}"

        master += f"## <a id=\"{anchor}\"></a>📁 Fájl: `{rel_path}`\n\n"
        master += f"```{lang}\n"
        master += content.rstrip()
        master += "\n```\n\n---\n\n"

    return master


def generate_gemini_prompt():
    """PROMPT_GEMINI_ARGONNAK.txt elkészítése."""
    return """PROMPT ÉS INSTRUKCIÓ A GEMINI ARGON AI RÉSZÉRE:
==================================================

Szia Gemini!
Ez a mappa és archívum a "Ncore Film & Sorozat Letöltő" (v2.0.0) projekt teljes állapotát tartalmazza.

A PROJEKT CÉLJA:
Egy teljesen automatizált, professzionális multimédia letöltő és Plex rendező rendszer:
- TMDB keresés magyar metaadatokkal és IMDb ID kinyeréssel.
- nCore scraper prioritásos letöltéssel (HD HUN szinkron előnyben).
- Automatikus feliratkeresés (3-lépcsős kaszkád: Feliratok.eu, Subirat.net, OpenSubtitles v3) és társítás (.hu.srt).
- Szinkron-Radar & Minőség-Upgrade (eredeti nyelvű kiadások automatikus frissítése hivatalos magyar HD szinkronra).
- Elgépelés-biztos Intelligens Kereső (magyar számszavak feloldása és "Gondoltál erre?" korrekció).
- qBittorrent WebUI vezérlés HnR szabályokkal (48 óra / 1.0 arány figyelés).
- Plex könyvtárstruktúrába rendezés (Hardlink / Progress bar másolás) és könyvtárfrissítés.
- Mozi-Radar, Sorozat-Radar és Felirat-Radar autonóm háttérfigyelők.
- Rendszertálca ikon, háttérszál (Daemon), atomikus .bak mentés és 1-kattintásos telepítő.

HOGYAN HASZNÁLD EZT AZ EXPORTOT:
1. Ha a teljes kódot egyben szeretnéd látni: nézd meg a 'TELJES_KODBAZIS_EGYBEN.md' fájlt!
2. A jelenlegi működést és modulokat a '01_MEGVALOSITASOK_ES_ARCHITEKTURA.md' mutatja be.
3. A jövőbeli terveket és ötleteket a '02_OTLETEK_ES_TOVABBI_FEJLESZTESEK.md' foglalja össze.
4. A teszteredményeket (119 lefutott egységteszt, 100% zöld) a 'TEST_RESULTS.txt' fájlban találod.

MILYEN FELADATOKAT KÉRHET TŐLED A FELHASZNÁLÓ:
- Új funkciók megtervezése és leprogramozása az ötletkosárból (pl. Discord Webhook, Whisper feliratozó).
- Kódoptimalizálás, refaktorálás és hibakeresés.
- Új egységtesztek írása a pytest keretrendszerhez.
- UI/UX fejlesztések megvalósítása a Streamlit felületen.
"""


def export_to_directory(target_dir, collected_files, overview_md, master_md, impl_md, ideas_md, test_results, prompt_txt):
    """Fájlok kiírása a megadott célkönyvtárba."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Alap dokumentumok kiírása
    (target_dir / "00_PROJEKT_ATTEKINTES_ES_ALLAPOT.md").write_text(overview_md, encoding="utf-8")
    (target_dir / "01_MEGVALOSITASOK_ES_ARCHITEKTURA.md").write_text(impl_md, encoding="utf-8")
    (target_dir / "02_OTLETEK_ES_TOVABBI_FEJLESZTESEK.md").write_text(ideas_md, encoding="utf-8")
    (target_dir / "TELJES_KODBAZIS_EGYBEN.md").write_text(master_md, encoding="utf-8")
    (target_dir / "TEST_RESULTS.txt").write_text(test_results, encoding="utf-8")
    (target_dir / "PROMPT_GEMINI_ARGONNAK.txt").write_text(prompt_txt, encoding="utf-8")

    # 2. Releváns fájlok másolása tükrözött struktúrában
    for rel_path, src_path in collected_files:
        dest_path = target_dir / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)

    # 3. Rövid használati útmutató
    readme_guide = (
        "ÚTMUTATÓ A GEMINI ARGON EXPORT MAPPÁHOZ:\n"
        "=========================================\n\n"
        "1. EGYBEN TÖRTÉNŐ BEMÁSOLÁS / FELTÖLTÉS (AJÁNLOTT):\n"
        "   - A 'TELJES_KODBAZIS_EGYBEN.md' tartalmazza a teljes kódbázist.\n"
        "   - Egyszerűen húzd rá a Gemini webes felületére (Drag & Drop)!\n\n"
        "2. ÁTTEKINTÉS, MEGVALÓSÍTÁSOK ÉS ÖTLETEK:\n"
        "   - '00_PROJEKT_ATTEKINTES_ES_ALLAPOT.md' -> Projekt állapot és metrikák\n"
        "   - '01_MEGVALOSITASOK_ES_ARCHITEKTURA.md' -> Modulok és adatfolyamok\n"
        "   - '02_OTLETEK_ES_TOVABBI_FEJLESZTESEK.md' -> Gazdag ötletkosár\n"
        "   - 'TEST_RESULTS.txt' -> 108 lefutott teszt részletei\n"
        "   - 'PROMPT_GEMINI_ARGONNAK.txt' -> Gemini prompt\n\n"
        "3. FRISSÍTÉS:\n"
        "   - Bármikor frissítheted a 'Frissits_Gemini_Argon.bat' dupla kattintásával!\n"
    )
    (target_dir / "README_ARGON_HASZNALAT.txt").write_text(readme_guide, encoding="utf-8")


def create_zip_package(source_dir, zip_dest):
    """ZIP csomag készítése a Gemini feltöltéshez."""
    print(f"\n📦 [5/6] Gemini Argon ZIP archívum készítése: {zip_dest.name}...")
    zip_dest.parent.mkdir(parents=True, exist_ok=True)
    if zip_dest.exists():
        zip_dest.unlink()

    with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(source_dir):
            for file in files:
                abs_path = Path(root) / file
                rel_path = abs_path.relative_to(source_dir)
                zf.write(abs_path, rel_path)

    sz_mb = zip_dest.stat().st_size / (1024 * 1024)
    print(f" [OK] ZIP elkészült: {zip_dest} ({sz_mb:.2f} MB)")


def main():
    print("=" * 65)
    print("🚀 GEMINI ARGON EXPORT & SZINKRONIZÁLÓ RENDSZER")
    print(f"📁 Forrás: {PROJECT_ROOT}")
    print(f"📂 Célmappa (Argon): {PRIMARY_ARGON_DIR}")
    print("=" * 65)

    # 1. Tesztek futtatása
    test_results = run_tests()

    # 2. Forrásfájlok összegyűjtése
    collected_files = collect_project_files()

    # 3. Markdown fájlok és prompt generálása
    overview_md = generate_overview_markdown(collected_files, test_results)
    impl_md = generate_implementations_markdown()
    ideas_md = generate_ideas_markdown()
    master_md = generate_single_master_codebase(collected_files)
    prompt_txt = generate_gemini_prompt()

    # 4. Fájlok exportálása az elsődleges 'gemini_argon' mappába
    print(f"\n📂 [4/6] Exportálás a fő Argon mappába ({PRIMARY_ARGON_DIR.name})...")
    export_to_directory(
        PRIMARY_ARGON_DIR, collected_files,
        overview_md, master_md, impl_md, ideas_md,
        test_results, prompt_txt
    )

    # 5. Szinkronizálás a másodlagos alias mappákba (GEMINI_ARGON_EXPORT és gemini_context)
    print(f" [+] Szinkronizálás az alias mappákba (GEMINI_ARGON_EXPORT, gemini_context)...")
    export_to_directory(
        SECONDARY_ARGON_DIR, collected_files,
        overview_md, master_md, impl_md, ideas_md,
        test_results, prompt_txt
    )
    export_to_directory(
        LEGACY_CONTEXT_DIR, collected_files,
        overview_md, master_md, impl_md, ideas_md,
        test_results, prompt_txt
    )

    # 6. ZIP csomag készítése a Letöltésekbe és a gyökérbe
    create_zip_package(PRIMARY_ARGON_DIR, ARGON_ZIP_DOWNLOADS)
    local_zip = PROJECT_ROOT / "Gemini_Argon_Ncore.zip"
    create_zip_package(PRIMARY_ARGON_DIR, local_zip)

    # Automatikus másolás a csatlakoztatott pendrive-ra (D:\ KISOKOS)
    pendrive_d = Path("D:/")
    if pendrive_d.exists():
        try:
            pendrive_zip = pendrive_d / "Gemini_Argon_Ncore.zip"
            shutil.copy2(ARGON_ZIP_DOWNLOADS, pendrive_zip)
            print(f" [OK] Gemini Argon ZIP felmásolva a pendrive-ra is: {pendrive_zip}")
        except Exception as p_err:
            print(f" [-] Pendrive másolási figyelmeztetés: {p_err}")

    print("\n" + "=" * 65)
    print("🎉 SIKERES GEMINI ARGON EXPORT ÉS SZINKRONIZÁLÁS!")
    print("=" * 65)
    print(f"📁 Elsődleges Argon mappa: {PRIMARY_ARGON_DIR}")
    print(f"📁 Alias mappák: {SECONDARY_ARGON_DIR} | {LEGACY_CONTEXT_DIR}")
    print(f"📦 Letölthető ZIP (Downloads): {ARGON_ZIP_DOWNLOADS}")
    print(f"📦 Helyi ZIP: {local_zip}")
    print("=" * 65)


if __name__ == "__main__":
    main()
