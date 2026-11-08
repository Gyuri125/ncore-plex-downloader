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
        orch._load_state = MagicMock(return_value=orch._tasks)

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


def test_confirm_before_download_setting():
    from src.config.settings import Settings
    s = Settings()
    assert hasattr(s, "CONFIRM_BEFORE_DOWNLOAD")
    assert isinstance(s.CONFIRM_BEFORE_DOWNLOAD, bool)


def test_organizer_atomic_cross_drive_copy(tmp_path):
    from src.core.organizer import MediaOrganizer
    organizer = MediaOrganizer()

    src_file = tmp_path / "source_movie.mkv"
    src_file.write_bytes(b"TEST_VIDEO_DATA" * 100)

    dst_dir = tmp_path / "plex_dest"
    dst_file = dst_dir / "target_movie.mkv"

    # Szimuláljuk, hogy az os.link nem sikerül (pl. különböző partíciók miatt)
    with patch("os.link", side_effect=OSError("Cross-device link")):
        success = organizer._place_file(src_file, dst_file)
        assert success is True
        assert dst_file.exists()
        assert dst_file.read_bytes() == src_file.read_bytes()
        # Átmeneti staging fájlnak már nem szabad léteznie
        staging = dst_dir / f".{dst_file.name}.transfer"
        assert not staging.exists()


def test_subtitle_service_feliratok_eu_and_subirat(tmp_path):
    from src.services.subtitle_service import SubtitleService
    from src.models.media import SubtitleItem
    service = SubtitleService()

    item = MediaItem(
        tmdb_id=12345,
        title_hu="Dűne Második Rész",
        title_en="Dune Part Two",
        media_type=MediaType.MOVIE,
    )
    rel = TorrentRelease(
        torrent_id="999",
        title="Dune.Part.Two.2024.1080p.MA.WEB-DL.DDP5.1.Atmos.H.264-FREEMEN",
        category="hd",
        size_bytes=5000,
        has_hungarian_audio=False,
    )

    # 1. Feliratok.eu találat teszt
    mock_feliratok_html = """
    <html><body><table>
        <tr>
            <td><a href="/index.php?action=letolt&fnev=Dune.Part.Two.2024.1080p.MA.WEB-DL.DDP5.1.Atmos.H.264-FREEMEN.hun.srt&felirat=123">Letöltés</a></td>
            <td>Dune Part Two 1080p WEB-DL</td>
        </tr>
    </table></body></html>
    """

    resp_feliratok = MagicMock()
    resp_feliratok.status_code = 200
    resp_feliratok.text = mock_feliratok_html
    resp_feliratok.raise_for_status = MagicMock()

    with patch.object(service.client, "get", return_value=resp_feliratok):
        sub = service.search_feliratok_info(item, rel)
        assert sub is not None
        assert "Dune.Part.Two.2024.1080p.MA.WEB-DL" in sub.download_url
        assert sub.source == "Feliratok.eu"

    # 2. Subirat.net találat teszt (ha a Feliratok.eu nem ad találatot)
    mock_subirat_search_html = """
    <html><body>
        <a href="https://subirat.net/t/dune-part-two">Dűne 2 adatlap</a>
    </body></html>
    """
    mock_subirat_title_html = """
    <html><body>
        <a href="http://adf.ly/123/https://subirat.net/t/dune-part-two/ad.srt">Reklám link</a>
        <a href="https://subirat.net/t/dune-part-two/uploader/Dune.Part.Two.2024.1080p.WEB-DL.srt">Valódi felirat</a>
    </body></html>
    """

    def mock_subirat_get(url, params=None, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "pages/kereso2" in str(url):
            resp.text = mock_subirat_search_html
        elif "/t/dune-part-two" in str(url):
            resp.text = mock_subirat_title_html
        return resp

    with patch.object(service.client, "get", side_effect=mock_subirat_get):
        sub_s = service.search_subirat(item, rel)
        assert sub_s is not None
        assert "adf.ly" not in sub_s.download_url
        assert "Dune.Part.Two.2024.1080p.WEB-DL.srt" in sub_s.download_url
        assert sub_s.source == "Subirat.net"


def test_subtitle_radar_manager_lifecycle(tmp_path):
    from src.core.subtitle_radar import SubtitleRadarManager

    radar_file = tmp_path / "subtitle_radar_test.json"
    mgr = SubtitleRadarManager(data_file=radar_file)

    assert len(mgr.get_all()) == 0
    assert len(mgr.get_monitoring()) == 0

    item = MediaItem(
        tmdb_id=555,
        title_hu="Gladiátor 2",
        title_en="Gladiator II",
        year=2024,
        media_type=MediaType.MOVIE,
    )
    rel = TorrentRelease(
        torrent_id="t555",
        title="Gladiator.II.2024.1080p.WEB-DL-FLUX",
        category="hd",
        size_bytes=4000,
        has_hungarian_audio=False,
    )

    plex_dir = tmp_path / "Gladiator II (2024)"
    plex_dir.mkdir(parents=True)
    video_file = plex_dir / "Gladiator II (2024) [1080p].mkv"
    video_file.write_bytes(b"VIDEO_CONTENT")

    # 1. Hozzáadás
    assert mgr.add(item, release=rel, destination_folder=str(plex_dir), destination_files=[video_file.name], task_id="task_g2") is True
    assert mgr.is_monitored(555) is True
    assert len(mgr.get_monitoring()) == 1

    # 2. Újra hozzáadás (nem duplikálódik)
    assert mgr.add(item, release=rel) is False
    assert len(mgr.get_monitoring()) == 1

    # 3. Ellenőrzés és találat szimuláció
    mock_orch = MagicMock()
    mock_orch.settings.DOWNLOAD_TEMP_DIR = tmp_path
    mock_orch.organizer = MagicMock()
    mock_orch.organizer.VIDEO_EXTENSIONS = [".mkv", ".mp4"]
    mock_orch.organizer._sanitize_filename = lambda s: s
    mock_orch._tasks = {"task_g2": {"has_subtitle": False}}

    mock_sub_path = tmp_path / "downloaded_sub.srt"
    mock_sub_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nTeszt felirat\n", encoding="utf-8")

    mock_sub_item = MagicMock()
    mock_sub_item.source = "Feliratok.eu"
    mock_orch.subtitle_service.last_subtitle_item = mock_sub_item
    mock_orch.subtitle_service.find_and_download_subtitle.return_value = mock_sub_path

    hits = mgr.check_and_download_subtitles(mock_orch)
    assert len(hits) == 1
    assert hits[0]["title"] == "Gladiátor 2"
    assert hits[0]["source"] == "Feliratok.eu"

    # Ellenőrizzük, hogy a felirat resolved lett és átkerült a Plex mappába
    assert len(mgr.get_monitoring()) == 0
    assert len(mgr.get_resolved()) == 1
    assert (plex_dir / f"{video_file.stem}.hu.srt").exists()

    # 4. Készek ürítése
    mgr.clear_resolved()
    assert len(mgr.get_all()) == 0


def test_tmdb_fuzzy_search_and_numbers():
    from src.services.tmdb_service import TMDBService
    service = TMDBService()

    # 1. Fuzzy javaslat tesztelése
    suggestion = service._find_fuzzy_suggestion("oppenhejmmer")
    assert suggestion == "Oppenheimer"

    # 2. Számnév kiterjesztés és keresés mock
    with patch.object(service, "_execute_search_queries") as mock_exec:
        service.search("gladiator ketto")
        # Ellenőrizzük, hogy az első hívás lekérdezései között szerepel a "gladiator 2" vagy "gladiator II"
        first_call_queries = mock_exec.call_args_list[0][0][0]
        assert "gladiator 2" in first_call_queries or "gladiator II" in first_call_queries


def test_audio_radar_manager_lifecycle(tmp_path):
    from src.core.audio_radar import AudioRadarManager

    radar_file = tmp_path / "audio_radar_test.json"
    mgr = AudioRadarManager(data_file=radar_file)

    assert len(mgr.get_all()) == 0
    assert len(mgr.get_monitoring()) == 0

    item = MediaItem(
        tmdb_id=888,
        title_hu="Dűne: Második rész",
        title_en="Dune: Part Two",
        year=2024,
        media_type=MediaType.MOVIE,
    )
    eng_rel = TorrentRelease(
        torrent_id="t_eng",
        title="Dune.Part.Two.2024.1080p.WEB-DL-ENG",
        category="hd",
        size_bytes=5000,
        has_hungarian_audio=False,
    )

    # 1. Hozzáadás
    assert mgr.add(item, current_release=eng_rel, destination_folder=str(tmp_path), task_id="task_dune") is True
    assert mgr.is_monitored(888) is True
    assert len(mgr.get_monitoring()) == 1

    # 2. Újra hozzáadás (nem duplikálódik)
    assert mgr.add(item, current_release=eng_rel) is False
    assert len(mgr.get_monitoring()) == 1

    # 3. Találat szimuláció (megjelenik a HD magyar szinkron)
    hun_rel = TorrentRelease(
        torrent_id="t_hun",
        title="Dune.Part.Two.2024.1080p.MAX.WEB-DL.HUN-FLUX",
        category="hd_hun",
        size_bytes=5200,
        has_hungarian_audio=True,
        seeders=45,
    )

    mock_orch = MagicMock()
    mock_orch.get_available_releases.return_value = [hun_rel]
    mock_orch.download_specific_release.return_value = {"success": True, "message": "OK"}

    hits = mgr.check_and_trigger_upgrades(mock_orch)
    assert len(hits) == 1
    assert hits[0]["title"] == "Dűne: Második rész"
    assert hits[0]["release"].title == hun_rel.title
    mock_orch.download_specific_release.assert_called_once_with(item, hun_rel)

    # Ellenőrizzük, hogy státusza átkerült upgraded-be
    assert len(mgr.get_monitoring()) == 0
    assert len(mgr.get_upgraded()) == 1
    assert mgr.get_upgraded()[0]["upgraded_release_title"] == hun_rel.title

    # 4. Készek ürítése
    mgr.clear_upgraded()
    assert len(mgr.get_all()) == 0


def test_history_item_dict_media_item_radar_check(tmp_path):
    """Teszteli, hogy feladatelőzményekben dict formátumú media_item esetén sincs AttributeError."""
    from src.core.audio_radar import AudioRadarManager
    from src.core.subtitle_radar import SubtitleRadarManager

    audio_mgr = AudioRadarManager(data_file=tmp_path / "audio_test.json")
    sub_mgr = SubtitleRadarManager(data_file=tmp_path / "sub_test.json")

    # 1. Normál szótár
    m_item_dict = {"tmdb_id": 9999, "title_hu": "Teszt Film", "year": 2024}
    m_tmdb_id = m_item_dict.get("tmdb_id") if isinstance(m_item_dict, dict) else getattr(m_item_dict, "tmdb_id", None)
    assert m_tmdb_id == 9999
    assert audio_mgr.is_monitored(m_tmdb_id) is False
    assert sub_mgr.is_monitored(m_tmdb_id) is False

    # 2. Üres szótár vagy hiányzó mezők
    m_empty = {}
    m_empty_id = m_empty.get("tmdb_id") if isinstance(m_empty, dict) else getattr(m_empty, "tmdb_id", None)
    assert m_empty_id is None
    assert audio_mgr.is_monitored(m_empty_id) is False
    assert sub_mgr.is_monitored(m_empty_id) is False

    # 3. None érték
    m_none = None
    m_none_id = m_none.get("tmdb_id") if isinstance(m_none, dict) else getattr(m_none, "tmdb_id", None)
    assert m_none_id is None
    assert audio_mgr.is_monitored(m_none_id) is False
    assert sub_mgr.is_monitored(m_none_id) is False


def test_subtitle_strict_year_and_type_matching():
    """Teszteli, hogy az évszám- és típuseltéréses téves feliratok (pl. Cousteau 2016 vs Odüsszeia 2026) szigorúan kizáródnak."""
    from src.services.subtitle_service import SubtitleService
    sub_service = SubtitleService()

    release_title = "The.Odyssey.2026.1080p.BluRay.DDP5.1.x264-ZoroSenpai"

    # Téves találatok Feliratok.eu mintájára:
    # 1. 2016-os francia Cousteau film (A mélység kalandora)
    # 2. 2026-os making of dokumentumfilm
    # 3. Sorozatepizód
    # 4. 1968-as 2001: Űrodüsszeia
    mismatched_candidates = [
        {"text": "A mélység kalandora (SubRip) The Odyssey (L'odyssée) (2016) (BDRip-EXTREME)", "url": "http://sub/cousteau2016"},
        {"text": "The Odyssey: The Making of an Epic (2026) (SKST)", "url": "http://sub/makingof"},
        {"text": "Totálisan szétcsúszva - 1x02 Wasted - 1x02 - The Odyssey", "url": "http://sub/series"},
        {"text": "2001: Űrodüsszeia (SubRip) 2001: A Space Odyssey (1968)", "url": "http://sub/space1968"},
    ]

    # Mindegyik téves jelöltet ki kell zárnia, None-t kell visszaadnia!
    best_mismatched = sub_service._pick_best_subtitle_match(
        mismatched_candidates, release_title, expected_year=2026
    )
    assert best_mismatched is None, "A 2016-os vagy making-of feliratot nem szabad kiválasztani a 2026-os filmhez!"

    # Ha bekerül a valódi 2026-os release felirat
    correct_candidates = mismatched_candidates + [
        {"text": "The.Odyssey.2026.1080p.BluRay.x264-ZoroSenpai.hu.srt", "url": "http://sub/correct2026"},
    ]
    best_correct = sub_service._pick_best_subtitle_match(
        correct_candidates, release_title, expected_year=2026
    )
    assert best_correct is not None
    assert best_correct["url"] == "http://sub/correct2026"
    assert best_correct["score"] >= 20






