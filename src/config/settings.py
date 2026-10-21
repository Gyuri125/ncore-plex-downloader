from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Központi alkalmazás konfiguráció a .env fájl alapján.
    Minden beállítás típusvalidált a pydantic-settings segítségével.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # TMDB (The Movie Database)
    TMDB_API_KEY: str = Field(default="", description="TMDB API kulcs")
    TMDB_LANGUAGE: str = Field(default="hu-HU", description="TMDB keresési és leírás nyelv")

    # nCore Hozzáférés
    NCORE_USERNAME: str = Field(default="", description="nCore felhasználónév")
    NCORE_PASSWORD: str = Field(default="", description="nCore jelszó")
    NCORE_PASSKEY: Optional[str] = Field(default=None, description="Opcionális passkey / cookie")

    # Torrent Kliens (qBittorrent ajánlott)
    TORRENT_CLIENT: str = Field(default="qbittorrent", description="Torrent kliens típusa (qbittorrent)")
    QBITTORRENT_HOST: str = Field(default="http://localhost:8080", description="qBittorrent WebUI URL")
    QBITTORRENT_USERNAME: str = Field(default="admin", description="qBittorrent WebUI felhasználónév")
    QBITTORRENT_PASSWORD: str = Field(default="adminadmin", description="qBittorrent WebUI jelszó")

    # nCore Hit & Run (HnR) / Seedelési szabályok
    MIN_SEED_TIME_HOURS: int = Field(default=48, description="Minimum seedelési idő órában (pl. 48)")
    TARGET_RATIO: float = Field(default=1.0, description="Elérendő seed arány (pl. 1.0)")
    AUTO_REMOVE_AFTER_SEED: bool = Field(default=True, description="Automatikus eltávolítás seedelés után")

    # Könyvtárak és Mappák
    DOWNLOAD_TEMP_DIR: Path = Field(
        default=Path("D:/Torrents/Downloading"),
        description="Ideiglenes letöltési mappa"
    )
    PLEX_MOVIES_DIR: Path = Field(
        default=Path("D:/Media/Movies"),
        description="Plex filmek könyvtára"
    )
    PLEX_SERIES_DIR: Path = Field(
        default=Path("D:/Media/TV Shows"),
        description="Plex sorozatok könyvtára"
    )

    # Felirat Kereső (OpenSubtitles)
    OPENSUBTITLES_API_KEY: Optional[str] = Field(default=None, description="OpenSubtitles API kulcs")
    OPENSUBTITLES_USERNAME: Optional[str] = Field(default=None, description="OpenSubtitles felhasználónév")
    OPENSUBTITLES_PASSWORD: Optional[str] = Field(default=None, description="OpenSubtitles jelszó")

    # Plex Médiaszerver Integráció
    PLEX_SERVER_URL: str = Field(default="http://localhost:32400", description="Plex szerver URL")
    PLEX_TOKEN: Optional[str] = Field(default=None, description="Plex X-Plex-Token")
    AUTO_REFRESH_PLEX: bool = Field(default=True, description="Automatikus Plex könyvtárfrissítés")

    # Alkalmazás és WebUI
    APP_HOST: str = Field(default="0.0.0.0", description="WebUI hoszt (0.0.0.0 esetén helyi hálózaton is elérhető)")
    APP_PORT: int = Field(default=8501, description="WebUI port")
    DEBUG_MODE: bool = Field(default=False, description="Részletes hibakeresési mód")


@lru_cache()
def get_settings() -> Settings:
    """Singleton getter a beállításokhoz."""
    return Settings()


def save_settings(updates: dict, env_path: Optional[Path] = None) -> bool:
    """
    Frissíti vagy létrehozza a .env fájlt a megadott értékekkel, és üríti a beállítások cache-ét.
    """
    if env_path is None:
        target = Path(".env")
        if not target.exists():
            candidate = Path(__file__).resolve().parent.parent.parent / ".env"
            if candidate.exists() or not target.parent.is_dir():
                target = candidate
        env_path = target

    existing_lines = []
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()
        except Exception:
            existing_lines = []

    written_keys = set()
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in updates:
                val = updates[key]
                new_lines.append(f"{key}={val if val is not None else ''}\n")
                written_keys.add(key)
                continue
        new_lines.append(line)

    for key, val in updates.items():
        if key not in written_keys:
            new_lines.append(f"{key}={val if val is not None else ''}\n")

    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        get_settings.cache_clear()
        return True
    except Exception:
        return False
