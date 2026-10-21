# AGENTS.md - Fejlesztői és AI Agent Architektúra Útmutató

Ez a dokumentum az AI kódoló asszisztensek (és fejlesztők) számára készült a projekt céljainak, architektúrájának, szabályainak és megvalósítási lépéseinek rögzítésére.

---

## 1. Projekt Áttekintés és Célkitűzés

A projekt célja egy végtelenül egyszerű, teljesen automatizált film- és sorozatletöltő rendszer létrehozása Pythonban, mely minimális felhasználói beavatkozást igényel, megelőzi a téves letöltéseket, és készre előkészíti a médiát a Plex szerver számára.

### Alapelv: Felhasználóbarát UI & Biztos Találat
- A felhasználó csak beírja a film vagy sorozat címét.
- A rendszer először egy filmes adatbázisban (TMDB) megkeresi a lehetséges jelölteket, és vizuálisan (poszter, magyar leírás, évszám, rendező, színészek) megjeleníti.
- A felhasználó egyetlen kattintással megerősíti: *"Igen, erre gondoltam!"*
- A megerősítés után a rendszer **autonóm módon** elvégzi a háttérmunkát:
  1. nCore keresés (lehetőleg IMDb kód alapján a 100%-os egyezésért).
  2. Prioritás: Legjobb minőségű magyar szinkron (`hd_hun`, `sorozat_hun`).
  3. Ha csak angol/eredeti nyelvű van: a legjobb minőségű kiadás letöltése + automatikus magyar felirat letöltés és társítás (`.hu.srt`).
  4. Letöltés qBittorrenten keresztül, nCore Hit&Run szabályok betartásával (pl. 48 óra vagy 1.0 arány elérése után leállítás).
  5. Plex-konform mappába mozgatás és átnevezés.
  6. Plex könyvtár automatikus frissítése.

---

## 2. Rendszerarchitektúra és Moduláris Felépítés

A projekt tiszta, szeparált modulokból épül fel a `src/` mappán belül:

```text
Ncore_Film&Sori_letöltő/
├── .env.example              # Konfigurációs sablon
├── .gitignore                # Git ignorált fájlok listája
├── AGENTS.md                 # Agent és fejlesztői irányelvek (ez a fájl)
├── README.md                 # Hivatalos GitHub README
├── requirements.txt          # Python függőségek
└── src/
    ├── __init__.py
    ├── config/
    │   ├── __init__.py
    │   └── settings.py       # Pydantic Settings (.env betöltés és validáció)
    ├── models/
    │   ├── __init__.py
    │   └── media.py          # Adatmodellek (MediaItem, TorrentRelease, SearchResult)
    ├── services/
    │   ├── __init__.py
    │   ├── tmdb_service.py   # Film/sorozat azonosítás, poszterek, IMDb ID kinyerés
    │   ├── ncore_service.py  # nCore belépés, keresés (IMDb ID és cím), torrent letöltés
    │   ├── subtitle_service.py # Feliratkereső motor (OpenSubtitles / Feliratok.info)
    │   ├── torrent_service.py  # qBittorrent WebUI vezérlés és seed-szabály figyelő
    │   └── plex_service.py     # Plex API kliens, könyvtárfrissítés
    ├── core/
    │   ├── __init__.py
    │   ├── organizer.py      # Fájlrendező és Plex-kompatibilis átnevező motor
    │   └── orchestrator.py   # Fő vezérlő folyamat (Search -> Download -> Process -> Plex)
    └── ui/
        ├── __init__.py
        └── app.py            # Felhasználóbarát Streamlit vagy WebUI felület
```

---

## 3. Kulcsfontosságú Logikák és Szabályok

### A. Metaadat Keresés és Megerősítés (TMDB Service)
- **Miért TMDB?** Ingyenes, kiváló magyar támogatással rendelkezik (címek, leírások, poszterek), és visszaadja a film/sorozat hivatalos **IMDb azonosítóját** (`imdb_id`, pl. `tt0111161`).
- A keresési találatok közül a felhasználó kiválasztja a megfelelőt.
- A kiválasztott elem tartalmazza:
  - `title_hu`: Magyar cím
  - `title_en`: Eredeti / angol cím
  - `year`: Kiadási év
  - `media_type`: `movie` vagy `tv`
  - `imdb_id`: IMDb azonosító kód (kritikus fontosságú!)

### B. nCore Keresési és Kiválasztási Stratégia (ncore_service.py)
1. **IMDb alapú keresés**:
   - Ha rendelkezésre áll az `imdb_id`, az nCore keresőjében elsődlegesen ezzel kell keresni. Ez kizárja az azonos című más filmek letöltését.
2. **Kategória prioritási sorrend (Filmeknél)**:
   - `hd_hun` (1080p/2160p Magyar szinkron) - **1. Prioritás**
   - `xvid_hun` (SD Magyar szinkron) - Ha HD nem érhető el, de a szinkron elengedhetetlen
   - `hd` (1080p/2160p Eredeti nyelv) - **Ha nincs szinkron, ez töltődik le, és indul a feliratkereső!**
   - `xvid` (SD Eredeti nyelv) - Végső megoldás
3. **Kategória prioritási sorrend (Sorozatoknál)**:
   - `sorozat_hun` (Magyar szinkron / felirat) - **1. Prioritás**
   - `sorozat` (Eredeti nyelvű sorozatok) - **Indul a feliratkereső!**
4. **Release minőségi szűrés**:
   - Preferált: BluRay, WEB-DL, Remux, x264, x265 / HEVC.
   - Kizárandó vagy legutolsó helyre sorolandó: CAM, Telesync (TS), rossz minőségű kamerás verziók.
   - Seeder ellenőrzés: Csak olyan torrentet válasszunk, ahol `seeders > 0`.

### C. Feliratkezelő Motor (subtitle_service.py)
- **Mikor indul?**
  - Ha a kiválasztott nCore kiadás **nem tartalmaz magyar hangot**, VAGY a felhasználó kifejezetten feliratos verziót kért.
- **Források**:
  - OpenSubtitles REST API v3
  - Feliratok.info (web scraper / kereső modul)
- **Illesztés**:
  - Release név alapján (pl. `Movie.2023.1080p.WEB-DL.DDP5.1.H.264-FLUX`) pontos időzítésű felirat keresése.
  - Átnevezés a videofájl nevére: `Filmnév (Év).hu.srt`.

### D. Torrent Kliens Integráció és HnR Szabályok (torrent_service.py)
- **Ajánlott kliens**: **qBittorrent** (a beépített WebUI API stabil, robusztus és modern a `qbittorrent-api` könyvtárral).
- **Megvalósítás**:
  - Torrent beküldése címkével: `plex-auto`.
  - Letöltési hely ideiglenes mappába (`DOWNLOAD_TEMP_DIR`).
  - nCore Hit & Run (HnR) figyelés:
    - Minimum 48 óra aktív seedelés VAGY 1.0-s feltöltési arány.
    - Amikor a feltétel teljesül, a torrentet leállítja és eltávolítja a qBittorrentből a fájlok törlése nélkül!

### E. Média Rendszerezés és Plex Frissítés (organizer.py & plex_service.py)
- Letöltés végeztével a média átmozgatása a Plex mappába:
  - **Film formátum**:
    `PLEX_MOVIES_DIR / {Cím} ({Év}) / {Cím} ({Év}) [{Minőség}].{kiterjesztés}`
    `PLEX_MOVIES_DIR / {Cím} ({Év}) / {Cím} ({Év}).hu.srt`
  - **Sorozat formátum**:
    `PLEX_SERIES_DIR / {Sorozat Cím} / Season {Évad_szám} / {Sorozat Cím} - S{Évad}E{Epizód}.{kiterjesztés}`
- **Plex könyvtár frissítés**:
  - `plexapi` meghívása -> Könyvtár frissítés (Library Scan triggerelése), így a film azonnal megjelenik a TV-n és a klienseken.

### F. Kezelőfelület és Hálózati Elérés (ui/app.py)
- **Többeszközös hozzáférés**:
  - Az alkalmazás a háttérben a helyi PC-n fut, de a felület böngészőből elérhető PC-ről és **mobiltelefonról / tabletről** is a helyi Wi-Fi hálózaton keresztül (pl. `http://192.168.1.X:8501`).
  - Reszponzív kártyás megjelenés: mobilon egy oszlopban nagy érintőgombokkal és borítókkal jelennek meg a filmek, PC-n tágas rácsos elrendezésben.
  - Opcionális: Később egy dedikált mobilapp (pl. Flutter/React Native) is könnyen ráköthető, mivel a háttérlogika tiszta service rétegre épül.

---

## 4. AI Agent és Fejlesztői Irányelvek

1. **Biztonság és Titkok védelme**:
   - Soha, semmilyen körülmények között ne kerüljenek éles jelszavak, nCore sütik vagy Plex tokenek a kódbázisba vagy a Git történetbe.
   - Minden konfigurációt a `.env` fájlból olvassunk a `pydantic-settings` segítségével.
2. **Kivételkezelés és Robusztusság**:
   - Hálózati hibák, nCore kapcsolat megszakadás vagy captcha esetére egyértelmű, emberi nyelven megfogalmazott hibaüzenetet kell adni a felületen.
   - Ne omoljon össze az alkalmazás, ha a Plex szerver épp nem elérhető; naplózzon figyelmeztetést és próbálja újra.
3. **Kódminőség**:
   - Típusannotációk használata (`typing`, Pydantic modellek).
   - Logolás a `loguru` segítségével az egyszerű hibakereséshez.
4. **Interfész Tervezés**:
   - A végfelhasználó nem rendszergazda: ne legyenek zavaró hibaüzenetek, bonyolult beállítások a főoldalon.
   - Legyen nagy keresőmező, nagy kártyák a filmeknek poszterrel, egyértelmű zöld gomb a letöltés indítására, és egy állapotjelző sáv a folyamatban lévő letöltésekhez.
