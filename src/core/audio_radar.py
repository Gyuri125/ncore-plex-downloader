import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from loguru import logger

from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.models.media import MediaItem, MediaType, TorrentRelease


class AudioRadarManager:
    """
    Szinkron-Radar & Minőség-Upgrade Menedzser.
    
    Figyeli azokat a filmeket és sorozatokat, amelyek jelenleg:
    - Csak eredeti nyelven (angolul + magyar felirattal) érhetők el a könyvtárban, VAGY
    - Csak gyengébb SD szinkronnal (xvid_hun) töltődtek le korábban.
    
    A háttérfolyamat csendben ellenőrzi az nCore-t (IMDb kód alapján), és amint
    megjelenik a hivatalos HD Magyar Szinkronos kiadás (hd_hun / sorozat_hun):
    - Automatikusan elindítja a jobb kiadás letöltését és a Plexbe rendezést.
    - Windows Toast értesítést küld a felhasználónak.
    - A HnR seedelés lejárta után takaríthatóvá teszi a korábbi gyengébb verziót.
    """

    def __init__(self, data_file: Optional[Path] = None):
        if data_file:
            self.data_file = data_file
        else:
            self.data_file = Path(__file__).resolve().parent.parent.parent / "data" / "audio_radar.json"

    def get_all(self) -> List[Dict[str, Any]]:
        """Visszaadja a szinkron-radaron lévő összes tételt."""
        return load_json_with_recovery(self.data_file, default_factory=list)

    def get_monitoring(self) -> List[Dict[str, Any]]:
        """Csak az aktívan figyelt (magyar szinkronra váró) tételeket adja vissza."""
        return [x for x in self.get_all() if x.get("status") == "monitoring"]

    def get_upgraded(self) -> List[Dict[str, Any]]:
        """A sikeresen szinkronnal frissített tételeket adja vissza."""
        return [x for x in self.get_all() if x.get("status") == "upgraded"]

    def is_monitored(self, tmdb_id: Any) -> bool:
        """Ellenőrzi, hogy egy adott média aktívan figyelt-e a Szinkron-Radaron."""
        if not tmdb_id:
            return False
        return any(
            x.get("tmdb_id") == tmdb_id and x.get("status") == "monitoring"
            for x in self.get_all()
        )

    def add(
        self,
        item: MediaItem,
        current_release: Optional[TorrentRelease] = None,
        destination_folder: Optional[Union[str, Path]] = None,
        task_id: Optional[str] = None,
    ) -> bool:
        """Hozzáad egy médiát a Szinkron-Radar figyelőlistájához."""
        items = self.get_all()
        radar_id = f"{item.tmdb_id}_{task_id or 'custom'}"

        for x in items:
            if x.get("radar_id") == radar_id or x.get("tmdb_id") == item.tmdb_id:
                if x.get("status") != "monitoring":
                    x["status"] = "monitoring"
                    x["added_at"] = datetime.now().isoformat()
                    if destination_folder:
                        x["destination_folder"] = str(destination_folder)
                    if current_release:
                        x["current_release_title"] = current_release.title
                        x["current_category"] = current_release.category
                    return self._save(items)
                return False  # Már rajta van aktívan

        entry = {
            "radar_id": radar_id,
            "tmdb_id": item.tmdb_id,
            "imdb_id": item.imdb_id,
            "title_hu": item.title_hu,
            "title_en": item.title_en,
            "year": item.year,
            "media_type": item.media_type.value if hasattr(item.media_type, "value") else str(item.media_type),
            "poster_url": item.poster_url,
            "current_release_title": current_release.title if current_release else None,
            "current_category": current_release.category if current_release else "hd",
            "target_category": "hd_hun" if item.media_type == MediaType.MOVIE else "sorozat_hun",
            "destination_folder": str(destination_folder) if destination_folder else None,
            "media_item": item.model_dump(),
            "task_id": task_id,
            "status": "monitoring",
            "added_at": datetime.now().isoformat(),
            "last_checked_at": None,
            "upgraded_at": None,
            "upgraded_release_title": None,
        }
        items.append(entry)
        logger.info(f"Szinkron-Radarhoz adva: '{item.title_hu}' (Várakozás HD magyar szinkronra)")
        return self._save(items)

    def remove(self, radar_id_or_tmdb_id: Any) -> bool:
        """Eltávolítja a tételt a Szinkron-Radarról radar_id vagy tmdb_id alapján."""
        items = self.get_all()
        filtered = [
            x for x in items
            if x.get("radar_id") != str(radar_id_or_tmdb_id) and x.get("tmdb_id") != radar_id_or_tmdb_id
        ]
        if len(filtered) == len(items):
            return True
        logger.info(f"Szinkron-Radarról eltávolítva (ID: {radar_id_or_tmdb_id})")
        return self._save(filtered)

    def mark_upgraded(self, radar_id_or_tmdb_id: Any, upgraded_release_title: str) -> bool:
        """Megjelöl egy tételt sikeresen frissítettként."""
        items = self.get_all()
        updated = False
        for x in items:
            if x.get("radar_id") == str(radar_id_or_tmdb_id) or x.get("tmdb_id") == radar_id_or_tmdb_id:
                x["status"] = "upgraded"
                x["upgraded_at"] = datetime.now().isoformat()
                x["upgraded_release_title"] = upgraded_release_title
                updated = True
        if updated:
            return self._save(items)
        return False

    def clear_upgraded(self) -> bool:
        """Törli az összes sikeresen frissített tételt a radarról."""
        items = self.get_all()
        active_items = [x for x in items if x.get("status") == "monitoring"]
        return self._save(active_items)

    def check_and_trigger_upgrades(
        self, orchestrator: Any, single_radar_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Periodikus és kézi ellenőrző motor.
        Végigmegy a figyelt filmeken, és ha elérhetővé válik a HD magyar szinkronos
        kiadás (hd_hun vagy sorozat_hun) aktív seederekkel, automatikusan elindítja a letöltést!
        """
        items = self.get_all()
        monitoring_items = [x for x in items if x.get("status") == "monitoring"]
        if single_radar_id:
            monitoring_items = [
                x for x in monitoring_items
                if x.get("radar_id") == single_radar_id or str(x.get("tmdb_id")) == str(single_radar_id)
            ]

        if not monitoring_items:
            return []

        logger.info(f"Szinkron-Radar ellenőrzés indul: {len(monitoring_items)} db figyelt tétel...")
        hits: List[Dict[str, Any]] = []

        for item_data in monitoring_items:
            try:
                m_dict = item_data.get("media_item")
                if not m_dict:
                    continue
                media_item = MediaItem(**m_dict)

                # nCore kiadások felderítése
                releases = orchestrator.get_available_releases(media_item)
                if not releases:
                    item_data["last_checked_at"] = datetime.now().isoformat()
                    continue

                # Keresünk minőségi magyar szinkronos kiadást (hd_hun vagy sorozat_hun)
                # Kizárjuk a kamerás (CAM) verziókat és a seeder nélküli torrenteket
                target_categories = {"hd_hun", "sorozat_hun"}
                valid_upgrades = [
                    r for r in releases
                    if r.has_hungarian_audio
                    and r.category in target_categories
                    and not r.is_cam
                    and r.seeders > 0
                ]

                # Ha nem talált HD szinkront, de a jelenlegi verzió teljesen angol volt,
                # elfogadható az xvid_hun is, de preferáljuk a HD-t
                if not valid_upgrades and item_data.get("current_category") in ("hd", "xvid"):
                    valid_upgrades = [
                        r for r in releases
                        if r.has_hungarian_audio
                        and not r.is_cam
                        and r.seeders > 0
                    ]

                if not valid_upgrades:
                    item_data["last_checked_at"] = datetime.now().isoformat()
                    continue

                # A legjobb minőségű kiadás kiválasztása
                best_upgrade = valid_upgrades[0]
                logger.info(
                    f"🎉 Szinkron-Radar találat! '{media_item.title_hu}' elérhető magyar szinkronnal: {best_upgrade.title}"
                )

                # Automatikus letöltés indítása az orchestratoron keresztül
                dl_result = orchestrator.download_specific_release(media_item, best_upgrade)
                if dl_result.get("success"):
                    item_data["status"] = "upgraded"
                    item_data["upgraded_at"] = datetime.now().isoformat()
                    item_data["upgraded_release_title"] = best_upgrade.title

                    hits.append({
                        "radar_id": item_data.get("radar_id"),
                        "title": media_item.title_hu or media_item.title_en,
                        "release": best_upgrade,
                        "category": best_upgrade.category,
                    })
                    logger.info(f"Szinkron-Radar letöltés sikeresen elindítva: {best_upgrade.title}")
                else:
                    logger.warning(f"Szinkron-Radar letöltés sikertelen: {dl_result.get('message')}")
                    item_data["last_checked_at"] = datetime.now().isoformat()

            except Exception as e:
                logger.error(f"Hiba a Szinkron-Radar tétel ellenőrzésekor ({item_data.get('title_hu')}): {str(e)}")
                item_data["last_checked_at"] = datetime.now().isoformat()

        self._save(items)
        return hits

    def _save(self, items: List[Dict[str, Any]]) -> bool:
        """Biztonságos mentés atomikus .bak védelemmel."""
        return atomic_save_json(self.data_file, items)

