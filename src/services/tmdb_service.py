import re
from typing import List, Optional
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

        self.client = httpx.Client(timeout=10.0, follow_redirects=True)
        self._search_cache: dict[str, List[MediaItem]] = {}
        self._details_cache: dict[tuple[int, str], MediaItem] = {}

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

    def get_popular_movies(self, limit: int = 10) -> List[MediaItem]:
        """Lekéri a népszerű filmeket Magyarországon."""
        return self._fetch_media_list(f"{self.BASE_URL}/movie/popular", MediaType.MOVIE, limit=limit, extra_params={"region": "HU"})

    def get_popular_series(self, limit: int = 10) -> List[MediaItem]:
        """Lekéri a népszerű sorozatokat."""
        return self._fetch_media_list(f"{self.BASE_URL}/tv/popular", MediaType.TV, limit=limit)

    def get_top_rated(self, media_type: MediaType = MediaType.MOVIE, limit: int = 10) -> List[MediaItem]:
        """Lekéri a legmagasabbra értékelt filmeket vagy sorozatokat."""
        path = "movie" if media_type == MediaType.MOVIE else "tv"
        return self._fetch_media_list(f"{self.BASE_URL}/{path}/top_rated", media_type, limit=limit)

    def get_by_genre(self, genre_id: int, media_type: MediaType = MediaType.MOVIE, limit: int = 10) -> List[MediaItem]:
        """Lekéri az adott műfaj legnépszerűbb filmjeit vagy sorozatait."""
        path = "movie" if media_type == MediaType.MOVIE else "tv"
        params = {
            "with_genres": genre_id,
            "sort_by": "popularity.desc",
        }
        return self._fetch_media_list(f"{self.BASE_URL}/discover/{path}", media_type, limit=limit, extra_params=params)

    def _fetch_media_list(
        self, endpoint: str, media_type: MediaType, limit: int = 10, extra_params: Optional[dict] = None
    ) -> List[MediaItem]:
        """Segédfüggvény TMDB lista végpontok lekéréséhez."""
        if not self._is_configured():
            return []
        params = {
            "api_key": self.api_key,
            "language": self.language,
            "page": 1,
        }
        if extra_params:
            params.update(extra_params)

        try:
            resp = self.client.get(endpoint, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            raw_results = data.get("results", [])[:limit]
            items = []
            for raw in raw_results:
                parsed = self._parse_basic_item(raw, media_type)
                if parsed and parsed.poster_url:
                    items.append(parsed)
            return items
        except Exception as e:
            logger.debug(f"Hiba a TMDB lista lekérésekor ({endpoint}): {e}")
            return []

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

        return MediaItem(
            tmdb_id=tmdb_id,
            imdb_id=None,
            media_type=media_type,
            title_hu=title_hu,
            title_en=title_en,
            year=year,
            overview=data.get("overview") or "",
            poster_url=poster_url,
            rating=round(data.get("vote_average", 0.0), 1) if data.get("vote_average") else None,
        )

    def _fetch_media_list(self, endpoint: str, params: dict, media_type: MediaType, limit: int = 8) -> List[MediaItem]:
        """Lekér egy listát a TMDB-ről (trending, népszerű, műfaj) és MediaItem listává alakítja."""
        try:
            req_params = {
                "api_key": self.api_key,
                "language": self.language,
                "region": "HU",
                **params,
            }
            resp = self.client.get(f"{self.BASE_URL}{endpoint}", params=req_params)
            if resp.status_code != 200:
                logger.warning(f"TMDB lista hiba ({endpoint}): {resp.status_code}")
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
            logger.error(f"Hiba a TMDB lista lekérésekor ({endpoint}): {e}")
            return []

    def get_popular_movies(self, limit: int = 8) -> List[MediaItem]:
        """A heti / legnépszerűbb filmek listája Magyarországon."""
        return self._fetch_media_list("/trending/movie/week", {}, MediaType.MOVIE, limit=limit)

    def get_popular_series(self, limit: int = 8) -> List[MediaItem]:
        """A heti legnépszerűbb sorozatok listája."""
        return self._fetch_media_list("/trending/tv/week", {}, MediaType.TV, limit=limit)

    def get_top_rated(self, media_type: MediaType = MediaType.MOVIE, limit: int = 8) -> List[MediaItem]:
        """A legjobbra értékelt filmek vagy sorozatok."""
        path = "/movie/top_rated" if media_type == MediaType.MOVIE else "/tv/top_rated"
        return self._fetch_media_list(path, {}, media_type, limit=limit)

    def get_by_genre(self, genre_id: int, media_type: MediaType = MediaType.MOVIE, limit: int = 8) -> List[MediaItem]:
        """Műfaj szerinti felfedezés (pl. Akció=28, Vígjáték=35, Animáció=16, Sci-Fi=878, Krimi=80)."""
        path = "/discover/movie" if media_type == MediaType.MOVIE else "/discover/tv"
        params = {
            "with_genres": str(genre_id),
            "sort_by": "popularity.desc",
        }
        return self._fetch_media_list(path, params, media_type, limit=limit)

