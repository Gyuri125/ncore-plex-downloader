import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import Settings, get_settings
from src.core.orchestrator import Orchestrator
from src.core.organizer import MediaOrganizer
from src.models.media import MediaItem, MediaType, SubtitleItem, TorrentRelease
from src.services.ncore_service import NcoreService
from src.services.plex_service import PlexService
from src.services.subtitle_service import SubtitleService
from src.services.tmdb_service import TMDBService
from src.services.torrent_service import TorrentService


# ==============================================================
# 1. KONFIGURÁCIÓ & SETTINGS TESZTEK
# ==============================================================
class TestSettings:
    def test_default_values(self):
        s = Settings(
            TMDB_API_KEY="test_api_key",
            NCORE_USERNAME="test_user",
            NCORE_PASSWORD="test_password",
        )
        assert s.TMDB_API_KEY == "test_api_key"
        assert s.TMDB_LANGUAGE == "hu-HU"
        assert s.MIN_SEED_TIME_HOURS == 48
        assert s.TARGET_RATIO == 1.0
        assert s.AUTO_REMOVE_AFTER_SEED is True
        assert s.TORRENT_CLIENT == "qbittorrent"

    def test_singleton_get_settings(self):
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


# ==============================================================
# 2. ADATMODELLEK (MODELS) TESZTEK
# ==============================================================
class TestModels:
    def test_media_item_movie(self):
        item = MediaItem(
            tmdb_id=155,
            imdb_id="tt0468569",
            media_type=MediaType.MOVIE,
            title_hu="A sötét lovag",
            title_en="The Dark Knight",
            year=2008,
            rating=9.0,
            director="Christopher Nolan",
            cast=["Christian Bale", "Heath Ledger"],
        )
        assert item.tmdb_id == 155
        assert item.imdb_id == "tt0468569"
        assert item.media_type == MediaType.MOVIE
        assert item.director == "Christopher Nolan"
        assert len(item.cast) == 2

    def test_media_item_series(self):
        item = MediaItem(
            tmdb_id=1399,
            imdb_id="tt0944947",
            media_type=MediaType.TV,
            title_hu="Trónok harca",
            title_en="Game of Thrones",
            year=2011,
            number_of_seasons=8,
        )
        assert item.media_type == MediaType.TV
        assert item.number_of_seasons == 8

    def test_torrent_release_model(self):
        rel = TorrentRelease(
            torrent_id="999888",
            title="Gladiator.2000.1080p.BluRay.x264.HuN",
            category="hd_hun",
            size_bytes=10737418240,  # 10 GB
            size_human="10 GB",
            seeders=35,
            leechers=2,
            has_hungarian_audio=True,
            has_hungarian_sub=True,
        )
        assert rel.torrent_id == "999888"
        assert rel.has_hungarian_audio is True
        assert rel.seeders == 35


# ==============================================================
# 3. TMDB SERVICE TESZTEK
# ==============================================================
class TestTMDBService:
    def test_unconfigured_handling(self):
        service = TMDBService(api_key="")
        assert service._is_configured() is False
        results = service.search("Gladiátor")
        assert results == []

    @patch("httpx.Client.get")
    def test_search_mock_success(self, mock_get):
        service = TMDBService(api_key="mock_valid_key")
        
        # Mock search response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": 98,
                    "media_type": "movie",
                    "title": "Gladiátor",
                    "original_title": "Gladiator",
                    "release_date": "2000-05-01",
                    "overview": "Egy római hadvezér története...",
                    "poster_path": "/gladiator.jpg",
                    "vote_average": 8.2,
                }
            ]
        }
        mock_get.return_value = mock_response

        # Mock get_details to avoid second network call
        with patch.object(service, "get_details") as mock_details:
            mock_details.return_value = MediaItem(
                tmdb_id=98,
                imdb_id="tt0172495",
                media_type=MediaType.MOVIE,
                title_hu="Gladiátor",
                title_en="Gladiator",
                year=2000,
                rating=8.2,
            )
            results = service.search("Gladiator")

        assert len(results) == 1
        assert results[0].tmdb_id == 98
        assert results[0].imdb_id == "tt0172495"
        assert results[0].title_hu == "Gladiátor"


# ==============================================================
# 4. NCORE SERVICE TESZTEK
# ==============================================================
class TestNcoreService:
    def test_size_parsing(self):
        assert NcoreService._parse_size_to_bytes("700 MB") == 700 * 1024**2
        assert NcoreService._parse_size_to_bytes("4.35 GB") == int(4.35 * 1024**3)
        assert NcoreService._parse_size_to_bytes("1.2 TB") == int(1.2 * 1024**4)
        assert NcoreService._parse_size_to_bytes("invalid") == 0

    def test_select_best_release_prioritizes_hungarian_hd(self):
        service = NcoreService(username="u", password="p")

        rel_hd_hun = TorrentRelease(
            torrent_id="1",
            title="Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.Atmos.H.264.HuN-FLUX",
            category="hd_hun",
            seeders=50,
            has_hungarian_audio=True,
        )
        rel_hd_eng = TorrentRelease(
            torrent_id="2",
            title="Dune.Part.Two.2024.2160p.UHD.BluRay.x265-FLUX",
            category="hd",
            seeders=120,
            has_hungarian_audio=False,
        )
        rel_cam = TorrentRelease(
            torrent_id="3",
            title="Dune.Part.Two.2024.HDCAM.HuN",
            category="xvid_hun",
            seeders=30,
            has_hungarian_audio=True,
        )

        best = service.select_best_release([rel_cam, rel_hd_eng, rel_hd_hun], prefer_hungarian=True)
        assert best is not None
        assert best.torrent_id == "1"
        assert best.has_hungarian_audio is True

    def test_select_best_release_discards_cam_and_prefers_quality(self):
        service = NcoreService(username="u", password="p")
        rel_cam = TorrentRelease(
            torrent_id="1",
            title="Movie.2024.CAM.HuN",
            category="xvid_hun",
            seeders=50,
            has_hungarian_audio=True,
        )
        rel_hd = TorrentRelease(
            torrent_id="2",
            title="Movie.2024.1080p.BluRay.x264",
            category="hd",
            seeders=40,
            has_hungarian_audio=False,
        )

        # When CAM is penalized, the clean HD release should win
        best = service.select_best_release([rel_cam, rel_hd], prefer_hungarian=False)
        assert best.torrent_id == "2"

    def test_html_parsing_mock(self):
        service = NcoreService(username="u", password="p")
        mock_html = """
        <div class="box_torrent">
            <nobr><a href="torrents.php?action=details&id=12345" title="The.Matrix.1999.1080p.BluRay.HuN">The Matrix</a></nobr>
            <a href="torrents.php?tipus=hd_hun">Cat</a>
            <div class="box_meret2">8.5 GB</div>
            <div class="box_s2">64</div>
            <div class="box_l2">3</div>
        </div>
        """
        releases = service._parse_torrent_list(mock_html)
        assert len(releases) == 1
        r = releases[0]
        assert r.torrent_id == "12345"
        assert r.title == "The.Matrix.1999.1080p.BluRay.HuN"
        assert r.category == "hd_hun"
        assert r.seeders == 64
        assert r.has_hungarian_audio is True


# ==============================================================
# 5. TORRENT SERVICE (QBITTORRENT & HNR) TESZTEK
# ==============================================================
class TestTorrentService:
    def test_service_initialization(self):
        service = TorrentService(host="http://localhost:8080", username="admin", password="pwd")
        assert service.host == "http://localhost:8080"
        assert service.min_seed_hours == 48
        assert service.target_ratio == 1.0

    def test_hnr_compliance_time_met(self):
        service = TorrentService()
        # Mock get_status to return 50 hours seeding, 0.5 ratio
        with patch.object(service, "get_status") as mock_status:
            mock_status.return_value = {
                "hash": "abc123hash",
                "name": "Film.2024",
                "is_completed": True,
                "progress": 100.0,
                "seeding_time_hours": 50.0,  # > 48h!
                "ratio": 0.5,
            }
            # Mock client torrents_delete
            service.client = MagicMock()
            result = service.check_hnr_compliance("abc123hash")

            assert result["compliant"] is True
            assert "Seed idő elérve" in result["reason"]

    def test_hnr_compliance_ratio_met(self):
        service = TorrentService()
        with patch.object(service, "get_status") as mock_status:
            mock_status.return_value = {
                "hash": "abc123hash",
                "name": "Film.2024",
                "is_completed": True,
                "progress": 100.0,
                "seeding_time_hours": 12.0,  # < 48h
                "ratio": 1.25,              # > 1.0!
            }
            service.client = MagicMock()
            result = service.check_hnr_compliance("abc123hash")

            assert result["compliant"] is True
            assert "Arány elérve" in result["reason"]

    def test_hnr_compliance_not_met_yet(self):
        service = TorrentService()
        with patch.object(service, "get_status") as mock_status:
            mock_status.return_value = {
                "hash": "abc123hash",
                "name": "Film.2024",
                "is_completed": True,
                "progress": 100.0,
                "seeding_time_hours": 10.0,
                "ratio": 0.3,
            }
            result = service.check_hnr_compliance("abc123hash")
            assert result["compliant"] is False


# ==============================================================
# 6. FELIRATKEZELŐ (SUBTITLE SERVICE) TESZTEK
# ==============================================================
class TestSubtitleService:
    def test_decode_hungarian_text_varieties(self):
        test_hungarian = "Árvíztűrő tükörfúrógép"

        # UTF-8
        assert SubtitleService._decode_hungarian_text(test_hungarian.encode("utf-8")) == test_hungarian

        # CP1250 (Windows)
        assert SubtitleService._decode_hungarian_text(test_hungarian.encode("cp1250")) == test_hungarian

        # ISO-8859-2 (Latin 2)
        assert SubtitleService._decode_hungarian_text(test_hungarian.encode("iso-8859-2")) == test_hungarian

    def test_subtitle_matching_score(self):
        service = SubtitleService()
        candidates = [
            {"text": "Gladiator.2000.720p.HDTV.srt", "url": "http://sub1.srt"},
            {"text": "Gladiator.2000.1080p.BluRay.FLUX.srt", "url": "http://sub2.srt"},
            {"text": "Gladiator.2000.CAM.srt", "url": "http://sub3.srt"},
        ]
        release_title = "Gladiator.2000.1080p.BluRay.x264-FLUX"
        best = service._pick_best_subtitle_match(candidates, release_title)
        assert best is not None
        assert best["url"] == "http://sub2.srt"


# ==============================================================
# 7. MEDIA ORGANIZER (PLEX RENDEZŐ ÉS HARDLINK) TESZTEK
# ==============================================================
class TestMediaOrganizer:
    def test_filename_sanitization(self):
        organizer = MediaOrganizer()
        assert organizer._sanitize_filename('Film: /"Cím"*?<>|') == "Film Cím"

    def test_quality_tag_extraction(self):
        organizer = MediaOrganizer()
        assert organizer._extract_quality_tag("Avatar.2009.2160p.UHD.Remux") == "4K BluRay"
        assert organizer._extract_quality_tag("Oppenheimer.2023.1080p.WEB-DL") == "1080p WEB-DL"
        assert organizer._extract_quality_tag("Unknown.Release") == "HD"

    def test_season_episode_extraction(self):
        organizer = MediaOrganizer()
        s, e = organizer._extract_season_episode("Stranger.Things.S04E07.1080p.mkv")
        assert s == 4 and e == 7

        s2, e2 = organizer._extract_season_episode("The.Office.1x01.Pilot.avi")
        assert s2 == 1 and e2 == 1

        s3, e3 = organizer._extract_season_episode("RandomMovie.2022.mkv")
        assert s3 is None and e3 is None

    def test_organize_movie_with_hardlink(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_dir = tmp_path / "downloads"
            movies_dir = tmp_path / "plex_movies"
            source_dir.mkdir()
            movies_dir.mkdir()

            # Create dummy media file
            dummy_video = source_dir / "Gladiator.2000.1080p.BluRay.mkv"
            dummy_video.write_bytes(b"MOCK_VIDEO_DATA_0000000000000")

            organizer = MediaOrganizer()
            organizer.settings.PLEX_MOVIES_DIR = movies_dir

            media_item = MediaItem(
                tmdb_id=98,
                title_hu="Gladiátor",
                year=2000,
                media_type=MediaType.MOVIE,
            )
            release = TorrentRelease(
                torrent_id="1",
                title="Gladiator.2000.1080p.BluRay",
                category="hd_hun",
            )

            placed = organizer.organize_media(source_dir, media_item, release)
            assert len(placed) >= 1
            expected_folder = movies_dir / "Gladiátor (2000)"
            assert expected_folder.exists()
            assert any(f.name.startswith("Gladiátor (2000) [1080p BluRay]") for f in expected_folder.iterdir())


# ==============================================================
# 8. PLEX SERVICE TESZTEK
# ==============================================================
class TestPlexService:
    def test_unconfigured_plex(self):
        service = PlexService(server_url="", token="")
        assert service._is_configured() is False
        assert service.refresh_library() is False

    @patch("src.services.plex_service.PlexServer")
    def test_refresh_library_calls_update(self, mock_plex_server_cls):
        service = PlexService(server_url="http://localhost:32400", token="valid_token")
        mock_server = MagicMock()
        mock_section = MagicMock()
        mock_section.type = "movie"
        mock_section.title = "Filmek"
        mock_server.library.sections.return_value = [mock_section]
        service.server = mock_server

        res = service.refresh_movies()
        assert res is True
        mock_section.update.assert_called_once()


# ==============================================================
# 9. ORCHESTRATOR INTEGRÁCIÓS TESZTEK
# ==============================================================
class TestOrchestrator:
    def test_orchestrator_initialization(self):
        orchestrator = Orchestrator()
        assert orchestrator.tmdb_service is not None
        assert orchestrator.ncore_service is not None
        assert orchestrator.torrent_service is not None
        assert orchestrator.organizer is not None
        assert orchestrator.plex_service is not None

    def test_search_delegates_to_tmdb(self):
        orchestrator = Orchestrator()
        with patch.object(orchestrator.tmdb_service, "search") as mock_search:
            mock_search.return_value = [
                MediaItem(tmdb_id=1, title_hu="Dűne", year=2021)
            ]
            res = orchestrator.search_media("Dűne")
            assert len(res) == 1
            assert res[0].title_hu == "Dűne"
