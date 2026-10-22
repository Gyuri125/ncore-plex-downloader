"""
Külső szolgáltatás integrációk (TMDB, nCore, qBittorrent, Feliratok, Plex)
"""
from src.services.ncore_service import NcoreService
from src.services.plex_service import PlexService
from src.services.subtitle_service import SubtitleService
from src.services.tmdb_service import TMDBService
from src.services.torrent_service import TorrentService
from src.services.tray_service import TrayService

__all__ = [
    "TMDBService",
    "NcoreService",
    "TorrentService",
    "SubtitleService",
    "PlexService",
    "TrayService",
]






