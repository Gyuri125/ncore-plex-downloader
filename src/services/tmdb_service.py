import datetime
import re
import time
from typing import Any, Dict, List, Optional
import httpx
from loguru import logger

from src.config.settings import get_settings
from src.models.media import MediaItem, MediaType

TMDB_GENRE_MAP_HU: Dict[int, str] = {
    28: "Akció", 12: "Kaland", 16: "Animáció", 35: "Vígjáték", 80: "Krimi",
    99: "Dokumentum", 18: "Dráma", 10751: "Családi", 14: "Fantasy", 36: "Történelmi",
    27: "Horror", 10402: "Zenei", 9648: "Rejtély", 10749: "Romantikus", 878: "Sci-Fi",
    10770: "TV film", 53: "Thriller", 10752: "Háborús", 37: "Western",
    10759: "Akció & Kaland", 10762: "Gyerek", 10763: "Hírek", 10764: "Reality",
    10765: "Sci-Fi & Fantasy", 10766: "Szappanopera", 10767: "Talk show", 10768: "Háborús & Politika",
}


class TMDBService:
    """
    TMDB (The Movie Database) API kliens.
    Felelős a filmek és sorozatok vizuális kereséséért, borítóképekért,
    magyar leírásokért és az nCore-hoz kulcsfontosságú IMDb azonosítóért.
    """

    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
    BACKDROP_BASE_URL = "https://image.tmdb.org/t/p/original"

    DEFAULT_API_KEY = "2c46288716a18fb7aadcc2a801f3fc6b"

    def __init__(self, api_key: Optional[str] = None, language: Optional[str] = None):
        settings = get_settings()
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = settings.TMDB_API_KEY or self.DEFAULT_API_KEY
            if self.api_key == "ide_ird_a_tmdb_api_kulcsodat":
                self.api_key = self.DEFAULT_API_KEY
        self.language = language or settings.TMDB_LANGUAGE

        # Hálózati hibatűrés: Automatikus újrapróbálkozás hálózati mikroszakadás esetén
        transport = httpx.HTTPTransport(retries=3)
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(12.0, connect=6.0),
            follow_redirects=True,
        )
        self._search_cache: dict[str, List[MediaItem]] = {}
        self._details_cache: dict[tuple[int, str], MediaItem] = {}
        self._discover_cache: dict[str, tuple[List[MediaItem], float]] = {}
        self._discover_ttl = 300.0  # 5 percig érvényes lista gyorsítótár a villámgyors lapozáshoz

        # Elgépelés-javító és intelligens keresési javaslattevő (Fuzzy Search & Did-You-Mean)
        self.last_search_suggestion: Optional[str] = None
        self._popular_titles_pool: set[str] = {
            "Oppenheimer", "Gladiátor", "Gladiátor II", "Gladiator", "Dűne", "Dűne: Második rész",
            "Dune", "Dune: Part Two", "Deadpool & Wolverine", "Deadpool", "Wolverine",
            "Avatar", "Avatar: A víz útja", "Csillagok között", "Interstellar", "Eredet", "Inception",
            "A sötét lovag", "The Dark Knight", "Batman", "Joker", "Pókember", "Spider-Man",
            "Bosszúállók", "Avengers", "A Gyűrűk Ura", "The Lord of the Rings", "A hobbit", "The Hobbit",
            "Harry Potter", "Csillagok háborúja", "Star Wars", "Mátrix", "The Matrix",
            "A keresztapa", "The Godfather", "Ponyvaregény", "Pulp Fiction", "Harcosok klubja", "Fight Club",
            "Forrest Gump", "A remény rabjai", "The Shawshank Redemption", "Shutter Island", "Viharsziget",
            "Trónok harca", "Game of Thrones", "Sárkányok háza", "House of the Dragon",
            "Breaking Bad", "Totál szívás", "Stranger Things", "Csernobil", "Chernobyl",
            "A sárkány csókja", "Vámpírnaplók", "The Vampire Diaries", "A korona", "The Crown",
            "Shogun", "A sógun", "A mackó", "The Bear", "A Fehér Lótusz", "The White Lotus",
            "Fekete tükör", "Black Mirror", "A Mandalóri", "The Mandalorian", "Loki", "Fallout",
            "A fiúk", "The Boys", "Peaky Blinders", "Birmingham bandája", "Squid Game", "Nyerd meg az életed",
        }

    def _find_fuzzy_suggestion(self, query: str) -> Optional[str]:
        """Keresési elgépelések (pl. 'oppenhejmmer') intelligens javítása népszerű címek alapján."""
        import difflib
        q_clean = query.strip().lower()
        if len(q_clean) < 3:
            return None

        lookup = {t.lower(): t for t in self._popular_titles_pool}

        # 1. Teljes cím egyezés keresése
        matches = difflib.get_close_matches(q_clean, lookup.keys(), n=1, cutoff=0.55)
        if matches:
            return lookup[matches[0]]

        # 2. Rész-szó / szótöredék egyezés (pl. 'harri potter' -> 'Harry Potter')
        q_words = [w for w in q_clean.split() if len(w) >= 3]
        for w in q_words:
            sub_matches = difflib.get_close_matches(w, lookup.keys(), n=1, cutoff=0.6)
            if sub_matches:
                return lookup[sub_matches[0]]

        return None

    def _execute_search_queries(
        self, queries_to_run: List[str], candidates: dict[int, tuple[dict, MediaType]]
    ) -> None:
        """Lefuttatja a keresési lekérdezéseket a multi, tv és movie végpontokon."""
        endpoints = [
            (f"{self.BASE_URL}/search/multi", "multi"),
            (f"{self.BASE_URL}/search/tv", "tv"),
            (f"{self.BASE_URL}/search/movie", "movie"),
        ]
        for q in queries_to_run:
            for endpoint, mode in endpoints:
                params = {
                    "api_key": self.api_key,
                    "query": q,
                    "language": self.language,
                    "include_adult": "false",
                    "page": 1,
                }
                try:
                    response = self.client.get(endpoint, params=params)
                    if response.status_code != 200:
                        continue
                    data = response.json()
                    raw_items = data.get("results", [])

                    for item in raw_items:
                        media_type_str = mode if mode in ("movie", "tv") else item.get("media_type")
                        if media_type_str not in ("movie", "tv"):
                            continue

                        tmdb_id = item.get("id")
                        if not tmdb_id or tmdb_id in candidates:
                            continue

                        media_type = MediaType.MOVIE if media_type_str == "movie" else MediaType.TV
                        candidates[tmdb_id] = (item, media_type)

                except Exception as e:
                    logger.debug(f"Keresési hiba ({endpoint}): {e}")

    def _prune_caches(self):
        """Kordában tartja a memóriabeli gyorsítótárak méretét 24/7 futás esetén."""
        now = time.time()
        if len(self._discover_cache) > 120:
            self._discover_cache = {
                k: v for k, v in self._discover_cache.items()
                if (now - v[1]) < self._discover_ttl
            }
            if len(self._discover_cache) > 80:
                sorted_d = sorted(self._discover_cache.items(), key=lambda x: x[1][1], reverse=True)
                self._discover_cache = dict(sorted_d[:50])

        if len(self._search_cache) > 150:
            self._search_cache.clear()

        if len(self._details_cache) > 250:
            self._details_cache.clear()

    def _is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, max_results: int = 6) -> List[MediaItem]:
        """
        Film és sorozat keresése cím alapján.
        Kezeli az összetett szavakat (pl. "vámpír naplók" -> "vámpírnaplók"),
        a magyar számnévi átváltást (pl. "gladiátor kettő" -> "Gladiátor 2"),
        az ékezet nélküli formátumokat, és elgépelés esetén intelligens fuzzy javaslatot ad.
        """
        if not query or not query.strip():
            return []

        import unicodedata

        self.last_search_suggestion = None
        raw_query = query.strip()
        cache_key = raw_query.lower()
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        queries = [raw_query]

        # 1. camelCase felbontás
        split_camel = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw_query)
        if split_camel != raw_query and split_camel not in queries:
            queries.append(split_camel)

        # 2. Szóközök eltávolítása (magyar összetett szavakhoz, pl. "vámpír naplók" -> "vámpírnaplók")
        no_spaces = raw_query.replace(" ", "")
        if no_spaces != raw_query and len(no_spaces) >= 3 and no_spaces not in queries:
            queries.append(no_spaces)

        # 3. Kötőjeles változatok
        hyphen_to_space = raw_query.replace("-", " ")
        if hyphen_to_space != raw_query and hyphen_to_space not in queries:
            queries.append(hyphen_to_space)

        # 4. Ékezetek eltávolítása (pl. "vampir naplok", "vampirnaplok")
        deaccent = "".join(c for c in unicodedata.normalize("NFD", raw_query) if unicodedata.category(c) != "Mn")
        if deaccent != raw_query and deaccent not in queries:
            queries.append(deaccent)
            deaccent_no_space = deaccent.replace(" ", "")
            if deaccent_no_space not in queries:
                queries.append(deaccent_no_space)

        # 5. Magyar számnévi bővítés (pl. "gladiator ketto" -> "gladiator 2", "gladiator II")
        hu_numbers = {
            r"\belső\b": "1", r"\belso\b": "1", r"\begy\b": "1",
            r"\bmásodik\b": "2", r"\bmasodik\b": "2", r"\bkettő\b": "2", r"\bketto\b": "2",
            r"\bharmadik\b": "3", r"\bharom\b": "3", r"\bhárom\b": "3",
            r"\bnegyedik\b": "4", r"\bnegy\b": "4", r"\bnégy\b": "4",
            r"\bötödik\b": "5", r"\botodik\b": "5", r"\böt\b": "5", r"\bot\b": "5",
        }
        roman_map = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V"}
        for pat, num in hu_numbers.items():
            if re.search(pat, raw_query.lower()):
                num_q = re.sub(pat, num, raw_query.lower())
                if num_q not in queries:
                    queries.append(num_q)
                roman = roman_map.get(num)
                if roman:
                    rom_q = re.sub(pat, roman, raw_query.lower())
                    if rom_q not in queries:
                        queries.append(rom_q)

        candidates: dict[int, tuple[dict, MediaType]] = {}

        try:
            # 1. Alapvető és kibővített lekérdezések futtatása
            self._execute_search_queries(queries, candidates)

            # 2. Intelligens fallback: Ha nincs találat és több szóból áll a keresés
            if not candidates and len(raw_query.split()) >= 2:
                words = [
                    w for w in re.split(r"[\s._\-]+", raw_query)
                    if len(w) >= 3 and w.lower() not in {"a", "az", "the", "and", "es", "és", "film", "sorozat"}
                ]
                if len(words) >= 1:
                    fallback_q = " ".join(words[:2])
                    if fallback_q not in queries:
                        self._execute_search_queries([fallback_q], candidates)

            # 3. Intelligens elgépelés-javítás (Fuzzy Did-You-Mean)
            if not candidates:
                suggestion = self._find_fuzzy_suggestion(raw_query)
                if suggestion and suggestion.lower() != raw_query.lower():
                    self.last_search_suggestion = suggestion
                    logger.info(f"Fuzzy keresési javaslat aktiválva: '{raw_query}' -> '{suggestion}'")
                    self._execute_search_queries([suggestion], candidates)

            # Relevancia és népszerűség alapú pontozás
            def calculate_score(cand: tuple[dict, MediaType]) -> float:
                item, _ = cand

                title = (item.get("title") or item.get("name") or "").strip().lower()
                orig_title = (item.get("original_title") or item.get("original_name") or "").strip().lower()
                pop = float(item.get("popularity") or 0.0)
                vote_count = float(item.get("vote_count") or 0.0)

                score = pop + min(vote_count / 100.0, 50.0)

                for q in queries:
                    q_lower = q.lower().strip()
                    if q_lower == title or q_lower == orig_title:
                        score += 1000.0
                    elif q_lower in title or q_lower in orig_title:
                        score += 50.0

                return score

            sorted_candidates = sorted(candidates.values(), key=calculate_score, reverse=True)
            top_candidates = sorted_candidates[:max_results]

            results: List[MediaItem] = []
            for item, media_type in top_candidates:
                tmdb_id = item["id"]
                detailed_item = self.get_details(tmdb_id, media_type)
                if detailed_item:
                    results.append(detailed_item)
                    if detailed_item.title_hu:
                        self._popular_titles_pool.add(detailed_item.title_hu)
                    if detailed_item.title_en:
                        self._popular_titles_pool.add(detailed_item.title_en)
                else:
                    fallback_item = self._parse_basic_item(item, media_type)
                    if fallback_item:
                        results.append(fallback_item)
                        if fallback_item.title_hu:
                            self._popular_titles_pool.add(fallback_item.title_hu)

            self._prune_caches()
            self._search_cache[cache_key] = results
            return results

        except Exception as e:
            logger.error(f"Hiba a TMDB keresés során: {str(e)}")
            return []

    def get_details(self, tmdb_id: int, media_type: MediaType) -> Optional[MediaItem]:
        """
        Lekéri egy adott film vagy sorozat részletes adatait,
        beleértve az IMDb ID-t, rendezőt, színészeket, előzetest és műfajokat.
        """
        if not self._is_configured():
            return None

        cache_key = (tmdb_id, media_type.value)
        if cache_key in self._details_cache:
            return self._details_cache[cache_key]

        path_segment = "movie" if media_type == MediaType.MOVIE else "tv"
        endpoint = f"{self.BASE_URL}/{path_segment}/{tmdb_id}"
        params = {
            "api_key": self.api_key,
            "language": self.language,
            "append_to_response": "videos,external_ids,credits,translations",
        }

        try:
            response = self.client.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            item = self._parse_detailed_item(data, media_type)
            self._prune_caches()
            self._details_cache[cache_key] = item
            return item

        except Exception as e:
            logger.warning(f"Nem sikerült a részletes TMDB lekérés (ID: {tmdb_id}): {str(e)}")
            return None

    def get_by_imdb_id(self, imdb_id: str) -> Optional[MediaItem]:
        """
        Megkeresi a filmet vagy sorozatot IMDb azonosító (pl. tt1405406) alapján a TMDB-n.
        """
        if not self._is_configured() or not imdb_id:
            return None

        clean_imdb = imdb_id.strip()
        endpoint = f"{self.BASE_URL}/find/{clean_imdb}"
        params = {
            "api_key": self.api_key,
            "external_source": "imdb_id",
            "language": self.language,
        }
        try:
            resp = self.client.get(endpoint, params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            movie_res = data.get("movie_results", [])
            if movie_res:
                return self.get_details(movie_res[0]["id"], MediaType.MOVIE)
            tv_res = data.get("tv_results", [])
            if tv_res:
                return self.get_details(tv_res[0]["id"], MediaType.TV)
        except Exception as e:
            logger.debug(f"Hiba a TMDB IMDb find során ({clean_imdb}): {e}")
        return None

    def _parse_detailed_item(self, data: dict, media_type: MediaType) -> MediaItem:
        tmdb_id = data.get("id")
        external_ids = data.get("external_ids", {})
        imdb_id = external_ids.get("imdb_id") or data.get("imdb_id")
        orig_lang = data.get("original_language")
        orig_title = data.get("original_title") if media_type == MediaType.MOVIE else data.get("original_name")

        # Angol / nemzetközi cím feloldása a fordításokból
        en_title = None
        translations = data.get("translations", {}).get("translations", [])
        for trans in translations:
            if trans.get("iso_639_1") == "en":
                t_data = trans.get("data", {})
                cand_en = t_data.get("title") if media_type == MediaType.MOVIE else t_data.get("name")
                if cand_en and cand_en.strip():
                    en_title = cand_en.strip()
                    break

        if not en_title or orig_lang == "en":
            en_title = orig_title

        if media_type == MediaType.MOVIE:
            title_hu = data.get("title") or en_title or orig_title or "Ismeretlen film"
            title_en = en_title
            release_date = data.get("release_date") or ""
            seasons_count = None
            runtime_minutes = data.get("runtime")
        else:
            title_hu = data.get("name") or en_title or orig_title or "Ismeretlen sorozat"
            title_en = en_title
            release_date = data.get("first_air_date") or ""
            seasons_count = data.get("number_of_seasons")
            runtimes = data.get("episode_run_time") or []
            runtime_minutes = runtimes[0] if runtimes else None

        year = int(release_date.split("-")[0]) if release_date and release_date.split("-")[0].isdigit() else None

        # Poszter és háttérkép
        poster_path = data.get("poster_path")
        poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None

        backdrop_path = data.get("backdrop_path")
        backdrop_url = f"{self.BACKDROP_BASE_URL}{backdrop_path}" if backdrop_path else None

        # Műfajok
        genres = [g.get("name") for g in data.get("genres", []) if g.get("name")]

        # Stáb & Színészek
        credits = data.get("credits", {})
        cast = [actor.get("name") for actor in credits.get("cast", [])[:8] if actor.get("name")]

        director = None
        for crew_member in credits.get("crew", []):
            if crew_member.get("job") == "Director":
                director = crew_member.get("name")
                break

        # YouTube előzetes kinyerése
        videos = data.get("videos", {}).get("results", [])
        trailer_key = None
        for v in videos:
            if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser"):
                trailer_key = v.get("key")
                break

        # Ha magyar nyelven nincs trailer, lekérjük az angol nyelvű trailereket
        if not trailer_key:
            try:
                path_segment = "movie" if media_type == MediaType.MOVIE else "tv"
                v_resp = self.client.get(
                    f"{self.BASE_URL}/{path_segment}/{tmdb_id}/videos",
                    params={"api_key": self.api_key, "language": "en-US"},
                )
                if v_resp.status_code == 200:
                    en_vids = v_resp.json().get("results", [])
                    for v in en_vids:
                        if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser"):
                            trailer_key = v.get("key")
                            break
            except Exception:
                pass

        trailer_url = f"https://www.youtube.com/watch?v={trailer_key}" if trailer_key else None

        # Gyűjtemény (franchise/saga) kinyerése
        belongs_to = data.get("belongs_to_collection")
        collection_id = belongs_to.get("id") if isinstance(belongs_to, dict) else None
        collection_name = belongs_to.get("name") if isinstance(belongs_to, dict) else None

        return MediaItem(
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            media_type=media_type,
            title_hu=title_hu,
            title_en=title_en,
            original_title=orig_title,
            original_language=orig_lang,
            year=year,
            overview=data.get("overview") or "Nincs elérhető magyar leírás.",
            poster_url=poster_url,
            backdrop_url=backdrop_url,
            rating=round(data.get("vote_average", 0.0), 1) if data.get("vote_average") else None,
            genres=genres,
            director=director,
            cast=cast,
            number_of_seasons=seasons_count,
            trailer_url=trailer_url,
            runtime_minutes=runtime_minutes,
            collection_id=collection_id,
            collection_name=collection_name,
        )

    def _parse_basic_item(
        self, data: dict, media_type: MediaType, en_title: Optional[str] = None
    ) -> Optional[MediaItem]:
        tmdb_id = data.get("id")
        if not tmdb_id:
            return None

        orig_lang = data.get("original_language")
        orig_title = data.get("original_title") if media_type == MediaType.MOVIE else data.get("original_name")
        resolved_en = en_title or (orig_title if orig_lang == "en" else None) or orig_title

        if media_type == MediaType.MOVIE:
            title_hu = data.get("title") or resolved_en or orig_title or "Ismeretlen"
            title_en = resolved_en
            release_date = data.get("release_date") or ""
        else:
            title_hu = data.get("name") or resolved_en or orig_title or "Ismeretlen"
            title_en = resolved_en
            release_date = data.get("first_air_date") or ""

        year = int(release_date.split("-")[0]) if release_date and release_date.split("-")[0].isdigit() else None
        poster_path = data.get("poster_path")
        poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None
        backdrop_path = data.get("backdrop_path")
        backdrop_url = f"{self.BACKDROP_BASE_URL}{backdrop_path}" if backdrop_path else None

        # Műfajok kinyerése a genre_ids mezőből
        raw_genre_ids = data.get("genre_ids", [])
        genres = [TMDB_GENRE_MAP_HU[gid] for gid in raw_genre_ids if gid in TMDB_GENRE_MAP_HU]

        return MediaItem(
            tmdb_id=tmdb_id,
            imdb_id=None,
            media_type=media_type,
            title_hu=title_hu,
            title_en=title_en,
            original_title=orig_title,
            original_language=orig_lang,
            year=year,
            genres=genres,
            overview=data.get("overview") or "",
            poster_url=poster_url,
            backdrop_url=backdrop_url,
            rating=round(data.get("vote_average", 0.0), 1) if data.get("vote_average") else None,
        )

    def _fetch_media_list(
        self,
        endpoint: str,
        params: dict,
        media_type: MediaType,
        page: int = 1,
        limit: int = 12,
    ) -> List[MediaItem]:
        """
        Lekér egy listát a TMDB-ről a megadott végpontról és oldalszámról,
        és MediaItem objektumok listájává alakítja.
        """
        if not self._is_configured():
            return []
        try:
            req_params = {
                "api_key": self.api_key,
                "language": self.language,
                "region": "HU",
                "page": page,
                **params,
            }
            resp = self.client.get(f"{self.BASE_URL}{endpoint}", params=req_params)
            if resp.status_code != 200:
                logger.warning(f"TMDB lista hiba ({endpoint}, page={page}): {resp.status_code}")
                return []
            data = resp.json()
            raw_items = data.get("results", [])

            # Angol / nemzetközi címek feloldása, ha nem-angol vagy ázsiai tételek vannak az oldalon
            en_titles = {}
            if any(raw.get("original_language") not in ("en", None) for raw in raw_items):
                try:
                    en_req_params = {**req_params, "language": "en-US"}
                    en_resp = self.client.get(f"{self.BASE_URL}{endpoint}", params=en_req_params)
                    if en_resp.status_code == 200:
                        en_data = en_resp.json()
                        for en_raw in en_data.get("results", []):
                            if "id" in en_raw:
                                en_titles[en_raw["id"]] = en_raw.get("title") or en_raw.get("name")
                except Exception as e:
                    logger.debug(f"Hiba az angol címek lekérésekor: {e}")

            items = []
            for raw in raw_items:
                if len(items) >= limit:
                    break
                item_type = media_type
                if raw.get("media_type"):
                    item_type = MediaType.MOVIE if raw["media_type"] == "movie" else MediaType.TV
                parsed = self._parse_basic_item(raw, item_type, en_title=en_titles.get(raw.get("id")))
                if parsed:
                    items.append(parsed)
            return items
        except Exception as e:
            logger.error(f"Hiba a TMDB lista lekérésekor ({endpoint}, page={page}): {e}")
            return []

    def discover_media(
        self,
        media_type: MediaType = MediaType.MOVIE,
        genre_ids: Optional[List[int]] = None,
        sort_by: str = "popularity.desc",
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        page: int = 1,
        limit: int = 12,
        query: Optional[str] = None,
        with_watch_providers: Optional[str] = None,
        with_origin_country: Optional[str] = None,
        with_original_language: Optional[str] = None,
    ) -> List[MediaItem]:
        """
        Univerzális média felfedező és szűrő motor a TMDB-n.
        Támogatja: műfaj, rendezési elv, évjárat/korszak szűrés, szöveges kulcsszó, streaming szolgáltató,
        nemzetiség/származási ország, eredeti nyelv és dinamikus lapozás.
        """
        if not self._is_configured():
            return []

        # Gyorsítótár kulcs előállítása
        cache_key = (
            f"{media_type.value}|{genre_ids}|{sort_by}|{year_min}|{year_max}|{page}|{limit}|{query}|"
            f"{with_watch_providers}|{with_origin_country}|{with_original_language}"
        )
        now = time.time()
        if cache_key in self._discover_cache:
            cached_items, ts = self._discover_cache[cache_key]
            if now - ts < self._discover_ttl:
                return cached_items

        # 1. Ha konkrét keresőszó van megadva a kategórián belül
        if query and query.strip():
            path = "/search/movie" if media_type == MediaType.MOVIE else "/search/tv"
            params = {
                "query": query.strip(),
                "page": page,
                "include_adult": "false",
            }
            if year_min and media_type == MediaType.MOVIE:
                params["primary_release_year"] = year_min
            elif year_min and media_type == MediaType.TV:
                params["first_air_date_year"] = year_min

            # A TMDB /search végpont nem támogatja a with_original_language és with_genres szűrést,
            # ezért nagyobb limitet (30) kérünk le, és kliensoldalon szűrjük a találatokat
            raw_res = self._fetch_media_list(path, params, media_type, page=page, limit=30)

            # Kliensoldali nyelvi szűrés ha meg van adva
            if with_original_language:
                allowed_langs = {l.strip().lower() for l in str(with_original_language).split("|") if l.strip()}
                raw_res = [it for it in raw_res if (it.original_language or "").lower() in allowed_langs]

            # Kliensoldali műfaji szűrés ha meg van adva
            if genre_ids:
                expected_genres = {TMDB_GENRE_MAP_HU[gid].lower() for gid in genre_ids if gid in TMDB_GENRE_MAP_HU}
                raw_res = [it for it in raw_res if any(g.lower() in expected_genres for g in it.genres)]

            res = raw_res[:limit]
            self._prune_caches()
            self._discover_cache[cache_key] = (res, now)
            return res

        # 2. Általános discover végpont
        path = "/discover/movie" if media_type == MediaType.MOVIE else "/discover/tv"
        params = {
            "sort_by": sort_by,
            "page": page,
            "include_adult": "false",
        }

        # Nemzetiség / Származási ország és eredeti nyelv szűrés
        if with_origin_country:
            params["with_origin_country"] = str(with_origin_country)
        if with_original_language:
            lang_str = str(with_original_language).strip()
            # A TMDB with_original_language csak egyetlen 2 betűs ISO-639-1 kódot támogat!
            if "|" not in lang_str and len(lang_str) == 2:
                params["with_original_language"] = lang_str
            elif not with_origin_country:
                # Ha nincs országkód, az első érvényes 2 betűs nyelvkódot használjuk
                first_lang = lang_str.split("|")[0].strip()
                if len(first_lang) == 2:
                    params["with_original_language"] = first_lang

        # Minőségi szűrés: a legmagasabb értékelésnél csak a releváns szavazatszámúakat nézzük
        if sort_by.startswith("vote_average"):
            params["vote_count.gte"] = 50 if media_type == MediaType.MOVIE else 20

        # Idő szerinti rendezés (legújabb kiadások elöl):
        # Ha nincs beállítva felső évkorlát, korlátozzuk a mai napra, hogy a valóban megjelent/aktuális filmek jöjjenek (ne 2030-as bejelentések)
        if "release_date.desc" in sort_by or "air_date.desc" in sort_by:
            today_str = datetime.date.today().isoformat()
            if not year_max:
                if media_type == MediaType.MOVIE:
                    params["primary_release_date.lte"] = today_str
                else:
                    params["first_air_date.lte"] = today_str
            if "vote_count.gte" not in params:
                params["vote_count.gte"] = 5  # szűrjük ki a leírás/szavazat nélküli szellemadatlapokat

        # Idő szerinti rendezés (klasszikusok / legrégebbiek elöl):
        if "release_date.asc" in sort_by or "air_date.asc" in sort_by:
            if "vote_count.gte" not in params:
                params["vote_count.gte"] = 40  # valódi, ismert klasszikusok megjelenítése

        if genre_ids:
            params["with_genres"] = ",".join(str(g) for g in genre_ids)

        # Streaming szolgáltató szűrés (Netflix, HBO Max, Disney+, stb.)
        if with_watch_providers:
            params["with_watch_providers"] = str(with_watch_providers)
            params["watch_region"] = "HU"

        if media_type == MediaType.MOVIE:
            if year_min:
                params["primary_release_date.gte"] = f"{year_min}-01-01"
            if year_max:
                params["primary_release_date.lte"] = f"{year_max}-12-31"
        else:
            if year_min:
                params["first_air_date.gte"] = f"{year_min}-01-01"
            if year_max:
                params["first_air_date.lte"] = f"{year_max}-12-31"

        res = self._fetch_media_list(path, params, media_type, page=page, limit=limit)
        self._prune_caches()
        self._discover_cache[cache_key] = (res, now)
        return res

    def get_similar(
        self, tmdb_id: int, media_type: MediaType = MediaType.MOVIE, limit: int = 8
    ) -> List[MediaItem]:
        """
        Lekéri a filmhez vagy sorozathoz hasonló tartalmakat a TMDB ajánló motorjából.
        """
        if not self._is_configured():
            return []
        path_seg = "movie" if media_type == MediaType.MOVIE else "tv"
        items = self._fetch_media_list(f"/{path_seg}/{tmdb_id}/recommendations", {}, media_type, page=1, limit=limit)
        if not items:
            items = self._fetch_media_list(f"/{path_seg}/{tmdb_id}/similar", {}, media_type, page=1, limit=limit)
        return items

    def get_collection_details(self, collection_id: int) -> Dict[str, Any]:
        """
        Lekéri egy teljes film-gyűjtemény (franchise/saga, pl. Harry Potter, Gyűrűk Ura)
        összes részét időrendi sorrendben.
        """
        if not self._is_configured() or not collection_id:
            return {}

        endpoint = f"{self.BASE_URL}/collection/{collection_id}"
        params = {
            "api_key": self.api_key,
            "language": self.language,
        }
        try:
            resp = self.client.get(endpoint, params=params)
            if resp.status_code != 200:
                return {}
            data = resp.json()
            raw_parts = data.get("parts", [])
            parts: List[MediaItem] = []
            for r in raw_parts:
                item = self._parse_basic_item(r, MediaType.MOVIE)
                if item:
                    parts.append(item)

            # Időrendbe rendezés a kiadási év szerint
            parts.sort(key=lambda x: x.year or 9999)

            poster_path = data.get("poster_path")
            poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None

            backdrop_path = data.get("backdrop_path")
            backdrop_url = f"{self.BACKDROP_BASE_URL}{backdrop_path}" if backdrop_path else None

            return {
                "id": collection_id,
                "name": data.get("name") or "Filmgyűjtemény",
                "overview": data.get("overview") or "",
                "poster_url": poster_url,
                "backdrop_url": backdrop_url,
                "parts": parts,
                "count": len(parts),
            }
        except Exception as e:
            logger.error(f"Hiba a filmgyűjtemény lekérésekor (ID: {collection_id}): {e}")
            return {}

    def get_person_credits(self, person_name: str, limit: int = 12) -> Dict[str, Any]:
        """
        Megkeresi a rendező vagy színész adatait és a legkiemelkedőbb filmjeit/sorozatait.
        """
        if not self._is_configured() or not person_name or not person_name.strip():
            return {}

        clean_name = person_name.strip()
        try:
            s_resp = self.client.get(
                f"{self.BASE_URL}/search/person",
                params={"api_key": self.api_key, "query": clean_name, "language": self.language},
            )
            if s_resp.status_code != 200:
                return {}
            s_data = s_resp.json()
            persons = s_data.get("results", [])
            if not persons:
                return {}

            person = persons[0]
            person_id = person["id"]
            name = person.get("name") or clean_name
            dept = person.get("known_for_department") or "Színész / Rendező"
            profile_path = person.get("profile_path")
            profile_url = f"{self.IMAGE_BASE_URL}{profile_path}" if profile_path else None

            c_resp = self.client.get(
                f"{self.BASE_URL}/person/{person_id}/combined_credits",
                params={"api_key": self.api_key, "language": self.language},
            )
            items: List[MediaItem] = []
            if c_resp.status_code == 200:
                c_data = c_resp.json()
                raw_list = c_data.get("crew", []) if dept == "Directing" else c_data.get("cast", [])
                if not raw_list:
                    raw_list = c_data.get("cast", []) + c_data.get("crew", [])

                raw_list.sort(key=lambda x: float(x.get("vote_count") or 0.0) + float(x.get("popularity") or 0.0), reverse=True)

                seen_ids = set()
                for r in raw_list:
                    m_id = r.get("id")
                    if m_id and m_id not in seen_ids:
                        seen_ids.add(m_id)
                        m_type = MediaType.MOVIE if r.get("media_type") == "movie" else MediaType.TV
                        parsed = self._parse_basic_item(r, m_type)
                        if parsed and parsed.poster_url:
                            items.append(parsed)
                        if len(items) >= limit:
                            break

            return {
                "id": person_id,
                "name": name,
                "department": dept,
                "profile_url": profile_url,
                "items": items,
            }
        except Exception as e:
            logger.error(f"Hiba a személy filmográfia lekérésekor ({person_name}): {e}")
            return {}

    def get_war_media(
        self,
        media_type: MediaType = MediaType.MOVIE,
        page: int = 1,
        limit: int = 12,
        sort_by: str = "popularity.desc",
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        query: Optional[str] = None,
    ) -> List[MediaItem]:
        """
        Kifejezetten háborús filmek és sorozatok lekérése (TMDB War: 10752 / War & Politics: 10768).
        """
        war_genre = 10752 if media_type == MediaType.MOVIE else 10768
        return self.discover_media(
            media_type=media_type,
            genre_ids=[war_genre],
            sort_by=sort_by,
            year_min=year_min,
            year_max=year_max,
            page=page,
            limit=limit,
            query=query,
        )

    def get_popular_movies(self, page: int = 1, limit: int = 12) -> List[MediaItem]:
        """A népszerű/felkapott filmek listája lapozással."""
        return self._fetch_media_list("/movie/popular", {}, MediaType.MOVIE, page=page, limit=limit)

    def get_popular_series(self, page: int = 1, limit: int = 12) -> List[MediaItem]:
        """A népszerű/felkapott sorozatok listája lapozással."""
        return self._fetch_media_list("/tv/popular", {}, MediaType.TV, page=page, limit=limit)

    def get_top_rated(
        self, media_type: MediaType = MediaType.MOVIE, page: int = 1, limit: int = 12
    ) -> List[MediaItem]:
        """A legjobbra értékelt filmek vagy sorozatok lapozással."""
        path = "/movie/top_rated" if media_type == MediaType.MOVIE else "/tv/top_rated"
        return self._fetch_media_list(path, {}, media_type, page=page, limit=limit)

    def get_by_genre(
        self,
        genre_id: int,
        media_type: MediaType = MediaType.MOVIE,
        page: int = 1,
        limit: int = 12,
        sort_by: str = "popularity.desc",
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        query: Optional[str] = None,
    ) -> List[MediaItem]:
        """Műfaj szerinti felfedezés dinamikus szűréssel és lapozással."""
        return self.discover_media(
            media_type=media_type,
            genre_ids=[genre_id],
            sort_by=sort_by,
            year_min=year_min,
            year_max=year_max,
            page=page,
            limit=limit,
            query=query,
        )

