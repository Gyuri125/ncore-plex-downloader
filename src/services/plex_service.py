from typing import Optional
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
                return True
            else:
                logger.warning(f"Nem található megfelelő típusú Plex szekció ({media_type})")
                return False

        except Exception as e:
            logger.error(f"Hiba a Plex könyvtárfrissítés során: {str(e)}")
            return False

    def refresh_movies(self) -> bool:
        """Kifejezetten a filmek szekció frissítése."""
        return self.refresh_library(media_type="movie")

    def refresh_series(self) -> bool:
        """Kifejezetten a sorozatok szekció frissítése."""
        return self.refresh_library(media_type="show")
