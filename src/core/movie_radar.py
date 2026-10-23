import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.models.media import MediaItem, MediaType


class MovieRadarManager:
    """
    Mozi-Radar & Automata Letöltő Menedzser.
    
    Figyeli a még csak moziban futó vagy frissen megjelent filmeket/sorozatokat,
    és amint elérhetővé válik a digitális, jó minőségű (nem CAM) szinkronos kiadás az nCore-on,
    autonóm módon elindítja a letöltést és a Plexbe rendezést.
    """

    def __init__(self, data_file: Optional[Path] = None):
        if data_file:
            self.data_file = data_file
        else:
            self.data_file = Path(__file__).resolve().parent.parent.parent / "data" / "movie_radar.json"

    def get_all(self) -> List[Dict[str, Any]]:
        """Visszaadja a radaron lévő összes tételt, sérülés esetén automatikus .bak visszaállítással."""
        return load_json_with_recovery(self.data_file, default_factory=list)

    def get_monitoring(self) -> List[Dict[str, Any]]:
        """Csak az aktívan figyelt (még le nem töltött) filmeket adja vissza."""
        return [x for x in self.get_all() if x.get("status") == "monitoring"]

    def is_monitored(self, tmdb_id: int) -> bool:
        """Ellenőrzi, hogy egy adott film aktívan figyelt-e a radaron."""
        return any(
            x.get("tmdb_id") == tmdb_id and x.get("status") == "monitoring"
            for x in self.get_all()
        )

    def add(self, item: MediaItem) -> bool:
        """Hozzáad egy filmet a Mozi-Radar aktív figyelőlistájához."""
        items = self.get_all()
        for x in items:
            if x.get("tmdb_id") == item.tmdb_id:
                if x.get("status") != "monitoring":
                    x["status"] = "monitoring"
                    x["added_at"] = datetime.now().isoformat()
                    return self._save(items)
                return False  # Már rajta van aktívan

        entry = {
            "tmdb_id": item.tmdb_id,
            "title_hu": item.title_hu,
            "title_en": item.title_en,
            "year": item.year,
            "media_type": item.media_type.value,
            "poster_url": item.poster_url,
            "media_item": item.model_dump(),
            "status": "monitoring",
            "added_at": datetime.now().isoformat(),
            "downloaded_at": None,
            "release_title": None,
        }
        items.append(entry)
        logger.info(f"Mozi-Radarhoz adva: '{item.title_hu}' ({item.year or ''})")
        return self._save(items)

    def remove(self, tmdb_id: int) -> bool:
        """Eltávolítja az elemet a Mozi-Radarról."""
        items = self.get_all()
        filtered = [x for x in items if x.get("tmdb_id") != tmdb_id]
        if len(filtered) == len(items):
            return True
        logger.info(f"Mozi-Radarról eltávolítva (TMDB ID: {tmdb_id})")
        return self._save(filtered)

    def toggle(self, item: MediaItem) -> bool:
        """Ki/be kapcsolja a radar figyelést. Visszaadja a bekapcsolt állapotot."""
        if self.is_monitored(item.tmdb_id):
            self.remove(item.tmdb_id)
            return False
        else:
            self.add(item)
            return True

    def check_and_trigger_downloads(self, orchestrator: Any) -> List[Dict[str, Any]]:
        """
        Periodikus ellenőrző motor.
        Végigmegy az aktívan figyelt filmeken, és ha az nCore-on elérhető
        digitális (nem CAM) kiadás, azonnal elindítja a letöltést!
        """
        items = self.get_all()
        monitoring_items = [x for x in items if x.get("status") == "monitoring"]
        if not monitoring_items:
            return []

        logger.info(f"Mozi-Radar ellenőrzés indul: {len(monitoring_items)} db figyelt tétel...")
        downloaded_now: List[Dict[str, Any]] = []

        for entry in monitoring_items:
            try:
                m_data = entry.get("media_item", {})
                media_type = MediaType.MOVIE if m_data.get("media_type") == "movie" else MediaType.TV
                item = MediaItem(**m_data)

                # Lekérjük az elérhető release-eket nCore-ról
                releases = orchestrator.check_release_availability(item)
                if not releases:
                    continue

                # Kiszűrjük a rossz minőségű mozis kamerás kiadásokat
                valid_releases = [r for r in releases if not r.is_cam]
                if not valid_releases:
                    logger.debug(f"Mozi-Radar: '{item.title_hu}' még csak CAM minőségben van fent, várakozás...")
                    continue

                best_release = valid_releases[0]

                # Letöltés indítása qBittorrenten keresztül
                logger.info(f"🎉 Mozi-Radar TALÁLAT! '{item.title_hu}' megjelent digitálisan: '{best_release.title}'")
                res = orchestrator.download_specific_release(item, best_release)
                if res.get("success"):
                    entry["status"] = "downloaded"
                    entry["downloaded_at"] = datetime.now().isoformat()
                    entry["release_title"] = best_release.title
                    downloaded_now.append({
                        "item": item,
                        "release": best_release,
                        "title": item.title_hu,
                    })

            except Exception as e:
                logger.error(f"Hiba a Mozi-Radar tételnél ({entry.get('title_hu')}): {e}")

        if downloaded_now:
            self._save(items)

        return downloaded_now

    def _save(self, items: List[Dict[str, Any]]) -> bool:
        return atomic_save_json(self.data_file, items, create_backup=True)
