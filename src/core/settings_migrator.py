#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Beállítás-migráló és perzisztens konfiguráció-megőrző modul.
Gondoskodik arról, hogy az alkalmazás frissítésekor vagy új mappába való
átmásolásakor a felhasználói beállítások (Plex token, jelszavak, nCore adatok,
egyedi könyvtárak és radar adatok) SOHA ne vesszenek el.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


def get_appdata_dir() -> Path:
    """Visszaadja a központi, perzisztens Windows AppData konfigurációs mappát."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        target = Path(appdata) / "NcoreMediaDownloader"
    else:
        target = Path.home() / ".config" / "NcoreMediaDownloader"
    target.mkdir(parents=True, exist_ok=True)
    return target


def parse_env_file(env_path: Path) -> Dict[str, str]:
    """
    Beolvas egy .env fájlt és kulcs-érték párokat ad vissza.
    Kiszűri a megjegyzéseket és az üres sorokat.
    """
    res: Dict[str, str] = {}
    if not env_path or not env_path.is_file():
        return res

    try:
        content = env_path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Idézőjelek levágása ha vannak
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                res[key] = val
    except Exception:
        pass
    return res


def is_valuable_setting(key: str, val: Optional[str]) -> bool:
    """Megállapítja, hogy egy beállítás valódi, felhasználó által megadott értéket képvisel-e."""
    if val is None:
        return False
    val = val.strip()
    if not val:
        return False
    if val.lower() in {"none", "null"}:
        return False

    # Placeholder sablonértékek kiszűrése (hogy az új verzió sablonja ne írja felül a korábbi adatokat)
    placeholders = {
        "your_ncore_username",
        "your_ncore_password",
        "your_ncore_passkey",
        "your_tmdb_api_key",
        "your_plex_token",
        "your_opensubtitles_api_key",
        "your_opensubtitles_username",
        "your_opensubtitles_password",
    }
    if val.lower() in placeholders:
        return False

    return True


def merge_env_contents(existing_env: Dict[str, str], template_text: str) -> str:
    """
    Összefésüli a meglévő konfigurációs értékeket az új sablonnal.
    Megtartja az .env.example kommentjeit és struktúráját, és behelyettesíti
    a meglévő értékeket.
    """
    out_lines: List[str] = []
    used_keys = set()

    for line in template_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in existing_env and existing_env[key]:
                out_lines.append(f"{key}={existing_env[key]}")
                used_keys.add(key)
                continue
            used_keys.add(key)
        out_lines.append(line)

    # Olyan egyedi kulcsok, amelyek a felhasználó korábbi .env-jében benne voltak, de a sablonban nincsenek
    remaining_keys = [k for k in existing_env if k not in used_keys and existing_env[k]]
    if remaining_keys:
        out_lines.append("")
        out_lines.append("# --- Korábbi egyéni beállítások ---")
        for k in remaining_keys:
            out_lines.append(f"{k}={existing_env[k]}")

    return "\n".join(out_lines) + "\n"


def find_candidate_env_files(project_root: Path) -> List[Path]:
    """
    Megkeresi a számítógépen fellelhető összes lehetséges .env konfigurációs fájlt,
    prioritási sorrendben.
    """
    candidates: List[Path] = []

    # 1. Projekt gyökérben lévő .env
    root_env = project_root / ".env"
    if root_env.is_file():
        candidates.append(root_env)

    # 2. Központi AppData .env
    appdata_env = get_appdata_dir() / ".env"
    if appdata_env.is_file():
        candidates.append(appdata_env)

    # 3. Szülő mappa .env
    parent_env = project_root.parent / ".env"
    if parent_env.is_file():
        candidates.append(parent_env)

    # 4. Standard korábbi telepítési mappák
    user_home = Path.home()
    search_dirs = [
        Path(r"C:\Segedprogramok\Ncore"),
        Path(r"C:\Segedprogramok\Ncore letöltő"),
        Path(r"C:\Media"),
        user_home / "PycharmProjects" / "Ncore_Film&Sori_letöltő",
        user_home / "Downloads" / "apának átvinni" / "Ncore letöltő",
        user_home / "Desktop" / "Ncore letöltő",
        user_home / "Documents" / "Ncore letöltő",
    ]
    for sdir in search_dirs:
        cand = sdir / ".env"
        if cand.is_file() and cand not in candidates:
            candidates.append(cand)

    return candidates


def backup_settings_to_appdata(project_root: Path) -> bool:
    """Biztonsági másolatot készít a jelenlegi .env-ről az AppData központi mappájába."""
    root_env = project_root / ".env"
    if not root_env.is_file() or root_env.stat().st_size == 0:
        return False

    try:
        appdata_dir = get_appdata_dir()
        target_env = appdata_dir / ".env"
        shutil.copy2(root_env, target_env)
        return True
    except Exception:
        return False


def backup_data_to_appdata(project_root: Path) -> bool:
    """Szinkronizálja a data/ mappa adatait a központi AppData mappába."""
    src_data = project_root / "data"
    if not src_data.is_dir():
        return False

    try:
        dst_data = get_appdata_dir() / "data"
        dst_data.mkdir(parents=True, exist_ok=True)
        for f in src_data.glob("*.json"):
            if f.is_file() and f.stat().st_size > 0:
                shutil.copy2(f, dst_data / f.name)
        return True
    except Exception:
        return False


def restore_data_from_appdata(project_root: Path, log_cb: Optional[Callable[[str], None]] = None) -> List[str]:
    """
    Ha az új verzió data/ mappája üres, automatikusan visszaállítja
    a korábbi radar és feladat adatokat az AppData-ból vagy szülőből.
    """
    target_data = project_root / "data"
    target_data.mkdir(parents=True, exist_ok=True)

    sources = [
        get_appdata_dir() / "data",
        project_root.parent / "data",
    ]

    restored: List[str] = []
    for sdir in sources:
        if not sdir.is_dir():
            continue
        for src_file in sdir.glob("*.json"):
            dst_file = target_data / src_file.name
            # Csak akkor másoljuk, ha a cél még nem létezik vagy 0 bájtos
            if (not dst_file.exists() or dst_file.stat().st_size == 0) and src_file.stat().st_size > 0:
                try:
                    shutil.copy2(src_file, dst_file)
                    restored.append(src_file.name)
                    if log_cb:
                        log_cb(f"Adatbázis visszaállítva: {src_file.name}")
                except Exception:
                    pass

    return restored


def migrate_and_preserve_settings(
    project_root: Path,
    log_cb: Optional[Callable[[str, str], None]] = None,
) -> Tuple[bool, Dict[str, str]]:
    """
    Központi beállítás-migráló és fésülő eljárás.
    
    1. Megkeresi az összes létező korábbi .env fájlt.
    2. Kinyeri az értékes beállításokat (Plex token, jelszavak, könyvtárak stb.).
    3. Összefésüli az új .env.example sablonnal.
    4. Elmenti a project_root/.env és az AppData/.env fájlokba.
    5. Biztonsági másolatot (.bak) készít.
    6. Naplózza a megtartott és átvitt beállításokat.
    """
    appdata_dir = get_appdata_dir()
    target_env = project_root / ".env"
    example_env = project_root / ".env.example"

    # Ha nincs sablon a projektben, próbáljuk meg a szülőből
    if not example_env.is_file() and (project_root.parent / ".env.example").is_file():
        try:
            shutil.copy2(project_root.parent / ".env.example", example_env)
        except Exception:
            pass

    template_text = ""
    if example_env.is_file():
        template_text = example_env.read_text(encoding="utf-8", errors="ignore")

    # Összegyűjtjük a korábbi beállításokat
    candidate_files = find_candidate_env_files(project_root)
    merged_settings: Dict[str, str] = {}

    # Fordított sorrendben járjuk be a jelölteket, hogy a legfontosabb (root / appdata) felülírja a régebbit
    for cand in reversed(candidate_files):
        parsed = parse_env_file(cand)
        for k, v in parsed.items():
            if is_valuable_setting(k, v):
                merged_settings[k] = v

    if not merged_settings and not template_text:
        return False, {}

    # Meghajtó igazítás (ha a gépnek nincs D: meghajtója, de a sablon D:-t tartalmaz)
    has_d_drive = Path("D:/").exists()
    if not has_d_drive and template_text:
        template_text = template_text.replace("D:/", "C:/").replace("D:\\", "C:\\")

    # Új .env tartalom összeállítása
    if template_text:
        final_content = merge_env_contents(merged_settings, template_text)
    else:
        # Ha valamiért nem lenne sablon, a beolvasott kulcsokból építkezünk
        final_content = "\n".join(f"{k}={v}" for k, v in merged_settings.items()) + "\n"

    # Biztonsági másolat a régi célfájlról (.env.bak)
    if target_env.is_file() and target_env.stat().st_size > 0:
        try:
            shutil.copy2(target_env, target_env.with_suffix(".env.bak"))
        except Exception:
            pass

    # Kiírás a projekt mappába
    try:
        target_env.write_text(final_content, encoding="utf-8")
    except Exception as exc:
        if log_cb:
            log_cb(f"Nem sikerült írni a .env fájlt: {exc}", "HIBA")
        return False, merged_settings

    # Másolat a központi AppData mappába
    try:
        appdata_dir.mkdir(parents=True, exist_ok=True)
        (appdata_dir / ".env").write_text(final_content, encoding="utf-8")
    except Exception:
        pass

    # Adatbázis fájlok helyreállítása is
    restore_data_from_appdata(project_root, log_cb=lambda m: log_cb(m, "OK") if log_cb else None)

    # Részletes naplózás a megtartott értékekről
    if log_cb:
        log_cb("Meglévő felhasználói beállítások migrálása és megőrzése sikeres!", "OK")
        important_keys = [
            ("PLEX_TOKEN", "Plex Médiaszerver Token", True),
            ("PLEX_SERVER_URL", "Plex Szerver URL", False),
            ("NCORE_USERNAME", "nCore Felhasználónév", False),
            ("NCORE_PASSWORD", "nCore Jelszó", True),
            ("TMDB_API_KEY", "TMDB API kulcs", True),
            ("DOWNLOAD_TEMP_DIR", "Ideiglenes letöltési mappa", False),
            ("PLEX_MOVIES_DIR", "Plex filmek célmappája", False),
            ("PLEX_SERIES_DIR", "Plex sorozatok célmappája", False),
            ("QBITTORRENT_EXE_PATH", "qBittorrent elérési út", False),
            ("DELETE_TORRENT_DATA_AFTER_SEED", "Seedelés utáni temp takarítás", False),
        ]
        for key, label, is_secret in important_keys:
            val = merged_settings.get(key)
            if val:
                if is_secret:
                    masked = val[:4] + "*" * max(len(val) - 6, 4) + val[-2:] if len(val) > 6 else "****"
                    log_cb(f" -> {label}: megőrizve ({masked})", "OK")
                else:
                    log_cb(f" -> {label}: {val}", "OK")

    return True, merged_settings
