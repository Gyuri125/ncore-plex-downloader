import re
from pathlib import Path
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.config.settings import get_settings
from src.models.media import MediaItem, SubtitleItem, TorrentRelease


class SubtitleService:
    """
    Magyar felirat kereső és letöltő motor.
    Két forrásból dolgozik:
    1. Feliratok.info (a legpontosabb magyar feliratos oldal, scene release-ekhez illesztve)
    2. OpenSubtitles REST API (nemzetközi adatbázis IMDb ID és release alapján)
    
    Automatikusan UTF-8 kódolásra konvertálja a feliratokat (megoldja az 'ő' és 'ű' betűk problémáját),
    és a Plex szabványnak megfelelő '{Videofájl}.hu.srt' néven menti el.
    """

    FELIRATOK_INFO_URL = "https://www.feliratok.info/index.php"
    OPENSUBTITLES_API_URL = "https://api.opensubtitles.com/api/v1"

    def __init__(self):
        settings = get_settings()
        self.os_api_key = settings.OPENSUBTITLES_API_KEY
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
            },
            follow_redirects=True,
        )

    def find_and_download_subtitle(
        self,
        media_item: MediaItem,
        release: TorrentRelease,
        destination_dir: Path,
        video_filename: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Keres és letölt egy illeszkedő magyar feliratot a kiadáshoz.
        Ha sikeres, visszaadja az elmentett .hu.srt fájl útvonalát.
        """
        logger.info(f"Magyar felirat keresése a '{release.title}' kiadáshoz...")

        # 1. Kísérlet: Feliratok.info (Kiváló magyar scene támogatás)
        subtitle = self.search_feliratok_info(media_item, release)

        # 2. Kísérlet: OpenSubtitles API (ha az első nem adott találatot)
        if not subtitle and self.os_api_key:
            logger.info("Feliratok.info nem adott találatot, próbálkozás OpenSubtitles-szel...")
            subtitle = self.search_opensubtitles(media_item, release)

        if not subtitle or not subtitle.download_url:
            logger.warning(f"Nem található magyar felirat a következőhöz: {release.title}")
            return None

        # Felirat letöltése és elmentése
        return self._download_and_save_srt(
            subtitle_url=subtitle.download_url,
            destination_dir=destination_dir,
            video_filename=video_filename or release.title,
        )

    def search_feliratok_info(
        self, media_item: MediaItem, release: TorrentRelease
    ) -> Optional[SubtitleItem]:
        """
        Keresés a Feliratok.info oldalon release név és film cím alapján.
        """
        search_query = media_item.title_en or media_item.title_hu
        params = {
            "kereses": search_query,
            "nyelv": "Magyar",
        }

        try:
            response = self.client.get(self.FELIRATOK_INFO_URL, params=params)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")
            result_rows = soup.find_all("tr", class_=re.compile(r"sor|table_sor"))

            candidates: List[dict] = []
            for row in result_rows:
                link = row.find("a", href=re.compile(r"action=letoltes"))
                if not link:
                    continue

                rel_text = row.text.strip()
                download_href = link["href"]
                full_download_url = (
                    download_href
                    if download_href.startswith("http")
                    else f"https://www.feliratok.info/{download_href.lstrip('/')}"
                )

                candidates.append({
                    "text": rel_text,
                    "url": full_download_url,
                })

            if not candidates:
                logger.debug(f"Nincs találat a Feliratok.info-n erre: {search_query}")
                return None

            # Legjobban illeszkedő felirat kiválasztása a release neve alapján
            best_match = self._pick_best_subtitle_match(candidates, release.title)
            if best_match:
                logger.info(f"Találat a Feliratok.info-n: {best_match['url']}")
                return SubtitleItem(
                    language="hu",
                    release_name=release.title,
                    download_url=best_match["url"],
                )

        except Exception as e:
            logger.error(f"Hiba a Feliratok.info kereséskor: {str(e)}")

        return None

    def search_opensubtitles(
        self, media_item: MediaItem, release: TorrentRelease
    ) -> Optional[SubtitleItem]:
        """
        Keresés az OpenSubtitles REST API v3-on keresztül IMDb ID és nyelvkód alapján.
        """
        if not self.os_api_key:
            return None

        headers = {
            "Api-Key": self.os_api_key,
            "User-Agent": "NcorePlexAutoDownloader v1.0",
        }

        params = {
            "languages": "hu",
        }

        # IMDb ID tisztítása (pl. tt0111161 -> 111161)
        if media_item.imdb_id:
            clean_imdb = media_item.imdb_id.lower().replace("tt", "").lstrip("0")
            params["imdb_id"] = clean_imdb
        else:
            params["query"] = media_item.title_en or media_item.title_hu

        try:
            endpoint = f"{self.OPENSUBTITLES_API_URL}/subtitles"
            response = self.client.get(endpoint, params=params, headers=headers)
            response.raise_for_status()

            data = response.json()
            items = data.get("data", [])
            if not items:
                return None

            # Letöltési azonosító kinyerése a legjobb találatból
            first_item = items[0]
            files = first_item.get("attributes", {}).get("files", [])
            if not files:
                return None

            file_id = files[0].get("file_id")

            # Letöltési link kérése
            download_endpoint = f"{self.OPENSUBTITLES_API_URL}/download"
            dl_res = self.client.post(
                download_endpoint,
                json={"file_id": file_id},
                headers=headers,
            )
            dl_res.raise_for_status()
            download_url = dl_res.json().get("link")

            if download_url:
                return SubtitleItem(
                    language="hu",
                    release_name=release.title,
                    download_url=download_url,
                )

        except Exception as e:
            logger.error(f"Hiba az OpenSubtitles kereséskor: {str(e)}")

        return None

    def _pick_best_subtitle_match(self, candidates: List[dict], release_title: str) -> Optional[dict]:
        """
        Pontozásos algoritmus a kiadás nevéhez (pl. 1080p, BluRay, csapatnév) legjobban passzoló felirathoz.
        """
        keywords = set(re.split(r"[\s._\-]+", release_title.upper()))

        best_score = -1
        best_candidate = None

        for cand in candidates:
            score = 0
            cand_text = cand["text"].upper()

            for kw in keywords:
                if len(kw) >= 3 and kw in cand_text:
                    score += 2

            # Prioritás a minőségi címkéknek
            if "BLURAY" in cand_text and "BLURAY" in release_title.upper():
                score += 5
            if "WEB-DL" in cand_text and "WEB-DL" in release_title.upper():
                score += 5
            if "1080P" in cand_text and "1080P" in release_title.upper():
                score += 4

            if score > best_score:
                best_score = score
                best_candidate = cand

        return best_candidate or (candidates[0] if candidates else None)

    def _download_and_save_srt(
        self, subtitle_url: str, destination_dir: Path, video_filename: str
    ) -> Optional[Path]:
        """
        Letölti a feliratot, átkódolja UTF-8-ra, és elmenti Plex formátumban:
        {VideoFájlnév}.hu.srt
        """
        destination_dir.mkdir(parents=True, exist_ok=True)

        # Fájlnév levágása a videó kiterjesztés nélkül
        base_name = Path(video_filename).stem
        target_path = destination_dir / f"{base_name}.hu.srt"

        try:
            response = self.client.get(subtitle_url)
            response.raise_for_status()

            raw_bytes = response.content

            # Kódolás javítása (gyakran CP1250 vagy ISO-8859-2 a magyar feliratoknál)
            text_content = self._decode_hungarian_text(raw_bytes)

            # Mentés szabványos UTF-8 kódolással
            target_path.write_text(text_content, encoding="utf-8")
            logger.info(f"Magyar felirat sikeresen elmentve: {target_path}")
            return target_path

        except Exception as e:
            logger.error(f"Hiba a feliratfájl letöltésekor: {str(e)}")
            return None

    @staticmethod
    def _decode_hungarian_text(raw_bytes: bytes) -> str:
        """
        Biztosítja a magyar ékezetes karakterek (ő, ű, ő, ű, á, é, í, ó, ö, ú, ü)
        helyes visszaadását többféle kódolási próbálkozással.
        """
        # 1. UTF-8
        try:
            return raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass

        # 2. Windows-1250 (Gyakori régi magyar feliratoknál)
        try:
            return raw_bytes.decode("cp1250")
        except UnicodeDecodeError:
            pass

        # 3. ISO-8859-2 (Latin-2)
        try:
            return raw_bytes.decode("iso-8859-2")
        except UnicodeDecodeError:
            pass

        # Fallback
        return raw_bytes.decode("utf-8", errors="replace")
