import datetime
import re
import threading
import unicodedata
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
    CATEGORIES_MOVIES_HUN = ["hd_hun", "xvid_hun", "dvd_hun", "dvd9_hun"]
    CATEGORIES_MOVIES_ENG = ["hd", "xvid", "dvd", "dvd9"]
    CATEGORIES_SERIES_HUN = ["hdser_hun", "xvidser_hun", "dvdser_hun", "sorozat_hun"]
    CATEGORIES_SERIES_ENG = ["hdser", "xvidser", "dvdser", "sorozat"]


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

        # Hálózati hibatűrés: Automatikus újrapróbálkozás mikroszakadások esetén
        transport = httpx.HTTPTransport(retries=3)
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(20.0, connect=8.0),
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
        self._login_lock = threading.Lock()

    def _is_configured(self) -> bool:
        return bool(self.username and self.password and self.username != "felhasznaloneved")

    def _ensure_logged_in(self) -> bool:
        """Szálbiztos nCore bejelentkezés dupla ellenőrzéssel (Double-checked locking)."""
        if self._is_logged_in:
            return True
        with self._login_lock:
            if self._is_logged_in:
                return True
            return self.login()

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
                # Passkey automatikus azonosítása, ha nincs beállítva
                if not self.passkey:
                    m = re.search(r"rss\.php\?key=([0-9a-f]{32})", response.text)
                    if m:
                        self.passkey = m.group(1)
                        logger.info("nCore letöltési kulcs (passkey) automatikusan felismerve a bejelentkezésből!")
                return True
            else:
                logger.error("Sikertelen nCore bejelentkezés! Hibás felhasználónév vagy jelszó.")
                return False

        except Exception as e:
            logger.error(f"Hiba az nCore bejelentkezés során: {str(e)}")
            return False

    ACCEPTED_RELEASE_EXTRAS = {
        "extended", "unrated", "directors", "cut", "remastered", "special", "edition",
        "imax", "proper", "repack", "rerip", "open", "matte", "dual", "hun", "eng", "sub",
        "theatrical", "final", "collector", "collectors", "anniversary", "restored",
        "part", "one", "1", "i"
    }

    @staticmethod
    def _normalize_title_text(text: str) -> str:
        if not text:
            return ""
        deaccent = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", deaccent).lower()
        return re.sub(r"\s+", " ", cleaned).strip()

    @classmethod
    def _get_release_title_stem(cls, rel_title: str) -> str:
        norm = cls._normalize_title_text(rel_title)
        # 1. Először a technikai/minőségi tageknél vágunk
        m_tag = re.search(r"\b(1080p|720p|2160p|4k|bluray|bdrip|web\s*dl|webrip|dvdrip|xvid|x264|x265|hevc|hun)\b", norm)
        if m_tag and m_tag.start() > 0:
            norm = norm[:m_tag.start()].strip()
        # 2. Vágjuk le a sorozat évad/epizód jelölések előtt
        m_series = re.search(r"\b(s\d{1,2}(?:e\d{1,3})?|season\s*\d{1,2}|e\d{1,3}|ep\d{1,3}|d\d{1,2}|disc\s*\d{1,2}|complete|pack)\b", norm)
        if m_series and m_series.start() > 0:
            norm = norm[:m_series.start()].strip()
        # 3. Az évszámnál csak akkor vágunk, ha nem a legelső szó (pl. '1917'), és mindig az utolsó évszámot vágjuk le (pl. 'Blade Runner 2049 2017')
        year_matches = list(re.finditer(r"\b(19\d\d|20\d\d)\b", norm))
        if year_matches:
            last_year = year_matches[-1]
            if last_year.start() > 0:
                norm = norm[:last_year.start()].strip()
        return norm

    def is_title_match(
        self,
        release_title: str,
        title_hu: str,
        title_en: Optional[str] = "",
        original_title: Optional[str] = "",
    ) -> bool:
        """
        Ellenőrzi, hogy a release címe valóban megegyezik-e a média magyar, angol vagy eredeti gyártási címével.
        Kiszűri az idegen filmeket, dokumentumfilmeket, mellékágakat és téves találatokat.
        """
        stem = self._get_release_title_stem(release_title)
        if not stem:
            return False

        stem_words = set(stem.split())
        all_candidates = [t for t in [title_hu, title_en, original_title] if t]

        for cand in all_candidates:
            # Kipróbáljuk a címet kezdő névelővel és névelő nélkül is (pl. 'A hobbit', 'The Dark Knight', 'La casa de papel')
            cands_to_check = [cand]
            cand_l = cand.lower().strip()
            for prefix in ("a ", "az ", "the ", "le ", "la ", "les ", "el ", "los ", "las ", "der ", "die ", "das "):
                if cand_l.startswith(prefix):
                    cands_to_check.append(cand[len(prefix):].strip())

            for c in cands_to_check:
                c_norm = self._normalize_title_text(c)
                if not c_norm:
                    continue

                c_words = c_norm.split()

                # 1. Pontos egyezés (pl. 'the odyssey' == 'the odyssey')
                if stem == c_norm:
                    return True

                # 2. Ha a stem a címmel kezdődik (pl. 'the odyssey extended' vagy 'dune part one')
                if stem.startswith(c_norm + " "):
                    extra_suffix = stem[len(c_norm):].strip()
                    extra_words = set(extra_suffix.split())
                    if extra_words.issubset(self.ACCEPTED_RELEASE_EXTRAS):
                        return True
                    continue

                # 3. Kétnyelvű kiadások az nCore-on (pl. 'Odusszeia The Odyssey' vagy 'La Casa de Papel Money Heist')
                if c_norm in stem and (len(c_words) >= 2 or len(c_norm) >= 5):
                    remaining = stem.replace(c_norm, " ")
                    for other_cand in [t for t in all_candidates if t != cand]:
                        other_norm = self._normalize_title_text(other_cand)
                        if other_norm:
                            remaining = remaining.replace(other_norm, " ")
                    rem_words = set(remaining.split())
                    if rem_words.issubset(self.ACCEPTED_RELEASE_EXTRAS) or len(rem_words) == 0:
                        return True

                # 4. Egyszavas vagy rövid címek
                if len(c_words) == 1 and c_words[0] in stem_words and len(stem_words) <= 3:
                    return True

        return False

    def search_for_media(self, media_item: MediaItem) -> List[TorrentRelease]:
        """
        Keresés egy MediaItem-hez (TMDB film vagy sorozat).
        1. Elsődlegesen IMDb kód alapján (100% tévedésmentes találat!).
        2. Ha nincs IMDb találat, akkor a magyar és eredeti cím alapján.
        3. Találatok szigorú ellenőrzése (IMDb kód, évszám, típus, kötelező címazonosság) a téves találatok kizárására.
        4. Rangsorolás: 1. Magyar szinkron, 2. Legjobb felbontás/minőség.
        """
        results: List[TorrentRelease] = []

        # 1. Keresés IMDb kód alapján
        if media_item.imdb_id:
            logger.info(f"nCore keresés IMDb ID alapján: {media_item.imdb_id} ({media_item.title_hu})")
            results = self.search_by_imdb(media_item.imdb_id, media_item.media_type)

        # 2. Ha nem talált semmit IMDb alapján, keresés cím szerint
        if not results:
            search_title = media_item.title_hu or media_item.title_en
            logger.info(f"nCore keresés cím alapján: {search_title} ({media_item.year or ''})")
            results = self.search_by_title(
                title=search_title,
                media_type=media_item.media_type,
                year=media_item.year,
            )
            # Ha a magyar cím nem adott találatot és van ettől eltérő angol cím, keresünk azzal is
            if not results and media_item.title_en and media_item.title_en.lower() != (search_title or "").lower():
                logger.info(f"nCore keresés angol cím alapján is: {media_item.title_en}")
                results = self.search_by_title(
                    title=media_item.title_en,
                    media_type=media_item.media_type,
                    year=media_item.year,
                )
            # Ha az angol cím sem adott találatot és van ettől eltérő eredeti gyártási cím, keresünk azzal is
            orig_t = getattr(media_item, "original_title", None)
            if not results and orig_t and orig_t.lower() not in ((search_title or "").lower(), (media_item.title_en or "").lower()):
                logger.info(f"nCore keresés eredeti gyártási cím alapján is: {orig_t}")
                results = self.search_by_title(
                    title=orig_t,
                    media_type=media_item.media_type,
                    year=media_item.year,
                )

        # 3. Ellenőrzés: csak a valóban egyező kiadások megtartása (szigorú cím- és évszám-szűrés)
        verified = [r for r in results if self.verify_release_match(r, media_item)]
        logger.info(f"nCore találatok: {len(results)} db nyers találatból {len(verified)} db felel meg a keresett filmnek.")

        # 4. Kiadások rendezése (1. Magyar szinkron, 2. Legjobb minőség)
        return self.sort_releases(verified, prefer_hungarian=True)

    def verify_release_match(self, release: TorrentRelease, media_item: MediaItem) -> bool:
        """
        Szigorúan ellenőrzi, hogy a release valóban a kiválasztott filmhez/sorozathoz tartozik-e.
        1. Kategória ellenőrzés (csak a megfelelő média típusú kategóriák fogadhatók el).
        2. IMDb azonosító ellenőrzése (ha a release-ben szerepel IMDb ID, annak pontosan egyeznie kell).
        3. Típus és lemezformátum ellenőrzés (filmeknél nem lehet sorozat epizód/évad, sem D01/Disc 1 sorozatcsomag).
        4. Évszám ellenőrzése (ha a címben van évszám, egyeznie kell +-1 év toleranciával; jövőbeli filmeknél kötelező az évszám).
        5. Kötelező címellenőrzés: A release törzsének kötelezően egyeznie kell a magyar vagy eredeti címmel!
        6. Szemét és nem kívánt kiadások kizárása (making of, behind the scenes, soundtrack, kulisszák mögött).
        """
        # 1. Kategória érvényesség
        valid_cats = (
            self.CATEGORIES_MOVIES_HUN + self.CATEGORIES_MOVIES_ENG
            if media_item.media_type == MediaType.MOVIE
            else self.CATEGORIES_SERIES_HUN + self.CATEGORIES_SERIES_ENG
        )
        if release.category not in valid_cats:
            return False

        # 2. IMDb azonosító ellenőrzés (ha mindkettőnél megvan, az a legbiztosabb)
        if release.imdb_id and media_item.imdb_id:
            if release.imdb_id.strip().lower() != media_item.imdb_id.strip().lower():
                return False
            # Ha egyezik az IMDb kód és jó a kategória, az garantált egyezés!
            return True

        title_lower = release.title.lower()

        # 3. Sorozat epizódok és több-lemezes kiadások kizárása filmek esetén
        if media_item.media_type == MediaType.MOVIE:
            # Epizódok és lemezcímkék (pl. S01, E01, D01, Disc 1, Part 1)
            if re.search(r"\b(s\d{1,2}|season\s*\d{1,2}|e\d{1,3}|ep\d{1,3}|d\d{1,2}|disc\s*\d{1,2})\b", title_lower):
                item_title_clean = (media_item.title_hu + " " + (media_item.title_en or "")).lower()
                if not re.search(r"\b(part|disc|d\d)\b", item_title_clean):
                    return False

            # Extrák, kulisszák mögött, bemutatók kizárása
            if re.search(r"\b(making of|behind the scenes|featurette|soundtrack|ost|kulisszak mogott)\b", title_lower):
                return False

        # 4. Évszám ellenőrzés
        current_year = datetime.date.today().year
        found_years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", release.title)]
        if media_item.media_type == MediaType.MOVIE and media_item.year:
            if found_years:
                if not any(abs(y - media_item.year) <= 1 for y in found_years):
                    return False
            else:
                # Ha jövőbeli vagy aktuális filmről van szó (pl. 2026), és a release címében egyáltalán nincs évszám,
                # az biztosan nem a valódi premier kiadás
                if media_item.year >= current_year:
                    return False

        # 5. Kötelező Címellenőrzés (magyar, angol VAGY eredeti gyártási cím alapján)
        orig_t = getattr(media_item, "original_title", None)
        if not self.is_title_match(release.title, media_item.title_hu, media_item.title_en, orig_t):
            return False

        return True


    def search_by_imdb(self, imdb_id: str, media_type: MediaType) -> List[TorrentRelease]:
        """
        Keresés kizárólag IMDb azonosító (pl. 'tt0111161') alapján az nCore-on.
        """
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
        if not self._ensure_logged_in():
            logger.warning("Nincs aktív nCore munkamenet a kereséshez.")
            return []

        try:
            response = self.client.get(self.TORRENTS_URL, params=params)
            response.raise_for_status()

            # Ha lejárt a munkamenet, bejelentkezünk újra
            if "login.php" in str(response.url):
                logger.info("nCore munkamenet lejárt, újra bejelentkezés...")
                with self._login_lock:
                    self._is_logged_in = False
                    if not self.login():
                        return []
                response = self.client.get(self.TORRENTS_URL, params=params)
                response.raise_for_status()

            # Passkey kinyerése ha még nem ismert
            if not self.passkey:
                m = re.search(r"rss\.php\?key=([0-9a-f]{32})", response.text)
                if m:
                    self.passkey = m.group(1)
                    logger.info("nCore letöltési kulcs (passkey) automatikusan felismerve a keresési válaszból!")

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

        # Passkey ellenőrzése a fejlécből, ha hiányzik
        if not self.passkey:
            m = re.search(r"rss\.php\?key=([0-9a-f]{32})", html_content)
            if m:
                self.passkey = m.group(1)

        # nCore torrent sorok: div.box_torrent elemek a listában
        torrent_rows = soup.find_all("div", class_="box_torrent")

        for row in torrent_rows:
            try:
                # Cím és részletek link kinyerése
                torrent_txt = row.find("div", class_="torrent_txt")
                title_link = None
                if torrent_txt:
                    title_link = torrent_txt.find("a", href=re.compile(r"action=details"))
                if not title_link:
                    title_link = row.find("a", href=re.compile(r"action=details"))
                if not title_link:
                    title_link = row.find("a", title=True)
                if not title_link:
                    continue

                raw_title = title_link.get("title") or title_link.text.strip()
                details_href = title_link.get("href", "")

                # Torrent azonosító kinyerése az URL-ből (pl. id=123456)
                id_match = re.search(r"id=(\d+)", details_href)
                if not id_match:
                    continue
                torrent_id = id_match.group(1)

                # IMDb azonosító kinyerése a sorból ha van
                imdb_id = None
                infolink = row.find("a", class_="infolink")
                if infolink and "href" in infolink.attrs:
                    m_imdb = re.search(r"tt\d+", infolink["href"])
                    if m_imdb:
                        imdb_id = m_imdb.group(0)

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

                # Seederek és leecherek száma
                s_div = row.find("div", class_="box_s2")
                seeders_raw = s_div.text.strip() if s_div else "0"
                seeders_digits = re.sub(r"[^\d]", "", seeders_raw)
                seeders = int(seeders_digits) if seeders_digits else 0

                l_div = row.find("div", class_="box_l2")
                leechers_raw = l_div.text.strip() if l_div else "0"
                leechers_digits = re.sub(r"[^\d]", "", leechers_raw)
                leechers = int(leechers_digits) if leechers_digits else 0

                # Letöltési URL generálása
                download_url = f"{self.TORRENTS_URL}?action=download&id={torrent_id}"
                if self.passkey:
                    download_url += f"&key={self.passkey}"

                # Szinkron és felirat jelenlét ellenőrzése
                has_hun_audio = (
                    "_hun" in category
                    or bool(re.search(r"\b(hun|magyar|dubbed|szinkron)\b", raw_title, re.IGNORECASE))
                    or ".hun." in raw_title.lower()
                    or "-hun" in raw_title.lower()
                    or "2xhun" in raw_title.lower()
                )
                has_hun_sub = (
                    has_hun_audio
                    or bool(re.search(r"\b(sub|felirat|forced)\b", raw_title, re.IGNORECASE))
                )

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
                    imdb_id=imdb_id,
                )
                releases.append(release)

            except Exception as e:
                logger.debug(f"Sor kihagyva az nCore feldolgozásakor hiba miatt: {str(e)}")
                continue

        logger.info(f"Összesen {len(releases)} találat feldolgozva az nCore-ról.")
        return releases

    def score_release(self, rel: TorrentRelease, prefer_hungarian: bool = True) -> int:
        """
        Pontszámot rendel egy release-hez a minőség, szinkron és seederek alapján.
        1. Magyar szinkron: legfelső prioritás (+5000 pont).
        2. Kategória és felbontás: 4K (+2500), 1080p (+2000), 720p (+1000).
        3. Forrás minőség: BluRay/Remux (+500), WEB-DL (+400).
        4. Kodek: x265/HEVC (+150), x264 (+100).
        5. Seeder stabilitás: max +500 pont.
        6. Rossz minőségek (CAM, TS) büntetése: -10000 pont.
        """
        score = 0
        title_upper = rel.title.upper()

        # 1. Magyar szinkron prioritás
        if prefer_hungarian:
            if rel.has_hungarian_audio or "_hun" in rel.category:
                score += 5000

        # 2. Felbontás és minőség
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

        # Büntetés rossz minőségekre és 3D kiadásokra (hogy 2D TV-re ne töltsön le SBS 3D-t)
        if any(bad in title_upper for bad in ["CAM", "TELESYNC", " TS ", ".TS.", "HDCAM", "SCREENER"]):
            score -= 10000
        if any(td in title_upper for td in [".3D.", " 3D ", "HALF-SBS", "H-SBS", "FULL-SBS", "H-OU"]):
            score -= 8000

        # 3. Kategória bónuszok
        if rel.category in ("hd_hun", "hdser_hun", "sorozat_hun"):
            score += 3000
        elif rel.category in ("xvid_hun", "xvidser_hun", "dvd_hun", "dvdser_hun"):
            score += 2000
        elif rel.category in ("hd", "hdser", "sorozat"):
            score += 1500
        elif rel.category in ("xvid", "xvidser", "dvd", "dvdser"):
            score += 1000

        # 4. Seeder stabilitás és halott torrentek (seeders <= 0) súlyos büntetése
        if rel.seeders <= 0:
            score -= 20000
        else:
            score += min(rel.seeders, 50) * 10

        return score

    def sort_releases(
        self, releases: List[TorrentRelease], prefer_hungarian: bool = True
    ) -> List[TorrentRelease]:
        """
        Rendezi a kiadásokat pontszám szerint csökkenő sorrendbe (legjobb elöl).
        """
        if not releases:
            return []
        return sorted(releases, key=lambda r: self.score_release(r, prefer_hungarian), reverse=True)

    def select_best_release(
        self, releases: List[TorrentRelease], prefer_hungarian: bool = True
    ) -> Optional[TorrentRelease]:
        """
        Kiválasztja a legmagasabb pontszámú kiadást.
        """
        sorted_releases = self.sort_releases(releases, prefer_hungarian=prefer_hungarian)
        if not sorted_releases:
            return None

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
        if not self._ensure_logged_in():
            logger.warning("Nincs aktív nCore munkamenet a torrent letöltéshez.")
            return None

        if not release.torrent_id:
            logger.error(f"Nincs letöltési URL vagy ID a torrenthez: {release.title}")
            return None

        download_url = release.download_url

        # Ha nincs kulcs a letöltési URL-ben, kinyerjük az nCore részletek oldaláról
        if not self.passkey or "&key=" not in download_url:
            try:
                details_url = f"{self.TORRENTS_URL}?action=details&id={release.torrent_id}"
                det_resp = self.client.get(details_url)
                key_match = re.search(r"action=download&amp;id=\d+&amp;key=([0-9a-f]{32})", det_resp.text) or re.search(
                    r"action=download&id=\d+&key=([0-9a-f]{32})", det_resp.text
                )
                if key_match:
                    self.passkey = key_match.group(1)
                    download_url = f"{self.TORRENTS_URL}?action=download&id={release.torrent_id}&key={self.passkey}"
            except Exception as e:
                logger.debug(f"Nem sikerült kulcsot lekérni a részletek oldalról: {e}")

        target_dir.mkdir(parents=True, exist_ok=True)
        # Biztonságos fájlnév készítése
        safe_title = re.sub(r'[\\/*?:"<>|]', "", release.title)
        torrent_filepath = target_dir / f"{safe_title}.torrent"

        try:
            logger.info(f".torrent letöltése: {release.title}...")
            response = self.client.get(download_url)
            response.raise_for_status()

            # Ellenőrizzük, hogy valóban torrent fájlt kaptunk-e (bencoded header: 'd8:announce' vagy hasonló)
            if response.content.startswith(b"d") and b"announce" in response.content[:150]:
                torrent_filepath.write_bytes(response.content)
                logger.info(f".torrent sikeresen elmentve: {torrent_filepath}")
                return torrent_filepath
            else:
                logger.error(
                    f"A letöltött válasz nem érvényes torrent fájl (lehet, hogy lejárt a munkamenet). "
                    f"Válasz hossza: {len(response.content)} bájt"
                )
                return None

        except Exception as e:
            logger.error(f"Hiba a torrent fájl letöltésekor: {str(e)}")
            return None

    @staticmethod
    def _parse_size_to_bytes(size_str: str) -> int:
        """
        Átváltja a méretszöveget (pl. '1.49 GiB', '700.5 MB') bájtokra.
        """
        match = re.search(r"([\d.,]+)\s*([KMGTPE]?I?B)", size_str.upper())
        if not match:
            return 0

        val_str, unit = match.groups()
        val = float(val_str.replace(",", "."))
        multipliers = {
            "B": 1,
            "KB": 1024,
            "KIB": 1024,
            "MB": 1024**2,
            "MIB": 1024**2,
            "GB": 1024**3,
            "GIB": 1024**3,
            "TB": 1024**4,
            "TIB": 1024**4,
            "PB": 1024**5,
            "PIB": 1024**5,
        }
        return int(val * multipliers.get(unit, 1))
