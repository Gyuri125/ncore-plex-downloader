import csv
import io
import re
from typing import Any, Dict, List, Optional
import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.models.media import MediaItem, MediaType


class WatchlistSyncService:
    """
    Külső Watchlist Szinkronizáló Szolgáltatás.
    
    Támogatja a legnépszerűbb filmes felületeket:
    1. Letterboxd (nyilvános Watchlist URL, RSS feed vagy exportált CSV)
    2. IMDb (Watchlist URL, egyéni lista URL, exportált CSV vagy közvetlen tt azonosítók)
    3. Trakt (nyilvános Watchlist URL vagy lista)
    4. Közvetlen CSV fájl feltöltés (mind az IMDb, mind a Letterboxd export CSV-jét kezeli)
    
    A beolvasott filmeket összeveti a Plex könyvtárral:
    - Ha már megvan: jelzi, hogy elérhető.
    - Ha hiányzik és már megjelent: automatikusan letölti az nCore-ról!
    - Ha hiányzik és még premier előtt áll / nincs fent: hozzáadja a Mozi-Radarhoz!
    """

    def __init__(self):
        transport = httpx.HTTPTransport(retries=3)
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=6.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "hu-HU,hu;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            follow_redirects=True,
        )

    def parse_input(self, input_text_or_url: str) -> List[Dict[str, Any]]:
        """
        Automatikusan felismeri és feldolgozza a megadott bemenetet
        (URL, IMDb felhasználói link, vagy soronkénti cím/ID lista).
        """
        raw = input_text_or_url.strip()
        if not raw:
            return []

        # 1. Ha közvetlen URL-ről van szó
        if raw.startswith("http://") or raw.startswith("https://"):
            if "letterboxd.com" in raw:
                return self.fetch_letterboxd_watchlist(raw)
            elif "imdb.com" in raw:
                return self.fetch_imdb_watchlist(raw)
            elif "trakt.tv" in raw:
                return self.fetch_trakt_watchlist(raw)

        # 2. Ha csak egy Letterboxd felhasználónevet adott meg (pl. 'username')
        if re.match(r"^[a-zA-Z0-9_\-]+$", raw) and not raw.startswith("tt") and not raw.startswith("ur"):
            # Megpróbáljuk Letterboxd profilként
            items = self.fetch_letterboxd_watchlist(f"https://letterboxd.com/{raw}/watchlist/")
            if items:
                return items

        # 3. Ha IMDb user ID (pl. 'ur12345678')
        if re.match(r"^ur\d+$", raw):
            return self.fetch_imdb_watchlist(f"https://www.imdb.com/user/{raw}/watchlist")

        # 4. Soronkénti szöveg (címek vagy IMDb azonosítók)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        parsed_items = []
        for line in lines:
            m_imdb = re.search(r"\b(tt\d{7,8})\b", line)
            if m_imdb:
                parsed_items.append({"imdb_id": m_imdb.group(1), "title": line, "year": None})
            else:
                m_year = re.search(r"\((\d{4})\)", line)
                year = int(m_year.group(1)) if m_year else None
                clean_t = re.sub(r"\(\d{4}\)", "", line).strip()
                parsed_items.append({"title": clean_t, "year": year, "imdb_id": None})

        return parsed_items

    def parse_csv_content(self, csv_text_or_bytes: Any) -> List[Dict[str, Any]]:
        """
        Feldolgozza az IMDb vagy Letterboxd által exportált hivatalos watchlist.csv fájlt.
        """
        if isinstance(csv_text_or_bytes, bytes):
            text = csv_text_or_bytes.decode("utf-8-sig", errors="replace")
        else:
            text = str(csv_text_or_bytes)

        reader = csv.DictReader(io.StringIO(text))
        items: List[Dict[str, Any]] = []

        for row in reader:
            # IMDb export formátum: Const, Title, Year, Release Date...
            # Letterboxd export formátum: Name, Year, Letterboxd URI...
            imdb_id = row.get("Const") or row.get("imdb_id") or row.get("IMDb ID")
            if not imdb_id:
                # Néha az URL oszlopban van benne az IMDb azonosító
                url_field = row.get("URL") or row.get("Letterboxd URI") or ""
                m = re.search(r"\b(tt\d{7,8})\b", url_field)
                if m:
                    imdb_id = m.group(1)

            title = row.get("Title") or row.get("Name") or row.get("title") or ""
            year_val = row.get("Year") or row.get("year")
            year = int(year_val) if year_val and str(year_val).isdigit() else None

            if title or imdb_id:
                items.append({
                    "title": title.strip(),
                    "year": year,
                    "imdb_id": imdb_id.strip() if imdb_id else None,
                })

        logger.info(f"Watchlist CSV sikeresen beolvasva: {len(items)} db tétel azonosítva.")
        return items

    def fetch_letterboxd_watchlist(self, url: str, max_pages: int = 3) -> List[Dict[str, Any]]:
        """
        Lekéri és feldolgozza egy nyilvános Letterboxd Watchlist tartalmát (több oldalas lapozással).
        """
        clean_url = url.rstrip("/")
        if not clean_url.endswith("/watchlist"):
            if "/watchlist" not in clean_url:
                clean_url += "/watchlist"

        logger.info(f"Letterboxd Watchlist lekérése: {clean_url} (max {max_pages} oldal)")
        items: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            page_url = f"{clean_url}/page/{page}/" if page > 1 else clean_url
            try:
                resp = self.client.get(page_url)
                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "lxml")
                posters = soup.find_all("div", class_=re.compile(r"film-poster|really-lazy-load"))
                if not posters:
                    break

                for p in posters:
                    name = p.get("data-film-name") or p.get("data-target-link") or ""
                    if not name:
                        img = p.find("img")
                        if img and img.get("alt"):
                            name = img["alt"]

                    year_val = p.get("data-film-release-year")
                    year = int(year_val) if year_val and str(year_val).isdigit() else None

                    if name:
                        clean_name = name.replace("/film/", "").replace("/", "").replace("-", " ").title() if name.startswith("/film/") else name
                        items.append({"title": clean_name.strip(), "year": year, "imdb_id": None})

            except Exception as e:
                logger.error(f"Hiba a Letterboxd lekérésekor ({page_url}): {e}")
                break

        logger.info(f"Letterboxd találatok összesen: {len(items)} db film kinyerve.")
        return items

    def fetch_imdb_watchlist(self, url: str) -> List[Dict[str, Any]]:
        """
        Lekéri és feldolgozza egy nyilvános IMDb Watchlist vagy egyéni lista tartalmát.
        """
        logger.info(f"IMDb Watchlist lekérése: {url}")
        items: List[Dict[str, Any]] = []

        try:
            resp = self.client.get(url)
            if resp.status_code not in (200, 202):
                logger.warning(f"IMDb oldal nem elérhető (HTTP {resp.status_code}): {url}")
                return []

            html = resp.text
            # IMDb azonosítók (tt1234567) kinyerése a linkekből és HTML-ből
            found_ids = list(dict.fromkeys(re.findall(r"/title/(tt\d{7,8})", html)))
            for imdb_id in found_ids:
                items.append({
                    "title": "",
                    "year": None,
                    "imdb_id": imdb_id,
                })

            logger.info(f"IMDb találatok: {len(items)} db egyedi IMDb filmkód kinyerve.")
        except Exception as e:
            logger.error(f"Hiba az IMDb lekérésekor: {e}")

        return items

    def fetch_trakt_watchlist(self, url: str) -> List[Dict[str, Any]]:
        """
        Lekéri egy Trakt.tv nyilvános Watchlist tartalmát.
        """
        logger.info(f"Trakt Watchlist lekérése: {url}")
        items: List[Dict[str, Any]] = []
        try:
            resp = self.client.get(url)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                grid_items = soup.find_all("div", class_=re.compile(r"grid-item|poster"))
                for g in grid_items:
                    t_elem = g.find(re.compile(r"h4|a"), class_=re.compile(r"title|name"))
                    y_elem = g.find("span", class_="year")
                    if t_elem:
                        t = t_elem.text.strip()
                        y = int(y_elem.text.strip()) if y_elem and y_elem.text.strip().isdigit() else None
                        items.append({"title": t, "year": y, "imdb_id": None})
            logger.info(f"Trakt találatok: {len(items)} db film kinyerve.")
        except Exception as e:
            logger.error(f"Hiba a Trakt lekérésekor: {e}")
        return items

    def sync_items(
        self,
        raw_items: List[Dict[str, Any]],
        orchestrator: Any,
        auto_download: bool = True,
        auto_radar: bool = True,
        max_items: int = 50,
        progress_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Végrehajtja a kinyert tételek szinkronizálását a rendszerrel:
        1. TMDB azonosítás.
        2. Plex könyvtár-ellenőrzés.
        3. nCore letöltés indítása vagy Mozi-Radarhoz adás.
        """
        results: List[Dict[str, Any]] = []
        downloaded_count = 0
        radar_count = 0
        plex_count = 0
        skipped_count = 0

        target_items = raw_items[:max_items]
        total_targets = len(target_items)
        logger.info(f"Külső Watchlist feldolgozása: {total_targets} tétel elemzése...")

        for idx, raw in enumerate(target_items, start=1):
            imdb_id = raw.get("imdb_id")
            title = raw.get("title")
            year = raw.get("year")

            if progress_callback:
                try:
                    display_name = title or imdb_id or f"Film #{idx}"
                    progress_callback(idx, total_targets, display_name)
                except Exception:
                    pass

            # 1. TMDB egyeztetés
            media_item: Optional[MediaItem] = None
            try:
                if imdb_id:
                    media_item = orchestrator.tmdb_service.get_by_imdb_id(imdb_id)
                elif title:
                    search_hits = orchestrator.search_media(title)
                    if search_hits:
                        if year:
                            # Évszám egyezés keresése
                            matching = [h for h in search_hits if h.year and abs(h.year - year) <= 1]
                            media_item = matching[0] if matching else search_hits[0]
                        else:
                            media_item = search_hits[0]
            except Exception as tmdb_err:
                logger.debug(f"TMDB feloldási hiba ({title or imdb_id}): {tmdb_err}")

            if not media_item:
                skipped_count += 1
                continue

            # Részletes adatok lekérése a pontos IMDb ID-hoz és műfajokhoz
            try:
                detailed = orchestrator.tmdb_service.get_details(media_item.tmdb_id, media_item.media_type)
                if detailed:
                    media_item = detailed
            except Exception:
                pass

            # 2. Ellenőrzés a Plex könyvtárban
            in_plex = orchestrator.plex_service.has_media(
                media_item.title_hu,
                media_item.year,
                alt_title=media_item.title_en,
            )

            if in_plex:
                plex_count += 1
                results.append({
                    "item": media_item,
                    "title": media_item.title_hu,
                    "year": media_item.year,
                    "poster_url": media_item.poster_url,
                    "status": "already_in_plex",
                    "status_label": "🍿 Már a Plexben van",
                    "status_color": "#38bdf8",
                })
                continue

            # 3. Kívánságlistához hozzáadás
            orchestrator.watchlist.add(media_item)

            # 4. nCore ellenőrzés
            releases = []
            try:
                releases = orchestrator.check_release_availability(media_item)
            except Exception as n_err:
                logger.debug(f"nCore hiba a szinkronizáláskor ({media_item.title_hu}): {n_err}")

            # CAM kiadások kiszűrése
            valid_releases = [r for r in releases if not r.is_cam and r.seeders > 0]

            if valid_releases and auto_download:
                best_rel = valid_releases[0]
                dl_res = orchestrator.download_specific_release(media_item, best_rel)
                if dl_res.get("success"):
                    downloaded_count += 1
                    results.append({
                        "item": media_item,
                        "title": media_item.title_hu,
                        "year": media_item.year,
                        "poster_url": media_item.poster_url,
                        "release_title": best_rel.title,
                        "status": "download_started",
                        "status_label": "🚀 Letöltés elindult!",
                        "status_color": "#10b981",
                    })
                    continue

            # 5. Ha nincs még elérhető digitális kiadás az nCore-on -> Mozi-Radar
            if auto_radar:
                orchestrator.radar.add(media_item)
                radar_count += 1
                results.append({
                    "item": media_item,
                    "title": media_item.title_hu,
                    "year": media_item.year,
                    "poster_url": media_item.poster_url,
                    "status": "added_to_radar",
                    "status_label": "🔔 Mozi-Radarhoz adva (Figyelés)",
                    "status_color": "#f59e0b",
                })
            else:
                results.append({
                    "item": media_item,
                    "title": media_item.title_hu,
                    "year": media_item.year,
                    "poster_url": media_item.poster_url,
                    "status": "in_watchlist",
                    "status_label": "⭐ Kívánságlistához adva",
                    "status_color": "#a855f7",
                })

        return {
            "total_parsed": len(raw_items),
            "processed_count": len(results),
            "downloaded_count": downloaded_count,
            "radar_count": radar_count,
            "plex_count": plex_count,
            "skipped_count": skipped_count,
            "results": results,
        }

