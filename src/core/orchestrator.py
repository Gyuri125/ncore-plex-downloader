import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

from src.config.settings import get_settings
from src.core.organizer import MediaOrganizer
from src.models.media import MediaItem, MediaType, TorrentRelease
from src.services.ncore_service import NcoreService
from src.services.plex_service import PlexService
from src.services.subtitle_service import SubtitleService
from src.services.tmdb_service import TMDBService
from src.services.torrent_service import TorrentService


class Orchestrator:
    """
    Központi munkafolyamat-vezérlő (Master Pipeline).
    
    Összeköti a teljes láncot:
    1. TMDB keresés (borítók, leírás, IMDb ID).
    2. nCore keresés és minőségi kiválasztás (IMDb ID alapján, szinkron prioritás).
    3. Torrent indítás a qBittorrentben.
    4. Szükség esetén automata magyar felirat letöltése.
    5. Befejezett letöltések átrendezése a Plex mappába (Hardlinkkel az azonnali lejátszáshoz).
    6. Plex szerver automatikus könyvtárfrissítése.
    7. nCore Hit & Run (48h / 1.0 arány) felügyelet.
    """

    STATE_FILE = Path("data/tasks_state.json")

    def __init__(self):
        self.settings = get_settings()
        self.tmdb_service = TMDBService()
        self.ncore_service = NcoreService()
        self.torrent_service = TorrentService()
        self.subtitle_service = SubtitleService()
        self.plex_service = PlexService()
        self.organizer = MediaOrganizer()

        # Mappák előkészítése biztonságosan (ne omoljon össze ha a meghajtó még nincs csatlakoztatva)
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        for dir_path in [
            self.settings.DOWNLOAD_TEMP_DIR,
            self.settings.PLEX_MOVIES_DIR,
            self.settings.PLEX_SERIES_DIR,
        ]:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Figyelem: A mappa ({dir_path}) nem érhető el közvetlenül: {e}")

        self._tasks: Dict[str, dict] = self._load_state()

    def search_media(self, query: str) -> List[MediaItem]:
        """
        Film vagy sorozat keresése TMDB-n.
        Visszaadja a jelöltek listáját borítókkal, leírással és IMDb kóddal.
        """
        logger.info(f"Keresés indítása a TMDB-n erre: '{query}'")
        return self.tmdb_service.search(query)

    def start_download_flow(
        self, media_item: MediaItem, prefer_hungarian: bool = True
    ) -> Dict[str, Any]:
        """
        Elindítja a teljes autonóm letöltési folyamatot a kiválasztott filmhez/sorozathoz.
        """
        logger.info(f"Letöltési folyamat indítása: '{media_item.title_hu}' ({media_item.year or ''})")

        # 1. nCore keresés (lehetőleg IMDb ID-vel)
        releases = self.ncore_service.search_for_media(media_item)
        if not releases:
            msg = f"Nem található kiadás az nCore-on ehhez: '{media_item.title_hu}'"
            logger.warning(msg)
            return {"success": False, "message": msg}

        # 2. Legjobb release kiválasztása
        best_release = self.ncore_service.select_best_release(
            releases, prefer_hungarian=prefer_hungarian
        )
        if not best_release:
            msg = f"Nem található megfelelő minőségű release: '{media_item.title_hu}'"
            logger.warning(msg)
            return {"success": False, "message": msg}

        # 3. .torrent fájl letöltése ideiglenes munkamappába
        torrent_temp_dir = self.settings.DOWNLOAD_TEMP_DIR / "_torrents"
        torrent_file = self.ncore_service.download_torrent_file(best_release, torrent_temp_dir)
        if not torrent_file:
            msg = "Nem sikerült letölteni a .torrent fájlt az nCore-ról."
            logger.error(msg)
            return {"success": False, "message": msg}

        # 4. Küldés qBittorrentnek
        torrent_hash = self.torrent_service.add_torrent(
            torrent_file=torrent_file,
            category="plex-auto",
            tags=["plex-auto", "ncore"],
        )
        if not torrent_hash:
            msg = "Nem sikerült hozzáadni a torrentet a qBittorrenthez."
            logger.error(msg)
            return {"success": False, "message": msg}

        # 5. Felirat kezelés (ha a kiadás nem tartalmaz magyar szinkront)
        sub_path_str = None
        if not best_release.has_hungarian_audio:
            logger.info("A kiadás nem tartalmaz magyar szinkront, feliratkeresés indul...")
            sub_file = self.subtitle_service.find_and_download_subtitle(
                media_item=media_item,
                release=best_release,
                destination_dir=torrent_temp_dir,
            )
            if sub_file:
                sub_path_str = str(sub_file)

        # 6. Feladat állapot mentése a folyamatos figyeléshez
        task_id = torrent_hash if torrent_hash != "OK" else best_release.torrent_id
        self._tasks[task_id] = {
            "task_id": task_id,
            "torrent_hash": torrent_hash,
            "media_item": media_item.model_dump(),
            "release": best_release.model_dump(),
            "status": "downloading",
            "plex_organized": False,
            "subtitle_path": sub_path_str,
        }
        self._save_state()

        return {
            "success": True,
            "message": f"Sikeresen elindítva: {best_release.title}",
            "release": best_release,
            "has_hungarian_audio": best_release.has_hungarian_audio,
            "has_subtitle": bool(sub_path_str),
        }

    def check_and_process_tasks(self) -> List[Dict[str, Any]]:
        """
        Időszakosan vagy UI megnyitásakor futtatandó:
        - Ellenőrzi a folyamatban lévő letöltések állapotát.
        - Ha a letöltés befejeződött: Hardlinkkel azonnal Plexbe rendezi és frissíti a könyvtárat!
        - Figyeli az nCore Hit & Run (48h / 1.0 arány) teljesülését és leállítja a feladatot.
        """
        updated_tasks = []

        for task_id, task in list(self._tasks.items()):
            torrent_hash = task.get("torrent_hash")
            if not torrent_hash or torrent_hash == "OK":
                continue

            status = self.torrent_service.get_status(torrent_hash)
            if not status:
                continue

            task["current_status"] = status
            is_completed = status.get("is_completed", False)

            # 1. Ha befejeződött a letöltés és még nincs Plexben, rendezzük át!
            if is_completed and not task.get("plex_organized", False):
                logger.info(f"Letöltés befejeződött! Plexbe rendezés: {status['name']}")
                content_path = status.get("content_path") or status.get("save_path")
                if content_path and Path(content_path).exists():
                    media_item = MediaItem(**task["media_item"])
                    release = TorrentRelease(**task["release"])

                    sub_files = []
                    if task.get("subtitle_path"):
                        sub_p = Path(task["subtitle_path"])
                        if sub_p.exists():
                            sub_files.append(sub_p)

                    # Hardlink / Másolás a Plex mappába
                    organized_files = self.organizer.organize_media(
                        source_path=Path(content_path),
                        media_item=media_item,
                        release=release,
                        attached_subtitles=sub_files,
                    )

                    if organized_files:
                        task["plex_organized"] = True
                        task["status"] = "in_plex_and_seeding"
                        # Plex könyvtár azonnali frissítése
                        self.plex_service.refresh_library(
                            media_type="movie" if media_item.media_type == MediaType.MOVIE else "show"
                        )
                        logger.info("Plex könyvtár frissítve az új tartalommal!")

            # 2. nCore HnR seedelés ellenőrzése
            if is_completed:
                hnr_result = self.torrent_service.check_hnr_compliance(torrent_hash)
                if hnr_result.get("compliant", False):
                    task["status"] = "completed_and_removed"
                    logger.info(f"HnR teljesítve, feladat lezárva: {status['name']}")

            updated_tasks.append(task)

        self._save_state()
        return updated_tasks

    def get_active_downloads(self) -> List[Dict[str, Any]]:
        """
        Visszaadja a qBittorrentben lévő aktív feladatok listáját a felület számára.
        """
        self.check_and_process_tasks()
        return self.torrent_service.list_active_downloads(category="plex-auto")

    def _save_state(self):
        try:
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Hiba az állapot mentésekor: {str(e)}")

    def _load_state(self) -> Dict[str, dict]:
        if not self.STATE_FILE.exists():
            return {}
        try:
            with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Hiba az állapot betöltésekor: {str(e)}")
            return {}
