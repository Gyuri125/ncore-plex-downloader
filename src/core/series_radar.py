import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.models.media import MediaItem, MediaType


class SeriesRadarManager:
    """
    Sorozat-Radar & Automata Új Epizód Letöltő Menedzser.
    
    Figyeli a futó vagy kedvenc sorozatokat, lekérdezi a legújabb epizódok
    megjelenési dátumát a TMDB-től, és amint felbukkan az új rész az nCore-on,
    automatikusan letölti (magyar szinkron vagy felirat prioritással),
    Plexbe rendezi a megfelelő Season mappába, és értesíti a felhasználót.
    """

    def __init__(self, data_file: Optional[Path] = None):
        if data_file:
            self.data_file = data_file
        else:
            self.data_file = Path(__file__).resolve().parent.parent.parent / "data" / "series_radar.json"

    def get_all(self) -> List[Dict[str, Any]]:
        """Visszaadja a sorozat-radaron lévő összes tételt, sérülés esetén automatikus .bak visszaállítással."""
        return load_json_with_recovery(self.data_file, default_factory=list)

    def get_monitoring(self) -> List[Dict[str, Any]]:
        """Csak az aktívan figyelt sorozatokat adja vissza."""
        return [x for x in self.get_all() if x.get("status") == "monitoring"]

    def is_monitored(self, tmdb_id: Any) -> bool:
        """Ellenőrzi, hogy egy adott sorozat aktívan figyelt-e a radaron."""
        if not tmdb_id:
            return False
        return any(
            x.get("tmdb_id") == tmdb_id and x.get("status") == "monitoring"
            for x in self.get_all()
        )

    def add(
        self,
        item: MediaItem,
        current_season: int = 1,
        current_episode: int = 0,
        prefer_hungarian: bool = True,
    ) -> bool:
        """Hozzáad egy sorozatot a Sorozat-Radar figyelőlistájához."""
        items = self.get_all()
        for x in items:
            if x.get("tmdb_id") == item.tmdb_id:
                if x.get("status") != "monitoring":
                    x["status"] = "monitoring"
                    x["added_at"] = datetime.now().isoformat()
                    return self._save(items)
                return False  # Már aktívan figyelt

        entry = {
            "tmdb_id": item.tmdb_id,
            "imdb_id": item.imdb_id,
            "title_hu": item.title_hu,
            "title_en": item.title_en,
            "original_title": getattr(item, "original_title", item.title_en),
            "year": item.year,
            "poster_url": item.poster_url,
            "media_item": item.model_dump(),
            "status": "monitoring",
            "current_season": current_season,
            "current_episode": current_episode,
            "last_downloaded_season": None,
            "last_downloaded_episode": None,
            "last_downloaded_at": None,
            "last_release_title": None,
            "prefer_hungarian": prefer_hungarian,
            "added_at": datetime.now().isoformat(),
            "last_checked_at": None,
        }
        items.append(entry)
        logger.info(f"Sorozat-Radarhoz adva: '{item.title_hu}' (Kezdőpont: S{current_season:02d}E{current_episode:02d})")
        return self._save(items)

    def remove(self, tmdb_id: int) -> bool:
        """Eltávolítja a sorozatot a Sorozat-Radarról."""
        items = self.get_all()
        filtered = [x for x in items if x.get("tmdb_id") != tmdb_id]
        if len(filtered) == len(items):
            return True
        logger.info(f"Sorozat-Radarról eltávolítva (TMDB ID: {tmdb_id})")
        return self._save(filtered)

    def toggle(self, item: MediaItem) -> bool:
        """Ki/be kapcsolja a sorozat-radar figyelést. Visszaadja a bekapcsolt állapotot."""
        if self.is_monitored(item.tmdb_id):
            self.remove(item.tmdb_id)
            return False
        else:
            self.add(item)
            return True

    def update_progress(self, tmdb_id: int, season: int, episode: int, release_title: Optional[str] = None):
        """Frissíti a sorozat legutóbb sikeresen letöltött epizódját."""
        items = self.get_all()
        for x in items:
            if x.get("tmdb_id") == tmdb_id:
                x["current_season"] = season
                x["current_episode"] = episode
                x["last_downloaded_season"] = season
                x["last_downloaded_episode"] = episode
                x["last_downloaded_at"] = datetime.now().isoformat()
                if release_title:
                    x["last_release_title"] = release_title
                break
        self._save(items)

    def check_and_trigger_downloads(self, orchestrator: Any) -> List[Dict[str, Any]]:
        """
        Periodikus ellenőrző motor.
        Végigmegy az aktívan figyelt sorozatokon, lekérdezi a következő várható epizódot,
        és ha elérhető az nCore-on, azonnal elindítja a letöltést és a Plexbe rendezést!
        """
        items = self.get_all()
        monitoring_items = [x for x in items if x.get("status") == "monitoring"]
        if not monitoring_items:
            return []

        logger.info(f"Sorozat-Radar ellenőrzés indul: {len(monitoring_items)} db figyelt sorozat...")
        downloaded_now: List[Dict[str, Any]] = []
        now_date_str = datetime.now().strftime("%Y-%m-%d")

        for entry in monitoring_items:
            try:
                tmdb_id = entry.get("tmdb_id")
                title_hu = entry.get("title_hu")
                title_en = entry.get("title_en") or title_hu
                orig_title = entry.get("original_title") or title_en

                cur_season = entry.get("current_season", 1)
                cur_ep = entry.get("current_episode", 0)
                next_ep = cur_ep + 1

                # 1. TMDB részletek lekérése az epizód létezésének és adásba kerülésének ellenőrzéséhez
                tv_details = None
                try:
                    tv_details = orchestrator.tmdb_service.get_details(tmdb_id, MediaType.TV)
                except Exception as t_err:
                    logger.debug(f"TMDB hiba a sorozat részleteinél ({title_hu}): {t_err}")

                item = tv_details or MediaItem(**entry["media_item"])

                # Évad és epizódszámok kinyerése a TMDB-ből
                seasons_list = getattr(item, "seasons", []) or []
                cur_season_info = next(
                    (s for s in seasons_list if s.get("season_number") == cur_season),
                    None
                )
                max_eps_in_season = cur_season_info.get("episode_count") if cur_season_info else None
                num_seasons = getattr(item, "number_of_seasons", None) or (len(seasons_list) if seasons_list else 1)

                # Ha a következő epizód meghaladja az aktuális évad epizódszámát, és van következő évad:
                if max_eps_in_season and next_ep > max_eps_in_season and cur_season < num_seasons:
                    logger.info(f"Sorozat-Radar évadátlépés: '{title_hu}' S{cur_season:02d} befejeződött ({max_eps_in_season} rész). Ugrás S{cur_season + 1:02d}E01-re!")
                    cur_season += 1
                    cur_ep = 0
                    next_ep = 1

                target_season = cur_season
                target_ep = next_ep
                ep_code = f"S{target_season:02d}E{target_ep:02d}"

                def _find_for_code(code_str: str) -> List[Any]:
                    queries = [
                        f"{title_hu} {code_str}",
                        f"{title_en} {code_str}",
                    ]
                    if orig_title and orig_title not in queries:
                        queries.append(f"{orig_title} {code_str}")

                    found = []
                    for query in queries:
                        if not query.strip():
                            continue
                        try:
                            rels = orchestrator.ncore_service.search_by_title(
                                title=query.strip(),
                                media_type=MediaType.TV,
                            )
                            if rels:
                                found.extend(rels)
                                break
                        except Exception as s_err:
                            logger.debug(f"nCore sorozat-keresési hiba ({query}): {s_err}")

                    if not found:
                        try:
                            title_rels = orchestrator.ncore_service.search_by_title(
                                title=title_en,
                                media_type=MediaType.TV,
                            )
                            for r in title_rels:
                                if code_str.lower() in r.title.lower():
                                    found.append(r)
                        except Exception:
                            pass
                    return found

                import re
                found_releases = []
                is_season_pack_found = False

                # 2. Ha az évad elején járunk (cur_ep == 0), először vizsgáljuk meg az évadcsomagokat!
                if cur_ep == 0:
                    pack_queries = [
                        f"{title_hu} {cur_season}. evad",
                        f"{title_en} S{cur_season:02d} Complete",
                        f"{title_en} S{cur_season:02d} Pack",
                        f"{title_en} Season {cur_season}",
                    ]
                    pack_cands = []
                    for pq in pack_queries:
                        try:
                            rels = orchestrator.ncore_service.search_by_title(title=pq, media_type=MediaType.TV)
                            for r in rels:
                                # Ellenőrizzük, hogy valóban évadcsomag-e (nem csak egyetlen epizód)
                                r_lower = r.title.lower()
                                is_single_ep = bool(re.search(r"\b[sS]\d{1,2}[eE]\d{1,2}\b", r.title))
                                is_pack = bool(re.search(r"\b(complete|pack|evad|teljes|season)\b", r_lower) or (f"s{cur_season:02d}" in r_lower and not is_single_ep))
                                if is_pack and not is_single_ep:
                                    pack_cands.append(r)
                            if pack_cands:
                                break
                        except Exception as p_err:
                            logger.debug(f"Évadcsomag keresési figyelmeztetés ({pq}): {p_err}")

                    if pack_cands:
                        found_releases = pack_cands
                        is_season_pack_found = True
                        ep_code = f"S{cur_season:02d} Teljes Évadcsomag"

                # 3. Ha nincs évadcsomag vagy nem cur_ep == 0, keressük a konkrét epizódot
                if not found_releases:
                    found_releases = _find_for_code(ep_code)

                # 4. Évadváltás vizsgálata fallbackként: ha az epizód nem található, de van következő évad
                if not found_releases and cur_season < num_seasons:
                    next_s_code = f"S{cur_season + 1:02d}E01"
                    found_next = _find_for_code(next_s_code)
                    if found_next:
                        target_season = cur_season + 1
                        target_ep = 1
                        ep_code = next_s_code
                        found_releases = found_next

                if not found_releases:
                    continue

                # Kiszűrjük a rossz minőségű vagy nem seedeletlen torrenteket, és ellenőrizzük az egyezést
                valid_releases = [
                    r for r in found_releases
                    if not r.is_cam and r.seeders > 0
                    and (not hasattr(orchestrator.ncore_service, "verify_release_match") or orchestrator.ncore_service.verify_release_match(r, item))
                ]
                if not valid_releases:
                    continue

                # Rangsorolás: preferált magyar szinkron/felirat
                sorted_releases = orchestrator.ncore_service.sort_releases(
                    valid_releases, prefer_hungarian=entry.get("prefer_hungarian", True)
                )
                best_release = sorted_releases[0]

                # Letöltés indítása qBittorrenten keresztül
                logger.info(f"🎉 Sorozat-Radar TALÁLAT! Új kiadás megjelent: '{title_hu}' {ep_code} ({best_release.title})")
                res = orchestrator.download_specific_release(item, best_release)
                if res.get("success"):
                    final_ep = (max_eps_in_season or 99) if is_season_pack_found else target_ep
                    downloaded_now.append({
                        "item": item,
                        "release": best_release,
                        "title": title_hu,
                        "episode_code": ep_code,
                        "next_season": target_season,
                        "next_ep": final_ep,
                    })

            except Exception as e:
                logger.error(f"Hiba a Sorozat-Radar tételnél ({entry.get('title_hu')}): {e}")

        if downloaded_now:
            fresh_items = self.get_all()
            updates_map = {
                d["item"].tmdb_id: (d["next_season"], d["next_ep"], d["release"].title)
                for d in downloaded_now
            }
            now_iso = datetime.now().isoformat()
            for entry in fresh_items:
                t_id = entry.get("tmdb_id")
                if t_id in updates_map:
                    s_num, e_num, rel_title = updates_map[t_id]
                    entry["current_season"] = s_num
                    entry["current_episode"] = e_num
                    entry["last_downloaded_season"] = s_num
                    entry["last_downloaded_episode"] = e_num
                    entry["last_downloaded_at"] = now_iso
                    entry["last_release_title"] = rel_title
                    entry["last_checked_at"] = now_iso
            self._save(fresh_items)

        return downloaded_now

    def _save(self, items: List[Dict[str, Any]]) -> bool:
        return atomic_save_json(self.data_file, items, create_backup=True)

