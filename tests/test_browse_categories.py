import datetime
from unittest.mock import MagicMock
from src.models.media import MediaType
from src.services.tmdb_service import TMDBService
from src.ui.app import CATEGORY_GENRES, fetch_browse_items


def test_category_genres_integrity():
    """Ellenőrzi, hogy az új szétbontott kategóriák léteznek és érvényes műfaj ID-kat tartalmaznak."""
    assert "😱 Horror" in CATEGORY_GENRES
    assert "🔪 Thriller" in CATEGORY_GENRES
    assert "🕵️ Krimi" in CATEGORY_GENRES
    assert "🔍 Rejtély" in CATEGORY_GENRES
    assert "💥 Akció" in CATEGORY_GENRES
    assert "🤠 Kaland" in CATEGORY_GENRES
    assert "🚀 Sci-Fi" in CATEGORY_GENRES
    assert "🧙 Fantasy" in CATEGORY_GENRES
    assert "🎨 Animáció" in CATEGORY_GENRES
    assert "👶 Családi" in CATEGORY_GENRES
    assert "⚔️ Háborús" in CATEGORY_GENRES
    assert "📜 Történelmi" in CATEGORY_GENRES

    # Horror és Thriller különválasztva
    assert CATEGORY_GENRES["😱 Horror"][MediaType.MOVIE] == [27]
    assert CATEGORY_GENRES["🔪 Thriller"][MediaType.MOVIE] == [53]

    # Krimi és Rejtély különválasztva
    assert CATEGORY_GENRES["🕵️ Krimi"][MediaType.MOVIE] == [80]
    assert CATEGORY_GENRES["🔍 Rejtély"][MediaType.MOVIE] == [9648]


def test_fetch_browse_items_genre_routing():
    """Ellenőrzi a fetch_browse_items hívások routingját a különböző szétbontott kategóriákra."""
    orchestrator = MagicMock()
    orchestrator.get_popular_movies.return_value = ["popular_movies"]
    orchestrator.discover_media.return_value = ["discovered_items"]

    # 1. Felkapott alapértelmezetten
    res = fetch_browse_items(
        orchestrator=orchestrator,
        category="🔥 Felkapott",
        media_type=MediaType.MOVIE,
        sort_by="popularity.desc",
        year_min=None,
        year_max=None,
        query=None,
        page=1,
    )
    assert res == ["popular_movies"]
    assert orchestrator.get_popular_movies.called

    # 2. Horror kategória filmre
    orchestrator.reset_mock()
    fetch_browse_items(
        orchestrator=orchestrator,
        category="😱 Horror",
        media_type=MediaType.MOVIE,
        sort_by="popularity.desc",
        year_min=None,
        year_max=None,
        query=None,
        page=1,
    )
    orchestrator.discover_media.assert_called_once_with(
        media_type=MediaType.MOVIE,
        genre_ids=[27],
        sort_by="popularity.desc",
        year_min=None,
        year_max=None,
        page=1,
        limit=12,
        query=None,
        with_watch_providers=None,
        with_origin_country=None,
        with_original_language=None,
    )

    # 3. Krimi kategória Netflixre és legújabb rendezéssel
    orchestrator.reset_mock()
    fetch_browse_items(
        orchestrator=orchestrator,
        category="🕵️ Krimi",
        media_type=MediaType.MOVIE,
        sort_by="primary_release_date.desc",
        year_min=2020,
        year_max=None,
        query="Budapest",
        page=2,
        watch_provider="8",
    )
    orchestrator.discover_media.assert_called_once_with(
        media_type=MediaType.MOVIE,
        genre_ids=[80],
        sort_by="primary_release_date.desc",
        year_min=2020,
        year_max=None,
        page=2,
        limit=12,
        query="Budapest",
        with_watch_providers="8",
        with_origin_country=None,
        with_original_language=None,
    )

    # 4. Nemzetiség szűrés tesztelése (pl. Dél-Korea + Dráma)
    orchestrator.reset_mock()
    fetch_browse_items(
        orchestrator=orchestrator,
        category="🎭 Dráma",
        media_type=MediaType.MOVIE,
        sort_by="popularity.desc",
        year_min=None,
        year_max=None,
        query=None,
        page=1,
        with_origin_country="KR",
        with_original_language="ko",
    )
    orchestrator.discover_media.assert_called_once_with(
        media_type=MediaType.MOVIE,
        genre_ids=[18],
        sort_by="popularity.desc",
        year_min=None,
        year_max=None,
        page=1,
        limit=12,
        query=None,
        with_watch_providers=None,
        with_origin_country="KR",
        with_original_language="ko",
    )


def test_tmdb_discover_sorting_safeguards():
    """Ellenőrzi, hogy a TMDB discover_media megfelelően alkalmazza az idő és értékelés szerinti védelmi paramétereket."""
    tmdb = TMDBService(api_key="test_key")
    tmdb._fetch_media_list = MagicMock(return_value=[])

    # 1. Idő szerinti rendezés (legújabbak elöl): automatikus mai dátum plafon és vote_count.gte szűrés
    tmdb.discover_media(
        media_type=MediaType.MOVIE,
        sort_by="primary_release_date.desc",
        year_min=None,
        year_max=None,
    )
    args = tmdb._fetch_media_list.call_args[0]
    path, params = args[0], args[1]
    assert path == "/discover/movie"
    assert params["sort_by"] == "primary_release_date.desc"
    assert "primary_release_date.lte" in params
    assert params["primary_release_date.lte"] == datetime.date.today().isoformat()
    assert params.get("vote_count.gte") == 5

    # 2. Értékelés szerinti rendezés: releváns szavazatszám megkövetelése
    tmdb._fetch_media_list.reset_mock()
    tmdb.discover_media(
        media_type=MediaType.MOVIE,
        sort_by="vote_average.desc",
    )
    args = tmdb._fetch_media_list.call_args[0]
    params = args[1]
    assert params["sort_by"] == "vote_average.desc"
    assert params.get("vote_count.gte") == 50


def test_nationality_presets_and_english_title_parsing():
    """Ellenőrzi a nemzetiségi beállítások érvényességét és az angol cím kinyerést ázsiai filmeknél."""
    from src.ui.app import NATIONALITY_OPTIONS

    # Nemzetiségek léteznek
    assert "🇰🇷 Dél-Koreai (K-Mozi / K-Drama)" in NATIONALITY_OPTIONS
    assert "🇯🇵 Japán (Anime / J-Cinema)" in NATIONALITY_OPTIONS
    assert "🇭🇺 Magyar alkotások" in NATIONALITY_OPTIONS
    assert NATIONALITY_OPTIONS["🇰🇷 Dél-Koreai (K-Mozi / K-Drama)"]["country"] == "KR"
    assert NATIONALITY_OPTIONS["🇰🇷 Dél-Koreai (K-Mozi / K-Drama)"]["lang"] == "ko"
    assert NATIONALITY_OPTIONS["🇯🇵 Japán (Anime / J-Cinema)"]["country"] == "JP"
    assert NATIONALITY_OPTIONS["🇯🇵 Japán (Anime / J-Cinema)"]["lang"] == "ja"

    # TMDB angol cím kinyerésének ellenőrzése
    tmdb = TMDBService(api_key="test_key")

    # 1. Részletes koreai film fordítással (pl. Élősködők -> Parasite)
    mock_detailed_kr = {
        "id": 496243,
        "title": "Élősködők",
        "original_title": "기생충",
        "original_language": "ko",
        "release_date": "2019-05-30",
        "translations": {
            "translations": [
                {"iso_639_1": "en", "data": {"title": "Parasite"}},
                {"iso_639_1": "de", "data": {"title": "Parasite"}},
            ]
        },
    }
    item = tmdb._parse_detailed_item(mock_detailed_kr, MediaType.MOVIE)
    assert item.title_hu == "Élősködők"
    assert item.title_en == "Parasite"
    assert item.original_title == "기생충"
    assert item.original_language == "ko"

    # 2. Részletes angol film (pl. Inception)
    mock_detailed_en = {
        "id": 27205,
        "title": "Eredet",
        "original_title": "Inception",
        "original_language": "en",
        "release_date": "2010-07-15",
        "translations": {"translations": []},
    }
    item_en = tmdb._parse_detailed_item(mock_detailed_en, MediaType.MOVIE)
    assert item_en.title_hu == "Eredet"
    assert item_en.title_en == "Inception"
    assert item_en.original_title == "Inception"
    assert item_en.original_language == "en"

