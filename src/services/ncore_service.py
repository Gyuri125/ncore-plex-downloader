import re
from pathlib import Path
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.config.settings import get_settings
from src.models.media import MediaItem, MediaType, TorrentRelease


class NcoreService:
    """
    nCore kliens és kereső motor.
    Kezeli a bejelentkezést, az IMDb kód és cím alapú keresést,
    a minőségi rangsorolást (szinkron és felbontás alapján) és a .torrent letöltést.
    """

    BASE_URL = "https://ncore.pro"
    LOGIN_URL = "https://ncore.pro/login.php"
    TORRENTS_URL = "https://ncore.pro/torrents.php"

    # Kategóriák kódjai az nCore-on
    CATEGORIES_MOVIES_HUN = ["hd_hun", "xvid_hun", "dvd_hun"]
    CATEGORIES_MOVIES_ENG = ["hd", "xvid", "dvd"]
    CATEGORIES_SERIES_HUN = ["sorozat_hun"]
    CATEGORIES_SERIES_ENG = ["sorozat"]

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        passkey: Optional[str] = None,
    ):
        settings = get_settings()
        self.username = username or settings.NCORE_USERNAME
        self.password = password or settings.NCORE_PASSWORD
        self.passkey = passkey or settings.NCORE_PASSKEY

        self.client = httpx.Client(
            timeout=20.0,
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
        self._is_logged_in = False

    def _is_configured(self) -> bool:
        return bool(self.username and self.password and self.username != "felhasznaloneved")

    def login(self) -> bool:
        """
        Bejelentkezés az nCore-ra felhasználónévvel és jelszóval.
        """
        if not self._is_configured():
            logger.warning("nCore belépési adatok nincsenek beállítva a .env fájlban!")
            return False

        try:
            logger.info(f"Bejelentkezés az nCore-ra mint '{self.username}'...")
            payload = {
                "nev": self.username,
                "pass": self.password,
                "ne_leptessen_ki": "1",
                "set_lang": "hu",
                "submitted": "1",
            }
            response = self.client.post(self.LOGIN_URL, data=payload)
            response.raise_for_status()

            # Ellenőrizzük, hogy sikeres volt-e (pl. profil vagy kijelentkezés link keresése)
            if "kijelentkezes" in response.text.lower() or "torrents.php" in response.text:
                self._is_logged_in = True
                logger.info("Sikeres nCore bejelentkezés!")
                return True
            else:
                logger.error("Sikertelen nCore bejelentkezés! Hibás felhasználónév vagy jelszó.")
                return False

        except Exception as e:
            logger.error(f"Hiba az nCore bejelentkezés során: {str(e)}")
            return False

    def search_for_media(self, media_item: MediaItem) -> List[TorrentRelease]:
        """
        Keresés egy MediaItem-hez (TMDB film vagy sorozat).
        1. Elsődlegesen IMDb kód alapján (100% tévedésmentes találat!).
        2. Ha nincs IMDb találat, akkor a magyar és eredeti cím alapján.
        """
        results: List[TorrentRelease] = []

        # 1. Keresés IMDb kód alapján
        if media_item.imdb_id:
            logger.info(f"nCore keresés IMDb ID alapján: {media_item.imdb_id} ({media_item.title_hu})")
            results = self.search_by_imdb(media_item.imdb_id, media_item.media_type)

        # 2. Ha nem talált semmit IMDb alapján, keresés cím szerint
        if not results:
            search_title = media_item.title_en or media_item.title_hu
            logger.info(f"nCore keresés cím alapján: {search_title} ({media_item.year or ''})")
            results = self.search_by_title(
                title=search_title,
                media_type=media_item.media_type,
                year=media_item.year,
            )

        return results

    def search_by_imdb(self, imdb_id: str, media_type: MediaType) -> List[TorrentRelease]:
        """
        Keresés kizárólag IMDb azonosító (pl. 'tt0111161') alapján az nCore-on.
        """
        # Tisztítás
        clean_imdb = imdb_id.strip()
        params = {
            "mire": clean_imdb,
            "miben": "imdb",
            "tipus": "kivalasztottak_szerint",
            "kivalasztott_tipus[]": (
                self.CATEGORIES_MOVIES_HUN + self.CATEGORIES_MOVIES_ENG
                if media_type == MediaType.MOVIE
                else self.CATEGORIES_SERIES_HUN + self.CATEGORIES_SERIES_ENG
            ),
            "miszerint": "seeders",
            "hogyan": "DESC",
        }
        return self._execute_search(params)

    def search_by_title(
        self, title: str, media_type: MediaType, year: Optional[int] = None
    ) -> List[TorrentRelease]:
        """
        Keresés cím (és opcionálisan évszám) alapján az nCore-on.
        """
        query = title.strip()
        params = {
            "mire": query,
            "miben": "name",
            "tipus": "kivalasztottak_szerint",
            "kivalasztott_tipus[]": (
                self.CATEGORIES_MOVIES_HUN + self.CATEGORIES_MOVIES_ENG
                if media_type == MediaType.MOVIE
                else self.CATEGORIES_SERIES_HUN + self.CATEGORIES_SERIES_ENG
            ),
            "miszerint": "seeders",
            "hogyan": "DESC",
        }
        return self._execute_search(params)

    def _execute_search(self, params: dict) -> List[TorrentRelease]:
        """
        Lefuttatja a GET kérést a torrents.php felé és feldolgozza a HTML táblázatot.
        """
        if not self._is_logged_in and not self.login():
            logger.warning("Nincs aktív nCore munkamenet a kereséshez.")
            return []

        try:
            response = self.client.get(self.TORRENTS_URL, params=params)
            response.raise_for_status()

            # Ha lejárt a munkamenet, bejelentkezünk újra
            if "login.php" in str(response.url):
                logger.info("nCore munkamenet lejárt, újra bejelentkezés...")
                if self.login():
                    response = self.client.get(self.TORRENTS_URL, params=params)
                    response.raise_for_status()
                else:
                    return []

            return self._parse_torrent_list(response.text)

        except Exception as e:
            logger.error(f"Hiba az nCore keresés során: {str(e)}")
            return []

    def _parse_torrent_list(self, html_content: str) -> List[TorrentRelease]:
        """
        Feldolgozza az nCore találati oldal HTML-jét és visszaadja a TorrentRelease objektumokat.
        """
        soup = BeautifulSoup(html_content, "lxml")
        releases: List[TorrentRelease] = []

        # nCore torrent sorok: div.box_torrent vagy tr elemek a torrent táblázatban
        torrent_rows = soup.find_all("div", class_="box_torrent")

        for row in torrent_rows:
            try:
                # Cím és letöltési link
                title_link = row.find("nobr").find("a") if row.find("nobr") else row.find("a", title=True)
                if not title_link:
                    continue

                raw_title = title_link.get("title") or title_link.text.strip()
                details_href = title_link.get("href", "")

                # Torrent azonosító kinyerése az URL-ből (pl. id=123456)
                id_match = re.search(r"id=(\d+)", details_href)
                if not id_match:
                    continue
                torrent_id = id_match.group(1)

                # Kategória kinyerése
                cat_img = row.find("a", href=re.compile(r"tipus="))
                category = "unknown"
                if cat_img and "href" in cat_img.attrs:
                    cat_match = re.search(r"tipus=([a-zA-Z0-9_]+)", cat_img["href"])
                    if cat_match:
                        category = cat_match.group(1).lower()

                # Méret kinyerése
                size_div = row.find("div", class_="box_meret2")
                size_human = size_div.text.strip() if size_div else "0 MB"
                size_bytes = self._parse_size_to_bytes(size_human)

                # Seederek és leedcherek száma
                s_div = row.find("div", class_="box_s2")
                seeders = int(s_div.text.strip()) if s_div and s_div.text.strip().isdigit() else 0

                l_div = row.find("div", class_="box_l2")
                leechers = int(l_div.text.strip()) if l_div and l_div.text.strip().isdigit() else 0

                # Letöltési URL generálása
                download_url = f"{self.TORRENTS_URL}?action=download&id={torrent_id}"
                if self.passkey:
                    download_url += f"&key={self.passkey}"

                # Szinkron és felirat jelenlét ellenőrzése
                has_hun_audio = "_hun" in category or bool(re.search(r"(hun|magyar|dub)", raw_title, re.IGNORECASE))
                has_hun_sub = "sub" in raw_title.lower() or "felirat" in raw_title.lower() or has_hun_audio

                release = TorrentRelease(
                    torrent_id=torrent_id,
                    title=raw_title,
                    category=category,
                    size_bytes=size_bytes,
                    size_human=size_human,
                    seeders=seeders,
                    leechers=leechers,
                    has_hungarian_audio=has_hun_audio,
                    has_hungarian_sub=has_hun_sub,
                    download_url=download_url,
                )
                releases.append(release)

            except Exception as e:
                logger.debug(f"Sor kihagyva az nCore feldolgozásakor hiba miatt: {str(e)}")
                continue

        logger.info(f"Összesen {len(releases)} találat feldolgozva az nCore-ról.")
        return releases

    def select_best_release(
        self, releases: List[TorrentRelease], prefer_hungarian: bool = True
    ) -> Optional[TorrentRelease]:
        """
        Kiválasztja a legjobb minőségű és legmegfelelőbb kiadást.
        
        Prioritási szempontok:
        1. Magyar szinkron megléte (ha prefer_hungarian=True).
        2. Minőség: 1080p, 2160p (4K), BluRay, WEB-DL kiemelése.
        3. Rossz minőségek (CAM, Telesync, TS) kizárása vagy leértékelése.
        4. Aktív seederek száma (ne akadjon el a letöltés).
        """
        if not releases:
            return None

        # Csak olyan release-eket nézünk, ahol legalább 1 seeder van
        valid_releases = [r for r in releases if r.seeders > 0]
        if not valid_releases:
            valid_releases = releases  # végső fallback

        def score_release(rel: TorrentRelease) -> int:
            score = 0
            title_upper = rel.title.upper()

            # 1. Magyar szinkron prioritás
            if prefer_hungarian:
                if rel.has_hungarian_audio or "_hun" in rel.category:
                    score += 5000

            # 2. Felbontás és forrás minőség
            if "2160P" in title_upper or "4K" in title_upper or "UHD" in title_upper:
                score += 2500
            elif "1080P" in title_upper:
                score += 2000
            elif "720P" in title_upper:
                score += 1000

            if "BLURAY" in title_upper or "REMUX" in title_upper:
                score += 500
            elif "WEB-DL" in title_upper or "WEBRIP" in title_upper:
                score += 400
            elif "HDTV" in title_upper:
                score += 200

            # Kodek
            if "X265" in title_upper or "HEVC" in title_upper:
                score += 150
            elif "X264" in title_upper or "H264" in title_upper:
                score += 100

            # Büntetés rossz minőségekre
            if any(bad in title_upper for bad in ["CAM", "TELESYNC", " TS ", ".TS.", "HDCAM", "SCREENER"]):
                score -= 10000

            # 3. Kategória bónuszok
            if "hd_hun" in rel.category:
                score += 3000
            elif "sorozat_hun" in rel.category:
                score += 3000
            elif "hd" in rel.category or "sorozat" in rel.category:
                score += 1500

            # 4. Seeder bónusz (max +500 pont a seeder stabilitásért)
            score += min(rel.seeders, 50) * 10

            return score

        # Rendezzük a pontszámok szerint csökkenő sorrendbe
        sorted_releases = sorted(valid_releases, key=score_release, reverse=True)
        best = sorted_releases[0]
        logger.info(
            f"Kiválasztott legjobb kiadás: '{best.title}' "
            f"(Kategória: {best.category}, Seederek: {best.seeders}, Szinkron: {best.has_hungarian_audio})"
        )
        return best

    def download_torrent_file(self, release: TorrentRelease, target_dir: Path) -> Optional[Path]:
        """
        Letölti a .torrent fájlt az nCore-ról a megadott mappába.
        Visszaadja a letöltött .torrent fájl elérési útját.
        """
        if not release.download_url:
            logger.error(f"Nincs letöltési URL a torrenthez: {release.title}")
            return None

        target_dir.mkdir(parents=True, exist_ok=True)
        # Biztonságos fájlnév készítése
        safe_title = re.sub(r'[\\/*?:"<>|]', "", release.title)
        torrent_filepath = target_dir / f"{safe_title}.torrent"

        try:
            logger.info(f".torrent letöltése: {release.title}...")
            response = self.client.get(release.download_url)
            response.raise_for_status()

            # Ellenőrizzük, hogy valóban torrent fájlt kaptunk-e (bencoded header: 'd8:announce' vagy hasonló)
            if response.content.startswith(b"d") and b"announce" in response.content[:100]:
                torrent_filepath.write_bytes(response.content)
                logger.info(f".torrent sikeresen elmentve: {torrent_filepath}")
                return torrent_filepath
            else:
                logger.error("A letöltött válasz nem érvényes torrent fájl (lehet, hogy újra be kell jelentkezni).")
                return None

        except Exception as e:
            logger.error(f"Hiba a torrent fájl letöltésekor: {str(e)}")
            return None

    @staticmethod
    def _parse_size_to_bytes(size_str: str) -> int:
        """
        Átváltja a méretszöveget (pl. '4.35 GB', '700.5 MB') bájtokra.
        """
        match = re.search(r"([\d.,]+)\s*([KMGTPE]?B)", size_str.upper())
        if not match:
            return 0

        val_str, unit = match.groups()
        val = float(val_str.replace(",", "."))
        multipliers = {
            "B": 1,
            "KB": 1024,
            "MB": 1024**2,
            "GB": 1024**3,
            "TB": 1024**4,
        }
        return int(val * multipliers.get(unit, 1))
