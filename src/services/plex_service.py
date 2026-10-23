import time
from typing import Dict, Optional, Tuple
from loguru import logger

try:
    from plexapi.server import PlexServer
except ImportError:
    PlexServer = None

from src.config.settings import get_settings


class PlexService:
    """
    Plex Médiaszerver API integráció.
    Felelős a Plex szerverhez való csatlakozásért és a könyvtárak automatikus
    frissítéséért (Library Scan), hogy az új filmek/sorozatok azonnal megjelenjenek a TV-n.
    """

    def __init__(self, server_url: Optional[str] = None, token: Optional[str] = None):
        settings = get_settings()
        self.server_url = server_url or settings.PLEX_SERVER_URL
        self.token = token or settings.PLEX_TOKEN
        self.auto_refresh = settings.AUTO_REFRESH_PLEX
        self.server: Optional["PlexServer"] = None

        # Memóriabeli TTL gyorsítótár a has_media lekérdezésekhez (120 mp lejárattal)
        self._has_media_cache: Dict[Tuple[str, Optional[int]], Tuple[bool, float]] = {}
        self._cache_ttl = 120.0

    def _is_configured(self) -> bool:
        return bool(
            self.server_url
            and self.token
            and self.token != "ide_ird_a_plex_tokent"
        )

    def connect(self) -> bool:
        """
        Csatlakozik a Plex Médiaszerverhez a megadott URL és X-Plex-Token segítségével.
        """
        if PlexServer is None:
            logger.warning("A 'plexapi' könyvtár nincs telepítve! Futtasd: pip install plexapi")
            return False

        if not self._is_configured():
            logger.warning("Plex szerver URL vagy Token nincs konfigurálva a .env fájlban.")
            return False

        try:
            logger.info(f"Csatlakozás a Plex szerverhez: {self.server_url}...")
            self.server = PlexServer(baseurl=self.server_url, token=self.token)
            logger.info(f"Sikeres kapcsolat a Plexhez! Szerver név: '{self.server.friendlyName}', Verzió: {self.server.version}")
            return True

        except Exception as e:
            logger.error(f"Nem sikerült csatlakozni a Plex szerverhez ({self.server_url}): {str(e)}")
            self.server = None
            return False

    def is_connected(self) -> bool:
        return self.server is not None

    def refresh_library(self, media_type: str = "all") -> bool:
        """
        Könyvtár frissítés (Library Scan) triggerelése.
        media_type lehet: 'movie', 'show' vagy 'all'.
        """
        if not self.auto_refresh:
            logger.info("Automatikus Plex frissítés ki van kapcsolva a beállításokban.")
            return True

        if not self.is_connected() and not self.connect():
            logger.warning("Plex könyvtárfrissítés kihagyva (nincs aktív szerver kapcsolat).")
            return False

        try:
            sections = self.server.library.sections()
            logger.info(f"Plex könyvtárfrissítés indítása (típus: {media_type})...")

            scanned_any = False
            for section in sections:
                if media_type == "all" or section.type == media_type:
                    logger.info(f"Plex szekció frissítése: '{section.title}' (Típus: {section.type})")
                    section.update()
                    scanned_any = True

            if scanned_any:
                logger.info("Plex könyvtárfrissítés sikeresen elindítva!")
                self.clear_cache()
                return True
            else:
                logger.warning(f"Nem található megfelelő típusú Plex szekció ({media_type})")
                return False

        except Exception as e:
            logger.error(f"Hiba a Plex könyvtárfrissítés során: {str(e)}")
            return False

    def clear_cache(self):
        """Kiüríti a meglévő Plex média jelenléti gyorsítótárat."""
        self._has_media_cache.clear()

    def refresh_movies(self) -> bool:
        """Kifejezetten a filmek szekció frissítése."""
        return self.refresh_library(media_type="movie")

    def refresh_series(self) -> bool:
        """Kifejezetten a sorozatok szekció frissítése."""
        return self.refresh_library(media_type="show")

    def has_media(self, title: str, year: Optional[int] = None) -> bool:
        """
        Ellenőrzi, hogy a film vagy sorozat már szerepel-e a Plex könyvtárban.
        Memóriabeli TTL gyorsítótárral van ellátva (120 mp), így 24 kártya kirajzolásakor
        nem indít 24 külön hálózati kérést, hanem azonnal, 0.001 ms alatt visszaadja az eredményt.
        """
        if not title:
            return False

        clean_title = title.strip().lower()
        cache_key = (clean_title, year)
        now = time.time()

        if cache_key in self._has_media_cache:
            res_val, timestamp = self._has_media_cache[cache_key]
            if now - timestamp < self._cache_ttl:
                return res_val

        if not self._is_configured():
            return False
        if not self.is_connected() and not self.connect():
            return False

        try:
            results = self.server.library.search(title)
            found = False
            for res in results:
                if res.title.strip().lower() == clean_title:
                    if year and hasattr(res, "year") and res.year:
                        if abs(res.year - year) <= 1:
                            found = True
                            break
                    else:
                        found = True
                        break
            self._has_media_cache[cache_key] = (found, now)
            return found
        except Exception:
            self._has_media_cache[cache_key] = (False, now)
            return False
