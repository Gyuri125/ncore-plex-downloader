import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

from src.config.settings import get_settings
from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.core.autostart import AutostartManager
from src.core.movie_radar import MovieRadarManager
from src.core.organizer import MediaOrganizer
from src.core.watchlist import WatchlistManager
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
    1. TMDB keresés és ajánlások (borítók, leírás, IMDb ID).
    2. nCore keresés és minőségi kiválasztás (IMDb ID alapján, szinkron prioritás).
    3. Torrent indítás a qBittorrentben.
    4. Szükség esetén automata magyar felirat letöltése.
    5. Befejezett letöltések átrendezése a Plex mappába (Hardlinkkel az azonnali lejátszáshoz).
    6. Plex szerver automatikus könyvtárfrissítése.
    7. nCore Hit & Run (48h / 1.0 arány) felügyelet.
    8. Kívánságlista, Mozi-Radar és automatikus Windows indítás kezelése.
    """

    STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "tasks_state.json"

    def __init__(self):
        self.settings = get_settings()
        self.tmdb_service = TMDBService()
        self.ncore_service = NcoreService()
        self.torrent_service = TorrentService()
        self.subtitle_service = SubtitleService()
        self.plex_service = PlexService()
        self.organizer = MediaOrganizer()
        self.watchlist = WatchlistManager()
        self._radar = MovieRadarManager()
        self.autostart = AutostartManager()

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

    @property
    def radar(self) -> MovieRadarManager:
        """Biztosítja a Mozi-Radar elérését még régebbi memóriabeli cache-elt példányok esetén is."""
        if not hasattr(self, "_radar") or self._radar is None:
            self._radar = MovieRadarManager()
        return self._radar

    @radar.setter
    def radar(self, value: MovieRadarManager):
        self._radar = value

    def search_media(self, query: str) -> List[MediaItem]:
        """
        Film vagy sorozat keresése TMDB-n.
        Visszaadja a jelöltek listáját borítókkal, leírással és IMDb kóddal.
        Ha a TMDB közvetlen szövegkeresése nem ad találatot,
        az nCore-ról azonosítja be az IMDb kódot és az alapján tölti be az adatlapot a TMDB-ről!
        """
        logger.info(f"Keresés indítása a TMDB-n erre: '{query}'")
        results = self.tmdb_service.search(query)
        if results:
            return results

        # Fallback: Keresés az nCore-on, és IMDb azonosító alapján TMDB adatlap betöltése
        logger.info(f"Közvetlen TMDB találat nem volt erre: '{query}'. nCore fallback keresés indul...")
        try:
            ncore_res = self.ncore_service.search_by_title(query, MediaType.TV)
            if not ncore_res:
                ncore_res = self.ncore_service.search_by_title(query, MediaType.MOVIE)

            seen_imdb = set()
            for r in ncore_res:
                if r.imdb_id and r.imdb_id not in seen_imdb:
                    seen_imdb.add(r.imdb_id)
                    item = self.tmdb_service.get_by_imdb_id(r.imdb_id)
                    if item and not any(x.tmdb_id == item.tmdb_id for x in results):
                        results.append(item)
                        if len(results) >= 4:
                            break
        except Exception as e:
            logger.debug(f"nCore fallback keresési hiba: {e}")

        return results


    def get_available_releases(
        self, media_item: MediaItem, prefer_hungarian: bool = True
    ) -> List[TorrentRelease]:
        """
        Lekéri az nCore-ról a filmhez/sorozathoz tartozó ellenőrzött kiadásokat rangsorolva
        (1. helyen a legjobb magyar szinkronos, majd a legjobb minőségek).
        """
        return self.ncore_service.search_for_media(media_item)

    def start_download_flow(
        self, media_item: MediaItem, prefer_hungarian: bool = True
    ) -> Dict[str, Any]:
        """
        Automatikusan kiválasztja és elindítja a legmegfelelőbb kiadás letöltését.
        """
        logger.info(f"Automata letöltési folyamat indítása: '{media_item.title_hu}' ({media_item.year or ''})")

        releases = self.get_available_releases(media_item, prefer_hungarian=prefer_hungarian)
        if not releases:
            msg = f"Nem található kiadás az nCore-on ehhez: '{media_item.title_hu}'"
            logger.warning(msg)
            return {"success": False, "message": msg}

        best_release = releases[0]
        return self.download_specific_release(media_item, best_release)

    def download_specific_release(
        self, media_item: MediaItem, release: TorrentRelease
    ) -> Dict[str, Any]:
        """
        A felhasználó által kiválasztott konkrét kiadás letöltését indítja el.
        """
        logger.info(f"Kiadás letöltése indítva: '{release.title}' ({media_item.title_hu})")

        # 1. .torrent fájl letöltése ideiglenes munkamappába
        torrent_temp_dir = self.settings.DOWNLOAD_TEMP_DIR / "_torrents"
        torrent_file = self.ncore_service.download_torrent_file(release, torrent_temp_dir)
        if not torrent_file:
            msg = "Nem sikerült letölteni a .torrent fájlt az nCore-ról."
            logger.error(msg)
            return {"success": False, "message": msg}

        # 2. Küldés qBittorrentnek
        torrent_hash = self.torrent_service.add_torrent(
            torrent_file=torrent_file,
            category="plex-auto",
            tags=["plex-auto", "ncore"],
        )
        if not torrent_hash:
            msg = "Nem sikerült hozzáadni a torrentet a qBittorrenthez. Ellenőrizd, hogy a qBittorrent el van-e indítva a gépen, és a WebUI engedélyezve van-e a beállításaiban (8080-as porton)!"
            logger.error(msg)
            return {"success": False, "message": msg}

        # 3. Felirat kezelés (ha a kiadás nem tartalmaz magyar szinkront)
        sub_path_str = None
        if not release.has_hungarian_audio:
            logger.info("A kiadás nem tartalmaz magyar szinkront, feliratkeresés indul...")
            sub_file = self.subtitle_service.find_and_download_subtitle(
                media_item=media_item,
                release=release,
                destination_dir=torrent_temp_dir,
            )
            if sub_file:
                sub_path_str = str(sub_file)

        # 4. Feladat állapot mentése a folyamatos figyeléshez
        import datetime
        task_id = torrent_hash if torrent_hash != "OK" else release.torrent_id
        self._tasks[task_id] = {
            "task_id": task_id,
            "torrent_hash": torrent_hash,
            "media_item": media_item.model_dump(),
            "release": release.model_dump(),
            "status": "downloading",
            "plex_organized": False,
            "subtitle_path": sub_path_str,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_state()

        return {
            "success": True,
            "message": f"Sikeresen elindítva: {release.title}",
            "release": release,
            "has_hungarian_audio": release.has_hungarian_audio,
            "has_subtitle": bool(sub_path_str),
        }

    def check_disk_space_for_release(self, release: TorrentRelease, safety_margin_gb: float = 10.0) -> Dict[str, Any]:
        """
        Ellenőrzi, hogy van-e elegendő hely a célmeghajtón a letöltéshez + biztonsági tartalékhoz.
        """
        import shutil
        try:
            target_dir = self.settings.DOWNLOAD_TEMP_DIR
            if not target_dir.exists():
                target_dir = target_dir.parent
            usage = shutil.disk_usage(target_dir)
            free_bytes = usage.free
            free_gb = round(free_bytes / (1024 ** 3), 1)
            req_gb = round(release.size_bytes / (1024 ** 3), 1)
            needed_gb = round(req_gb + safety_margin_gb, 1)

            has_enough = free_bytes >= (release.size_bytes + int(safety_margin_gb * (1024 ** 3)))
            shortage_gb = round(max(0.0, needed_gb - free_gb), 1)
            return {
                "has_enough": has_enough,
                "free_gb": free_gb,
                "required_gb": req_gb,
                "needed_gb": needed_gb,
                "safety_margin_gb": safety_margin_gb,
                "shortage_gb": shortage_gb,
            }
        except Exception as e:
            logger.error(f"Lemezterület ellenőrzési hiba: {e}")
            return {
                "has_enough": True,
                "free_gb": 999.0,
                "required_gb": 0.0,
                "needed_gb": 0.0,
                "safety_margin_gb": safety_margin_gb,
                "shortage_gb": 0.0,
            }

    def get_release_summary(self, media_item: MediaItem) -> Dict[str, Any]:
        """
        Lekéri a kiadások gyors összegzését (legjobb kiadás, felbontás, magyar hang, mozis-e).
        """
        releases = self.get_available_releases(media_item, prefer_hungarian=True)
        if not releases:
            return {
                "available": False,
                "badge_text": "🚫 Még nincs fent nCore-on",
                "badge_color": "#64748b",
                "best_release": None,
                "releases_count": 0,
            }

        best = releases[0]
        all_cam = all(r.is_cam for r in releases)
        if all_cam:
            return {
                "available": True,
                "badge_text": "⏳ Csak mozis (CAM) • Várj a digitálisra!",
                "badge_color": "#dc2626",
                "best_release": best,
                "releases_count": len(releases),
                "is_cam": True,
            }

        non_cam = [r for r in releases if not r.is_cam]
        active_best = non_cam[0] if non_cam else best
        specs = active_best.specs

        if active_best.has_hungarian_audio:
            badge_text = f"🔊 Magyar: {specs['resolution']} ({specs['hdr']}) • {active_best.size_human}"
            badge_color = "#10b981"
        else:
            badge_text = f"🇬🇧 Eredeti hang + Felirat • {specs['resolution']} • {active_best.size_human}"
            badge_color = "#3b82f6"

        return {
            "available": True,
            "badge_text": badge_text,
            "badge_color": badge_color,
            "best_release": active_best,
            "releases_count": len(releases),
            "is_cam": False,
        }

    def download_season_pack(
        self, media_item: MediaItem, season_num: Optional[int] = 1, prefer_hungarian: bool = True
    ) -> Dict[str, Any]:
        """
        Kiválasztja és letölti az adott évadhoz tartozó legjobb kiadást (évadcsomagot preferálva).
        """
        releases = self.get_available_releases(media_item, prefer_hungarian=prefer_hungarian)
        if not releases:
            return {"success": False, "message": f"Nem található kiadás a sorozathoz: {media_item.title_hu}"}

        season_releases = []
        for r in releases:
            if r.season_num == season_num or (season_num == -1 and r.is_pack):
                season_releases.append(r)

        if not season_releases:
            return {"success": False, "message": f"Nem található kiadás a(z) {season_num}. évadhoz."}

        best = self.ncore_service.select_best_release(season_releases, prefer_hungarian=prefer_hungarian)
        if not best:
            return {"success": False, "message": "Nem sikerült kiválasztani a legjobb kiadást."}

        return self.download_specific_release(media_item, best)

    def download_all_available_seasons(
        self, media_item: MediaItem, prefer_hungarian: bool = True
    ) -> Dict[str, Any]:
        """
        Letölti a sorozat összes elérhető évadát:
        - Ha van teljes sorozat csomag (Complete / S01-Sxx Pack), azt tölti le egyben.
        - Ha nincs, az összes fellelhető évadhoz külön beküldi a legjobb kiadást.
        """
        releases = self.get_available_releases(media_item, prefer_hungarian=prefer_hungarian)
        if not releases:
            return {"success": False, "message": f"Nem található kiadás a sorozathoz: {media_item.title_hu}"}

        # 1. Van-e teljes sorozat csomag?
        packs = [r for r in releases if r.season_num == -1 or "COMPLETE" in r.title.upper()]
        if packs:
            best_pack = self.ncore_service.select_best_release(packs, prefer_hungarian=prefer_hungarian)
            if best_pack:
                return self.download_specific_release(media_item, best_pack)

        # 2. Évadonkénti letöltés
        seasons = sorted(list({r.season_num for r in releases if r.season_num and r.season_num > 0}))
        if not seasons:
            best = self.ncore_service.select_best_release(releases, prefer_hungarian=prefer_hungarian)
            return self.download_specific_release(media_item, best) if best else {"success": False, "message": "Nincs letölthető kiadás."}

        queued = []
        for s in seasons:
            s_rels = [r for r in releases if r.season_num == s]
            best_s = self.ncore_service.select_best_release(s_rels, prefer_hungarian=prefer_hungarian)
            if best_s:
                res = self.download_specific_release(media_item, best_s)
                if res.get("success"):
                    queued.append(f"S{s:02d}")

        if queued:
            return {
                "success": True,
                "message": f"Sikeresen elindítva {len(queued)} évad ({', '.join(queued)}) a qBittorrentben!",
                "queued_seasons": queued,
            }
        return {"success": False, "message": "Nem sikerült elindítani az évadok letöltését."}

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

    def get_download_history(self) -> List[Dict[str, Any]]:
        """
        Visszaadja az eddigi letöltések teljes előzményét (legújabb elöl).
        """
        self.check_and_process_tasks()
        history = list(self._tasks.values())
        return sorted(history, key=lambda x: x.get("created_at", ""), reverse=True)

    def remove_task(self, task_id: str) -> bool:
        """
        Töröl egy feladatot az előzményekből.
        """
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save_state()
            return True
        return False

    def _save_state(self):
        atomic_save_json(self.STATE_FILE, self._tasks, create_backup=True)

    def _load_state(self) -> Dict[str, dict]:
        return load_json_with_recovery(self.STATE_FILE, default_factory=dict)

    # ==============================================================
    # KEZDŐLAPI AJÁNLÓK (TRENDING, NÉPSZERŰ, HÁBORÚS, MŰFAJOK, LAPOZÁS)
    # ==============================================================
    def get_popular_movies(self, page: int = 1, limit: int = 12) -> List[MediaItem]:
        """Lekéri a népszerű filmeket Magyarországon lapozással."""
        return self.tmdb_service.get_popular_movies(page=page, limit=limit)

    def get_popular_series(self, page: int = 1, limit: int = 12) -> List[MediaItem]:
        """Lekéri a legnépszerűbb sorozatokat lapozással."""
        return self.tmdb_service.get_popular_series(page=page, limit=limit)

    def get_top_rated(
        self, media_type: MediaType = MediaType.MOVIE, page: int = 1, limit: int = 12
    ) -> List[MediaItem]:
        """Lekéri a legmagasabbra értékelt filmeket vagy sorozatokat lapozással."""
        return self.tmdb_service.get_top_rated(media_type=media_type, page=page, limit=limit)

    def get_war_media(
        self,
        media_type: MediaType = MediaType.MOVIE,
        page: int = 1,
        limit: int = 12,
        sort_by: str = "popularity.desc",
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        query: Optional[str] = None,
    ) -> List[MediaItem]:
        """Lekéri a háborús filmeket és sorozatokat szűrőkkel és lapozással."""
        return self.tmdb_service.get_war_media(
            media_type=media_type,
            page=page,
            limit=limit,
            sort_by=sort_by,
            year_min=year_min,
            year_max=year_max,
            query=query,
        )

    def get_by_genre(
        self,
        genre_id: int,
        media_type: MediaType = MediaType.MOVIE,
        page: int = 1,
        limit: int = 12,
        sort_by: str = "popularity.desc",
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        query: Optional[str] = None,
    ) -> List[MediaItem]:
        """Lekéri az adott műfaj tartalmait szűrőkkel és lapozással."""
        return self.tmdb_service.get_by_genre(
            genre_id=genre_id,
            media_type=media_type,
            page=page,
            limit=limit,
            sort_by=sort_by,
            year_min=year_min,
            year_max=year_max,
            query=query,
        )

    def discover_media(
        self,
        media_type: MediaType = MediaType.MOVIE,
        genre_ids: Optional[List[int]] = None,
        sort_by: str = "popularity.desc",
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        page: int = 1,
        limit: int = 12,
        query: Optional[str] = None,
        with_watch_providers: Optional[str] = None,
    ) -> List[MediaItem]:
        """Univerzális média felfedező és szűrő motor a TMDB-n."""
        return self.tmdb_service.discover_media(
            media_type=media_type,
            genre_ids=genre_ids,
            sort_by=sort_by,
            year_min=year_min,
            year_max=year_max,
            page=page,
            limit=limit,
            query=query,
            with_watch_providers=with_watch_providers,
        )

    def get_similar_media(self, item: MediaItem, limit: int = 8) -> List[MediaItem]:
        """Hasonló filmek vagy sorozatok lekérése a TMDB ajánló motorjából."""
        return self.tmdb_service.get_similar(item.tmdb_id, item.media_type, limit=limit)

    def get_collection_details(self, collection_id: int) -> Dict[str, Any]:
        """Egy teljes filmgyűjtemény (franchise) részeinek és adatainak lekérése."""
        return self.tmdb_service.get_collection_details(collection_id)

    def get_person_filmography(self, person_name: str, limit: int = 12) -> Dict[str, Any]:
        """Egy színész vagy rendező adatlapjának és legfontosabb filmjeinek lekérése."""
        return self.tmdb_service.get_person_credits(person_name, limit=limit)

    def download_collection_all(self, collection_id: int) -> Dict[str, Any]:
        """
        Elindítja egy film-gyűjtemény (pl. Harry Potter 1-8) összes részének letöltését sorban!
        """
        coll = self.get_collection_details(collection_id)
        parts = coll.get("parts", [])
        if not parts:
            return {"success": False, "message": "A gyűjtemény nem tartalmaz elérhető filmeket."}

        started_count = 0
        failed_count = 0
        for part in parts:
            res = self.start_download_flow(part, prefer_hungarian=True)
            if res.get("success"):
                started_count += 1
            else:
                failed_count += 1

        msg = f"Gyűjtemény letöltés: {started_count} db film elindítva a qBittorrentben!"
        if failed_count > 0:
            msg += f" ({failed_count} db filmet még nem találtunk nCore-on)."
        return {"success": started_count > 0, "message": msg, "started": started_count}

    def check_release_availability(self, item: MediaItem) -> List[TorrentRelease]:
        """
        Egy adott MediaItem nCore elérhetőségének ellenőrzése (IMDb kód vagy cím alapján).
        Gondoskodik az IMDb kód előzetes feloldásáról a részletes TMDB adatlapon keresztül.
        """
        detailed = item
        if not item.imdb_id:
            detailed = self.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
        return self.get_available_releases(detailed, prefer_hungarian=True)

    def check_releases_batch(
        self,
        items: List[MediaItem],
        max_workers: int = 3,
        callback: Optional[Any] = None,
    ) -> Dict[int, List[TorrentRelease]]:
        """
        Több MediaItem nCore elérhetőségének párhuzamos lekérdezése ThreadPoolExecutor segítségével.
        3 szállal fut, így 3-4x gyorsabb, mint az egymás utáni lekérdezés, miközben biztonságos az nCore szerver felé.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: Dict[int, List[TorrentRelease]] = {}
        if not items:
            return results

        def _worker(it: MediaItem):
            try:
                rels = self.check_release_availability(it)
                return it.tmdb_id, rels, it
            except Exception as e:
                logger.debug(f"Hiba a tétel nCore ellenőrzésekor ({it.title_hu}): {e}")
                return it.tmdb_id, [], it

        completed_count = 0
        total_count = len(items)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_worker, it) for it in items]
            for future in as_completed(futures):
                tmdb_id, rels, it = future.result()
                results[tmdb_id] = rels
                completed_count += 1
                if callback:
                    try:
                        callback(completed_count, total_count, it, rels)
                    except Exception:
                        pass

        return results

    # ==============================================================
    # LEMEZTERÜLET ÉS HNR STATISZTIKÁK
    # ==============================================================
    def get_storage_and_hnr_stats(self) -> Dict[str, Any]:
        """
        Kiszámítja a hardlinkkel megspórolt lemezterületet és az nCore HnR statisztikákat.
        """
        saved_bytes = 0
        organized_count = 0
        for task in self._tasks.values():
            if task.get("plex_organized"):
                organized_count += 1
                rel = task.get("release", {})
                size = rel.get("size_bytes", 0)
                if size > 0:
                    saved_bytes += size

        active_downloads = self.torrent_service.list_active_downloads()
        seeding_count = 0
        hnr_met_count = 0
        total_seed_hours = 0.0

        for d in active_downloads:
            seed_hours = d.get("seeding_time_hours", 0.0)
            ratio = d.get("ratio", 0.0)
            is_comp = d.get("is_completed", False)
            if is_comp:
                seeding_count += 1
                total_seed_hours += seed_hours
                if seed_hours >= self.settings.MIN_SEED_TIME_HOURS or ratio >= self.settings.TARGET_RATIO:
                    hnr_met_count += 1

        gb_saved = round(saved_bytes / (1024 ** 3), 2)
        mb_saved = round(saved_bytes / (1024 ** 2), 1)
        size_human = f"{gb_saved} GB" if gb_saved >= 1.0 else f"{mb_saved} MB"

        return {
            "saved_bytes": saved_bytes,
            "saved_human": size_human,
            "organized_count": organized_count,
            "seeding_count": seeding_count,
            "hnr_met_count": hnr_met_count,
            "total_seed_hours": round(total_seed_hours, 1),
            "target_hours": self.settings.MIN_SEED_TIME_HOURS,
            "target_ratio": self.settings.TARGET_RATIO,
        }
