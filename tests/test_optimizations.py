import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.core.movie_radar import MovieRadarManager
from src.core.orchestrator import Orchestrator
from src.core.organizer import MediaOrganizer
from src.models.media import MediaItem, MediaType, TorrentRelease
from src.services.plex_service import PlexService
from src.services.tmdb_service import TMDBService


def test_atomic_storage_and_recovery(tmp_path):
    target_file = tmp_path / "data.json"
    data1 = {"key": "value1"}
    data2 = {"key": "value2"}

    # 1. Mentés
    assert atomic_save_json(target_file, data1) is True
    assert load_json_with_recovery(target_file, dict) == data1

    # 2. Második mentés (.bak létrehozása)
    assert atomic_save_json(target_file, data2) is True
    bak_file = target_file.with_suffix(".json.bak")
    assert bak_file.exists()

    # 3. Fő fájl szándékos megrongálása
    target_file.write_text("CORRUPTED_JSON_CONTENT{{{", encoding="utf-8")
    recovered = load_json_with_recovery(target_file, dict)
    assert recovered in (data1, data2)
    # A fő fájl is helyreállt
    assert load_json_with_recovery(target_file, dict) in (data1, data2)


def test_plex_service_ttl_cache():
    plex = PlexService(server_url="http://mock:32400", token="mock_token")
    plex.server = MagicMock()

    mock_item = MagicMock()
    mock_item.title = "Dűne"
    mock_item.year = 2021
    plex.server.library.search.return_value = [mock_item]

    # Első hívás: lekéri a szervertől
    assert plex.has_media("Dűne", 2021) is True
    assert plex.server.library.search.call_count == 1

    # Második hívás: a memóriabeli gyorsítótárból szolgálja ki azonnal
    assert plex.has_media("Dűne", 2021) is True
    assert plex.server.library.search.call_count == 1

    # Gyorsítótár kiürítése
    plex.clear_cache()
    assert plex.has_media("Dűne", 2021) is True
    assert plex.server.library.search.call_count == 2


def test_tmdb_service_discover_cache():
    tmdb = TMDBService(api_key="mock_key")
    mock_item = MediaItem(tmdb_id=1, title_hu="Teszt", media_type=MediaType.MOVIE)

    with patch.object(tmdb, "_fetch_media_list", return_value=[mock_item]) as mock_fetch:
        res1 = tmdb.discover_media(page=1, limit=5)
        assert len(res1) == 1
        assert mock_fetch.call_count == 1

        # Második hívás: azonos paraméterekkel memóriából jön
        res2 = tmdb.discover_media(page=1, limit=5)
        assert len(res2) == 1
        assert mock_fetch.call_count == 1


def test_organizer_file_readiness(tmp_path):
    org = MediaOrganizer()

    # Befejezetlen kiterjesztés kiszűrése
    incomp_file = tmp_path / "movie.!qB"
    incomp_file.write_bytes(b"data")
    assert org._is_file_ready(incomp_file) is False

    # Üres fájl kiszűrése
    empty_file = tmp_path / "empty.mkv"
    empty_file.write_bytes(b"")
    assert org._is_file_ready(empty_file) is False

    # Kész videófájl elfogadása
    valid_file = tmp_path / "ready.mkv"
    valid_file.write_bytes(b"VALID_VIDEO_CONTENT" * 100)
    assert org._is_file_ready(valid_file) is True


def test_movie_radar_manager(tmp_path):
    radar_file = tmp_path / "radar.json"
    radar = MovieRadarManager(data_file=radar_file)

    item = MediaItem(tmdb_id=999, title_hu="Mozi Film", media_type=MediaType.MOVIE)
    assert radar.add(item) is True
    assert radar.is_monitored(999) is True
    assert len(radar.get_monitoring()) == 1

    # Ismételt hozzáadás False-t ad vissza
    assert radar.add(item) is False

    # Eltávolítás
    assert radar.remove(999) is True
    assert radar.is_monitored(999) is False


def test_orchestrator_parallel_batch_check():
    orch = Orchestrator()
    items = [
        MediaItem(tmdb_id=1, title_hu="Film 1", media_type=MediaType.MOVIE),
        MediaItem(tmdb_id=2, title_hu="Film 2", media_type=MediaType.MOVIE),
        MediaItem(tmdb_id=3, title_hu="Film 3", media_type=MediaType.MOVIE),
    ]

    mock_release = TorrentRelease(
        torrent_id="t1",
        title="Film.1.1080p.HUN",
        category="hd_hun",
        size_human="5 GB",
        seeders=10,
        leechers=1,
    )

    with patch.object(orch, "check_release_availability", return_value=[mock_release]):
        progress_events = []
        def _cb(done, total, it, rels):
            progress_events.append((done, total))

        results = orch.check_releases_batch(items, max_workers=3, callback=_cb)
        assert len(results) == 3
        assert len(progress_events) == 3
        assert results[1] == [mock_release]
