import pytest
from pathlib import Path
from src.config.settings import Settings
from src.models.media import MediaItem, MediaType, TorrentRelease
from src.services.ncore_service import NcoreService
from src.core.organizer import MediaOrganizer
from src.services.subtitle_service import SubtitleService


def test_settings_defaults():
    s = Settings(TMDB_API_KEY="test_key", NCORE_USERNAME="user", NCORE_PASSWORD="pwd")
    assert s.TMDB_API_KEY == "test_key"
    assert s.MIN_SEED_TIME_HOURS == 48
    assert s.TARGET_RATIO == 1.0


def test_media_item_model():
    item = MediaItem(
        tmdb_id=123,
        imdb_id="tt0111161",
        media_type=MediaType.MOVIE,
        title_hu="A remény rabjai",
        title_en="The Shawshank Redemption",
        year=1994,
        rating=9.3,
    )
    assert item.tmdb_id == 123
    assert item.imdb_id == "tt0111161"
    assert item.media_type == MediaType.MOVIE


def test_ncore_scoring_and_ranking():
    service = NcoreService(username="test", password="test")
    
    # 3 releases: 1 HD HUN, 1 HD ENG, 1 CAM HUN
    rel_hun_hd = TorrentRelease(
        torrent_id="1",
        title="Gladiator.2000.1080p.BluRay.x264.HuN",
        category="hd_hun",
        seeders=45,
        has_hungarian_audio=True,
    )
    rel_eng_hd = TorrentRelease(
        torrent_id="2",
        title="Gladiator.2000.1080p.BluRay.x264-FLUX",
        category="hd",
        seeders=80,
        has_hungarian_audio=False,
    )
    rel_cam_hun = TorrentRelease(
        torrent_id="3",
        title="Gladiator.2000.CAM.HuN",
        category="xvid_hun",
        seeders=10,
        has_hungarian_audio=True,
    )

    best = service.select_best_release([rel_cam_hun, rel_eng_hd, rel_hun_hd], prefer_hungarian=True)
    assert best is not None
    assert best.torrent_id == "1"  # HD HUN must win!


def test_organizer_helpers():
    organizer = MediaOrganizer()
    
    # Filename sanitizer
    assert organizer._sanitize_filename('Film: /"Teszt"*?<>|') == "Film Teszt"
    
    # Quality tag extractor
    assert "1080p" in organizer._extract_quality_tag("Avatar.2009.1080p.BluRay.x264")
    assert "BluRay" in organizer._extract_quality_tag("Avatar.2009.1080p.BluRay.x264")
    
    # Season/Episode extraction
    season, episode = organizer._extract_season_episode("Game.of.Thrones.S03E09.1080p.mkv")
    assert season == 3
    assert episode == 9
    
    s2, ep2 = organizer._extract_season_episode("Breaking.Bad.2x04.HDTV.avi")
    assert s2 == 2
    assert ep2 == 4


def test_subtitle_encoding():
    # CP1250 text with Hungarian accents (á, é, ő, ű)
    text = "Árvíztűrő tükörfúrógép"
    cp1250_bytes = text.encode("cp1250")
    
    decoded = SubtitleService._decode_hungarian_text(cp1250_bytes)
    assert decoded == text


def test_save_settings_helper(tmp_path):
    from src.config.settings import save_settings, get_settings
    env_file = tmp_path / ".env"
    env_file.write_text("TMDB_API_KEY=old_key\nNCORE_USERNAME=old_user\n", encoding="utf-8")
    
    success = save_settings({"NCORE_USERNAME": "new_user", "MIN_SEED_TIME_HOURS": "72"}, env_path=env_file)
    assert success is True
    
    content = env_file.read_text(encoding="utf-8")
    assert "NCORE_USERNAME=new_user" in content
    assert "MIN_SEED_TIME_HOURS=72" in content
    assert "TMDB_API_KEY=old_key" in content


def test_series_season_extraction_and_matching():
    from src.models.media import extract_season_tag, extract_season_num

    assert extract_season_num("The.Vampire.Diaries.S08.1080p.BluRay") == 8
    assert extract_season_tag("The.Vampire.Diaries.S08.1080p.BluRay") == "S08 (8. Évad)"

    assert extract_season_num("Vámpírnaplók S07 720p") == 7
    assert extract_season_tag("Vámpírnaplók S07 720p") == "S07 (7. Évad)"

    assert extract_season_num("Breaking.Bad.1.Evad.Hun") == 1
    assert extract_season_tag("Breaking.Bad.1.Evad.Hun") == "S01 (1. Évad)"

    assert extract_season_num("The.Walking.Dead.S01-S11.Complete") == -1
    assert "Csomag" in extract_season_tag("The.Walking.Dead.S01-S11.Complete")

    rel = TorrentRelease(
        torrent_id="99",
        title="The.Vampire.Diaries.S08.1080p.BluRay.REMUX",
        category="hdser_hun",
        has_hungarian_audio=True
    )
    assert rel.season_num == 8
    assert rel.season_tag == "S08 (8. Évad)"

    # Series year difference should not disqualify release (debut 2009, S08 aired 2017)
    service = NcoreService(username="test", password="test")
    series_item = MediaItem(
        tmdb_id=18165,
        media_type=MediaType.TV,
        title_hu="Vámpírnaplók",
        title_en="The Vampire Diaries",
        year=2009,
    )
    assert service.verify_release_match(rel, series_item) is True
