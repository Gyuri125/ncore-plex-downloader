import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.autostart import AutostartManager
from src.core.watchlist import WatchlistManager
from src.models.media import MediaItem, MediaType, TorrentRelease
from src.services.tmdb_service import TMDBService
from src.core.orchestrator import Orchestrator


def test_watchlist_manager(tmp_path):
    storage_file = tmp_path / "watchlist_test.json"
    mgr = WatchlistManager(data_file=storage_file)
    assert len(mgr.get_all()) == 0

    item = MediaItem(
        tmdb_id=123,
        title_hu="Teszt Film",
        title_en="Test Movie",
        year=2024,
        media_type=MediaType.MOVIE,
        overview="Leírás",
    )

    # Hozzáadás
    assert mgr.add(item) is True
    assert mgr.is_in_watchlist(123) is True
    assert len(mgr.get_all()) == 1

    # Duplikáció kiszűrése
    assert mgr.add(item) is False

    # Toggle (kivétel)
    is_now_in = mgr.toggle(item)
    assert is_now_in is False
    assert mgr.is_in_watchlist(123) is False

    # Toggle (visszarakás)
    is_now_in = mgr.toggle(item)
    assert is_now_in is True
    assert mgr.is_in_watchlist(123) is True

    # Törlés
    assert mgr.remove(123) is True
    assert mgr.is_in_watchlist(123) is False


def test_autostart_manager(tmp_path):
    test_startup_file = tmp_path / "Startup" / AutostartManager.FILE_NAME
    with patch.object(AutostartManager, "get_startup_file", return_value=test_startup_file):
        assert AutostartManager.is_autostart_enabled() is False

        dummy_exe = tmp_path / "test.exe"
        dummy_exe.touch()

        # Bekapcsolás
        assert AutostartManager.set_autostart(True, exe_path=dummy_exe) is True
        assert test_startup_file.exists()
        assert AutostartManager.is_autostart_enabled() is True
        content = test_startup_file.read_text(encoding="utf-8")
        assert "start \"\" /min" in content
        assert str(dummy_exe) in content

        # Kikapcsolás
        assert AutostartManager.set_autostart(False) is True
        assert not test_startup_file.exists()
        assert AutostartManager.is_autostart_enabled() is False


def test_tmdb_recommendations():
    tmdb = TMDBService(api_key="mock_key")
    mock_results = {
        "results": [
            {
                "id": 101,
                "title": "Népszerű Film",
                "original_title": "Popular Movie",
                "release_date": "2024-05-01",
                "overview": "Teszt összefoglaló",
                "vote_average": 8.5,
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_results

    with patch.object(tmdb.client, "get", return_value=mock_resp):
        movies = tmdb.get_popular_movies(limit=5)
        assert len(movies) == 1
        assert movies[0].tmdb_id == 101
        assert movies[0].media_type == MediaType.MOVIE
        assert movies[0].title_hu == "Népszerű Film"

        series = tmdb.get_popular_series(limit=5)
        assert len(series) == 1
        assert series[0].media_type == MediaType.TV

        genre_items = tmdb.get_by_genre(genre_id=28, media_type=MediaType.MOVIE, limit=5)
        assert len(genre_items) == 1


def test_orchestrator_storage_and_hnr_stats(tmp_path):
    with patch("src.core.orchestrator.get_settings") as mock_settings:
        s = MagicMock()
        s.DOWNLOAD_TEMP_DIR = tmp_path / "temp"
        s.PLEX_MOVIES_DIR = tmp_path / "movies"
        s.PLEX_SERIES_DIR = tmp_path / "series"
        s.MIN_SEED_TIME_HOURS = 48
        s.TARGET_RATIO = 1.0
        mock_settings.return_value = s

        orch = Orchestrator()
        orch._tasks = {
            "task_1": {
                "plex_organized": True,
                "release": {"size_bytes": 5 * (1024 ** 3)},  # 5 GB
            },
            "task_2": {
                "plex_organized": True,
                "release": {"size_bytes": 3 * (1024 ** 3)},  # 3 GB
            },
        }

        mock_active = [
            {"name": "Torrent 1", "is_completed": True, "seeding_time_hours": 30.0, "ratio": 0.8},
            {"name": "Torrent 2", "is_completed": True, "seeding_time_hours": 50.0, "ratio": 1.2},
            {"name": "Torrent 3", "is_completed": False, "seeding_time_hours": 0.0, "ratio": 0.0},
        ]
        orch.torrent_service.list_active_downloads = MagicMock(return_value=mock_active)

        stats = orch.get_storage_and_hnr_stats()
        assert stats["saved_human"] == "8.0 GB"
        assert stats["organized_count"] == 2
        assert stats["seeding_count"] == 2
        assert stats["hnr_met_count"] == 1  # Csak a Torrent 2 érte el a 48h / 1.0 arányt
        assert stats["total_seed_hours"] == 80.0


def test_release_specs_parsing():
    # 1. 4K UHD Dolby Vision Atmos
    rel1 = TorrentRelease(
        torrent_id="111",
        title="Gladiator.II.2024.2160p.UHD.HDR.DV.TrueHD.Atmos.7.1.x265.HUN.ENG-FLUX",
        category="hd_hun",
        size_bytes=18 * (1024 ** 3),
        size_human="18.0 GB",
        seeders=45,
        has_hungarian_audio=True,
    )
    assert rel1.specs["resolution"] == "2160p (4K UHD)"
    assert rel1.specs["hdr"] == "Dolby Vision + HDR"
    assert rel1.specs["audio"] == "Dolby Atmos"
    assert rel1.specs["codec"] == "HEVC / x265"
    assert rel1.is_cam is False

    # 2. 1080p DDP 5.1 WEB-DL
    rel2 = TorrentRelease(
        torrent_id="222",
        title="Deadpool.and.Wolverine.2024.1080p.WEB-DL.DDP5.1.H.264.HUN-BiRD",
        category="hd_hun",
        size_bytes=4 * (1024 ** 3),
        size_human="4.0 GB",
        seeders=120,
        has_hungarian_audio=True,
    )
    assert rel2.specs["resolution"] == "1080p (Full HD)"
    assert rel2.specs["source"] == "WEB-DL"
    assert rel2.specs["audio"] == "Dolby Digital Plus 5.1 (DDP)"
    assert rel2.is_cam is False

    # 3. CAM release
    rel3 = TorrentRelease(
        torrent_id="333",
        title="Venom.The.Last.Dance.2024.CAM.XViD-HUN",
        category="xvid_hun",
        size_bytes=1 * (1024 ** 3),
        size_human="1.4 GB",
        seeders=5,
        has_hungarian_audio=True,
    )
    assert rel3.is_cam is True
    assert rel3.specs["source"] == "CAM (Mozis)"

    # 4. Series pack
    rel4 = TorrentRelease(
        torrent_id="444",
        title="The.Vampire.Diaries.S01-S08.1080p.WEB-DL.HUN-FLUX",
        category="sorozat_hun",
        size_bytes=80 * (1024 ** 3),
        size_human="80.0 GB",
        seeders=25,
        has_hungarian_audio=True,
    )
    assert rel4.is_pack is True
    assert rel4.season_num == -1


def test_check_disk_space_and_summary(tmp_path):
    with patch("src.core.orchestrator.get_settings") as mock_settings:
        s = MagicMock()
        s.DOWNLOAD_TEMP_DIR = tmp_path
        s.PLEX_MOVIES_DIR = tmp_path
        s.PLEX_SERIES_DIR = tmp_path
        mock_settings.return_value = s

        orch = Orchestrator()

        # Mock disk usage: 20 GB free
        with patch("shutil.disk_usage") as mock_usage:
            # total, used, free
            mock_usage.return_value = MagicMock(free=20 * (1024 ** 3))

            small_rel = TorrentRelease(
                torrent_id="s1",
                title="Small.Movie.1080p.WEB-DL-HUN",
                category="hd_hun",
                size_bytes=5 * (1024 ** 3),  # 5 GB (5 + 10 = 15 GB <= 20 GB -> has enough)
            )
            res_small = orch.check_disk_space_for_release(small_rel, safety_margin_gb=10.0)
            assert res_small["has_enough"] is True
            assert res_small["required_gb"] == 5.0

            huge_rel = TorrentRelease(
                torrent_id="h1",
                title="Huge.4K.Movie.2160p.Remux-HUN",
                category="hd_hun",
                size_bytes=15 * (1024 ** 3),  # 15 GB (15 + 10 = 25 GB > 20 GB -> shortage)
            )
            res_huge = orch.check_disk_space_for_release(huge_rel, safety_margin_gb=10.0)
            assert res_huge["has_enough"] is False
            assert res_huge["shortage_gb"] == 5.0


def test_series_pack_and_seasons(tmp_path):
    with patch("src.core.orchestrator.get_settings") as mock_settings:
        s = MagicMock()
        s.DOWNLOAD_TEMP_DIR = tmp_path
        s.PLEX_MOVIES_DIR = tmp_path
        s.PLEX_SERIES_DIR = tmp_path
        mock_settings.return_value = s

        orch = Orchestrator()

        item = MediaItem(
            tmdb_id=999,
            title_hu="Teszt Sorozat",
            media_type=MediaType.TV,
        )

        mock_releases = [
            TorrentRelease(
                torrent_id="p1",
                title="Teszt.Sorozat.S01-S03.Complete.1080p-HUN",
                category="sorozat_hun",
                seeders=20,
                has_hungarian_audio=True,
            ),
            TorrentRelease(
                torrent_id="s1",
                title="Teszt.Sorozat.S01.1080p-HUN",
                category="sorozat_hun",
                seeders=15,
                has_hungarian_audio=True,
            ),
        ]
        orch.get_available_releases = MagicMock(return_value=mock_releases)
        orch.download_specific_release = MagicMock(return_value={"success": True, "message": "OK"})

        # Teljes sorozat letöltése
        res_all = orch.download_all_available_seasons(item)
        assert res_all["success"] is True

        # S01 pack letöltése
        res_s1 = orch.download_season_pack(item, season_num=1)
        assert res_s1["success"] is True

