import json
from pathlib import Path
from typing import List, Optional
from loguru import logger

from src.models.media import MediaItem


class WatchlistManager:
    """
    Kívánságlista ("Később megnézem") kezelő.
    A mentett filmeket és sorozatokat a data/watchlist.json fájlban tárolja.
    """

    def __init__(self, data_file: Optional[Path] = None):
        if data_file:
            self.data_file = data_file
        else:
            self.data_file = Path(__file__).resolve().parent.parent.parent / "data" / "watchlist.json"
        self._ensure_file()

    def _ensure_file(self):
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.data_file.exists():
                self.data_file.write_text("[]", encoding="utf-8")
        except Exception as e:
            logger.error(f"Hiba a kívánságlista fájl előkészítésekor: {e}")

    def get_all(self) -> List[MediaItem]:
        """Visszaadja a kívánságlistán lévő összes filmet és sorozatot."""
        try:
            if not self.data_file.exists():
                return []
            content = self.data_file.read_text(encoding="utf-8")
            if not content.strip():
                return []
            raw_list = json.loads(content)
            items = []
            for raw in raw_list:
                try:
                    items.append(MediaItem(**raw))
                except Exception:
                    pass
            return items
        except Exception as e:
            logger.error(f"Hiba a kívánságlista olvasásakor: {e}")
            return []

    def is_in_watchlist(self, tmdb_id: int) -> bool:
        """Ellenőrzi, hogy az adott film/sorozat szerepel-e a kívánságlistán."""
        items = self.get_all()
        return any(item.tmdb_id == tmdb_id for item in items)

    def add(self, item: MediaItem) -> bool:
        """Hozzáadja az elemet a kívánságlistához."""
        items = self.get_all()
        if any(x.tmdb_id == item.tmdb_id for x in items):
            return False  # Már rajta van

        items.append(item)
        return self._save(items)

    def remove(self, tmdb_id: int) -> bool:
        """Eltávolítja az elemet a kívánságlistáról."""
        items = self.get_all()
        filtered = [x for x in items if x.tmdb_id != tmdb_id]
        if len(filtered) == len(items):
            return True  # Nem volt rajta
        return self._save(filtered)

    def toggle(self, item: MediaItem) -> bool:
        """Hozzáadja vagy eltávolítja az elemet. Visszaadja az új állapotot (True=hozzáadva, False=törölve)."""
        if self.is_in_watchlist(item.tmdb_id):
            self.remove(item.tmdb_id)
            return False
        else:
            self.add(item)
            return True

    def _save(self, items: List[MediaItem]) -> bool:
        try:
            self._ensure_file()
            raw_list = [item.model_dump() for item in items]
            self.data_file.write_text(json.dumps(raw_list, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"Hiba a kívánságlista mentésekor: {e}")
            return False
