import re
import time
from typing import Any, Dict, List, Optional
import httpx
from loguru import logger

from src.config.settings import get_settings
from src.models.media import MediaItem, MediaType


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

    def _is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, max_results: int = 6) -> List[MediaItem]:
        """
        Film és sorozat keresése cím alapján.
        Kezeli az összetett szavakat (pl. "vámpír naplók" -> "vámpírnaplók"),
        ékezet nélküli és alternatív formátumokat, valamint sorozatokat és filmeket egyaránt.
        """
        if not query or not query.strip():
            return []

        import unicodedata

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

        candidates: dict[int, tuple[dict, MediaType]] = {}

        try:
            for q in queries:
                endpoints = [
                    (f"{self.BASE_URL}/search/multi", "multi"),
                    (f"{self.BASE_URL}/search/tv", "tv"),
                    (f"{self.BASE_URL}/search/movie", "movie"),
                ]

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
                else:
                    fallback_item = self._parse_basic_item(item, media_type)
                    if fallback_item:
                        results.append(fallback_item)

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
            "append_to_response": "videos,external_ids,credits",
        }

        try:
            response = self.client.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            item = self._parse_detailed_item(data, media_type)
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

        if media_type == MediaType.MOVIE:
            title_hu = data.get("title") or data.get("original_title") or "Ismeretlen film"
            title_en = data.get("original_title")
            release_date = data.get("release_date") or ""
            seasons_count = None
            runtime_minutes = data.get("runtime")
        else:
            title_hu = data.get("name") or data.get("original_name") or "Ismeretlen sorozat"
            title_en = data.get("original_name")
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

    def _parse_basic_item(self, data: dict, media_type: MediaType) -> Optional[MediaItem]:
        tmdb_id = data.get("id")
        if not tmdb_id:
            return None

        if media_type == MediaType.MOVIE:
            title_hu = data.get("title") or data.get("original_title") or "Ismeretlen"
            title_en = data.get("original_title")
            release_date = data.get("release_date") or ""
        else:
            title_hu = data.get("name") or data.get("original_name") or "Ismeretlen"
            title_en = data.get("original_name")
            release_date = data.get("first_air_date") or ""

        year = int(release_date.split("-")[0]) if release_date and release_date.split("-")[0].isdigit() else None
        poster_path = data.get("poster_path")
        poster_url = f"{self.IMAGE_BASE_URL}{poster_path}" if poster_path else None
        backdrop_path = data.get("backdrop_path")
        backdrop_url = f"{self.BACKDROP_BASE_URL}{backdrop_path}" if backdrop_path else None

        return MediaItem(
            tmdb_id=tmdb_id,
            imdb_id=None,
            media_type=media_type,
            title_hu=title_hu,
            title_en=title_en,
            year=year,
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
            items = []
            for raw in raw_items:
                if len(items) >= limit:
                    break
                item_type = media_type
                if raw.get("media_type"):
                    item_type = MediaType.MOVIE if raw["media_type"] == "movie" else MediaType.TV
                parsed = self._parse_basic_item(raw, item_type)
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
    ) -> List[MediaItem]:
        """
        Univerzális média felfedező és szűrő motor a TMDB-n.
        Támogatja: műfaj, rendezési elv, évjárat/korszak szűrés, szöveges kulcsszó, streaming szolgáltató és dinamikus lapozás.
        """
        if not self._is_configured():
            return []

        # Gyorsítótár kulcs előállítása
        cache_key = f"{media_type.value}|{genre_ids}|{sort_by}|{year_min}|{year_max}|{page}|{limit}|{query}|{with_watch_providers}"
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

            res = self._fetch_media_list(path, params, media_type, page=page, limit=limit)
            self._discover_cache[cache_key] = (res, now)
            return res

        # 2. Általános discover végpont
        path = "/discover/movie" if media_type == MediaType.MOVIE else "/discover/tv"
        params = {
            "sort_by": sort_by,
            "page": page,
            "include_adult": "false",
        }

        # Minőségi szűrés: a legmagasabb értékelésnél csak a releváns szavazatszámúakat nézzük
        if sort_by.startswith("vote_average"):
            params["vote_count.gte"] = 50

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

