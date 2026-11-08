import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from loguru import logger

from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.models.media import MediaItem, MediaType, TorrentRelease


class SubtitleRadarManager:
    """
    Felirat-Radar & Automata Utólagos Felirat Letöltő Menedzser.
    
    Figyeli azokat a filmeket és sorozatokat, amelyek nem magyar szinkronosak,
    és a letöltésük idején még nem volt elérhető hozzájuk magyar felirat.
    
    A háttérben periodikusan lekérdezi a 3 legfőbb magyar feliratforrást:
    1. Feliratok.eu (scene release-ekhez illesztve)
    2. Subirat.net (friss streaming és mozifilmes független feliratok)
    3. OpenSubtitles REST API (IMDb ID alapján)
    
    Amint felbukkan a magyar felirat:
    - Automatikusan letölti és UTF-8-ra konvertálja.
    - Beilleszti a Plex mappába a videófájl mellé ({VideoFájlnév}.hu.srt).
    - Frissíti a Plex könyvtárat.
    - Windows Toast értesítést küld a felhasználónak.
    """

    def __init__(self, data_file: Optional[Path] = None):
        if data_file:
            self.data_file = data_file
        else:
            self.data_file = Path(__file__).resolve().parent.parent.parent / "data" / "subtitle_radar.json"

    def get_all(self) -> List[Dict[str, Any]]:
        """Visszaadja a felirat-radaron lévő összes tételt."""
        return load_json_with_recovery(self.data_file, default_factory=list)

    def get_monitoring(self) -> List[Dict[str, Any]]:
        """Csak az aktívan figyelt (még feliratra váró) tételeket adja vissza."""
        return [x for x in self.get_all() if x.get("status") == "monitoring"]

    def get_resolved(self) -> List[Dict[str, Any]]:
        """A sikeresen megtalált és Plexhez társított feliratos tételeket adja vissza."""
        return [x for x in self.get_all() if x.get("status") == "resolved"]

    def is_monitored(self, tmdb_id: Any, release_title: Optional[str] = None) -> bool:
        """Ellenőrzi, hogy egy adott média aktívan figyelt-e a Felirat-Radaron."""
        if not tmdb_id:
            return False
        for x in self.get_all():
            if x.get("tmdb_id") == tmdb_id and x.get("status") == "monitoring":
                if release_title:
                    if x.get("release_title") == release_title:
                        return True
                else:
                    return True
        return False

    def add(
        self,
        item: MediaItem,
        release: Optional[TorrentRelease] = None,
        destination_folder: Optional[Union[str, Path]] = None,
        destination_files: Optional[List[str]] = None,
        task_id: Optional[str] = None,
    ) -> bool:
        """Hozzáad egy médiát a Felirat-Radar figyelőlistájához."""
        items = self.get_all()
        rel_title = release.title if release else (item.title_en or item.title_hu)
        radar_id = f"{item.tmdb_id}_{release.torrent_id if release else 'custom'}"

        for x in items:
            if x.get("radar_id") == radar_id or (x.get("tmdb_id") == item.tmdb_id and x.get("release_title") == rel_title):
                if x.get("status") != "monitoring":
                    x["status"] = "monitoring"
                    x["added_at"] = datetime.now().isoformat()
                    if destination_folder:
                        x["destination_folder"] = str(destination_folder)
                    if destination_files:
                        x["destination_files"] = destination_files
                    return self._save(items)
                return False  # Már rajta van aktívan

        entry = {
            "radar_id": radar_id,
            "tmdb_id": item.tmdb_id,
            "imdb_id": item.imdb_id,
            "title_hu": item.title_hu,
            "title_en": item.title_en,
            "original_title": getattr(item, "original_title", item.title_en),
            "year": item.year,
            "media_type": item.media_type.value if hasattr(item.media_type, "value") else str(item.media_type),
            "poster_url": item.poster_url,
            "release_title": rel_title,
            "release_category": release.category if release else None,
            "destination_folder": str(destination_folder) if destination_folder else None,
            "destination_files": destination_files or [],
            "media_item": item.model_dump(),
            "release": release.model_dump() if release else None,
            "task_id": task_id,
            "status": "monitoring",
            "added_at": datetime.now().isoformat(),
            "last_checked_at": None,
            "resolved_at": None,
            "found_source": None,
            "subtitle_path": None,
        }
        items.append(entry)
        logger.info(f"Felirat-Radarhoz adva: '{item.title_hu}' ({rel_title})")
        return self._save(items)

    def remove(self, radar_id_or_tmdb_id: Any) -> bool:
        """Eltávolítja a tételt a Felirat-Radarról radar_id vagy tmdb_id alapján."""
        items = self.get_all()
        filtered = [
            x for x in items
            if x.get("radar_id") != str(radar_id_or_tmdb_id) and x.get("tmdb_id") != radar_id_or_tmdb_id
        ]
        if len(filtered) == len(items):
            return True
        logger.info(f"Felirat-Radarról eltávolítva (ID: {radar_id_or_tmdb_id})")
        return self._save(filtered)

    def mark_resolved(self, radar_id_or_tmdb_id: Any, subtitle_path: str, source: str = "Online") -> bool:
        """Megjelöl egy tételt felirattal rendelkezőként (sikeres feliratcsatolás után)."""
        items = self.get_all()
        updated = False
        for x in items:
            if x.get("radar_id") == str(radar_id_or_tmdb_id) or x.get("tmdb_id") == radar_id_or_tmdb_id:
                x["status"] = "resolved"
                x["resolved_at"] = datetime.now().isoformat()
                x["subtitle_path"] = str(subtitle_path)
                x["found_source"] = source
                updated = True
        if updated:
            return self._save(items)
        return False

    def clear_resolved(self) -> bool:
        """Törli az összes sikeresen feliratozott tételt a radarról."""
        items = self.get_all()
        active_items = [x for x in items if x.get("status") == "monitoring"]
        return self._save(active_items)

    def check_and_download_subtitles(
        self, orchestrator: Any, single_radar_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Periodikus és kézi ellenőrző motor.
        Végigmegy az aktívan figyelt filmeken és sorozatokon, lekérdezi a 3 feliratos
        szolgáltatót, és amint felirat érhető el, automatikusan letölti és Plexbe rendezi.
        """
        items = self.get_all()
        monitoring_items = [x for x in items if x.get("status") == "monitoring"]
        if single_radar_id:
            monitoring_items = [x for x in monitoring_items if x.get("radar_id") == single_radar_id or str(x.get("tmdb_id")) == str(single_radar_id)]

        if not monitoring_items:
            return []

        logger.info(f"Felirat-Radar ellenőrzés indul: {len(monitoring_items)} db figyelt tétel...")
        hits: List[Dict[str, Any]] = []

        for item_data in monitoring_items:
            try:
                m_dict = item_data.get("media_item")
                if not m_dict:
                    continue
                media_item = MediaItem(**m_dict)

                rel_dict = item_data.get("release")
                if rel_dict:
                    release = TorrentRelease(**rel_dict)
                else:
                    release = TorrentRelease(
                        torrent_id="radar_sub",
                        title=item_data.get("release_title", media_item.title_en or media_item.title_hu),
                        category="film",
                        size_bytes=1000,
                        has_hungarian_audio=False,
                    )

                dest_folder_str = item_data.get("destination_folder")
                dest_dir = Path(dest_folder_str) if dest_folder_str and Path(dest_folder_str).exists() else None

                if not dest_dir:
                    # Ha nincs Plex célmappa elmentve, ideiglenes munkamappába kérjük
                    dest_dir = orchestrator.settings.DOWNLOAD_TEMP_DIR / release.title
                    dest_dir.mkdir(parents=True, exist_ok=True)

                logger.info(f"Felirat-Radar keresés: '{media_item.title_hu}' ({release.title})")
                sub_path = orchestrator.subtitle_service.find_and_download_subtitle(
                    media_item=media_item,
                    release=release,
                    destination_dir=dest_dir,
                )

                if sub_path and sub_path.exists():
                    source_name = "Online Feliratbázis"
                    last_sub = getattr(orchestrator.subtitle_service, "last_subtitle_item", None)
                    if last_sub and getattr(last_sub, "source", None):
                        source_name = last_sub.source

                    # Ha van Plex célkönyvtár, illesszük a feliratot a videófájl(ok) nevéhez
                    final_sub_path = sub_path
                    if dest_folder_str and Path(dest_folder_str).exists():
                        target_plex_dir = Path(dest_folder_str)

                        if sub_path.is_dir():
                            clean_show = orchestrator.organizer._sanitize_filename(media_item.title_hu or media_item.title_en or "Show")
                            for srt_item in sorted(sub_path.glob("*.srt")):
                                sub_s, sub_e = orchestrator.organizer._extract_season_episode(srt_item.name)
                                target_s = sub_s or (release.season_num if release.season_num and release.season_num > 0 else 1)
                                if sub_e:
                                    dest_srt = target_plex_dir / f"{clean_show} - S{target_s:02d}E{sub_e:02d}.hu.srt"
                                else:
                                    dest_srt = target_plex_dir / f"{srt_item.stem}.hu.srt"
                                shutil.copy2(srt_item, dest_srt)
                            final_sub_path = target_plex_dir
                        else:
                            clean_title = orchestrator.organizer._sanitize_filename(media_item.title_hu or media_item.title_en or "Movie")
                            year_str = f" ({media_item.year})" if media_item.year else ""
                            plex_videos = [f for f in target_plex_dir.glob("*") if f.suffix.lower() in orchestrator.organizer.VIDEO_EXTENSIONS]
                            if plex_videos:
                                main_vid = max(plex_videos, key=lambda f: f.stat().st_size)
                                dest_sub_exact = target_plex_dir / f"{main_vid.stem}.hu.srt"
                                if sub_path != dest_sub_exact:
                                    shutil.copy2(sub_path, dest_sub_exact)
                                final_sub_path = dest_sub_exact
                            else:
                                dest_sub_simple = target_plex_dir / f"{clean_title}{year_str}.hu.srt"
                                if sub_path != dest_sub_simple:
                                    shutil.copy2(sub_path, dest_sub_simple)
                                final_sub_path = dest_sub_simple

                        # Plex könyvtár azonnali frissítése
                        m_type = "movie" if media_item.media_type == MediaType.MOVIE else "show"
                        orchestrator.plex_service.refresh_library(media_type=m_type)

                    # Állapot frissítése resolved-re
                    item_data["status"] = "resolved"
                    item_data["resolved_at"] = datetime.now().isoformat()
                    item_data["subtitle_path"] = str(final_sub_path)
                    item_data["found_source"] = source_name

                    # Kapcsolódó letöltési feladat állapotának frissítése (ha van task_id)
                    task_id = item_data.get("task_id")
                    if task_id and task_id in orchestrator._tasks:
                        task = orchestrator._tasks[task_id]
                        task["has_subtitle"] = True
                        task["subtitle_path"] = str(final_sub_path)
                        if last_sub:
                            task["subtitle_info"] = last_sub.model_dump()
                        orchestrator._save_state()

                    hits.append({
                        "radar_id": item_data.get("radar_id"),
                        "title": media_item.title_hu or media_item.title_en,
                        "release_title": release.title,
                        "source": source_name,
                        "subtitle_path": str(final_sub_path),
                    })
                    logger.info(f"🎉 Felirat-Radar siker: '{media_item.title_hu}' felirata megérkezett ({source_name})!")
                else:
                    item_data["last_checked_at"] = datetime.now().isoformat()

            except Exception as e:
                logger.error(f"Hiba a Felirat-Radar tétel ellenőrzésekor ({item_data.get('title_hu')}): {str(e)}")
                item_data["last_checked_at"] = datetime.now().isoformat()

        self._save(items)
        return hits

    def _save(self, items: List[Dict[str, Any]]) -> bool:
        """Biztonságos mentés atomikus .bak védelemmel."""
        return atomic_save_json(self.data_file, items)

