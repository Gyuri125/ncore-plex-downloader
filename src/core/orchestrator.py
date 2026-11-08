import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from loguru import logger

from src.config.settings import get_settings
from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.core.autostart import AutostartManager
from src.core.movie_radar import MovieRadarManager
from src.core.organizer import MediaOrganizer
from src.core.series_radar import SeriesRadarManager
from src.core.subtitle_radar import SubtitleRadarManager
from src.core.audio_radar import AudioRadarManager
from src.core.watchlist import WatchlistManager
from src.core.watchlist_sync import WatchlistSyncService
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
        self._series_radar = SeriesRadarManager()
        self._subtitle_radar = SubtitleRadarManager()
        self._audio_radar = AudioRadarManager()
        self.watchlist_sync = WatchlistSyncService()
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

    @property
    def series_radar(self) -> SeriesRadarManager:
        """Biztosítja a Sorozat-Radar elérését."""
        if not hasattr(self, "_series_radar") or self._series_radar is None:
            self._series_radar = SeriesRadarManager()
        return self._series_radar

    @series_radar.setter
    def series_radar(self, value: SeriesRadarManager):
        self._series_radar = value

    @property
    def subtitle_radar(self) -> SubtitleRadarManager:
        """Biztosítja a Felirat-Radar elérését."""
        if not hasattr(self, "_subtitle_radar") or self._subtitle_radar is None:
            self._subtitle_radar = SubtitleRadarManager()
        return self._subtitle_radar

    @subtitle_radar.setter
    def subtitle_radar(self, value: SubtitleRadarManager):
        self._subtitle_radar = value

    @property
    def audio_radar(self) -> AudioRadarManager:
        """Biztosítja a Szinkron-Radar elérését."""
        if not hasattr(self, "_audio_radar") or self._audio_radar is None:
            self._audio_radar = AudioRadarManager()
        return self._audio_radar

    @audio_radar.setter
    def audio_radar(self, value: AudioRadarManager):
        self._audio_radar = value

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
        sub_info_dict = None
        if not release.has_hungarian_audio:
            logger.info("A kiadás nem tartalmaz magyar szinkront, feliratkeresés indul...")
            sub_file = self.subtitle_service.find_and_download_subtitle(
                media_item=media_item,
                release=release,
                destination_dir=torrent_temp_dir,
            )
            if sub_file:
                sub_path_str = str(sub_file)
                last_sub = getattr(self.subtitle_service, "last_subtitle_item", None)
                if last_sub:
                    sub_info_dict = last_sub.model_dump()
                else:
                    sub_info_dict = {
                        "source": "Feliratok.info",
                        "language": "hu",
                        "file_name": sub_file.name,
                        "release_name": release.title,
                    }

        # 4. Feladat állapot mentése a folyamatos figyeléshez
        # Először frissítünk a lemezről, hogy ne írjuk felül a háttérfolyamat módosításait
        self._tasks = self._load_state()
        task_id = torrent_hash if torrent_hash != "OK" else release.torrent_id
        self._tasks[task_id] = {
            "task_id": task_id,
            "torrent_hash": torrent_hash,
            "media_item": media_item.model_dump(),
            "release": release.model_dump(),
            "status": "downloading",
            "plex_organized": False,
            "subtitle_path": sub_path_str,
            "subtitle_info": sub_info_dict,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_state()

        return {
            "success": True,
            "message": f"Sikeresen elindítva: {release.title}",
            "release": release,
            "has_hungarian_audio": release.has_hungarian_audio,
            "has_subtitle": bool(sub_path_str),
            "subtitle_info": sub_info_dict,
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
        - Mindig betölti a legfrissebb lemezállapotot a háttérfolyamat és a WebUI szinkronban tartásához.
        - Ellenőrzi a folyamatban lévő letöltések állapotát.
        - Ha a letöltés befejeződött: Hardlinkkel vagy másolással azonnal Plexbe rendezi és frissíti a könyvtárat!
        - Figyeli az nCore Hit & Run (48h / 1.0 arány) teljesülését és leállítja a feladatot.
        """
        self._tasks = self._load_state()
        updated_tasks = []
        active_list = None

        for task_id, task in list(self._tasks.items()):
            torrent_hash = task.get("torrent_hash")
            if not torrent_hash:
                continue

            # Ha "OK" került mentésre, próbáljuk feloldani a valódi hash-t a qBittorrentből
            if torrent_hash == "OK":
                if active_list is None:
                    active_list = self.torrent_service.list_active_downloads(category="plex-auto")
                rel_title = task.get("release", {}).get("title", "").lower()
                for act in active_list:
                    act_name = act.get("name", "").lower()
                    if act_name and (act_name in rel_title or rel_title in act_name):
                        torrent_hash = act["hash"]
                        task["torrent_hash"] = torrent_hash
                        break
                if torrent_hash == "OK":
                    continue

            status = self.torrent_service.get_status(torrent_hash)
            if not status:
                continue

            task["current_status"] = status
            is_completed = status.get("is_completed", False)

            # 0. Beragadt feltöltési mód öngyógyító feloldása nem kész letöltéseknél
            if not is_completed:
                stuck_fixed = self.torrent_service.fix_stuck_upload_mode(torrent_hash)
                if stuck_fixed:
                    logger.info(f"Beragadt feltöltési mód feloldva: {', '.join(stuck_fixed)}")

            # 1. Ha befejeződött a letöltés és még nincs Plexben, rendezzük át!
            if is_completed and not task.get("plex_organized", False):
                logger.info(f"Letöltés befejeződött! Plexbe rendezés: {status['name']}")
                
                # Forrás elérési út feloldása robusztusan (content_path vagy save_path / name)
                source_path = None
                raw_content = status.get("content_path")
                if raw_content and Path(raw_content).exists():
                    source_path = Path(raw_content)
                elif status.get("save_path"):
                    cand = Path(status["save_path"]) / status.get("name", "")
                    if cand.exists():
                        source_path = cand
                    elif Path(status["save_path"]).exists():
                        source_path = Path(status["save_path"])

                if not source_path or not source_path.exists():
                    temp_dir = self.settings.DOWNLOAD_TEMP_DIR
                    t_name = status.get("name", "")
                    if temp_dir and temp_dir.exists():
                        cand2 = temp_dir / t_name
                        if cand2.exists():
                            source_path = cand2
                        else:
                            for sub in temp_dir.iterdir():
                                if sub.name == t_name or (t_name and t_name in sub.name):
                                    source_path = sub
                                    break

                if source_path and source_path.exists():
                    media_item = MediaItem(**task["media_item"])
                    release = TorrentRelease(**task["release"])

                    sub_files = []
                    if task.get("subtitle_path"):
                        sub_p = Path(task["subtitle_path"])
                        if sub_p.exists():
                            if sub_p.is_dir():
                                sub_files.extend(list(sub_p.glob("*.srt")))
                            else:
                                sub_files.append(sub_p)

                    # Ha nem magyar szinkronos és nem volt még felirat, a letöltés végeztével
                    # automatikusan megpróbáljuk megkeresni a Plexbe rendezés előtt!
                    if not release.has_hungarian_audio and not sub_files:
                        logger.info(f"Letöltés kész, utólagos feliratkeresés a(z) '{release.title}' kiadáshoz...")
                        sub_dest = source_path if source_path.is_dir() else source_path.parent
                        sub_retry = self.subtitle_service.find_and_download_subtitle(
                            media_item=media_item,
                            release=release,
                            destination_dir=sub_dest,
                        )
                        if sub_retry and sub_retry.exists():
                            task["subtitle_path"] = str(sub_retry)
                            last_sub = getattr(self.subtitle_service, "last_subtitle_item", None)
                            if last_sub:
                                task["subtitle_info"] = last_sub.model_dump()
                            if sub_retry.is_dir():
                                sub_files.extend(list(sub_retry.glob("*.srt")))
                            else:
                                sub_files.append(sub_retry)

                    # Hardlink / Másolás a Plex mappába
                    organized_files = self.organizer.organize_media(
                        source_path=source_path,
                        media_item=media_item,
                        release=release,
                        attached_subtitles=sub_files,
                    )

                    if organized_files:
                        target_dir = organized_files[0].parent
                        is_hl = getattr(self.organizer, "last_placement_hardlink", True)
                        has_embedded = getattr(self.organizer, "last_has_embedded_hungarian_sub", False)
                        task["plex_organized"] = True
                        task["status"] = "in_plex_and_seeding"
                        task["is_hardlink"] = is_hl
                        if has_embedded:
                            task["has_embedded_hungarian_sub"] = True
                        task["destination_folder"] = str(target_dir)
                        task["destination_files"] = [f.name for f in organized_files]
                        task["organized_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        # Plex könyvtár azonnali frissítése
                        self.plex_service.refresh_library(
                            media_type="movie" if media_item.media_type == MediaType.MOVIE else "show"
                        )
                        logger.info(
                            f"Plex könyvtár sikeresen frissítve! ({len(organized_files)} fájl elhelyezve a(z) '{target_dir}' mappában, hardlink={is_hl})"
                        )
                        # Ha nem magyar hangú és nincs felirata sem, automatikusan figyeljük a Felirat-Radaron
                        if (
                            not release.has_hungarian_audio
                            and not task.get("subtitle_path")
                            and not task.get("has_embedded_hungarian_sub", False)
                        ):
                            logger.info(f"Nincs magyar szinkron/felirat, hozzáadva a Felirat-Radarhoz: '{media_item.title_hu}'")
                            self.subtitle_radar.add(
                                item=media_item,
                                release=release,
                                destination_folder=str(target_dir),
                                destination_files=[f.name for f in organized_files],
                                task_id=task_id,
                            )
                            task["subtitle_monitored_by_radar"] = True

                        # Ha nem magyar hangú, automatikusan figyeljük a Szinkron-Radaron későbbi HD szinkronra
                        if not release.has_hungarian_audio:
                            self.audio_radar.add(
                                item=media_item,
                                current_release=release,
                                destination_folder=str(target_dir),
                                task_id=task_id,
                            )
                            task["audio_monitored_by_radar"] = True
                        else:
                            self.audio_radar.mark_upgraded(media_item.tmdb_id, release.title)
                    else:
                        logger.warning(f"Nem sikerült médiafájlokat elhelyezni a forrásból: {source_path}")
                else:
                    logger.warning(f"A letöltött fájl útvonala nem létezik a lemezen: {raw_content}")

            # 2. nCore HnR seedelés ellenőrzése és automata temp törlés
            if is_completed:
                # Csak akkor töröljük a letöltési temp adatokat, ha a film már 100%-ban átkerült
                # a Plexbe, a célfájl ténylegesen ott van a lemezen és nem üres!
                can_delete_temp = False
                delete_setting = getattr(self.settings, "DELETE_TORRENT_DATA_AFTER_SEED", True)
                if task.get("plex_organized", False) and delete_setting:
                    dest_folder = task.get("destination_folder")
                    dest_files = task.get("destination_files", [])
                    if dest_folder and Path(dest_folder).exists():
                        for df in dest_files:
                            target_file = Path(dest_folder) / df
                            if target_file.exists() and target_file.stat().st_size > 1024 * 1024:
                                can_delete_temp = True
                                break

                hnr_result = self.torrent_service.check_hnr_compliance(
                    torrent_hash, delete_data_files=can_delete_temp
                )
                if hnr_result.get("compliant", False):
                    task["status"] = "completed_and_removed"
                    task["temp_deleted"] = can_delete_temp
                    logger.info(
                        f"HnR teljesítve, feladat lezárva: {status['name']} (letöltési temp törölve: {can_delete_temp})"
                    )

            updated_tasks.append(task)

        self._save_state()
        return updated_tasks

    def get_transfer_progress(self) -> Optional[Dict[str, Any]]:
        """
        Visszaadja a jelenleg futó lemezek közötti másolási feladat adatait (% kész, MB/s sebesség, ETA).
        Ha nincs aktív másolás, None értéket ad.
        """
        if hasattr(self.organizer, "get_current_transfer"):
            return self.organizer.get_current_transfer()
        return getattr(self.organizer, "current_transfer", None)

    def get_last_placement_info(self) -> Optional[Dict[str, Any]]:
        """
        Visszaadja a legutóbbi sikeres elhelyezés adatait (célmappa, fájlok, hardlink/másolás).
        """
        if hasattr(self.organizer, "get_last_placement_info"):
            return self.organizer.get_last_placement_info()
        return getattr(self.organizer, "last_placement_info", None)

    def attach_or_update_subtitle(self, task_id: str) -> Dict[str, Any]:
        """
        Kifejezetten egy feladathoz/filmhez kapcsolódó magyar felirat keresése és elhelyezése utólagosan is.
        Ha a film már a Plexben van, közvetlenül a Plex mappába másolja a .hu.srt fájlt és frissíti a Plexet!
        """
        self._tasks = self._load_state()
        task = self._tasks.get(task_id)
        if not task:
            # Hash vagy task_id alapján is megpróbáljuk megtalálni
            task = next((t for t in self._tasks.values() if t.get("torrent_hash") == task_id or t.get("task_id") == task_id), None)

        if not task:
            return {"success": False, "message": "A megadott letöltési feladat nem található a rendszerben."}

        media_item = MediaItem(**task["media_item"])
        release = TorrentRelease(**task["release"])

        # Célmappa meghatározása: ha már Plexben van, oda keressük, egyébként az ideiglenes munkamappába
        dest_folder = None
        if task.get("destination_folder") and Path(task["destination_folder"]).exists():
            dest_folder = Path(task["destination_folder"])
        else:
            dest_folder = self.settings.DOWNLOAD_TEMP_DIR / release.title

        dest_folder.mkdir(parents=True, exist_ok=True)

        logger.info(f"Kézi feliratkeresés indítása: '{media_item.title_hu}' ({release.title}) -> {dest_folder}")
        sub_file = self.subtitle_service.find_and_download_subtitle(
            media_item=media_item,
            release=release,
            destination_dir=dest_folder,
        )

        if not sub_file or not sub_file.exists():
            # Automatikus hozzáadás a Felirat-Radarhoz a későbbi megjelenés figyelésére
            self.subtitle_radar.add(
                item=media_item,
                release=release,
                destination_folder=str(dest_folder) if dest_folder and dest_folder.exists() else None,
                task_id=task.get("task_id", task_id),
            )
            task["subtitle_monitored_by_radar"] = True
            self._save_state()
            return {
                "success": False,
                "message": (
                    f"Sajnos sem a Feliratok.eu-n, sem a Subirat.net-en, sem az OpenSubtitles-on "
                    f"nem található még megfelelő magyar felirat ehhez a kiadáshoz ({release.title}).\n"
                    f"🔔 Automatikusan hozzáadva a Felirat-Radarhoz: amint megjelenik a fordítás, "
                    f"a háttérfolyamat automatikusan letölti és társítja a Plexhez!"
                ),
            }

        task["subtitle_path"] = str(sub_file)
        last_sub = getattr(self.subtitle_service, "last_subtitle_item", None)
        if last_sub:
            task["subtitle_info"] = last_sub.model_dump()
        else:
            task["subtitle_info"] = {
                "source": "Feliratok.info",
                "file_name": sub_file.name,
                "language": "hu",
                "release_name": release.title,
            }

        # Ha a film már el van helyezve a Plexben, a feliratot a Plex mappába másoljuk átnevezve
        placed_sub_name = sub_file.name
        if task.get("destination_folder"):
            target_plex_dir = Path(task["destination_folder"])
            import shutil

            if sub_file.is_dir():
                # Évadcsomag felirat mappa esetén epizódokhoz illesztjük a .srt fájlokat
                clean_show_title = self.organizer._sanitize_filename(media_item.title_hu or media_item.title_en or "Show")
                for srt_item in sorted(sub_file.glob("*.srt")):
                    sub_s, sub_e = self.organizer._extract_season_episode(srt_item.name)
                    target_s = sub_s or (release.season_num if release.season_num and release.season_num > 0 else 1)
                    if sub_e:
                        dest_srt = target_plex_dir / f"{clean_show_title} - S{target_s:02d}E{sub_e:02d}.hu.srt"
                    else:
                        dest_srt = target_plex_dir / f"{srt_item.stem}.hu.srt"
                    shutil.copy2(srt_item, dest_srt)
                placed_sub_name = f"{sub_file.name} (évadcsomag)"
            else:
                clean_title = self.organizer._sanitize_filename(media_item.title_hu or media_item.title_en or "Movie")
                year_str = f" ({media_item.year})" if media_item.year else ""
                quality_tag = self.organizer._extract_quality_tag(release.title)

                # Megkeressük a fő videófájlt a Plex mappában a pontos névegyezéshez
                plex_videos = [f for f in target_plex_dir.glob("*") if f.suffix.lower() in self.organizer.VIDEO_EXTENSIONS]
                if plex_videos:
                    main_vid = max(plex_videos, key=lambda f: f.stat().st_size)
                    dest_sub_exact = target_plex_dir / f"{main_vid.stem}.hu.srt"
                    dest_sub_short = target_plex_dir / f"{clean_title}{year_str}.hu.srt"
                    if sub_file != dest_sub_exact:
                        shutil.copy2(sub_file, dest_sub_exact)
                    if sub_file != dest_sub_short and not dest_sub_short.exists():
                        shutil.copy2(sub_file, dest_sub_short)
                    placed_sub_name = dest_sub_exact.name
                else:
                    dest_sub = target_plex_dir / f"{clean_title}{year_str} [{quality_tag}].hu.srt"
                    if sub_file != dest_sub:
                        shutil.copy2(sub_file, dest_sub)
                    placed_sub_name = dest_sub.name

            # Plex könyvtár azonnali frissítése az új felirattal
            self.plex_service.refresh_library(
                media_type="movie" if media_item.media_type == MediaType.MOVIE else "show"
            )

        self._save_state()
        src_name = task["subtitle_info"].get("source", "Feliratok.eu")
        self.subtitle_radar.mark_resolved(media_item.tmdb_id, subtitle_path=str(sub_file), source=src_name)
        return {
            "success": True,
            "message": f"✅ Magyar felirat sikeresen csatolva és a film mellé helyezve ({src_name})!",
            "subtitle_path": str(sub_file),
            "subtitle_name": placed_sub_name,
            "subtitle_info": task["subtitle_info"],
        }

    def attach_manual_subtitle(
        self,
        task_id: str,
        file_name: str,
        file_bytes: bytes,
    ) -> Dict[str, Any]:
        """
        Kézi felirat (.srt vagy .zip) feltöltése és hozzárendelése egy filmhez vagy sorozathoz.
        Automatikusan UTF-8 kódolásra konvertálja (megelőzve az ékezetes karakterhibákat),
        a Plex szabványnak megfelelően elhelyezi a célmappában (videó pontos stemje + tiszta cím),
        és azonnal frissíti a Plex könyvtárat!
        """
        self._tasks = self._load_state()
        task = self._tasks.get(task_id)
        if not task:
            task = next(
                (t for t in self._tasks.values() if t.get("torrent_hash") == task_id or t.get("task_id") == task_id),
                None,
            )

        if not task:
            return {"success": False, "message": "A letöltési feladat nem található a rendszerben."}

        if not file_bytes:
            return {"success": False, "message": "A feltöltött feliratfájl üres!"}

        import io
        import zipfile

        extracted_srts: List[tuple[str, str]] = []
        is_zip = file_bytes.startswith(b"PK\x03\x04") or file_name.lower().endswith(".zip")

        if is_zip:
            try:
                with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                    srt_names = [n for n in zf.namelist() if n.lower().endswith(".srt") and not n.startswith("__MACOSX")]
                    if not srt_names:
                        return {"success": False, "message": "A feltöltött ZIP archívum nem tartalmazott .srt fájlt!"}
                    for name in srt_names:
                        b = zf.read(name)
                        txt = self.subtitle_service._decode_hungarian_text(b)
                        extracted_srts.append((Path(name).name, txt))
            except Exception as exc:
                return {"success": False, "message": f"Nem sikerült kicsomagolni a felirat ZIP-et: {exc}"}
        else:
            txt = self.subtitle_service._decode_hungarian_text(file_bytes)
            extracted_srts.append((file_name, txt))

        media_item = MediaItem(**task["media_item"])
        release = TorrentRelease(**task["release"])

        dest_folder = None
        if task.get("destination_folder") and Path(task["destination_folder"]).exists():
            dest_folder = Path(task["destination_folder"])
        else:
            dest_folder = self.settings.DOWNLOAD_TEMP_DIR / release.title
            dest_folder.mkdir(parents=True, exist_ok=True)

        saved_files: List[Path] = []
        is_plex_placed = bool(task.get("destination_folder") and Path(task["destination_folder"]).exists())

        if media_item.media_type == MediaType.MOVIE:
            clean_title = self.organizer._sanitize_filename(media_item.title_hu or media_item.title_en or "Movie")
            year_str = f" ({media_item.year})" if media_item.year else ""
            quality_tag = self.organizer._extract_quality_tag(release.title)

            plex_videos = [f for f in dest_folder.glob("*") if f.suffix.lower() in self.organizer.VIDEO_EXTENSIONS]
            for orig_name, srt_content in extracted_srts:
                if plex_videos:
                    main_vid = max(plex_videos, key=lambda f: f.stat().st_size)
                    dest_sub_exact = dest_folder / f"{main_vid.stem}.hu.srt"
                    dest_sub_short = dest_folder / f"{clean_title}{year_str}.hu.srt"
                    dest_sub_exact.write_text(srt_content, encoding="utf-8")
                    if dest_sub_short != dest_sub_exact and not dest_sub_short.exists():
                        dest_sub_short.write_text(srt_content, encoding="utf-8")
                    saved_files.append(dest_sub_exact)
                else:
                    dest_sub = dest_folder / f"{clean_title}{year_str} [{quality_tag}].hu.srt"
                    dest_sub.write_text(srt_content, encoding="utf-8")
                    saved_files.append(dest_sub)
        else:
            clean_show_title = self.organizer._sanitize_filename(media_item.title_hu)
            for orig_name, srt_content in extracted_srts:
                sub_s, sub_e = self.organizer._extract_season_episode(orig_name)
                if not sub_s and release and release.season_num and release.season_num > 0:
                    sub_s = release.season_num
                target_season = sub_s or 1

                if "Season" in dest_folder.name:
                    season_folder = dest_folder
                else:
                    season_folder = dest_folder / f"Season {target_season:02d}"
                    season_folder.mkdir(parents=True, exist_ok=True)

                if sub_e:
                    dest_sub = season_folder / f"{clean_show_title} - S{target_season:02d}E{sub_e:02d}.hu.srt"
                else:
                    dest_sub = season_folder / f"{Path(orig_name).stem}.hu.srt"

                dest_sub.write_text(srt_content, encoding="utf-8")
                saved_files.append(dest_sub)

        primary_saved = saved_files[0] if saved_files else None
        task["subtitle_path"] = str(primary_saved) if primary_saved else None
        task["has_subtitle"] = True
        task["subtitle_info"] = {
            "source": "Saját feltöltés (.srt)",
            "file_name": file_name,
            "saved_file": primary_saved.name if primary_saved else file_name,
            "language": "hu",
            "release_name": release.title,
            "is_manual": True,
            "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(saved_files),
        }

        # Ha a cél Plex mappa, azonnali Plex frissítés
        if is_plex_placed:
            self.plex_service.refresh_library(
                media_type="movie" if media_item.media_type == MediaType.MOVIE else "show"
            )

        self._save_state()
        if primary_saved:
            self.subtitle_radar.mark_resolved(media_item.tmdb_id, str(primary_saved), "Saját feltöltés (.srt)")
        logger.info(
            f"Kézi felirat sikeresen csatolva: '{file_name}' -> {primary_saved} ({len(saved_files)} db)"
        )
        return {
            "success": True,
            "message": f"✅ Saját magyar felirat sikeresen csatolva és UTF-8-ra formázva: {primary_saved.name if primary_saved else file_name}!",
            "subtitle_path": str(primary_saved) if primary_saved else None,
            "subtitle_name": primary_saved.name if primary_saved else file_name,
            "subtitle_info": task["subtitle_info"],
        }

    def get_active_downloads(self, include_all: bool = True) -> List[Dict[str, Any]]:
        """
        Visszaadja a qBittorrentben lévő aktív feladatok listáját a felület számára.
        Alapértelmezetten az összes qBittorrent torrentet visszaadja (nem csak a plex-auto-t),
        így a korábbi vagy kézzel hozzáadott torrentek sem vesznek el a felületről.
        """
        self.check_and_process_tasks()
        return self.torrent_service.list_active_downloads(include_all=include_all)

    def force_organize_task(
        self,
        task_id_or_hash: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Kényszerített média mozgatás a Plex mappába:
        Azonnal megkeresi a letöltött fájlokat a lemezen, és áthelyezi/átmásolja őket a Plex mappába
        (akkor is, ha a torrent állapota beragadt, vagy korábban nem sikerült a rendezés).
        Valós idejű haladáskövetést (progress_callback) biztosít a felület számára.
        """
        self._tasks = self._load_state()
        task = self._tasks.get(task_id_or_hash)

        # Ha nem találjuk közvetlen task_id alapján, keressük hash szerint
        if not task:
            for t_id, t_data in self._tasks.items():
                if t_data.get("torrent_hash") == task_id_or_hash:
                    task = t_data
                    task_id_or_hash = t_id
                    break

        status = self.torrent_service.get_status(task.get("torrent_hash") if task else task_id_or_hash)
        torrent_name = status.get("name", "") if status else (task.get("release", {}).get("title") if task else "")

        # Forrás útvonal feloldása
        source_path = None
        if status:
            raw_c = status.get("content_path")
            if raw_c and Path(raw_c).exists():
                source_path = Path(raw_c)
            elif status.get("save_path"):
                cand1 = Path(status["save_path"]) / status.get("name", "")
                if cand1.exists():
                    source_path = cand1
                elif Path(status["save_path"]).exists():
                    source_path = Path(status["save_path"])

        # Közvetlenül a taskban tárolt útvonal ellenőrzése
        if (not source_path or not source_path.exists()) and task:
            for k in ["content_path", "source_path"]:
                cand = task.get(k)
                if cand and Path(cand).exists():
                    source_path = Path(cand)
                    break

        # Fallback keresés a beállított letöltési munkamappában
        if not source_path or not source_path.exists():
            temp_dir = self.settings.DOWNLOAD_TEMP_DIR
            if temp_dir and temp_dir.exists():
                cand2 = temp_dir / torrent_name
                if cand2.exists():
                    source_path = cand2
                else:
                    for sub in temp_dir.iterdir():
                        if sub.name == torrent_name or (torrent_name and torrent_name in sub.name):
                            source_path = sub
                            break

        if not source_path or not source_path.exists():
            return {
                "success": False,
                "message": f"Nem található a letöltött fájl a lemezen! Keresett hely: {self.settings.DOWNLOAD_TEMP_DIR}",
            }

        # Ha a feladat nem szerepelt a tasks_state-ben (pl. meglévő korábbi torrent importálása)
        if not task:
            import re
            from src.models.media import extract_season_num
            from src.services.ncore_service import NcoreService

            season_num = extract_season_num(torrent_name)
            is_series = bool(season_num is not None or re.search(r"\b[sS]\d{1,2}[eE]?\d{0,2}\b", torrent_name))
            m_type = MediaType.TV if is_series else MediaType.MOVIE

            clean_stem = NcoreService._get_release_title_stem(torrent_name) or torrent_name
            year_matches = re.findall(r"\b(19\d\d|20\d\d)\b", torrent_name)
            parsed_year = int(year_matches[-1]) if year_matches else None

            # Próbáljuk megkeresni TMDB-n a pontos adatlapot
            media_item = None
            try:
                tmdb_res = self.tmdb_service.search(clean_stem)
                if tmdb_res:
                    typed_res = [r for r in tmdb_res if r.media_type == m_type]
                    media_item = typed_res[0] if typed_res else tmdb_res[0]
            except Exception:
                pass

            if not media_item:
                media_item = MediaItem(
                    tmdb_id=0,
                    media_type=m_type,
                    title_hu=clean_stem.title(),
                    title_en=clean_stem.title(),
                    year=parsed_year,
                )

            has_hun = bool(re.search(r"\b(hun|magyar|szinkron)\b", torrent_name, re.IGNORECASE) or ".hun." in torrent_name.lower())
            release = TorrentRelease(
                torrent_id="manual",
                title=torrent_name,
                category=("sorozat_hun" if has_hun else "sorozat") if m_type == MediaType.TV else ("hd_hun" if has_hun else "hd"),
                size_bytes=source_path.stat().st_size if source_path.is_file() else 0,
                has_hungarian_audio=has_hun,
                download_url="",
            )

            task_id = status.get("hash") if status else task_id_or_hash
            task = {
                "task_id": task_id,
                "torrent_hash": task_id,
                "media_item": media_item.model_dump(),
                "release": release.model_dump(),
                "status": "downloading",
                "plex_organized": False,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._tasks[task_id] = task

        media_item = MediaItem(**task["media_item"])
        release = TorrentRelease(**task["release"])

        sub_files = []
        if task.get("subtitle_path"):
            sub_p = Path(task["subtitle_path"])
            if sub_p.exists():
                if sub_p.is_dir():
                    sub_files.extend(list(sub_p.glob("*.srt")))
                else:
                    sub_files.append(sub_p)

        # Utólagos feliratkeresés, ha eredeti nyelvű és még nincs felirat
        if not release.has_hungarian_audio and not sub_files:
            sub_dest = source_path if source_path.is_dir() else source_path.parent
            sub_retry = self.subtitle_service.find_and_download_subtitle(
                media_item=media_item,
                release=release,
                destination_dir=sub_dest,
            )
            if sub_retry and sub_retry.exists():
                task["subtitle_path"] = str(sub_retry)
                last_sub = getattr(self.subtitle_service, "last_subtitle_item", None)
                if last_sub:
                    task["subtitle_info"] = last_sub.model_dump()
                if sub_retry.is_dir():
                    sub_files.extend(list(sub_retry.glob("*.srt")))
                else:
                    sub_files.append(sub_retry)

        # Média rendszerezése haladás-visszahívással (callback)
        organized_files = self.organizer.organize_media(
            source_path=source_path,
            media_item=media_item,
            release=release,
            attached_subtitles=sub_files,
            callback=progress_callback,
        )

        if not organized_files:
            return {
                "success": False,
                "message": f"Nem található feldolgozható videófájl a forrásban: {source_path}",
            }

        target_dir = organized_files[0].parent
        is_hl = getattr(self.organizer, "last_placement_hardlink", True)
        task["plex_organized"] = True
        task["status"] = "in_plex_and_seeding"
        task["is_hardlink"] = is_hl
        task["destination_folder"] = str(target_dir)
        task["destination_files"] = [f.name for f in organized_files]
        task["organized_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Ellenőrizzük a beágyazott magyar felirat meglétét az MKV konténerekben
        has_embedded_sub = False
        for f in organized_files:
            if f.suffix.lower() == ".mkv" and hasattr(self.organizer, "check_mkv_embedded_hungarian_sub"):
                if self.organizer.check_mkv_embedded_hungarian_sub(f):
                    has_embedded_sub = True
                    break
        if has_embedded_sub:
            task["has_embedded_hungarian_sub"] = True

        # Ha nincs magyar hang és felirat sem, hozzáadjuk a Felirat-Radarhoz
        if (
            not release.has_hungarian_audio
            and not task.get("subtitle_path")
            and not has_embedded_sub
        ):
            self.subtitle_radar.add(
                item=media_item,
                release=release,
                destination_folder=str(target_dir),
                destination_files=[f.name for f in organized_files],
                task_id=task.get("task_id", task_id_or_hash),
            )
            task["subtitle_monitored_by_radar"] = True

        # Ha nem magyar hangú, automatikusan figyeljük a Szinkron-Radaron későbbi HD szinkronra
        if not release.has_hungarian_audio:
            self.audio_radar.add(
                item=media_item,
                current_release=release,
                destination_folder=str(target_dir),
                task_id=task.get("task_id", task_id_or_hash),
            )
            task["audio_monitored_by_radar"] = True
        else:
            self.audio_radar.mark_upgraded(media_item.tmdb_id, release.title)

        self._save_state()

        # Plex frissítés
        self.plex_service.refresh_library(
            media_type="movie" if media_item.media_type == MediaType.MOVIE else "show"
        )

        method_str = "Hardlink (azonnali)" if is_hl else "Valódi másolás (másik meghajtó)"
        return {
            "success": True,
            "message": f"✅ Sikeresen elhelyezve a Plex könyvtárban ({len(organized_files)} fájl)! Módszer: {method_str}",
            "destination_folder": str(target_dir),
            "destination_files": [f.name for f in organized_files],
            "is_hardlink": is_hl,
        }

    def force_delete_task(self, task_id_or_hash: str, delete_data: bool = True) -> Dict[str, Any]:
        """
        Kényszerített törlés:
        Eltávolítja a torrentet a qBittorrentből, és ha delete_data=True,
        törli a duplikált letöltési temp adatokat is, felszabadítva a lemezterületet.
        """
        self._tasks = self._load_state()
        task = self._tasks.get(task_id_or_hash)
        torrent_hash = task.get("torrent_hash") if task else task_id_or_hash

        if not self.torrent_service._ensure_connected():
            return {"success": False, "message": "Nem sikerült csatlakozni a qBittorrenthez."}

        try:
            self.torrent_service.client.torrents_delete(delete_files=delete_data, torrent_hashes=torrent_hash)
            if task:
                task["status"] = "completed_and_removed"
                task["temp_deleted"] = delete_data
                self._save_state()

            msg = "Torrent és letöltési adatok sikeresen törölve (tárhely felszabadítva)!" if delete_data else "Torrent eltávolítva a qBittorrentből (fájlok megtartva)."
            return {"success": True, "message": f"✅ {msg}"}
        except Exception as e:
            return {"success": False, "message": f"Hiba a törlés során: {e}"}

    def cleanup_all_seeded_torrents(self) -> Dict[str, Any]:
        """
        Minden olyan torrentet eltávolít és kitakarít a qBittorrentből és a letöltési mappából,
        amely elérte a Hit & Run szabályt (legalább 48 óra seed vagy 1.0 arány),
        DE előtte garantálja, hogy a média már biztonságban a Plexben van!
        """
        self._tasks = self._load_state()
        active_list = self.torrent_service.list_active_downloads(include_all=True)
        cleaned_count = 0
        cleaned_names = []

        target_h = self.settings.MIN_SEED_TIME_HOURS
        target_r = self.settings.TARGET_RATIO

        for t in active_list:
            seed_h = t.get("seeding_time_hours", 0.0)
            ratio = t.get("ratio", 0.0)
            is_comp = t.get("is_completed", False) or seed_h > 0
            if is_comp and (seed_h >= target_h or ratio >= target_r):
                t_hash = t["hash"]
                task = self._tasks.get(t_hash)
                # Biztonsági védelem: ha még nincs a Plexben, előbb rendezzük át!
                if not task or not task.get("plex_organized", False):
                    org_res = self.force_organize_task(t_hash)
                    if not org_res.get("success"):
                        logger.warning(f"Törlés kihagyva, mert a film még nincs a Plexben: {t.get('name', t_hash)}")
                        continue

                res = self.force_delete_task(t_hash, delete_data=True)
                if res.get("success"):
                    cleaned_count += 1
                    cleaned_names.append(t["name"])

        return {
            "success": True,
            "count": cleaned_count,
            "names": cleaned_names,
            "message": f"✅ {cleaned_count} db Hit & Run szabályt teljesített torrent sikeresen törölve a letöltési adatokkal együtt!"
            if cleaned_count > 0
            else "ℹ️ Jelenleg nincs Hit & Run szabályt (48h / 1.0 arány) teljesített torrent a qBittorrentben.",
        }

    def sync_all_qbittorrent_torrents(self, auto_organize: bool = True) -> Dict[str, Any]:
        """
        Beolvassa a qBittorrentben lévő ÖSSZES torrentet (nem csak a plex-auto kategóriát!).
        Ha talál befejezett letöltést, amelyet még nem rendezett a Plexbe, automatikusan
        áthelyezi/átmásolja, és ha már elérte a 48 óra seedelést, felkészíti a takarításra.
        """
        active_list = self.torrent_service.list_active_downloads(include_all=True)
        imported_count = 0
        organized_count = 0

        for t in active_list:
            h = t["hash"]
            task = self._tasks.get(h)
            if not task:
                imported_count += 1
            if auto_organize and t.get("is_completed", False):
                if not task or not task.get("plex_organized", False):
                    org_res = self.force_organize_task(h)
                    if org_res.get("success"):
                        organized_count += 1

        self.check_and_process_tasks()
        return {
            "success": True,
            "total_torrents": len(active_list),
            "imported_count": imported_count,
            "organized_count": organized_count,
            "message": f"✅ {len(active_list)} torrent ellenőrizve ({imported_count} új beolvasva, {organized_count} azonnal Plexbe rendezve)!",
        }

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
        self._tasks = self._load_state()
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save_state()
            return True
        return False

    def get_watched_plex_media(self) -> List[Dict[str, Any]]:
        """
        Lekéri a megnézett Plex filmeket/sorozatokat, és összeveti a helyi feladatokkal,
        hogy jelölje, ha a Hit & Run (48 óra seed vagy 1.0 arány) is teljesült már,
        így 100%-os biztonsággal törölhető a tárhely felszabadításához.
        """
        watched = self.plex_service.get_watched_items()
        active_downloads = {
            d["hash"]: d for d in self.torrent_service.list_active_downloads(include_all=True)
        }

        for item in watched:
            title_lower = item["title"].lower()
            matching_task = None
            for t in self._tasks.values():
                t_title = t.get("media_item", {}).get("title_hu", "").lower()
                t_en = t.get("media_item", {}).get("title_en", "").lower()
                if title_lower in (t_title, t_en) or (t_title and t_title in title_lower):
                    matching_task = t
                    break

            is_hnr_done = False
            t_hash = matching_task.get("torrent_hash") if matching_task else None
            if t_hash and t_hash in active_downloads:
                t_info = active_downloads[t_hash]
                seed_h = t_info.get("seeding_time_hours", 0.0)
                ratio = t_info.get("ratio", 0.0)
                if seed_h >= self.settings.MIN_SEED_TIME_HOURS or ratio >= self.settings.TARGET_RATIO:
                    is_hnr_done = True
            elif not matching_task or matching_task.get("status") in ("completed_and_removed", "completed"):
                is_hnr_done = True

            item["is_hnr_done"] = is_hnr_done
            item["matching_task"] = matching_task

        return watched

    def delete_watched_plex_media(
        self,
        rating_key: str,
        file_paths: List[str],
        delete_torrent: bool = True,
    ) -> Dict[str, Any]:
        """
        Törli a megnézett médiafájlokat a merevlemezről, opcionálisan törli a hozzá
        tartozó torrentet a qBittorrentből, és azonnal frissíti a Plex könyvtárat.
        """
        ok, freed_bytes = self.plex_service.delete_item_files(file_paths)
        freed_gb = freed_bytes / (1024 ** 3)

        if delete_torrent:
            for t in self._tasks.values():
                dest_files = t.get("destination_files", [])
                if any(Path(fp).name in dest_files for fp in file_paths):
                    t_hash = t.get("torrent_hash")
                    if t_hash:
                        self.force_delete_task(t_hash, delete_data=True)
                    break

        return {
            "success": ok,
            "freed_bytes": freed_bytes,
            "freed_human": f"{freed_gb:.2f} GB" if freed_gb >= 1.0 else f"{freed_bytes / (1024 ** 2):.1f} MB",
            "message": f"✅ Sikeresen törölve! Felszabadított tárhely: {freed_gb:.2f} GB.",
        }

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
        with_origin_country: Optional[str] = None,
        with_original_language: Optional[str] = None,
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
            with_origin_country=with_origin_country,
            with_original_language=with_original_language,
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

    def download_collection_all(
        self,
        collection_id: int,
        skip_existing_in_plex: bool = True,
    ) -> Dict[str, Any]:
        """
        Elindítja egy film-gyűjtemény (pl. Harry Potter 1-8) hiányzó részeinek letöltését.
        Ha skip_existing_in_plex=True, akkor a Plexben már meglévő részeket automatikusan átugorja!
        """
        coll = self.get_collection_details(collection_id)
        parts = coll.get("parts", [])
        if not parts:
            return {"success": False, "message": "A gyűjtemény nem tartalmaz elérhető filmeket.", "started": 0, "skipped": 0, "failed": 0}

        started_count = 0
        skipped_count = 0
        failed_count = 0
        started_titles: List[str] = []
        skipped_titles: List[str] = []
        failed_titles: List[str] = []

        for part in parts:
            if skip_existing_in_plex and self.plex_service.is_configured:
                try:
                    if self.plex_service.has_media(part.title_hu, part.year, alt_title=part.title_en):
                        logger.info(f"Gyűjtemény: {part.title_hu} már létezik a Plex könyvtárban, letöltés átugorva.")
                        skipped_count += 1
                        skipped_titles.append(part.title_hu)
                        continue
                except Exception as e:
                    logger.debug(f"Plex ellenőrzési hiba a gyűjteménynél ({part.title_hu}): {e}")

            res = self.start_download_flow(part, prefer_hungarian=True)
            if res.get("success"):
                started_count += 1
                started_titles.append(part.title_hu)
            else:
                failed_count += 1
                failed_titles.append(part.title_hu)

        msg_parts = []
        if started_count > 0:
            msg_parts.append(f"{started_count} db film letöltése elindítva")
        if skipped_count > 0:
            msg_parts.append(f"{skipped_count} db film már a Plexben van (átugorva)")
        if failed_count > 0:
            msg_parts.append(f"{failed_count} db filmet nem találtunk nCore-on")

        msg = "Gyűjtemény: " + ", ".join(msg_parts) if msg_parts else "Nem volt letöltendő új film a gyűjteményben."

        return {
            "success": started_count > 0 or (skipped_count > 0 and failed_count == 0),
            "message": msg,
            "started": started_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "started_titles": started_titles,
            "skipped_titles": skipped_titles,
            "failed_titles": failed_titles,
        }

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
        if self.STATE_FILE.exists():
            self._tasks = self._load_state()

        saved_bytes = 0
        organized_count = 0
        for task in self._tasks.values():
            if task.get("plex_organized"):
                organized_count += 1
                if task.get("is_hardlink", True):
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

    def check_subtitle_radar(self, single_radar_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Azonnali ellenőrzés futtatása a Felirat-Radaron.
        Végigmegy a figyelt tételeken a 3 szolgáltatóval (Feliratok.eu, Subirat.net, OpenSubtitles),
        és ha talál, letölti, Plexbe illeszti és frissíti a Plexet.
        """
        return self.subtitle_radar.check_and_download_subtitles(self, single_radar_id=single_radar_id)

    def check_audio_radar(self, single_radar_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Azonnali ellenőrzés futtatása a Szinkron-Radaron.
        Visszaadja a frissen megtalált és elindított új HD szinkronos letöltéseket.
        """
        return self.audio_radar.check_and_trigger_upgrades(self, single_radar_id=single_radar_id)
