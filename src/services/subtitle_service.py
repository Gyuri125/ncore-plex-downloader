import re
from pathlib import Path
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.config.settings import get_settings
from src.models.media import MediaItem, MediaType, SubtitleItem, TorrentRelease


class SubtitleService:
    """
    Magyar felirat kereső és letöltő motor.
    Két forrásból dolgozik:
    1. Feliratok.info (a legpontosabb magyar feliratos oldal, scene release-ekhez illesztve)
    2. OpenSubtitles REST API (nemzetközi adatbázis IMDb ID és release alapján)
    
    Automatikusan UTF-8 kódolásra konvertálja a feliratokat (megoldja az 'ő' és 'ű' betűk problémáját),
    és a Plex szabványnak megfelelő '{Videofájl}.hu.srt' néven menti el.
    """

    FELIRATOK_EU_URL = "https://feliratok.eu/index.php"
    FELIRATOK_INFO_URL = FELIRATOK_EU_URL  # Visszafelé kompatibilis hivatkozás
    SUBIRAT_URL = "https://subirat.net/pages/kereso2"
    OPENSUBTITLES_API_URL = "https://api.opensubtitles.com/api/v1"

    def __init__(self):
        settings = get_settings()
        self.os_api_key = settings.OPENSUBTITLES_API_KEY
        self.last_subtitle_item: Optional[SubtitleItem] = None
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
        3-lépcsős kaszkád keresést alkalmaz:
        1. Feliratok.eu (nCore scene kiadásokhoz legpontosabb)
        2. Subirat.net (friss streaming és mozifilmek fordításai)
        3. OpenSubtitles REST API (nemzetközi adatbázis IMDb ID alapján)
        """
        logger.info(f"Magyar felirat keresése a '{release.title}' kiadáshoz...")
        self.last_subtitle_item = None

        # 1. Kísérlet: Feliratok.eu (Kiváló hazai scene támogatás)
        subtitle = self.search_feliratok_info(media_item, release)

        # 2. Kísérlet: Subirat.net (Streaming és mozifilmek független feliratai)
        if not subtitle:
            logger.info("Feliratok.eu nem adott találatot, próbálkozás Subirat.net-tel...")
            subtitle = self.search_subirat(media_item, release)

        # 3. Kísérlet: OpenSubtitles API (ha az előző kettő nem adott találatot)
        if not subtitle and self.os_api_key:
            logger.info("Subirat sem adott találatot, próbálkozás OpenSubtitles-szel...")
            subtitle = self.search_opensubtitles(media_item, release)

        if not subtitle or not subtitle.download_url:
            logger.warning(f"Nem található magyar felirat a következőhöz: {release.title}")
            return None

        self.last_subtitle_item = subtitle

        # Felirat letöltése és elmentése
        saved_path = self._download_and_save_srt(
            subtitle_url=subtitle.download_url,
            destination_dir=destination_dir,
            video_filename=video_filename or release.title,
        )
        if saved_path and self.last_subtitle_item:
            self.last_subtitle_item.file_name = saved_path.name

        return saved_path

    def search_feliratok_info(
        self, media_item: MediaItem, release: TorrentRelease
    ) -> Optional[SubtitleItem]:
        """
        Keresés a Feliratok.eu / Feliratok.info oldalon release név és film cím alapján.
        Sorban megpróbálja az angol, a magyar és az eredeti gyártási címet.
        """
        queries = []
        for q in [media_item.title_en, media_item.title_hu, getattr(media_item, "original_title", None)]:
            if q and q.strip() and q.strip() not in queries:
                queries.append(q.strip())

        for search_query in queries:
            # A Feliratok.eu a 'search' paramétert várja, a régebbi tesztek miatt a 'kereses'-t is átadjuk
            params = {
                "search": search_query,
                "kereses": search_query,
                "nyelv": "Magyar",
            }

            try:
                response = self.client.get(self.FELIRATOK_EU_URL, params=params)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "lxml")
                # Keresünk minden olyan linket, amely letöltésre mutat (action=letolt vagy action=letoltes)
                letolt_links = soup.find_all("a", href=re.compile(r"action=letolt(?:es)?"))

                candidates: List[dict] = []
                for link in letolt_links:
                    download_href = link["href"]
                    full_download_url = (
                        download_href
                        if download_href.startswith("http")
                        else f"https://feliratok.eu/{download_href.lstrip('/')}"
                    )

                    # Fájlnév kinyerése fnev paraméterből vagy a szülő sorból
                    row = link.find_parent("tr")
                    fnev_match = re.search(r"fnev=([^&]+)", download_href)
                    fnev = fnev_match.group(1) if fnev_match else ""
                    rel_text = f"{fnev} {row.text.strip() if row else link.text.strip()}".strip()

                    candidates.append({
                        "text": rel_text,
                        "url": full_download_url,
                    })

                if not candidates:
                    logger.debug(f"Nincs találat a Feliratok.eu-n erre: {search_query}")
                    continue

                # Legjobban illeszkedő felirat kiválasztása a release neve és éve alapján
                best_match = self._pick_best_subtitle_match(
                    candidates, release.title, expected_year=media_item.year, media_item=media_item
                )
                if best_match:
                    logger.info(f"Találat a Feliratok.eu-n ('{search_query}'): {best_match['url']}")
                    return SubtitleItem(
                        language="hu",
                        release_name=release.title,
                        download_url=best_match["url"],
                        source="Feliratok.eu",
                        match_score=best_match.get("score", 0),
                    )

            except Exception as e:
                logger.error(f"Hiba a Feliratok.eu kereséskor ('{search_query}'): {str(e)}")

        return None

    def search_subirat(
        self, media_item: MediaItem, release: TorrentRelease
    ) -> Optional[SubtitleItem]:
        """
        Keresés a Subirat.net oldalon.
        Kifejezetten erős a legfrissebb streaming és mozifilmes magyar fordításokban.
        Kiszűri az adf.ly reklámlinkeket, és közvetlen .srt vagy .zip fájlokat tölt le.
        """
        queries = []
        for q in [media_item.title_en, media_item.title_hu, getattr(media_item, "original_title", None)]:
            if q and q.strip() and q.strip() not in queries:
                queries.append(q.strip())

        for search_query in queries:
            try:
                response = self.client.get(self.SUBIRAT_URL, params={"search": search_query})
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "lxml")
                # Találati linkek kigyűjtése (/t/ slug alapú adatlapok)
                title_links: List[str] = []
                for a in soup.find_all("a", href=re.compile(r"/t/")):
                    href = a.get("href", "")
                    if href.startswith("http:"):
                        href = "https:" + href[5:]
                    elif href.startswith("/"):
                        href = f"https://subirat.net{href}"
                    if href not in title_links and "/pages/" not in href:
                        title_links.append(href)

                if not title_links:
                    logger.debug(f"Nincs találat a Subirat.net-en erre: {search_query}")
                    continue

                candidates: List[dict] = []
                # Legfeljebb az első 3 legrelevánsabb adatlapot látogatjuk meg
                for title_page_url in title_links[:3]:
                    try:
                        res_page = self.client.get(title_page_url)
                        if res_page.status_code != 200:
                            continue
                        page_soup = BeautifulSoup(res_page.text, "lxml")
                        # Közvetlen .srt és .zip letöltési linkek gyűjtése (adf.ly kizárásával)
                        for sub_a in page_soup.find_all("a", href=re.compile(r"\.(srt|zip)$", re.IGNORECASE)):
                            sub_href = sub_a.get("href", "")
                            if "adf.ly" in sub_href:
                                continue
                            if sub_href.startswith("http:"):
                                sub_href = "https:" + sub_href[5:]
                            elif sub_href.startswith("/"):
                                sub_href = f"https://subirat.net{sub_href}"

                            sub_name = f"{sub_a.text.strip()} {Path(sub_href).name}".strip()
                            candidates.append({
                                "text": sub_name,
                                "url": sub_href,
                            })
                    except Exception as err:
                        logger.debug(f"Subirat aloldal olvasási hiba ({title_page_url}): {err}")

                if not candidates:
                    continue

                best_match = self._pick_best_subtitle_match(
                    candidates, release.title, expected_year=media_item.year, media_item=media_item
                )
                if best_match:
                    logger.info(f"Találat a Subirat.net-en ('{search_query}'): {best_match['url']}")
                    return SubtitleItem(
                        language="hu",
                        release_name=release.title,
                        download_url=best_match["url"],
                        source="Subirat.net",
                        match_score=best_match.get("score", 0),
                    )

            except Exception as e:
                logger.error(f"Hiba a Subirat.net kereséskor ('{search_query}'): {str(e)}")

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
                    source="OpenSubtitles",
                    match_score=100,
                )

        except Exception as e:
            logger.error(f"Hiba az OpenSubtitles kereséskor: {str(e)}")

        return None

    def _pick_best_subtitle_match(
        self,
        candidates: List[dict],
        release_title: str,
        expected_year: Optional[int] = None,
        media_item: Optional[MediaItem] = None,
    ) -> Optional[dict]:
        """
        Pontozásos algoritmus a kiadás nevéhez (pl. 1080p, BluRay, csapatnév) legjobban passzoló felirathoz.
        Szigorú évszám-, release-csoport és tartalom-szűréssel a téves filmek (pl. 2016 vs 2026) kizárására!
        """
        if not candidates:
            return None

        # 1. Célévszám kinyerése a release névből vagy a MediaItem-ből
        year_match = re.search(r"\b(19\d\d|20\d\d)\b", release_title)
        target_year = int(year_match.group(1)) if year_match else (
            expected_year or (media_item.year if media_item else None)
        )

        # 2. Release cím kulcsszavak és ismert kiadási csapat kinyerése
        tokens = [t.upper() for t in re.split(r"[\s._\-]+", release_title) if len(t) >= 2]
        keywords = set(tokens)

        # Release csapatnév (pl. ZoroSenpai, FLUX, BiDA, EVO - általában a cím utolsó eleme)
        rel_group = tokens[-1] if tokens else ""

        # Sorozat-e a release
        is_series_release = bool(
            re.search(r"\b[sS]\d{1,2}[eE]?\d{0,2}\b", release_title)
            or "SEASON" in release_title.upper()
            or (media_item and media_item.media_type != MediaType.MOVIE)
        )

        best_score = -1
        best_candidate = None

        for cand in candidates:
            cand_text = cand["text"].upper()

            # --- KIZÁRÁSI FELTÉTELEK (Disqualifications) ---

            # A) Évszám ellenőrzés: ha mindkettőben van évszám, és nem egyeznek (legfeljebb 1 év tűrés)
            cand_years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", cand_text)]
            if target_year and cand_years:
                if not any(abs(cy - target_year) <= 1 for cy in cand_years):
                    # Teljesen más évjáratú film/feldolgozás (pl. 2016-os Cousteau vs 2026-os Odüsszeia)!
                    continue

            # B) Dokumentumfilm / Making of kizárása, ha a letöltött kiadás maga nem making of
            if any(doc_tag in cand_text for doc_tag in ["MAKING OF", "BEHIND THE SCENES", "INTERVIEW"]):
                if not any(doc_tag in release_title.upper() for doc_tag in ["MAKING OF", "BEHIND THE SCENES"]):
                    continue

            # C) Sorozat vs Film típuseltérés kizárása
            has_series_tag = bool(re.search(r"\b[sS]\d{1,2}[eE]\d{1,2}\b", cand_text) or re.search(r"\b\d{1,2}X\d{1,2}\b", cand_text))
            if not is_series_release and has_series_tag:
                # Filmhez nem töltünk le sorozatepizód feliratot!
                continue

            # --- PONTOZÁS ---
            score = 0

            # Évszám egyezés bónusz
            if target_year and cand_years and any(abs(cy - target_year) <= 1 for cy in cand_years):
                score += 15

            # Csapatnév egyezés (a legfontosabb időzítési garancia!)
            if rel_group and len(rel_group) >= 3 and rel_group in cand_text:
                score += 12

            # Cím- és egyéb kulcsszavak egyezése
            for kw in keywords:
                if len(kw) >= 3 and kw in cand_text:
                    if kw not in {"1080P", "2160P", "720P", "BLURAY", "WEB-DL", "WEBRIP", "HDTV", "REMUX", "DDP5", "X264", "X265", "HEVC"}:
                        score += 3
                    else:
                        score += 1

            # Minőségi címkék
            if "BLURAY" in cand_text and "BLURAY" in release_title.upper():
                score += 4
            if "WEB-DL" in cand_text and "WEB-DL" in release_title.upper():
                score += 4
            if "1080P" in cand_text and "1080P" in release_title.upper():
                score += 3
            if "2160P" in cand_text and "2160P" in release_title.upper():
                score += 3

            cand["score"] = score
            if score > best_score:
                best_score = score
                best_candidate = cand

        # Szigorú küszöb: csak akkor fogadjuk el a feliratot, ha legalább 6 pontot elért,
        # és semmiképp sem adjuk vissza vakon a legelső találatot!
        if best_candidate and best_score >= 6:
            return best_candidate

        return None

    def _download_and_save_srt(
        self, subtitle_url: str, destination_dir: Path, video_filename: str
    ) -> Optional[Path]:
        """
        Letölti a feliratot, átkódolja UTF-8-ra, és elmenti Plex formátumban:
        {VideoFájlnév}.hu.srt
        """
        destination_dir.mkdir(parents=True, exist_ok=True)

        # Csak valódi videókiterjesztést vágjunk le, a release nevek pontjait (pl. .H.264-FLUX) ne!
        clean_name = re.sub(r'[\\/*?:"<>|]', "", video_filename).strip()
        if Path(clean_name).suffix.lower() in {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}:
            base_name = Path(clean_name).stem
        else:
            base_name = clean_name

        target_path = destination_dir / f"{base_name}.hu.srt"

        try:
            req_headers = {}
            if "feliratok" in subtitle_url:
                req_headers["Referer"] = "https://feliratok.eu/index.php"
            elif "subirat.net" in subtitle_url:
                req_headers["Referer"] = "https://subirat.net/"

            response = self.client.get(subtitle_url, headers=req_headers if req_headers else None)
            response.raise_for_status()

            raw_bytes = response.content

            # Ha a válasz ZIP csomagolású (pl. Feliratok.info esetén tipikus)
            if raw_bytes.startswith(b"PK\x03\x04"):
                try:
                    import io
                    import zipfile
                    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                        srt_entries = [n for n in zf.namelist() if n.lower().endswith(".srt") and not n.startswith("__MACOSX")]
                        if len(srt_entries) > 1:
                            # Évadcsomag felirat: összes .srt kicsomagolása UTF-8-ra konvertálva dedikált almappába
                            pack_sub_dir = destination_dir / f"{base_name}_subs"
                            pack_sub_dir.mkdir(parents=True, exist_ok=True)
                            for entry in srt_entries:
                                entry_bytes = zf.read(entry)
                                entry_text = self._decode_hungarian_text(entry_bytes)
                                out_file = pack_sub_dir / Path(entry).name
                                out_file.write_text(entry_text, encoding="utf-8")
                            logger.info(f"Évadcsomag felirat ({len(srt_entries)} db .srt) sikeresen kicsomagolva: {pack_sub_dir}")
                            return pack_sub_dir
                        elif len(srt_entries) == 1:
                            raw_bytes = zf.read(srt_entries[0])
                            logger.info(f"Sikeresen kicsomagolva a felirat ZIP archívumból: {srt_entries[0]}")
                        else:
                            logger.warning("A letöltött ZIP archívum nem tartalmazott .srt fájlt.")
                            return None
                except Exception as zip_err:
                    logger.warning(f"Nem sikerült a felirat ZIP kibontása ({zip_err}).")
                    return None

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
