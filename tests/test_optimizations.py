import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.atomic_storage import atomic_save_json, load_json_with_recovery
from src.core.movie_radar import MovieRadarManager
from src.core.orchestrator import Orchestrator
from src.core.organizer import MediaOrganizer
from src.models.media import MediaItem, MediaType, TorrentRelease
from src.services.ncore_service import NcoreService
from src.services.plex_service import PlexService
from src.services.subtitle_service import SubtitleService
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


def test_diagnostic_service_health():
    from src.services.diagnostic_service import DiagnosticService

    orch_mock = MagicMock()
    orch_mock.torrent_service._ensure_connected.return_value = True
    orch_mock.torrent_service.client.app.version = "4.6.0"

    orch_mock.plex_service.is_connected.return_value = True
    orch_mock.plex_service.server.friendlyName = "OtthoniPlex"
    sec1 = MagicMock()
    sec1.title = "Filmek"
    orch_mock.plex_service.server.library.sections.return_value = [sec1]

    diag = DiagnosticService(orchestrator=orch_mock)
    health = diag.get_system_health()

    assert health["services"]["qbittorrent"]["connected"] is True
    assert "4.6.0" in health["services"]["qbittorrent"]["details"]
    assert health["services"]["plex"]["connected"] is True
    assert "OtthoniPlex" in health["services"]["plex"]["details"]
    assert "Filmek" in health["services"]["plex"]["details"]


def test_ncore_login_thread_safety():
    import concurrent.futures
    from src.services.ncore_service import NcoreService

    ncore = NcoreService(username="testuser", password="testpassword")
    call_count = 0

    def mock_login():
        nonlocal call_count
        call_count += 1
        ncore._is_logged_in = True
        return True

    with patch.object(ncore, "login", side_effect=mock_login):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(ncore._ensure_logged_in) for _ in range(10)]
            results = [f.result() for f in futures]

        assert all(results)
        assert call_count == 1


def test_subtitle_service_zip_unpack(tmp_path):
    import io
    import zipfile
    from src.services.subtitle_service import SubtitleService

    sub_service = SubtitleService()

    # Készítünk egy memóriabeli ZIP fájlt .srt tartalommal
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("Movie.1080p.hu.srt", "1\n00:00:01,000 --> 00:00:03,000\nSzia világ!")
    zip_bytes = zip_buffer.getvalue()

    mock_resp = MagicMock()
    mock_resp.content = zip_bytes
    mock_resp.raise_for_status = MagicMock()

    with patch.object(sub_service.client, "get", return_value=mock_resp):
        out_path = sub_service._download_and_save_srt(
            subtitle_url="http://mock/sub.zip",
            destination_dir=tmp_path,
            video_filename="Movie.1080p.mkv",
        )
        assert out_path is not None
        assert out_path.exists()
        assert out_path.name == "Movie.1080p.hu.srt"
        content = out_path.read_text(encoding="utf-8")
        assert "Szia világ!" in content


def test_organize_series_with_subtitles(tmp_path):
    org = MediaOrganizer()
    org.settings.PLEX_SERIES_DIR = tmp_path / "Series"

    src_dir = tmp_path / "Downloads" / "Show.S01"
    src_dir.mkdir(parents=True)
    video_file = src_dir / "Show.S01E01.mkv"
    video_file.write_bytes(b"VIDEO" * 200)

    sub_file = src_dir / "Show.S01E01.hu.srt"
    sub_file.write_text("1\n00:00:01 --> 00:00:02\nTest", encoding="utf-8")

    item = MediaItem(tmdb_id=10, title_hu="A Sorozat", media_type=MediaType.TV)
    placed = org.organize_media(
        source_path=src_dir,
        media_item=item,
        attached_subtitles=[sub_file],
    )

    assert len(placed) >= 2
    season_dir = tmp_path / "Series" / "A Sorozat" / "Season 01"
    assert (season_dir / "A Sorozat - S01E01.mkv").exists()
    assert (season_dir / "A Sorozat - S01E01.hu.srt").exists()


def test_tmdb_cache_pruning():
    tmdb = TMDBService(api_key="mock_key")
    # Töltsük fel mesterségesen a gyorsítótárakat
    for i in range(160):
        tmdb._discover_cache[f"key_{i}"] = ([], 0.0)
    for i in range(200):
        tmdb._search_cache[f"search_{i}"] = []
    for i in range(300):
        tmdb._details_cache[(i, "movie")] = MagicMock()

    tmdb._prune_caches()

    # Méretek lecsökkentek a korlát alá
    assert len(tmdb._discover_cache) <= 80
    assert len(tmdb._search_cache) == 0
    assert len(tmdb._details_cache) == 0


def test_verify_release_match_title_strictness():
    from src.services.ncore_service import NcoreService

    ncore = NcoreService()
    odyssey = MediaItem(
        tmdb_id=1368337,
        title_hu="Odüsszeia",
        title_en="The Odyssey",
        year=2026,
        media_type=MediaType.MOVIE,
        imdb_id="tt33764258",
    )

    # 1. Idegen NASA dokumentumfilm elutasítása (nincs IMDb, nincs évszám, nincs címazonosság)
    nasa_rel = TorrentRelease(
        torrent_id="1",
        title="Nasa - Az amerikai űrkutatás története D01",
        category="xvid_hun",
        size_human="1.5 GB",
        seeders=10,
    )
    assert ncore.verify_release_match(nasa_rel, odyssey) is False

    # 2. Dan Snow TV dokumentumfilm elutasítása
    dan_snow_rel = TorrentRelease(
        torrent_id="2",
        title="The.Odyssey.with.Dan.Snow.2026.1080p.HDTV.H264-DARKFLiX",
        category="hd",
        size_human="4.5 GB",
        seeders=15,
    )
    assert ncore.verify_release_match(dan_snow_rel, odyssey) is False

    # 3. Régi 1997-es Odüsszeia elutasítása a 2026-os filmhez (évszám és eltérő IMDb miatt)
    old_rel = TorrentRelease(
        torrent_id="3",
        title="Odusszeia.1997.DVDRip.Xvid.HUN",
        category="xvid_hun",
        imdb_id="tt0118414",
        size_human="1.4 GB",
        seeders=5,
    )
    assert ncore.verify_release_match(old_rel, odyssey) is False

    # 4. Valódi 2026-os kiadás elfogadása
    real_rel = TorrentRelease(
        torrent_id="4",
        title="The.Odyssey.2026.1080p.WEB-DL.DDP5.1.H.264",
        category="hd",
        size_human="5.5 GB",
        seeders=25,
    )
    assert ncore.verify_release_match(real_rel, odyssey) is True

    # 5. Névelő nélküli magyar release elfogadása
    hobbit = MediaItem(
        tmdb_id=49051,
        title_hu="A hobbit: Váratlan utazás",
        title_en="The Hobbit: An Unexpected Journey",
        year=2012,
        media_type=MediaType.MOVIE,
    )
    hobbit_rel = TorrentRelease(
        torrent_id="5",
        title="Hobbit.Varatlan.utazas.2012.1080p.BluRay.x264.HUN",
        category="hd_hun",
        size_human="8 GB",
        seeders=30,
    )
    assert ncore.verify_release_match(hobbit_rel, hobbit) is True


def test_ncore_title_stem_and_sequels():
    from src.services.ncore_service import NcoreService

    ncore = NcoreService(username="u", password="p")

    # 1. Évszámmal kezdődő címek (1917, 2012)
    assert ncore._get_release_title_stem("1917.2019.1080p.BluRay.x264-SPARKS") == "1917"
    assert ncore._get_release_title_stem("1917.1080p.BluRay.x264") == "1917"
    assert ncore.is_title_match("1917.2019.1080p.BluRay.x264-SPARKS", "1917", "1917") is True

    assert ncore._get_release_title_stem("2012.2009.1080p.BluRay") == "2012"
    assert ncore.is_title_match("2012.2009.1080p.BluRay", "2012", "2012") is True

    # 2. Címben szereplő évszám (Blade Runner 2049)
    assert ncore._get_release_title_stem("Blade.Runner.2049.2017.1080p.BluRay.x264") == "blade runner 2049"
    assert ncore.is_title_match("Blade.Runner.2049.2017.1080p.BluRay.x264", "Szárnyas fejvadász 2049", "Blade Runner 2049") is True

    # 3. Folytatások kizárása az első résznél (Gladiator 2 vs Gladiator 1)
    assert ncore._get_release_title_stem("Gladiator.2.2024.1080p.WEB-DL") == "gladiator 2"
    assert ncore.is_title_match("Gladiator.2.2024.1080p.WEB-DL", "Gladiátor", "Gladiator") is False
    assert ncore.is_title_match("Gladiator.2.2024.1080p.WEB-DL", "Gladiátor 2", "Gladiator 2") is True

    # 4. Extended/Director's cut elfogadása
    assert ncore.is_title_match("Gladiator.2000.Extended.1080p.BluRay", "Gladiátor", "Gladiator") is True


def test_subtitle_service_season_pack_multiple_subs(tmp_path):
    import io
    import zipfile
    from src.services.subtitle_service import SubtitleService
    from src.models.media import MediaItem, MediaType, TorrentRelease

    sub_service = SubtitleService()

    # Memóriabeli ZIP 3 epizód feliratával
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("Shogun.S01E01.hu.srt", "1\n00:00:01,000 --> 00:00:02,000\n1. Rész")
        zf.writestr("Shogun.S01E02.hu.srt", "1\n00:00:01,000 --> 00:00:02,000\n2. Rész")
        zf.writestr("Shogun.S01E03.hu.srt", "1\n00:00:01,000 --> 00:00:02,000\n3. Rész")

    mock_resp = MagicMock()
    mock_resp.content = zip_buf.getvalue()
    mock_resp.raise_for_status = MagicMock()

    item = MediaItem(tmdb_id=100, title_hu="A sógun", media_type=MediaType.TV)
    rel = TorrentRelease(
        torrent_id="t_sho",
        title="Shogun.S01.1080p.WEB-DL",
        category="sorozat",
        size_human="15 GB",
        seeders=10,
    )

    mock_cand = MagicMock()
    mock_cand.download_url = "http://mock/subs.zip"

    with patch.object(sub_service, "search_feliratok_info", return_value=mock_cand):
        with patch.object(sub_service.client, "get", return_value=mock_resp):
            out = sub_service.find_and_download_subtitle(item, rel, destination_dir=tmp_path)
            assert out is not None
            assert out.is_dir()
            extracted_srts = list(out.glob("*.srt"))
            assert len(extracted_srts) == 3


def test_movie_radar_race_condition_safe(tmp_path):
    radar_file = tmp_path / "radar_race.json"
    radar = MovieRadarManager(data_file=radar_file)

    it1 = MediaItem(tmdb_id=1, title_hu="Film 1", media_type=MediaType.MOVIE)
    radar.add(it1)

    mock_orch = MagicMock()
    rel1 = TorrentRelease(torrent_id="t1", title="Film.1.1080p.WEB-DL", category="hd", size_human="2 GB", seeders=5)
    mock_orch.check_release_availability.return_value = [rel1]

    # Szimuláljuk, hogy a letöltés alatt a felhasználó felvesz egy 2. filmet a WebUI-on
    def mock_download(item, rel):
        it2 = MediaItem(tmdb_id=2, title_hu="Film 2 (WebUI Új)", media_type=MediaType.MOVIE)
        radar.add(it2)
        return {"success": True}

    mock_orch.download_specific_release.side_effect = mock_download

    triggered = radar.check_and_trigger_downloads(mock_orch)
    assert len(triggered) == 1

    # Ellenőrizzük, hogy a Film 2 nem veszett el, és a Film 1 állapota 'downloaded'
    all_items = radar.get_all()
    assert len(all_items) == 2
    by_id = {x["tmdb_id"]: x for x in all_items}
    assert by_id[1]["status"] == "downloaded"
    assert by_id[2]["status"] == "monitoring"


def test_ncore_dune_part_one_matching():
    from src.services.ncore_service import NcoreService

    ncore = NcoreService(username="u", password="p")
    # Part One és Part 1 elfogadása
    assert ncore.is_title_match("Dune.Part.One.2021.1080p.WEB-DL", "Dűne", "Dune") is True
    assert ncore.is_title_match("Dune.Part.1.2021.1080p.WEB-DL", "Dűne", "Dune") is True
    assert ncore.is_title_match("Rebel.Moon.Part.1.2023.1080p.WEB-DL", "Rebel Moon", "Rebel Moon") is True

    # Folytatások kizárása az első résznél
    assert ncore.is_title_match("Dune.Part.Two.2024.1080p.WEB-DL", "Dűne", "Dune") is False
    assert ncore.is_title_match("Rebel.Moon.Part.2.2024.1080p.WEB-DL", "Rebel Moon", "Rebel Moon") is False


def test_organizer_subtitle_language_priority(tmp_path):
    org = MediaOrganizer()
    eng_sub = tmp_path / "Movie.eng.srt"
    hun_sub = tmp_path / "Movie.hun.srt"
    other_sub = tmp_path / "Movie.srt"

    eng_sub.write_text("eng")
    hun_sub.write_text("hun")
    other_sub.write_text("other")

    subs = org._find_subtitle_files(tmp_path)
    assert len(subs) == 3
    # A magyar felirat a lista élén kell legyen
    assert subs[0] == hun_sub


def test_organizer_cleanup_subs_and_srts(tmp_path):
    import time
    with patch("src.core.organizer.get_settings") as mock_settings:
        s = MagicMock()
        s.DOWNLOAD_TEMP_DIR = tmp_path
        mock_settings.return_value = s

        org = MediaOrganizer()
        temp_torrents = tmp_path / "_torrents"
        temp_torrents.mkdir(parents=True)

        old_torrent = temp_torrents / "old.torrent"
        old_srt = temp_torrents / "old.srt"
        old_subs_dir = temp_torrents / "series_subs"
        old_subs_dir.mkdir()
        (old_subs_dir / "s01e01.srt").write_text("sub")

        new_torrent = temp_torrents / "new.torrent"

        old_torrent.write_text("data")
        old_srt.write_text("sub")
        new_torrent.write_text("data")

        # 8 nappal ezelőtti időbélyeg
        old_time = time.time() - (8 * 86400)
        import os
        os.utime(old_torrent, (old_time, old_time))
        os.utime(old_srt, (old_time, old_time))
        os.utime(old_subs_dir, (old_time, old_time))

        deleted = org.cleanup_old_temp_files(days=7)
        assert deleted == 3
        assert not old_torrent.exists()
        assert not old_srt.exists()
        assert not old_subs_dir.exists()
        assert new_torrent.exists()


def test_orchestrator_ok_hash_recovery(tmp_path):
    with patch("src.core.orchestrator.get_settings") as mock_settings:
        s = MagicMock()
        s.DOWNLOAD_TEMP_DIR = tmp_path / "temp"
        s.PLEX_MOVIES_DIR = tmp_path / "movies"
        s.PLEX_SERIES_DIR = tmp_path / "series"
        s.MIN_SEED_TIME_HOURS = 48
        s.TARGET_RATIO = 1.0
        mock_settings.return_value = s

        orch = Orchestrator()
        task_data = {
            "task_1": {
                "torrent_hash": "OK",
                "media_item": {"tmdb_id": 1, "title_hu": "Gladiátor", "media_type": "movie", "year": 2000},
                "release": {"torrent_id": "1", "title": "Gladiator.2000.1080p.BluRay.x264", "category": "hd", "size_bytes": 1000},
                "plex_organized": False,
            }
        }
        orch._load_state = MagicMock(return_value=task_data)
        orch._save_state = MagicMock()
        orch._tasks = task_data

        # Mockoljuk a qBittorrent aktív letöltéseit
        orch.torrent_service.list_active_downloads = MagicMock(return_value=[
            {"hash": "real_hash_12345", "name": "Gladiator.2000.1080p.BluRay.x264"}
        ])

        # Mockoljuk a státusz lekérdezést
        orch.torrent_service.get_status = MagicMock(return_value={
            "hash": "real_hash_12345",
            "name": "Gladiator.2000.1080p.BluRay.x264",
            "is_completed": True,
            "save_path": str(tmp_path / "temp"),
            "content_path": str(tmp_path / "temp" / "gladiator.mkv"),
        })

        # Hozzunk létre egy kamu videófájlt
        tmp_movie = tmp_path / "temp" / "gladiator.mkv"
        tmp_movie.parent.mkdir(parents=True, exist_ok=True)
        tmp_movie.write_bytes(b"VIDEO" * 100)

        with patch.object(orch.organizer, "organize_media", return_value=[tmp_path / "movies" / "Gladiator.mkv"]):
            orch.check_and_process_tasks()

        # A torrent_hash sikeresen feloldódott "real_hash_12345"-re és a feladat kész lett
        assert orch._tasks["task_1"]["torrent_hash"] == "real_hash_12345"
        assert orch._tasks["task_1"]["plex_organized"] is True


def test_tmdb_multi_lang_sanitization_and_search_query_filtering():
    """Ellenőrzi a többnyelvű pipe (|) paraméterek tisztítását és a kategórián belüli keresés kliensoldali szűrését."""
    tmdb = TMDBService(api_key="test_key")
    tmdb._fetch_media_list = MagicMock(return_value=[])

    # 1. Pipe nyelvek tisztítása: ha van with_origin_country és a nyelvben pipe (|) van, nem küldünk érvénytelen nyelvkódot
    tmdb.discover_media(
        media_type=MediaType.MOVIE,
        with_origin_country="SE|DK|NO|FI|IS",
        with_original_language="sv|da|no|fi|is",
    )
    args = tmdb._fetch_media_list.call_args[0]
    params = args[1]
    assert params["with_origin_country"] == "SE|DK|NO|FI|IS"
    assert "with_original_language" not in params

    # 2. Egyetlen érvényes 2 betűs nyelvkód esetén átadásra kerül
    tmdb._fetch_media_list.reset_mock()
    tmdb.discover_media(
        media_type=MediaType.MOVIE,
        with_origin_country="KR",
        with_original_language="ko",
    )
    params = tmdb._fetch_media_list.call_args[0][1]
    assert params["with_origin_country"] == "KR"
    assert params["with_original_language"] == "ko"

    # 3. Kategórián belüli keresés (query) kliensoldali nyelv és műfaj szűrése
    tmdb._fetch_media_list.reset_mock()
    item_kr_horror = MediaItem(
        tmdb_id=1,
        title_hu="Koreai Horror",
        original_language="ko",
        genres=["Horror"],
        media_type=MediaType.MOVIE,
    )
    item_us_action = MediaItem(
        tmdb_id=2,
        title_hu="Amerikai Akció",
        original_language="en",
        genres=["Akció"],
        media_type=MediaType.MOVIE,
    )
    tmdb._fetch_media_list.return_value = [item_kr_horror, item_us_action]

    res = tmdb.discover_media(
        media_type=MediaType.MOVIE,
        query="zombi",
        genre_ids=[27],  # Horror műfaj ID
        with_original_language="ko",
    )
    assert len(res) == 1
    assert res[0].tmdb_id == 1
    assert res[0].title_hu == "Koreai Horror"


def test_ncore_original_title_matching_and_verification():
    """Ellenőrzi a release címek egyezését az eredeti gyártási cím (original_title) alapján is."""
    ncore = NcoreService()

    # 1. Spanyol sorozat eredeti címmel: La Casa de Papel
    assert ncore.is_title_match(
        release_title="La.Casa.de.Papel.S01.1080p.NF.WEB-DL.DDP5.1.x264-FLUX",
        title_hu="A nagy pénzrablás",
        title_en="Money Heist",
        original_title="La casa de papel",
    ) is True

    # 2. Japán anime film eredeti latin betűs címmel
    assert ncore.is_title_match(
        release_title="Sen.to.Chihiro.no.Kamikakushi.2001.1080p.BluRay.x264",
        title_hu="Chihiro Szellemországban",
        title_en="Spirited Away",
        original_title="Sen to Chihiro no kamikakushi",
    ) is True

    # 3. verify_release_match elfogadja az original_title alapján
    media_item = MediaItem(
        tmdb_id=71446,
        title_hu="A nagy pénzrablás",
        title_en="Money Heist",
        original_title="La casa de papel",
        media_type=MediaType.TV,
        year=2017,
    )
    rel = TorrentRelease(
        torrent_id="999",
        title="La.Casa.de.Papel.S01.1080p.NF.WEB-DL",
        category="sorozat_hun",
        size_bytes=10 * (1024 ** 3),
    )
    assert ncore.verify_release_match(rel, media_item) is True


def test_subtitle_service_fallback_queries():
    """Ellenőrzi, hogy a feliratkereső megpróbálja a magyar és eredeti címet is, ha az angol nem hoz találatot."""
    sub_service = SubtitleService()

    media_item = MediaItem(
        tmdb_id=71446,
        title_hu="A nagy pénzrablás",
        title_en="Money Heist",
        original_title="La casa de papel",
        media_type=MediaType.TV,
    )
    release = TorrentRelease(
        torrent_id="101",
        title="La.Casa.de.Papel.S01E01.1080p.mkv",
        category="sorozat",
        size_bytes=1000,
    )

    call_urls = []

    def mock_get(url, params=None):
        search_term = (params or {}).get("kereses", "")
        call_urls.append(search_term)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if search_term == "Money Heist":
            # Nincs találat az angol címre
            resp.text = "<html><body><table></table></body></html>"
        elif search_term == "A nagy pénzrablás":
            # Találat a magyar címre
            resp.text = (
                "<html><body><table>"
                "<tr class='sor'><td><a href='action=letoltes&f=123'>Letöltés</a>"
                "La.Casa.de.Papel.S01E01.1080p.hu.srt</td></tr>"
                "</table></body></html>"
            )
        return resp

    with patch.object(sub_service.client, "get", side_effect=mock_get):
        result = sub_service.search_feliratok_info(media_item, release)
        assert result is not None
        assert "action=letoltes&f=123" in result.download_url
        assert "Money Heist" in call_urls
        assert "A nagy pénzrablás" in call_urls


def test_plex_has_media_bilingual_and_original_title():
    """Ellenőrzi a PlexService.has_media kétnyelvű (title_hu + alt_title) és originalTitle ellenőrzését."""
    plex = PlexService()
    plex.server = MagicMock()
    plex._is_configured = MagicMock(return_value=True)
    plex.is_connected = MagicMock(return_value=True)

    # Mock Plex találat, amelyben a cím angolul van, vagy az originalTitle-ben szerepel
    mock_plex_item = MagicMock()
    mock_plex_item.title = "Money Heist"
    mock_plex_item.originalTitle = "La casa de papel"
    mock_plex_item.year = 2017

    plex.server.library.search.return_value = [mock_plex_item]

    # 1. Magyar címre keresve, de átadva az angol címet alt_title-ként
    assert plex.has_media("A nagy pénzrablás", year=2017, alt_title="Money Heist") is True

    # 2. Eredeti címre keresve
    assert plex.has_media("Nem Létező", year=2017, alt_title="La casa de papel") is True

    # 3. Évszám eltérés (pl. 1990) -> False
    assert plex.has_media("A nagy pénzrablás", year=1990, alt_title="Money Heist") is False


def test_ncore_dead_torrent_and_3d_penalties():
    """Teszteli a 0 seeders és a 3D/SBS kiadások pontlevonását az nCore rangsorolásban."""
    ncore = NcoreService()
    alive_rel = TorrentRelease(
        torrent_id="1",
        title="Dune.Part.Two.2024.1080p.BluRay.x264.HuN",
        category="hd_hun",
        size_bytes=10 * 1024 * 1024 * 1024,
        seeders=15,
        leechers=2,
    )
    dead_rel = TorrentRelease(
        torrent_id="2",
        title="Dune.Part.Two.2024.1080p.BluRay.x264.HuN",
        category="hd_hun",
        size_bytes=10 * 1024 * 1024 * 1024,
        seeders=0,
        leechers=2,
    )
    rel_3d = TorrentRelease(
        torrent_id="3",
        title="Dune.Part.Two.2024.1080p.3D.HALF-SBS.BluRay.x264.HuN",
        category="hd_hun",
        size_bytes=10 * 1024 * 1024 * 1024,
        seeders=15,
        leechers=2,
    )

    alive_score = ncore.score_release(alive_rel, prefer_hungarian=True)
    dead_score = ncore.score_release(dead_rel, prefer_hungarian=True)
    score_3d = ncore.score_release(rel_3d, prefer_hungarian=True)

    # A halott torrent legalább 20000 pont levonást kap
    assert alive_score - dead_score >= 20000
    # A 3D kiadás 8000 pont levonást kap
    assert alive_score - score_3d >= 8000
    # Rendezéskor az élő torrent megelőzi a halottat
    sorted_rels = ncore.sort_releases([dead_rel, alive_rel])
    assert sorted_rels[0].torrent_id == "1"


def test_organizer_movie_subtitle_stem_matching(tmp_path):
    """Ellenőrzi, hogy a felirat a videófájl pontos nevére ({video_stem}.hu.srt) és a rövid névre is elhelyezésre kerül."""
    org = MediaOrganizer()
    movies_dir = tmp_path / "Movies"
    org.settings.PLEX_MOVIES_DIR = movies_dir

    source_dir = tmp_path / "download"
    source_dir.mkdir()
    video_file = source_dir / "film.mkv"
    video_file.write_bytes(b"DATA" * 500)
    sub_file = source_dir / "film.srt"
    sub_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nTeszt\n", encoding="utf-8")

    media_item = MediaItem(tmdb_id=1, title_hu="A Bajnok", year=2024, media_type=MediaType.MOVIE)
    release = TorrentRelease(
        torrent_id="10",
        title="A.Bajnok.2024.1080p.BluRay.x264",
        category="hd",
        size_bytes=2000,
    )

    placed = org.organize_media(source_dir, media_item, release)
    assert len(placed) >= 2

    # A film könyvtára
    movie_folder = movies_dir / "A Bajnok (2024)"
    assert movie_folder.exists()

    # Videó fájl ellenőrzése
    video_dest = movie_folder / "A Bajnok (2024) [1080p BluRay].mkv"
    assert video_dest.exists()

    # Pontos stem megegyező felirat okostévékhez és Plex-hez
    stem_sub = movie_folder / "A Bajnok (2024) [1080p BluRay].hu.srt"
    assert stem_sub.exists()

    # Rövid felirat kompatibilitáshoz
    short_sub = movie_folder / "A Bajnok (2024).hu.srt"
    assert short_sub.exists()


def test_organizer_series_fallback_indexing_and_3digit(tmp_path):
    """Teszteli a sorozat epizódok számozatlan fallback indexelését és a 3-jegyű (pl. 101, 214) felismerést."""
    org = MediaOrganizer()
    series_dir = tmp_path / "Series"
    org.settings.PLEX_SERIES_DIR = series_dir

    # 1. 3-jegyű epizód felismerés
    s1, e1 = org._extract_season_episode("Sorozat.101.HDTV.mkv")
    assert s1 == 1 and e1 == 1
    s2, e2 = org._extract_season_episode("Sorozat.214.WEB-DL.mkv")
    assert s2 == 2 and e2 == 14
    # Felbontás nem keverendő össze epizóddal
    s_res, e_res = org._extract_season_episode("Sorozat.720p.HDTV.mkv")
    assert e_res != 720

    # 2. Számozatlan több epizódos sorozatcsomag rendezése fallback indexeléssel
    source_dir = tmp_path / "series_download"
    source_dir.mkdir()
    f1 = source_dir / "Episode_Alpha.mkv"
    f1.write_bytes(b"DATA1" * 100)
    f2 = source_dir / "Episode_Beta.mkv"
    f2.write_bytes(b"DATA2" * 100)

    media_item = MediaItem(tmdb_id=2, title_hu="Rejtélyek Városa", media_type=MediaType.TV)
    placed = org.organize_media(source_dir, media_item)

    season1_dir = series_dir / "Rejtélyek Városa" / "Season 01"
    assert season1_dir.exists()
    dest_e01 = season1_dir / "Rejtélyek Városa - S01E01.mkv"
    dest_e02 = season1_dir / "Rejtélyek Városa - S01E02.mkv"
    assert dest_e01.exists()
    assert dest_e02.exists()


def test_tmdb_discover_media_strict_query_filter():
    """Teszteli, hogy a discover_media szigorúan szűri a találatokat, és üreset ad vissza, ha nincs egyezés."""
    tmdb = TMDBService(api_key="mock_key")
    item1 = MediaItem(tmdb_id=1, title_hu="Batman", title_en="Batman", original_language="en", genres=["Akció"], media_type=MediaType.MOVIE)
    item2 = MediaItem(tmdb_id=2, title_hu="Superman", title_en="Superman", original_language="en", genres=["Kaland"], media_type=MediaType.MOVIE)

    with patch.object(tmdb, "_fetch_media_list", return_value=[item1, item2]):
        # Szűrés magyar nyelvre ('hu') - egyik sem magyar, így üres listát kell kapnunk, nem pedig visszahullást az angolra!
        res_lang = tmdb.discover_media(query="man", with_original_language="hu", limit=10)
        assert res_lang == []

        # Szűrés 'Horror' műfajra (egyik sem Horror)
        res_genre = tmdb.discover_media(query="man", genre_ids=[27], limit=10) # 27 = Horror
        assert res_genre == []


def test_plex_service_extract_local_token():
    """Teszteli a helyi gépből történő Plex token kinyerést."""
    # Windows registry mockolása
    with patch("platform.system", return_value="Windows"):
        with patch("winreg.OpenKey"), patch("winreg.QueryValueEx", return_value=("mock_local_plex_token_12345", 1)):
            token = PlexService.extract_local_token()
            assert token == "mock_local_plex_token_12345"

    # Ha a kulcs nem található
    with patch("platform.system", return_value="Windows"):
        with patch("winreg.OpenKey", side_effect=OSError("Not found")):
            token_none = PlexService.extract_local_token()
            assert token_none is None


def test_plex_service_fetch_token_from_plex_account():
    """Teszteli a python-plexapi MyPlexAccount alapú hitelesítést."""
    mock_acc = MagicMock()
    mock_acc.authenticationToken = "token_from_account_abc"
    mock_acc.username = "plexuser"
    mock_acc.email = "plexuser@example.com"

    mock_res = MagicMock()
    mock_res.provides = "server"
    mock_res.name = "MyPlexServer"
    mock_conn = MagicMock()
    mock_conn.uri = "http://192.168.1.100:32400"
    mock_conn.local = True
    mock_res.connections = [mock_conn]
    mock_res.accessToken = "server_tok_xyz"
    mock_acc.resources.return_value = [mock_res]

    with patch("plexapi.myplex.MyPlexAccount", return_value=mock_acc):
        res = PlexService.fetch_token_from_plex_account("user", "pass")
        assert res["success"] is True
        assert res["token"] == "token_from_account_abc"
        assert res["username"] == "plexuser"
        assert len(res["servers"]) == 1
        assert res["servers"][0]["name"] == "MyPlexServer"
        assert res["servers"][0]["uri"] == "http://192.168.1.100:32400"


def test_plex_service_pin_login_flow():
    """Teszteli a Plex OAuth / PIN login osztály működését."""
    mock_pin_obj = MagicMock()
    mock_pin_obj._getCode.return_value = "PIN4"
    mock_pin_obj.oauthUrl.return_value = "https://app.plex.tv/auth/#!?code=PIN4"
    mock_pin_obj.checkLogin.return_value = True
    mock_pin_obj.token = "oauth_token_val_789"

    with patch("plexapi.myplex.MyPlexPinLogin", return_value=mock_pin_obj):
        started = PlexService.start_pin_login()
        assert started["success"] is True
        assert started["code"] == "PIN4"
        assert "oauthUrl" in started["oauth_url"] or "app.plex.tv" in started["oauth_url"]

        verified_token = PlexService.check_pin_login(mock_pin_obj)
        assert verified_token == "oauth_token_val_789"


def test_plex_service_save_and_apply_token():
    """Teszteli az új token mentését és alkalmazását a PlexService-ben."""
    plex = PlexService(server_url="http://mock:32400", token="old_token")
    with patch("src.config.settings.save_settings", return_value=True), \
         patch.object(plex, "connect", return_value=True) as mock_connect:
        ok = plex.save_and_apply_token("new_secret_token", server_url="http://newhost:32400")
        assert ok is True
        assert plex.token == "new_secret_token"
        assert plex.server_url == "http://newhost:32400"
        assert mock_connect.called


def test_organizer_multipart_movie(tmp_path):
    """Teszteli a több-lemezes (CD1 és CD2) filmek és feliratok Plex - ptX formátumú rendezését."""
    org = MediaOrganizer()
    movies_dir = tmp_path / "Movies"
    org.settings.PLEX_MOVIES_DIR = movies_dir

    source_dir = tmp_path / "download_2cd"
    source_dir.mkdir()
    cd1_vid = source_dir / "A.Gyuru.Ura.CD1.avi"
    cd1_vid.write_bytes(b"DATA" * 1000)
    cd2_vid = source_dir / "A.Gyuru.Ura.CD2.avi"
    cd2_vid.write_bytes(b"DATA" * 1000)

    cd1_sub = source_dir / "A.Gyuru.Ura.CD1.srt"
    cd1_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nCD1 felirat\n", encoding="utf-8")
    cd2_sub = source_dir / "A.Gyuru.Ura.CD2.srt"
    cd2_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nCD2 felirat\n", encoding="utf-8")

    media_item = MediaItem(tmdb_id=120, title_hu="A Gyűrűk Ura", year=2001, media_type=MediaType.MOVIE)
    release = TorrentRelease(
        torrent_id="555",
        title="A.Gyuru.Ura.2001.2xDVD.XviD.HuN",
        category="xvid_hun",
        size_bytes=1400 * 1024 * 1024,
    )

    placed = org.organize_media(source_dir, media_item, release)
    movie_folder = movies_dir / "A Gyűrűk Ura (2001)"
    assert movie_folder.exists()

    pt1_file = movie_folder / "A Gyűrűk Ura (2001) [HD] - pt1.avi"
    pt2_file = movie_folder / "A Gyűrűk Ura (2001) [HD] - pt2.avi"
    assert pt1_file.exists()
    assert pt2_file.exists()

    pt1_sub = movie_folder / "A Gyűrűk Ura (2001) [HD] - pt1.hu.srt"
    pt2_sub = movie_folder / "A Gyűrűk Ura (2001) [HD] - pt2.hu.srt"
    assert pt1_sub.exists()
    assert pt2_sub.exists()


def test_series_radar_manager(tmp_path):
    """Teszteli a SeriesRadarManager műveleteit (hozzáadás, eltávolítás, toggle, haladás és epizódkeresés)."""
    from src.core.series_radar import SeriesRadarManager

    db_file = tmp_path / "series_radar.json"
    radar = SeriesRadarManager(data_file=db_file)

    series_item = MediaItem(
        tmdb_id=94997,
        title_hu="A Sógun",
        title_en="Shogun",
        year=2024,
        media_type=MediaType.TV,
    )

    # 1. Hozzáadás
    assert radar.add(series_item, current_season=1, current_episode=4) is True
    assert radar.is_monitored(94997) is True
    assert len(radar.get_monitoring()) == 1

    # 2. Haladás frissítése
    radar.update_progress(94997, season=1, episode=5, release_title="Shogun.S01E05.1080p")
    entry = radar.get_all()[0]
    assert entry["current_episode"] == 5
    assert entry["last_release_title"] == "Shogun.S01E05.1080p"

    # 3. Toggle eltávolítja
    assert radar.toggle(series_item) is False
    assert radar.is_monitored(94997) is False

    # 4. Toggle újra hozzáadja
    assert radar.toggle(series_item) is True
    assert radar.is_monitored(94997) is True

    # 5. check_and_trigger_downloads mockolt környezetben
    mock_orch = MagicMock()
    mock_orch.tmdb_service.get_details.return_value = series_item
    mock_release = TorrentRelease(
        torrent_id="999",
        title="Shogun.S01E06.1080p.WEB-DL.HunSub",
        category="sorozat_hun",
        seeders=42,
    )
    mock_orch.ncore_service.search_by_title.return_value = [mock_release]
    mock_orch.ncore_service.sort_releases.return_value = [mock_release]
    mock_orch.download_specific_release.return_value = {"success": True, "message": "OK"}

    # Jelenlegi epizód 0 (alapértelmezett a toggle után), így S01E01-et várja
    hits = radar.check_and_trigger_downloads(mock_orch)
    assert len(hits) == 1
    assert hits[0]["title"] == "A Sógun"
    assert mock_orch.download_specific_release.called


def test_watchlist_sync_csv_parsing():
    """Teszteli az IMDb és Letterboxd exportált CSV-k formátumának beolvasását."""
    from src.core.watchlist_sync import WatchlistSyncService

    sync_service = WatchlistSyncService()

    # IMDb formátumú CSV
    imdb_csv = (
        "Position,Const,Created,Modified,Description,Title,URL,Title Type,IMDb Rating,Runtime (mins),Year,Genres,Num Votes,Release Date,Directors\n"
        "1,tt0111161,2024-01-01,2024-01-01,,A remény rabjai,https://www.imdb.com/title/tt0111161/,movie,9.3,142,1994,Drama,2800000,1994-09-23,Frank Darabont\n"
        "2,tt1375666,2024-01-01,2024-01-01,,Eredet,https://www.imdb.com/title/tt1375666/,movie,8.8,148,2010,Action,2500000,2010-07-16,Christopher Nolan\n"
    )
    items_imdb = sync_service.parse_csv_content(imdb_csv)
    assert len(items_imdb) == 2
    assert items_imdb[0]["imdb_id"] == "tt0111161"
    assert items_imdb[0]["title"] == "A remény rabjai"
    assert items_imdb[0]["year"] == 1994

    # Letterboxd formátumú CSV
    letterboxd_csv = (
        "Date,Name,Year,Letterboxd URI\n"
        "2024-02-01,Dune: Part Two,2024,https://boxd.it/h2vQ\n"
        "2024-02-01,Parasite,2019,https://boxd.it/hHum\n"
    )
    items_lb = sync_service.parse_csv_content(letterboxd_csv)
    assert len(items_lb) == 2
    assert items_lb[0]["title"] == "Dune: Part Two"
    assert items_lb[0]["year"] == 2024


def test_watchlist_sync_items_flow():
    """Teszteli a szinkronizáció 3 fő ágát: már Plexben lévő, elérhető letöltendő és Mozi-Radarba kerülő."""
    from src.core.watchlist_sync import WatchlistSyncService

    sync_service = WatchlistSyncService()

    item_in_plex = MediaItem(tmdb_id=1, title_hu="Plex Film", year=2020, media_type=MediaType.MOVIE)
    item_downloadable = MediaItem(tmdb_id=2, title_hu="Letöltendő Film", year=2023, media_type=MediaType.MOVIE)
    item_unreleased = MediaItem(tmdb_id=3, title_hu="Mozi Film", year=2026, media_type=MediaType.MOVIE)

    mock_orch = MagicMock()
    # TMDB keresés visszatérési értékek
    def mock_get_by_imdb(imdb_id):
        if imdb_id == "tt001":
            return item_in_plex
        elif imdb_id == "tt002":
            return item_downloadable
        elif imdb_id == "tt003":
            return item_unreleased
        return None

    mock_orch.tmdb_service.get_by_imdb_id.side_effect = mock_get_by_imdb
    mock_orch.tmdb_service.get_details.side_effect = lambda tid, mtype: next(
        (x for x in [item_in_plex, item_downloadable, item_unreleased] if x.tmdb_id == tid), None
    )

    # Plex könyvtár állapot: csak az 1. film van meg
    mock_orch.plex_service.has_media.side_effect = lambda t, y, alt_title=None: (t == "Plex Film")

    # nCore elérhetőség: csak a 2. filmhez van jó release
    mock_rel = TorrentRelease(torrent_id="77", title="Letoltendo.2023.1080p", category="hd_hun", seeders=15)
    mock_orch.check_release_availability.side_effect = lambda m: [mock_rel] if m.tmdb_id == 2 else []
    mock_orch.download_specific_release.return_value = {"success": True}

    raw_items = [
        {"imdb_id": "tt001", "title": "Plex Film", "year": 2020},
        {"imdb_id": "tt002", "title": "Letöltendő Film", "year": 2023},
        {"imdb_id": "tt003", "title": "Mozi Film", "year": 2026},
    ]

    res = sync_service.sync_items(raw_items, mock_orch, auto_download=True, auto_radar=True)

    assert res["processed_count"] == 3
    assert res["plex_count"] == 1
    assert res["downloaded_count"] == 1
    assert res["radar_count"] == 1
    assert mock_orch.radar.add.called
    assert mock_orch.watchlist.add.called


def test_download_collection_all_skip_plex():
    """Teszteli a gyűjtemény letöltését úgy, hogy a Plexben már létező részek automatikusan átugrásra kerülnek."""
    orch = Orchestrator()
    orch.tmdb_service = MagicMock()
    orch.plex_service = MagicMock()
    orch.plex_service.is_configured = True

    part1 = MediaItem(tmdb_id=101, title_hu="Harry Potter 1", title_en="Harry Potter 1", year=2001, media_type=MediaType.MOVIE)
    part2 = MediaItem(tmdb_id=102, title_hu="Harry Potter 2", title_en="Harry Potter 2", year=2002, media_type=MediaType.MOVIE)
    part3 = MediaItem(tmdb_id=103, title_hu="Harry Potter 3", title_en="Harry Potter 3", year=2004, media_type=MediaType.MOVIE)

    orch.tmdb_service.get_collection_details.return_value = {
        "name": "Harry Potter Gyűjtemény",
        "parts": [part1, part2, part3],
    }

    # HP1 és HP2 már megvan Plexben, HP3 hiányzik
    orch.plex_service.has_media.side_effect = lambda t, y, alt_title=None: ("1" in t or "2" in t)

    with patch.object(orch, "start_download_flow", return_value={"success": True, "message": "OK"}) as mock_start_dl:
        result = orch.download_collection_all(collection_id=124, skip_existing_in_plex=True)
        assert result["success"] is True
        assert result["started"] == 1  # Csak a HP3 indult el!
        assert result["skipped"] == 2  # HP1 és HP2 átugorva
        assert "Harry Potter 3" in result["started_titles"]
        assert "Harry Potter 1" in result["skipped_titles"]
        assert mock_start_dl.call_count == 1


def test_organizer_h264_h265_codec_not_misidentified_as_episode():
    """Teszteli, hogy a H.264 és H.265 kodekek nem lesznek tévesen 2. évad 64/65. részekként felismerve."""
    # 1. H.264 végű release egyértelmű kiterjesztés előtti résszámmal
    fn1 = "Sorozat.Nev.05.1080p.WEB.H.264-GROUP.mkv"
    s1, e1 = MediaOrganizer._extract_season_episode(fn1)
    assert e1 == 5
    assert e1 != 64

    # 2. H.265 kodek
    fn2 = "Show.Title.08.720p.HDTV.H.265.mkv"
    s2, e2 = MediaOrganizer._extract_season_episode(fn2)
    assert e2 == 8
    assert e2 != 65

    # 3. Valódi 3-jegyű epizód (104 -> S01E04)
    fn3 = "Classic.Series.104.HDTV.avi"
    s3, e3 = MediaOrganizer._extract_season_episode(fn3)
    assert s3 == 1
    assert e3 == 4


def test_subtitle_service_empty_zip_and_release_stem_preservation(tmp_path):
    """Teszteli az üres ZIP archívum kezelését és a release név pontjainak (pl. H.264) megőrzését."""
    sub_service = SubtitleService()

    # 1. Release név videó kiterjesztés nélkül (pl. Movie.2025.H.264-FLUX)
    # Nem szabad, hogy a .264-FLUX-ot kiterjesztésnek nézze!
    dummy_text_sub = b"1\n00:00:01,000 --> 00:00:02,000\nSzia vilag!\n"
    with patch.object(sub_service.client, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.content = dummy_text_sub
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        res = sub_service._download_and_save_srt(
            subtitle_url="http://mock/sub.srt",
            destination_dir=tmp_path,
            video_filename="Movie.2025.1080p.WEB-DL.H.264-FLUX",
        )
        assert res is not None
        assert res.name == "Movie.2025.1080p.WEB-DL.H.264-FLUX.hu.srt"
        assert res.exists()

    # 2. Üres / nem .srt tartalmú ZIP kezelése
    import io
    import zipfile
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("subtitles.sub", b"NOT_AN_SRT_FILE")

    with patch.object(sub_service.client, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.content = zip_buffer.getvalue()
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        res_zip = sub_service._download_and_save_srt(
            subtitle_url="http://mock/sub.zip",
            destination_dir=tmp_path,
            video_filename="Movie.2025.mkv",
        )
        # Nem szabad, hogy a nyers ZIP bájtokat kiírja .hu.srt néven!
        assert res_zip is None


def test_series_radar_season_rollover(tmp_path):
    """Teszteli a Sorozat-Radar automatikus évadváltását (S01 vége -> S02E01 keresése)."""
    from src.core.series_radar import SeriesRadarManager

    db_file = tmp_path / "series_radar_rollover.json"
    radar = SeriesRadarManager(data_file=db_file)

    series_item = MediaItem(
        tmdb_id=12345,
        title_hu="Házasodjunk",
        title_en="Getting Married",
        year=2023,
        media_type=MediaType.TV,
        number_of_seasons=2,
    )

    # 1. évad 10. részén áll (ez volt az évadzáró)
    radar.add(series_item, current_season=1, current_episode=10)

    mock_orch = MagicMock()
    mock_orch.tmdb_service.get_details.return_value = series_item

    # S01E11 nincs fent, de S02E01 elérhető az nCore-on!
    def mock_search_by_title(title, media_type):
        if "S02E01" in title:
            return [TorrentRelease(
                torrent_id="222",
                title="Getting.Married.S02E01.1080p.WEB-DL.HuN",
                category="sorozat_hun",
                seeders=25,
            )]
        return []

    mock_orch.ncore_service.search_by_title.side_effect = mock_search_by_title
    mock_orch.ncore_service.sort_releases.side_effect = lambda rels, prefer_hungarian: rels
    mock_orch.ncore_service.verify_release_match.return_value = True
    mock_orch.download_specific_release.return_value = {"success": True}

    hits = radar.check_and_trigger_downloads(mock_orch)
    assert len(hits) == 1
    assert hits[0]["episode_code"] == "S02E01"

    # Mentett állapot frissült S02E01-re
    updated = radar.get_all()[0]
    assert updated["current_season"] == 2
    assert updated["current_episode"] == 1


def test_watchlist_sync_progress_callback():
    """Teszteli a szinkronizáció valós idejű progress_callback hívását."""
    from src.core.watchlist_sync import WatchlistSyncService

    sync_service = WatchlistSyncService()
    mock_orch = MagicMock()
    mock_orch.tmdb_service.get_by_imdb_id.return_value = MediaItem(tmdb_id=1, title_hu="Film", year=2024, media_type=MediaType.MOVIE)
    mock_orch.plex_service.has_media.return_value = True

    progress_events = []
    def _cb(cur, total, name):
        progress_events.append((cur, total, name))

    raw_items = [
        {"imdb_id": "tt111", "title": "Film 1", "year": 2024},
        {"imdb_id": "tt222", "title": "Film 2", "year": 2024},
    ]

    res = sync_service.sync_items(raw_items, mock_orch, progress_callback=_cb)
    assert len(progress_events) == 2
    assert progress_events[0] == (1, 2, "Film 1")
    assert progress_events[1] == (2, 2, "Film 2")


def test_organizer_copy_with_progress_and_feedback(tmp_path):
    """Teszteli a lemezek közötti másolás valós idejű haladáskövetését és visszajelzését."""
    organizer = MediaOrganizer()

    src_file = tmp_path / "source_movie.mkv"
    # Létrehozunk egy 2 MB-os tesztfájlt
    chunk = b"X" * (1024 * 1024)
    src_file.write_bytes(chunk * 2)

    dst_file = tmp_path / "dest" / "Dest Movie (2024).mkv"
    dst_file.parent.mkdir(parents=True, exist_ok=True)

    callback_events = []
    def _progress_cb(info):
        callback_events.append(info.copy())

    success = organizer._copy_with_progress(src_file, dst_file, chunk_size=512 * 1024, callback=_progress_cb)
    assert success is True
    assert dst_file.exists()
    assert dst_file.stat().st_size == src_file.stat().st_size
    assert organizer.get_current_transfer() is None

    # Callbackek ellenőrzése
    assert len(callback_events) > 0
    last_event = callback_events[-1]
    assert last_event["percent"] == 100.0
    assert last_event["filename"] == "Dest Movie (2024).mkv"
    assert "eta_str" in last_event
    assert "speed_mbps" in last_event
    assert last_event["destination_folder"] == str(dst_file.parent)


def test_organizer_placement_info_and_hardlink(tmp_path):
    """Teszteli az elhelyezési információk (destination_folder, destination_files, is_hardlink) rögzítését."""
    organizer = MediaOrganizer()
    organizer.settings.PLEX_MOVIES_DIR = tmp_path / "PlexMovies"

    src_folder = tmp_path / "downloads" / "Avatar.2009.1080p.mkv"
    src_folder.parent.mkdir(parents=True, exist_ok=True)
    src_folder.write_bytes(b"MOVIE_CONTENT" * 1000)

    media = MediaItem(tmdb_id=19995, title_hu="Avatar", year=2009, media_type=MediaType.MOVIE)
    release = TorrentRelease(torrent_id="123", title="Avatar.2009.1080p.mkv", category="hd_hun", size_bytes=10000)

    placed = organizer.organize_media(src_folder, media, release)
    assert len(placed) == 1
    assert placed[0].exists()

    info = organizer.get_last_placement_info()
    assert info is not None
    assert info["status"] == "completed"
    assert str(organizer.settings.PLEX_MOVIES_DIR / "Avatar (2009)") == info["destination_folder"]
    assert len(info["destination_files"]) == 1
    assert "Avatar (2009)" in info["destination_files"][0]
    assert "is_hardlink" in info
    assert "timestamp" in info


def test_orchestrator_transfer_progress_and_task_destination(tmp_path):
    """Teszteli az orchestrator feladatok célkönyvtár metaadatainak rögzítését és visszaadását."""
    from src.core.organizer import MediaOrganizer
    organizer = MediaOrganizer()
    organizer.settings.PLEX_MOVIES_DIR = tmp_path / "PlexMovies"

    mock_orch = Orchestrator()
    mock_orch.organizer = organizer

    # Elhelyezés előtt
    assert mock_orch.get_transfer_progress() is None

    # Szimulálunk egy letöltési feladatot
    src_vid = tmp_path / "downloads" / "Gladiator.2000.mkv"
    src_vid.parent.mkdir(parents=True, exist_ok=True)
    src_vid.write_bytes(b"GLADIATOR_VIDEO" * 500)

    task_id = "test_task_123"
    task = {
        "task_id": task_id,
        "media_item": {"tmdb_id": 98, "title_hu": "Gladiátor", "year": 2000, "media_type": "movie"},
        "release": {"torrent_id": "456", "title": "Gladiator.2000.mkv", "category": "hd_hun", "size_bytes": 1000},
        "content_path": str(src_vid),
        "status": "downloaded",
        "plex_organized": False,
    }
    mock_orch._tasks = {task_id: task}
    mock_orch._load_state = MagicMock(return_value={task_id: task})
    mock_orch._save_state = MagicMock()

    res = mock_orch.force_organize_task(task_id)
    assert res["success"] is True
    assert "destination_folder" in res
    assert "destination_files" in res
    assert task["plex_organized"] is True
    assert "destination_folder" in task
    assert "destination_files" in task
    assert "organized_at" in task
    assert "Gladiátor (2000)" in task["destination_folder"]


def test_plex_service_extract_local_token_safe(tmp_path):
    """Teszteli, hogy a helyi Plex token kinyerése biztonságosan fut le és kezeli a Preferences.xml-t."""
    # Ha nincs Preferences.xml vagy Registry kulcs
    res = PlexService.extract_local_token()
    assert res is None or isinstance(res, str)

    # Szimulálunk egy Preferences.xml fájlt
    pref_file = tmp_path / "Preferences.xml"
    pref_file.write_text('<Preferences PlexOnlineToken="SAFE_MOCK_TOKEN_XYZ123" />', encoding="utf-8")

    with patch("platform.system", return_value="Linux"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("xml.etree.ElementTree.parse") as mock_parse:
        mock_tree = MagicMock()
        mock_root = MagicMock()
        mock_root.attrib = {"PlexOnlineToken": "SAFE_MOCK_TOKEN_XYZ123"}
        mock_tree.getroot.return_value = mock_root
        mock_parse.return_value = mock_tree

        token = PlexService.extract_local_token()
        assert token == "SAFE_MOCK_TOKEN_XYZ123"


def test_subtitle_service_metadata_and_scoring(tmp_path):
    """Teszteli a feliratkereső metaadatait (forrás, pontszám, .hu.srt név)."""
    sub_service = SubtitleService()

    candidates = [
        {"text": "Dune.Part.Two.2024.720p.HDTV", "url": "http://sub/1"},
        {"text": "Dune.Part.Two.2024.1080p.WEB-DL.FLUX", "url": "http://sub/2"},
    ]
    release_title = "Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.H.264-FLUX"

    best = sub_service._pick_best_subtitle_match(candidates, release_title)
    assert best is not None
    assert best["url"] == "http://sub/2"
    assert best.get("score", 0) > 10  # 1080p és WEB-DL és FLUX egyezések

    # Mentés tesztelése és SubtitleItem metaadat frissülés
    dest_dir = tmp_path / "subs"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(sub_service.client, "get") as mock_get:
        mock_res = MagicMock()
        mock_res.content = "1\n00:00:01,000 --> 00:00:04,000\nMagyar szöveg teszt őű".encode("utf-8")
        mock_res.raise_for_status = MagicMock()
        mock_get.return_value = mock_res

        saved = sub_service._download_and_save_srt("http://sub/2", dest_dir, "Dune.Part.Two.2024.1080p.mkv")
        assert saved is not None
        assert saved.exists()
        assert saved.name == "Dune.Part.Two.2024.1080p.hu.srt"
        assert "őű" in saved.read_text(encoding="utf-8")


def test_orchestrator_attach_or_update_subtitle(tmp_path):
    """Teszteli a felirat utólagos kézi vagy automata pótlását és elhelyezését."""
    mock_orch = Orchestrator()
    mock_orch.organizer.settings.PLEX_MOVIES_DIR = tmp_path / "PlexMovies"

    # Létrehozunk egy cél Plex mappát a filmmel
    target_dir = tmp_path / "PlexMovies" / "Oppenheimer (2023)"
    target_dir.mkdir(parents=True, exist_ok=True)
    vid_file = target_dir / "Oppenheimer (2023) [1080p].mkv"
    vid_file.write_bytes(b"VIDEO_CONTENT" * 100)

    task_id = "test_task_oppenheimer"
    task = {
        "task_id": task_id,
        "torrent_hash": "hash_oppen_123",
        "media_item": {"tmdb_id": 872585, "title_hu": "Oppenheimer", "year": 2023, "media_type": "movie"},
        "release": {"torrent_id": "999", "title": "Oppenheimer.2023.1080p.mkv", "category": "hd", "size_bytes": 1000, "has_hungarian_audio": False},
        "destination_folder": str(target_dir),
        "destination_files": [vid_file.name],
        "plex_organized": True,
        "subtitle_path": None,
    }
    mock_orch._tasks = {task_id: task}
    mock_orch._load_state = MagicMock(return_value={task_id: task})
    mock_orch._save_state = MagicMock()

    # Kamu feliratfájl generálása a subtitle_service mockolásával
    sub_mock_file = tmp_path / "temp_sub.srt"
    sub_mock_file.write_text("1\n00:00:01,000 --> 00:00:04,000\nOppenheimer felirat", encoding="utf-8")
    mock_orch.subtitle_service.find_and_download_subtitle = MagicMock(return_value=sub_mock_file)

    res = mock_orch.attach_or_update_subtitle(task_id)
    assert res["success"] is True
    assert "Feliratok.info" in res["message"] or "csatolva" in res["message"]

    # Ellenőrizzük, hogy a felirat bekerült-e a Plex mappába a videó nevével megegyezően
    expected_sub = target_dir / "Oppenheimer (2023) [1080p].hu.srt"
    assert expected_sub.exists()
    assert "Oppenheimer felirat" in expected_sub.read_text(encoding="utf-8")


def test_installer_logging_and_dependency_verification(tmp_path):
    """Teszteli az automatikus telepítő naplózást (telepites_log.txt) és a függőségek önellenőrzését."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import setup_environment

    # 1. Naplóbejegyzés létrehozása és formátuma
    setup_environment.append_to_install_log(tmp_path, "Teszt telepítési lépés indítása", level="INFO")
    setup_environment.append_to_install_log(tmp_path, "Modul sikeresen ellenőrizve", level="OK")

    log_file = tmp_path / "logs" / "telepites_log.txt"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "[INFO] Teszt telepítési lépés indítása" in content
    assert "[OK] Modul sikeresen ellenőrizve" in content

    # 2. Függőségi self-test ellenőrzése
    # A jelenlegi virtuális környezetben minden tesztelt modul telepítve van
    all_ok = setup_environment.verify_dependencies(tmp_path)
    assert all_ok is True

    updated_content = log_file.read_text(encoding="utf-8")
    assert "Függőségek integritás-ellenőrzésének indítása" in updated_content
    assert "Modul épségben: streamlit" in updated_content


def test_settings_migrator_preserves_plex_token_and_passwords(tmp_path):
    """Teszteli, hogy az új verzió telepítésekor az elmentett változók (Plex token, jelszavak stb.) megmaradnak."""
    from src.core.settings_migrator import (
        parse_env_file,
        merge_env_contents,
        migrate_and_preserve_settings,
        restore_data_from_appdata,
    )

    # 1. merge_env_contents tesztelése
    old_env = {
        "PLEX_TOKEN": "my_secret_plex_token_12345",
        "NCORE_USERNAME": "test_user",
        "NCORE_PASSWORD": "super_secret_password",
        "CUSTOM_KEY": "custom_value",
    }
    template = """# TMDB
TMDB_API_KEY=
# nCore
NCORE_USERNAME=
NCORE_PASSWORD=
# Plex
PLEX_TOKEN=
PLEX_SERVER_URL=http://localhost:32400
# Új beállítás az új verzióban
CONFIRM_BEFORE_DOWNLOAD=true
"""
    merged = merge_env_contents(old_env, template)
    assert "PLEX_TOKEN=my_secret_plex_token_12345" in merged
    assert "NCORE_USERNAME=test_user" in merged
    assert "NCORE_PASSWORD=super_secret_password" in merged
    assert "CONFIRM_BEFORE_DOWNLOAD=true" in merged
    assert "CUSTOM_KEY=custom_value" in merged

    # 2. migrate_and_preserve_settings tesztelése új projekt mappában
    old_project = tmp_path / "old_version"
    old_project.mkdir()
    (old_project / ".env").write_text(
        "PLEX_TOKEN=persisted_token_999\n"
        "NCORE_USERNAME=apa_user\n"
        "NCORE_PASSWORD=apa_pass\n"
        "PLEX_MOVIES_DIR=E:/Egyedi/Filmek\n"
        "PLEX_SERIES_DIR=E:/Egyedi/Sorozatok\n"
        "DOWNLOAD_TEMP_DIR=E:/Egyedi/Torrentek\n",
        encoding="utf-8"
    )

    new_project = tmp_path / "new_version"
    new_project.mkdir()
    (new_project / ".env.example").write_text(template, encoding="utf-8")

    # Mockoljuk az appdata-t és a keresett mappákat
    with patch("src.core.settings_migrator.get_appdata_dir", return_value=tmp_path / "mock_appdata"), \
         patch("src.core.settings_migrator.find_candidate_env_files", return_value=[old_project / ".env"]):
        
        log_messages = []
        ok, res = migrate_and_preserve_settings(new_project, log_cb=lambda m, lvl: log_messages.append((lvl, m)))
        assert ok is True
        assert res["PLEX_TOKEN"] == "persisted_token_999"
        assert res["NCORE_USERNAME"] == "apa_user"
        assert res["PLEX_MOVIES_DIR"] == "E:/Egyedi/Filmek"
        assert res["PLEX_SERIES_DIR"] == "E:/Egyedi/Sorozatok"
        assert res["DOWNLOAD_TEMP_DIR"] == "E:/Egyedi/Torrentek"

        new_env_content = (new_project / ".env").read_text(encoding="utf-8")
        assert "PLEX_TOKEN=persisted_token_999" in new_env_content
        assert "NCORE_USERNAME=apa_user" in new_env_content
        assert "PLEX_MOVIES_DIR=E:/Egyedi/Filmek" in new_env_content
        assert "PLEX_SERIES_DIR=E:/Egyedi/Sorozatok" in new_env_content
        assert "DOWNLOAD_TEMP_DIR=E:/Egyedi/Torrentek" in new_env_content
        assert "CONFIRM_BEFORE_DOWNLOAD=true" in new_env_content
        # .env.bak biztonsági mentés létrejött
        assert (tmp_path / "mock_appdata" / ".env").exists()


def test_torrent_service_fix_stuck_upload_mode():
    from src.services.torrent_service import TorrentService

    torrent_svc = TorrentService()
    torrent_svc.client = MagicMock()

    # 1. Normális letöltésben lévő torrent (nem beragadt)
    tor_normal = MagicMock()
    tor_normal.hash = "hash_normal"
    tor_normal.name = "Normal.Movie"
    tor_normal.progress = 0.45
    tor_normal.state = "downloading"
    tor_normal.super_seeding = False

    # 2. Beragadt torrent (csak feltöltésben van, holott 0%-on áll)
    tor_stuck = MagicMock()
    tor_stuck.hash = "hash_stuck"
    tor_stuck.name = "Stuck.Movie"
    tor_stuck.progress = 0.05
    tor_stuck.state = "uploading"
    tor_stuck.super_seeding = True

    # 3. Kész torrent (100% letöltve, rendes seedelés)
    tor_completed = MagicMock()
    tor_completed.hash = "hash_completed"
    tor_completed.name = "Done.Movie"
    tor_completed.progress = 1.0
    tor_completed.state = "uploading"
    tor_completed.super_seeding = False

    torrent_svc.client.torrents_info.return_value = [tor_normal, tor_stuck, tor_completed]

    with patch.object(torrent_svc, "_ensure_connected", return_value=True):
        fixed = torrent_svc.fix_stuck_upload_mode()
        assert len(fixed) == 1
        assert fixed[0] == "Stuck.Movie"

        # Ellenőrizzük, hogy a qBittorrent metódusai lefutottak a feloldáshoz
        torrent_svc.client.torrents_set_super_seeding.assert_called_with(enable=False, torrent_hashes="hash_stuck")
        torrent_svc.client.torrents_set_force_start.assert_called_with(enable=True, torrent_hashes="hash_stuck")
        torrent_svc.client.torrents_resume.assert_called_with(torrent_hashes="hash_stuck")
        torrent_svc.client.torrents_recheck.assert_called_with(torrent_hashes="hash_stuck")


def test_torrent_service_hnr_compliance_auto_delete_data():
    from src.services.torrent_service import TorrentService

    torrent_svc = TorrentService()
    torrent_svc.auto_remove = True
    torrent_svc.min_seed_hours = 48
    torrent_svc.target_ratio = 1.0
    torrent_svc.client = MagicMock()

    mock_tor = MagicMock()
    mock_tor.hash = "hash_done"
    mock_tor.name = "Completed.Movie"
    mock_tor.seeding_time = 49 * 3600  # 49 óra seedelés (48h teljesült)
    mock_tor.ratio = 0.5
    mock_tor.progress = 1.0
    mock_tor.state = "uploading"
    mock_tor.content_path = "C:/Downloads/Completed.Movie.mkv"
    mock_tor.save_path = "C:/Downloads"
    mock_tor.size = 1000000000
    mock_tor.total_size = 1000000000
    mock_tor.dlspeed = 0
    mock_tor.upspeed = 100000
    mock_tor.eta = 0
    torrent_svc.client.torrents_info.return_value = [mock_tor]

    with patch.object(torrent_svc, "_ensure_connected", return_value=True):
        # 1. delete_data_files=False esetén csak a torrent törlődik, a fájlok megmaradnak
        res_keep = torrent_svc.check_hnr_compliance("hash_done", delete_data_files=False)
        assert res_keep["compliant"] is True
        assert res_keep["action_taken"] == "removed_files_kept"
        torrent_svc.client.torrents_delete.assert_called_with(delete_files=False, torrent_hashes="hash_done")

        # 2. delete_data_files=True esetén a torrent és az ideiglenes fájlok is törlődnek
        res_delete = torrent_svc.check_hnr_compliance("hash_done", delete_data_files=True)
        assert res_delete["compliant"] is True
        assert res_delete["action_taken"] == "removed_and_temp_deleted"
        torrent_svc.client.torrents_delete.assert_called_with(delete_files=True, torrent_hashes="hash_done")


def test_orchestrator_check_and_process_tasks_safe_temp_deletion(tmp_path):
    from src.core.orchestrator import Orchestrator

    orch = Orchestrator()
    orch.torrent_service = MagicMock()
    orch.organizer = MagicMock()
    orch.plex_service = MagicMock()

    # Valódi Plex célmappa szimulálása
    plex_movie_dir = tmp_path / "PlexMovies" / "Test Movie (2025)"
    plex_movie_dir.mkdir(parents=True)
    plex_video_file = plex_movie_dir / "Test Movie (2025) [1080p].mkv"
    plex_video_file.write_bytes(b"A" * (2 * 1024 * 1024))  # 2 MB méret (> 1 MB)

    # 1. Feladat: Plexbe szervezve, célfájl létezik -> törölheti az ideiglenes torrent adatot
    task_safe = {
        "id": "task_1",
        "torrent_hash": "hash_safe",
        "title": "Test Movie",
        "status": "completed",
        "plex_organized": True,
        "destination_folder": str(plex_movie_dir),
        "destination_files": [plex_video_file.name],
    }

    # 2. Feladat: Nincs még Plexbe szervezve vagy hiányzik a fájl -> tilos törölni az adatot!
    task_unsafe = {
        "id": "task_2",
        "torrent_hash": "hash_unsafe",
        "title": "Unsafe Movie",
        "status": "completed",
        "plex_organized": False,
        "destination_folder": None,
        "destination_files": [],
    }

    mock_tasks = {"task_1": task_safe, "task_2": task_unsafe}

    orch.settings.DELETE_TORRENT_DATA_AFTER_SEED = True
    orch.torrent_service.get_status.return_value = {
        "name": "Test.Movie",
        "is_completed": True,
        "progress": 1.0,
        "state": "uploading",
    }
    orch.torrent_service.check_hnr_compliance.return_value = {
        "compliant": True,
        "action_taken": "removed_and_temp_deleted",
        "reason": "48h seedelve",
    }

    with patch.object(orch, "_load_state", return_value=mock_tasks), \
         patch.object(orch, "_save_state"):
        orch.check_and_process_tasks()

        # Ellenőrizzük, hogy a safe feladatnál delete_data_files=True hívódott meg
        call_safe = [
            c for c in orch.torrent_service.check_hnr_compliance.call_args_list
            if c.args and c.args[0] == "hash_safe"
        ]
        assert len(call_safe) == 1
        assert call_safe[0].kwargs.get("delete_data_files") is True

        # Ellenőrizzük, hogy az unsafe feladatnál delete_data_files=False hívódott meg!
        call_unsafe = [
            c for c in orch.torrent_service.check_hnr_compliance.call_args_list
            if c.args and c.args[0] == "hash_unsafe"
        ]
        assert len(call_unsafe) == 1
        assert call_unsafe[0].kwargs.get("delete_data_files") is False


def test_orchestrator_attach_manual_subtitle_movie(tmp_path):
    from src.core.orchestrator import Orchestrator

    orch = Orchestrator()
    orch.plex_service = MagicMock()

    plex_movie_dir = tmp_path / "PlexMovies" / "Gladiator II (2024)"
    plex_movie_dir.mkdir(parents=True)
    video_file = plex_movie_dir / "Gladiator II (2024) [1080p].mkv"
    video_file.write_bytes(b"VIDEO" * 100)

    task = {
        "task_id": "task_glad",
        "torrent_hash": "hash_glad",
        "media_item": {
            "tmdb_id": 100,
            "title_hu": "Gladiátor 2",
            "title_en": "Gladiator II",
            "year": 2024,
            "media_type": "movie",
        },
        "release": {
            "torrent_id": "t_glad",
            "title": "Gladiator.II.2024.1080p.WEB-DL.H.264-FLUX",
            "category": "hd",
            "size_human": "6 GB",
            "has_hungarian_audio": False,
        },
        "status": "completed",
        "plex_organized": True,
        "destination_folder": str(plex_movie_dir),
    }

    mock_tasks = {"task_glad": task}
    srt_content_cp1250 = "1\n00:00:01,000 --> 00:00:04,000\nGladiátor visszatér: árvíztűrő tükörfúrógép.".encode("cp1250")

    with patch.object(orch, "_load_state", return_value=mock_tasks), \
         patch.object(orch, "_save_state"):
        res = orch.attach_manual_subtitle(
            task_id="task_glad",
            file_name="Gladiator.II.custom.srt",
            file_bytes=srt_content_cp1250,
        )

        assert res["success"] is True
        assert task["has_subtitle"] is True
        assert task["subtitle_info"]["is_manual"] is True
        assert task["subtitle_info"]["source"] == "Saját feltöltés (.srt)"

        # Ellenőrizzük, hogy a felirat UTF-8 kódolással létrejött a Plex mappában mind a fő videó stemjével, mind tiszta címmel
        dest_sub_exact = plex_movie_dir / "Gladiator II (2024) [1080p].hu.srt"
        dest_sub_short = plex_movie_dir / "Gladiátor 2 (2024).hu.srt"
        assert dest_sub_exact.exists()
        assert dest_sub_short.exists()

        text_read = dest_sub_exact.read_text(encoding="utf-8")
        assert "Gladiátor visszatér" in text_read

        # Plex könyvtárfrissítés meghívódott
        orch.plex_service.refresh_library.assert_called_with(media_type="movie")


def test_orchestrator_attach_manual_subtitle_zip(tmp_path):
    import io
    import zipfile
    from src.core.orchestrator import Orchestrator

    orch = Orchestrator()
    orch.plex_service = MagicMock()

    plex_movie_dir = tmp_path / "PlexMovies" / "Inception (2010)"
    plex_movie_dir.mkdir(parents=True)
    video_file = plex_movie_dir / "Inception (2010) [1080p].mkv"
    video_file.write_bytes(b"VIDEO" * 100)

    task = {
        "task_id": "task_inc",
        "media_item": {
            "tmdb_id": 200,
            "title_hu": "Eredet",
            "title_en": "Inception",
            "year": 2010,
            "media_type": "movie",
        },
        "release": {
            "torrent_id": "t_inc",
            "title": "Inception.2010.1080p.BluRay",
            "category": "hd",
            "has_hungarian_audio": False,
        },
        "destination_folder": str(plex_movie_dir),
    }

    mock_tasks = {"task_inc": task}

    # ZIP készítése memóriában
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("Inception.1080p.hu.srt", "1\n00:00:01 --> 00:00:03\nÁlom az álomban")
    zip_bytes = zip_buf.getvalue()

    with patch.object(orch, "_load_state", return_value=mock_tasks), \
         patch.object(orch, "_save_state"):
        res = orch.attach_manual_subtitle(
            task_id="task_inc",
            file_name="Inception_subs.zip",
            file_bytes=zip_bytes,
        )
        assert res["success"] is True
        dest_sub = plex_movie_dir / "Inception (2010) [1080p].hu.srt"
        assert dest_sub.exists()
        assert "Álom az álomban" in dest_sub.read_text(encoding="utf-8")


def test_orchestrator_attach_manual_subtitle_series(tmp_path):
    from src.core.orchestrator import Orchestrator

    orch = Orchestrator()
    orch.plex_service = MagicMock()

    plex_series_dir = tmp_path / "PlexSeries" / "Severance" / "Season 02"
    plex_series_dir.mkdir(parents=True)
    ep_video = plex_series_dir / "Severance - S02E05.mkv"
    ep_video.write_bytes(b"EPISODE" * 100)

    task = {
        "task_id": "task_sev",
        "media_item": {
            "tmdb_id": 300,
            "title_hu": "Különválás",
            "title_en": "Severance",
            "media_type": "tv",
        },
        "release": {
            "torrent_id": "t_sev",
            "title": "Severance.S02E05.1080p.WEB",
            "category": "sorozat",
            "season_num": 2,
            "episode_num": 5,
            "has_hungarian_audio": False,
        },
        "destination_folder": str(plex_series_dir),
    }

    mock_tasks = {"task_sev": task}
    srt_text = "1\n00:00:01 --> 00:00:02\nKülönválás epizód felirat"

    with patch.object(orch, "_load_state", return_value=mock_tasks), \
         patch.object(orch, "_save_state"):
        res = orch.attach_manual_subtitle(
            task_id="task_sev",
            file_name="Severance.S02E05.hu.srt",
            file_bytes=srt_text.encode("utf-8"),
        )
        assert res["success"] is True
        dest_sub = plex_series_dir / "Különválás - S02E05.hu.srt"
        assert dest_sub.exists()
        assert "Különválás epizód felirat" in dest_sub.read_text(encoding="utf-8")
        orch.plex_service.refresh_library.assert_called_with(media_type="show")


def test_torrent_service_seed_time_completed_and_hnr():
    """Teszteli, hogy 350 órás seedeléssel a torrent késznek minősül, és a HnR szabály azonnal teljesültnek minősül."""
    from src.services.torrent_service import TorrentService
    ts = TorrentService()
    ts.client = MagicMock()

    mock_torrent = MagicMock()
    mock_torrent.hash = "hash_350h"
    mock_torrent.name = "Long.Seeding.Movie.2024"
    mock_torrent.state = "forcedUP"  # Felhasználó kényszerített indítása
    mock_torrent.progress = 0.999
    mock_torrent.seeding_time = 350 * 3600  # 350 óra
    mock_torrent.ratio = 2.5
    mock_torrent.dlspeed = 0
    mock_torrent.upspeed = 150000
    mock_torrent.eta = 0
    mock_torrent.save_path = "D:/Torrents"
    mock_torrent.content_path = "D:/Torrents/Long.Seeding.Movie.2024"

    ts.client.torrents_info.return_value = [mock_torrent]

    status = ts.get_status("hash_350h")
    assert status is not None
    assert status["is_completed"] is True
    assert status["seeding_time_hours"] == 350.0
    assert status["ratio"] == 2.5

    hnr = ts.check_hnr_compliance("hash_350h", delete_data_files=True)
    assert hnr["compliant"] is True
    assert "Seed idő elérve: 350.0h" in hnr["reason"]
    ts.client.torrents_delete.assert_called_with(delete_files=True, torrent_hashes="hash_350h")


def test_orchestrator_force_organize_with_progress_callback(tmp_path):
    """Teszteli a kényszerített Plexbe átmozgatást a valós idejű haladás-visszahívással (ETA, sebesség, honnan-hová)."""
    orch = Orchestrator()
    orch.settings.PLEX_MOVIES_DIR = tmp_path / "PlexMovies"
    orch.settings.DOWNLOAD_TEMP_DIR = tmp_path / "Downloads"
    orch.organizer.settings.PLEX_MOVIES_DIR = tmp_path / "PlexMovies"

    src_dir = tmp_path / "Downloads" / "Existing.Movie.2024.1080p"
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "Existing.Movie.2024.mkv"
    src_file.write_bytes(b"EXISTING_VIDEO_BYTES" * 100)

    task_id = "hash_existing_mov"
    task = {
        "task_id": task_id,
        "torrent_hash": task_id,
        "media_item": {"tmdb_id": 55, "title_hu": "Meglévő Film", "year": 2024, "media_type": "movie"},
        "release": {"torrent_id": "r1", "title": "Existing.Movie.2024.1080p", "category": "hd_hun", "size_bytes": 1000},
        "content_path": str(src_file),
        "status": "downloading",
        "plex_organized": False,
    }

    recorded_progress = []
    def _cb(info):
        recorded_progress.append(info)

    with patch.object(orch, "_load_state", return_value={task_id: task}), \
         patch.object(orch, "_save_state"), \
         patch.object(orch.plex_service, "refresh_library"):
        res = orch.force_organize_task(task_id, progress_callback=_cb)
        assert res["success"] is True
        assert task["plex_organized"] is True
        assert task["status"] == "in_plex_and_seeding"
        assert (orch.settings.PLEX_MOVIES_DIR / "Meglévő Film (2024)").exists()


def test_orchestrator_force_delete_and_cleanup_all_seeded():
    """Teszteli a seedelt torrentek kényszerített törlését és a tömeges takarítást."""
    orch = Orchestrator()
    orch.torrent_service = MagicMock()
    orch.torrent_service._ensure_connected.return_value = True

    mock_tasks = {
        "h1": {"task_id": "h1", "torrent_hash": "h1", "status": "in_plex_and_seeding"},
    }
    with patch.object(orch, "_load_state", return_value=mock_tasks), \
         patch.object(orch, "_save_state"):
        del_res = orch.force_delete_task("h1", delete_data=True)
        assert del_res["success"] is True
        assert mock_tasks["h1"]["status"] == "completed_and_removed"
        assert mock_tasks["h1"]["temp_deleted"] is True
        orch.torrent_service.client.torrents_delete.assert_called_with(delete_files=True, torrent_hashes="h1")

    # cleanup_all_seeded_torrents teszt
    orch.torrent_service.list_active_downloads.return_value = [
        {"hash": "h_old", "name": "Old.Torrent", "seeding_time_hours": 350.0, "ratio": 1.5, "is_completed": True},
        {"hash": "h_new", "name": "New.Torrent", "seeding_time_hours": 2.0, "ratio": 0.1, "is_completed": False},
    ]
    with patch.object(orch, "_load_state", return_value={"h_old": {"task_id": "h_old", "plex_organized": True}}), \
         patch.object(orch, "force_delete_task", return_value={"success": True}) as mock_f_del:
        clean_res = orch.cleanup_all_seeded_torrents()
        assert clean_res["success"] is True
        assert clean_res["count"] == 1
        assert "Old.Torrent" in clean_res["names"]
        mock_f_del.assert_called_with("h_old", delete_data=True)


def test_orchestrator_sync_all_qbittorrent_torrents():
    """Teszteli a qBittorrentben lévő összes meglévő torrent szinkronizálását és auto-rendezését."""
    orch = Orchestrator()
    orch.torrent_service = MagicMock()
    orch.torrent_service._ensure_connected.return_value = True

    orch.torrent_service.list_active_downloads.return_value = [
        {"hash": "h_exist", "name": "Dune.Part.Two.2024", "is_completed": True, "seeding_time_hours": 50.0},
    ]
    orch._tasks = {}

    with patch.object(orch, "force_organize_task", return_value={"success": True}) as mock_org, \
         patch.object(orch, "check_and_process_tasks"):
        sync_res = orch.sync_all_qbittorrent_torrents(auto_organize=True)
        assert sync_res["success"] is True
        assert sync_res["total_torrents"] == 1
        assert sync_res["imported_count"] == 1
        assert sync_res["organized_count"] == 1
        mock_org.assert_called_with("h_exist")


def test_cleanup_all_seeded_auto_organizes_unorganized_first():
    """Garantálja, hogy ha a film még nincs a Plexben, a takarítás előtt előbb rendezi (zero data loss)."""
    orch = Orchestrator()
    orch.torrent_service = MagicMock()
    orch.torrent_service._ensure_connected.return_value = True

    orch.torrent_service.list_active_downloads.return_value = [
        {"hash": "h_unorg", "name": "Unorganized.Movie.2024", "seeding_time_hours": 100.0, "ratio": 2.0, "is_completed": True},
    ]

    with patch.object(orch, "_load_state", return_value={"h_unorg": {"plex_organized": False}}), \
         patch.object(orch, "force_organize_task", return_value={"success": True}) as mock_org, \
         patch.object(orch, "force_delete_task", return_value={"success": True}) as mock_del:
        res = orch.cleanup_all_seeded_torrents()
        assert res["success"] is True
        assert res["count"] == 1
        mock_org.assert_called_once_with("h_unorg")
        mock_del.assert_called_once_with("h_unorg", delete_data=True)


def test_attach_or_update_subtitle_directory_season_pack(tmp_path):
    """Teszteli az évadcsomag (.srt könyvtár) kezelését IsADirectoryError nélkül."""
    orch = Orchestrator()
    orch.subtitle_service = MagicMock()
    orch.plex_service = MagicMock()

    # Mappaként érkező évadcsomag felirat szimulálása
    sub_dir = tmp_path / "subtitles_pack"
    sub_dir.mkdir()
    srt1 = sub_dir / "Show.S01E01.720p.srt"
    srt2 = sub_dir / "Show.S01E02.720p.srt"
    srt1.write_text("1\n00:00:01,000 --> 00:00:02,000\nSzia!", encoding="utf-8")
    srt2.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello!", encoding="utf-8")

    orch.subtitle_service.find_and_download_subtitle.return_value = sub_dir

    plex_dest = tmp_path / "Plex" / "Series" / "TestShow" / "Season 1"
    plex_dest.mkdir(parents=True)

    mock_task = {
        "task_id": "t_series",
        "media_item": {
            "tmdb_id": 123,
            "media_type": "tv",
            "title_hu": "Teszt Sorozat",
            "title_en": "Test Show",
            "year": 2024,
        },
        "release": {
            "torrent_id": "1",
            "title": "Test.Show.S01.HUN.WEB-DL",
            "category": "sorozat",
            "size_bytes": 1000,
            "has_hungarian_audio": False,
            "download_url": "",
            "season_num": 1,
        },
        "destination_folder": str(plex_dest),
        "status": "in_plex_and_seeding",
    }

    with patch.object(orch, "_load_state", return_value={"t_series": mock_task}), \
         patch.object(orch, "_save_state"):
        res = orch.attach_or_update_subtitle("t_series")
        assert res["success"] is True
        # Ellenőrizzük, hogy mindkét srt átmásolódott a Plex mappába
        copied_srts = list(plex_dest.glob("*.hu.srt"))
        assert len(copied_srts) == 2


def test_mkv_embedded_hungarian_sub_detection(tmp_path):
    """Teszteli a beágyazott MKV magyar felirat detektálást tiszta fejléc elemzéssel."""
    mkv_file = tmp_path / "movie_with_sub.mkv"
    # Szimulálunk egy Matroska fejlécet S_TEXT/UTF8 és hun nyelvi taggel
    header_bytes = (
        b"\x1a\x45\xdf\xa3"  # EBML ID
        + b"\x00" * 32
        + b"\x16\x54\xae\x6b"  # Tracks ID
        + b"\x00" * 16
        + b"S_TEXT/UTF8"
        + b"\x00" * 8
        + b"\x22\xb5\x9c\x83hun"  # Language: hun
        + b"\x00" * 64
    )
    mkv_file.write_bytes(header_bytes)

    assert MediaOrganizer.check_mkv_embedded_hungarian_sub(mkv_file) is True

    # Nem MKV fájl esetén
    txt_file = tmp_path / "movie.mp4"
    txt_file.write_bytes(header_bytes)
    assert MediaOrganizer.check_mkv_embedded_hungarian_sub(txt_file) is False


def test_series_radar_cross_season_and_season_pack(tmp_path):
    """Teszteli a Sorozat-Radar automatikus évadátlépését és évadcsomag kezelését."""
    from src.core.series_radar import SeriesRadarManager

    data_file = tmp_path / "series_radar.json"
    manager = SeriesRadarManager(data_file=data_file)

    item = MediaItem(
        tmdb_id=999,
        media_type=MediaType.TV,
        title_hu="Fallout",
        title_en="Fallout",
        year=2024,
    )
    manager.add(item, current_season=1, current_episode=0)

    # 1. Évadcsomag detektálása cur_ep == 0 esetén
    mock_orch = MagicMock()
    mock_orch.tmdb_service.get_details.return_value = item
    setattr(item, "seasons", [{"season_number": 1, "episode_count": 8}, {"season_number": 2, "episode_count": 8}])
    setattr(item, "number_of_seasons", 2)

    pack_release = TorrentRelease(
        torrent_id="pack1",
        title="Fallout.S01.Complete.1080p.WEB-DL",
        category="sorozat_hun",
        size_bytes=10000000,
        has_hungarian_audio=True,
        download_url="",
        seeders=15,
    )

    mock_orch.ncore_service.search_by_title.return_value = [pack_release]
    mock_orch.ncore_service.sort_releases.return_value = [pack_release]
    mock_orch.download_specific_release.return_value = {"success": True}

    downloads = manager.check_and_trigger_downloads(mock_orch)
    assert len(downloads) == 1
    assert downloads[0]["next_ep"] == 8  # Teljes évad letöltve!


def test_plex_service_watched_and_playback():
    """Teszteli a megnézett médiaelemek és az aktív lejátszás lekérdezését."""
    plex = PlexService(server_url="http://mock:32400", token="mock_token")
    plex.server = MagicMock()

    # Mock könyvtár megnézett elemekkel
    sec_movies = MagicMock()
    sec_movies.type = "movie"
    m1 = MagicMock()
    m1.title = "Oppenheimer"
    m1.year = 2023
    m1.ratingKey = "101"
    m1.isWatched = True
    m1.viewCount = 2
    part1 = MagicMock()
    part1.file = "D:/Plex/Oppenheimer.mkv"
    part1.size = 25 * 1024 * 1024 * 1024  # 25 GB
    med1 = MagicMock()
    med1.parts = [part1]
    m1.media = [med1]
    sec_movies.search.return_value = [m1]

    plex.server.library.sections.return_value = [sec_movies]

    watched = plex.get_watched_items()
    assert len(watched) == 1
    assert watched[0]["title"] == "Oppenheimer"
    assert "25.00 GB" in watched[0]["size_human"]

    # Mock aktív lejátszási munkamenetek
    s1 = MagicMock()
    s1.usernames = ["Apa"]
    s1.title = "Dűne 2"
    s1.player.title = "LG OLED TV"
    s1.player.state = "playing"
    plex.server.sessions.return_value = [s1]

    assert plex.has_active_playback() is True
    sessions = plex.get_active_sessions()
    assert len(sessions) == 1
    assert sessions[0]["player"] == "LG OLED TV"


def test_torrent_service_speed_limits():
    """Teszteli a sebességkorlátok beállítását és lekérdezését a TorrentService-ben."""
    from src.services.torrent_service import TorrentService
    ts = TorrentService()
    ts.client = MagicMock()

    with patch.object(ts, "_ensure_connected", return_value=True):
        ok = ts.set_speed_limits(download_limit_kbs=5000, upload_limit_kbs=1000)
        assert ok is True
        ts.client.transfer_set_download_limit.assert_called_with(5000 * 1024)
        ts.client.transfer_set_upload_limit.assert_called_with(1000 * 1024)

        ts.client.transfer_info.return_value = {
            "dl_rate_limit": 5000 * 1024,
            "up_rate_limit": 1000 * 1024,
            "dl_info_speed": 4500 * 1024,
            "up_info_speed": 800 * 1024,
        }
        limits = ts.get_speed_limits()
        assert limits["dl_limit_kbs"] == 5000
        assert limits["ul_limit_kbs"] == 1000


def test_background_worker_plex_throttling():
    """Teszteli, hogy a BackgroundWorker automatikusan visszaveszi a torrent sebességet Plex lejátszáskor."""
    from src.core.background_worker import BackgroundWorker

    orch = MagicMock()
    orch._tasks = {}
    orch.plex_service.has_active_playback.return_value = True

    worker = BackgroundWorker(orchestrator=orch, interval_seconds=1)

    # Ciklus futtatása: az első iteráció során kérjük a leállást
    def stop_after_one():
        worker._stop_event.set()

    orch.check_and_process_tasks.side_effect = stop_after_one

    with patch("time.sleep"):
        worker._run_loop()

    assert worker._plex_throttled is True
    orch.torrent_service.set_speed_limits.assert_called_with(download_limit_kbs=5000, upload_limit_kbs=1000)














