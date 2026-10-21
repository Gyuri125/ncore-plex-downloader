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

    def __init__(self, api_key: Optional[str] = None, language: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key or settings.TMDB_API_KEY
        self.language = language or settings.TMDB_LANGUAGE

    def _is_configured(self) -> bool:
        return bool(self.api_key and self.api_key != "ide_ird_a_tmdb_api_kulcsodat")

    def search(self, query: str, max_results: int = 6) -> List[MediaItem]:
        """
        Film és sorozat keresése cím alapján.
        Visszaadja a találatokat magyar címmel, leírással, poszterrel és IMDb kóddal.
        """
        if not query or not query.strip():
            return []

        if not self._is_configured():
            logger.warning("TMDB API kulcs nincs beállítva a .env fájlban!")
            return []

        endpoint = f"{self.BASE_URL}/search/multi"
        params = {
            "api_key": self.api_key,
            "query": query.strip(),
            "language": self.language,
            "include_adult": "false",
            "page": 1,
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(endpoint, params=params)
                response.raise_for_status()
                data = response.json()

            results: List[MediaItem] = []
            raw_items = data.get("results", [])

            for item in raw_items:
                media_type_str = item.get("media_type")
                if media_type_str not in ("movie", "tv"):
                    continue

                media_type = MediaType.MOVIE if media_type_str == "movie" else MediaType.TV
                tmdb_id = item.get("id")

                # Részletes adatok lekérése (IMDb ID, színészek, rendező miatt)
                detailed_item = self.get_details(tmdb_id, media_type)
                if detailed_item:
                    results.append(detailed_item)
                else:
                    # Alap fallback, ha a részletes hívás nem sikerül
                    fallback_item = self._parse_basic_item(item, media_type)
                    if fallback_item:
                        results.append(fallback_item)

                if len(results) >= max_results:
                    break

            return results

        except httpx.HTTPStatusError as e:
            logger.error(f"TMDB HTTP hiba: {e.response.status_code} - {e.response.text}")
            return []
        except Exception as e:
            logger.error(f"Hiba a TMDB keresés során: {str(e)}")
            return []

    def get_details(self, tmdb_id: int, media_type: MediaType) -> Optional[MediaItem]:
        """
        Lekéri egy adott film vagy sorozat részletes adatait,
        beleértve az IMDb ID-t, rendezőt, színészeket és műfajokat.
        """
        if not self._is_configured():
            return None

        path_segment = "movie" if media_type == MediaType.MOVIE else "tv"
        endpoint = f"{self.BASE_URL}/{path_segment}/{tmdb_id}"
        params = {
            "api_key": self.api_key,
            "language": self.language,
            "append_to_response": "external_ids,credits",
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(endpoint, params=params)
                response.raise_for_status()
                data = response.json()

            return self._parse_detailed_item(data, media_type)

        except Exception as e:
            logger.warning(f"Nem sikerült a részletes TMDB lekérés (ID: {tmdb_id}): {str(e)}")
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
        else:
            title_hu = data.get("name") or data.get("original_name") or "Ismeretlen sorozat"
            title_en = data.get("original_name")
            release_date = data.get("first_air_date") or ""
            seasons_count = data.get("number_of_seasons")

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
        cast = [actor.get("name") for actor in credits.get("cast", [])[:5] if actor.get("name")]

        director = None
        for crew_member in credits.get("crew", []):
            if crew_member.get("job") == "Director":
                director = crew_member.get("name")
                break

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
